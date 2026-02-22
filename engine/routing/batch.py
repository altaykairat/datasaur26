"""Engine Subpackage: Batch Processor

Encapsulates the high-performance logic for routing entire DataFrames of tickets,
integrating OCR extraction, idempotency checks, AI enrichment, and DB storage.
"""
import pandas as pd
from typing import Callable, Optional
import uuid
import re

from database.connection import get_db
from database.models import Ticket, TicketStatus
from utils.ocr import (
    is_ocr_available, extract_text_from_image, combine_description_with_ocr, resolve_image_path
)
from utils.logging import get_safe_logger
from utils.concurrency import ModelConcurrency
from engine.routing.geo import GeoRouter
from engine.routing.skills import SkillMatcher
from engine.routing.load_balancer import LoadBalancer

logger = get_safe_logger("batch_processor")

DESCRIPTION_VARIANTS = {"Описание ", "Описание"}
IMAGE_COLUMN_VARIANTS = {"Скриншот", "Вложение", "Вложения", "Screenshot", "Attachment", "Image"}
REQUIRED_CSV_COLUMNS = {"GUID клиента", "Сегмент клиента", "Населённый пункт", "Область"}

class BatchProcessor:
    """Handles routing operations for massive batch imports (CSV)."""

    def __init__(self, router):
        """Bind to the main TicketRouter orchestrator."""
        self.router = router

    def process_csv(self, df: pd.DataFrame, progress_callback: Optional[Callable] = None) -> pd.DataFrame:
        """
        Route an entire CSV DataFrame of tickets.
        """
        # ---- Schema Validation ----
        actual_columns = set(df.columns)

        has_description = bool(actual_columns & DESCRIPTION_VARIANTS)
        if not has_description:
            raise ValueError(
                f"CSV missing required description column. "
                f"Expected one of: {DESCRIPTION_VARIANTS}. Got: {actual_columns}"
            )

        missing = REQUIRED_CSV_COLUMNS - actual_columns
        if missing:
            raise ValueError(
                f"CSV missing required columns: {missing}. "
                f"Available columns: {actual_columns}"
            )

        results = []
        total = len(df)

        with get_db() as db:
            offices = self.router._load_offices(db)

            for idx, row in df.iterrows():
                correlation_id = self.router._generate_correlation_id()
                result = None # Initialize to prevent leakage in except block
                guid = str(row.get("GUID клиента", "")).strip() or str(uuid.uuid4())
                description = "Not extracted"
                segment = "Mass"
                flags = {}

                try:
                    # ---- Extract fields with safe defaults ----
                    original_description = str(row.get("Описание ", "") or row.get("Описание", "") or "")
                    description = original_description
                    segment = str(row.get("Сегмент клиента", "Mass") or "Mass")
                    city = str(row.get("Населённый пункт", "") or "")
                    region = str(row.get("Область", "") or "")
                    guid = str(row.get("GUID клиента", "")).strip() or str(uuid.uuid4())

                    flags = {}
                    
                    # Extract Gender and Age
                    gender_raw = str(row.get("Пол клиента", ""))
                    if gender_raw:
                        if gender_raw.lower() == "мужской":
                            flags["client_gender"] = "Мужчина"
                        elif gender_raw.lower() == "женский":
                            flags["client_gender"] = "Женщина"
                        else:
                            flags["client_gender"] = gender_raw
                        
                    dob_raw = str(row.get("Дата рождения", ""))
                    if dob_raw:
                        try:
                            from datetime import datetime
                            dob_date = datetime.strptime(dob_raw.split()[0], "%Y-%m-%d")
                            flags["client_age"] = datetime.now().year - dob_date.year
                        except Exception:
                            pass

                    for img_col in IMAGE_COLUMN_VARIANTS:
                        img_source = row.get(img_col, "")
                        if img_source and str(img_source).strip():
                            source_path = resolve_image_path(str(img_source).strip())
                            if source_path:
                                flags["attachment_path"] = source_path
                                # OCR extraction (optional if available)
                                if is_ocr_available():
                                    ocr_result = extract_text_from_image(source_path)
                                    if ocr_result["success"]:
                                        description = combine_description_with_ocr(description, ocr_result["text"])
                                        flags["ocr_extracted"] = True
                                        flags["ocr_source"] = ocr_result["source_type"]
                                        flags["ocr_chars"] = len(ocr_result["text"])
                                        logger.info(f"Row {idx}: OCR extracted {len(ocr_result['text'])} chars from {img_col}")
                                    else:
                                        flags["ocr_failed"] = ocr_result.get("error", "unknown")
                                break

                    # Check if vision should run based on model mode
                    can_run_vision = (self.router.ai_engine.mode == "qwen")
                    if flags.get("attachment_path") and not can_run_vision:
                        flags["needs_review"] = True # Attachment exists but no vision capability for this model

                    flags["is_empty_text"] = not original_description.strip()
                    is_empty_ticket = flags["is_empty_text"] and not flags.get("attachment_path")
                    if is_empty_ticket:
                        flags["needs_review"] = True
                        flags["empty_ticket"] = True

                    has_link = False
                    if not is_empty_ticket:
                        has_link = bool(re.search(r"(?i)\b(?:https?://|www\.)\S+|\b[a-zA-Z0-9.-]+\.(?:com|ru|kz|net|org|info|biz|su|cc|io|me|co)\b(?:/\S*)?", description))
                        if has_link:
                            flags["contains_link"] = True
                            flags["needs_review"] = True

                    # ---- Idempotency Check ----
                    if guid:
                        existing = db.query(Ticket).filter(Ticket.client_guid == guid).first()
                        if existing:
                            ai_json = existing.ai_analysis_json or {}
                            flags["duplicate_ticket_id"] = True
                            result = {
                                "ticket_index": idx,
                                "client_guid": guid,
                                "ai_type": ai_json.get("type", ""),
                                "ai_priority": ai_json.get("priority", 5),
                                "ai_language": ai_json.get("language", ""),
                                "ai_sentiment": ai_json.get("sentiment", ""),
                                "ai_address": ai_json.get("normalized_address", "Unknown"),
                                "ai_summary": ai_json.get("summary", ""),
                                "routed_office": existing.client_city,
                                "office_rule": "duplicate_skipped",
                                "assigned_manager": "Duplicate — see original",
                                "assigned_manager_id": existing.assigned_manager_id,
                                "segment": existing.segment,
                                "status": "DUPLICATE",
                                "flags": flags,
                                "correlation_id": correlation_id,
                            }
                            results.append(result)
                            if progress_callback:
                                progress_callback(idx + 1, total, result)
                            continue

                    # ---- AI Enrichment ----
                    if is_empty_ticket:
                        ai_analysis = {
                            "type": "Неизвестно", "priority": 5, "language": "RU",
                            "sentiment": "Нейтральный", "confidence": 0.0,
                            "summary": "No description or attachment provided."
                        }
                    elif has_link:
                        ai_analysis = self.router._enrich(description)
                        # The enrichment will already have the link warning if handled by router
                        # but we ensure the sentiment is normalized by the engine.
                    else:
                        ai_analysis = self.router._enrich(description)

                        # Vision processing step for batch
                        # Run vision ONLY for qwen mode (user request)
                        if flags.get("attachment_path") and self.router.ai_engine.mode == "qwen":
                            # If ai_fallback is true, use a generic instruction
                            instruction = ai_analysis.get("image_extraction_instruction", "Опиши детали на изображении.")
                            try:
                                with ModelConcurrency.get_vision_semaphore():
                                    vision_res = self.router.vision_engine.analyze_image(
                                        flags["attachment_path"], instruction
                                    )
                                    
                                if vision_res.get("has_error_message"):
                                    flags["image_has_error"] = True
                                if not vision_res.get("vision_fallback"):
                                    ai_analysis["summary"] += f" [Вложение: {vision_res.get('image_summary')}]"
                                    if vision_res.get("extracted_text"):
                                        ai_analysis["summary"] += f" [Извлеченный текст: {vision_res.get('extracted_text')}]"
                            except Exception as ve:
                                logger.error(f"Vision enrichment failure (batch) Row {idx}: {ve}")
                                flags["vision_processing_failed"] = True

                        # ---- FLAG LOGIC (Mutually Exclusive) ----
                        is_spam = ai_analysis["type"] == "Спам"
                        is_fraud = ai_analysis["type"] == "Мошеннические действия"
                        has_attachment = bool(flags.get("attachment_path"))
                        is_empty_text = flags.get("is_empty_text", False)
                        has_link = flags.get("contains_link", False)

                        # 1. Review (Yellow)
                        if is_spam or (has_attachment and is_empty_text):
                            flags["needs_review"] = True
                            flags["needs_clarification"] = False
                        # 2. Clarify (Green)
                        elif (has_attachment and not is_empty_text) or (has_link and not is_spam and not is_fraud):
                            flags["needs_clarification"] = True
                            flags["needs_review"] = False
                        else:
                            # Clear flags if conditions no longer met
                            flags["needs_review"] = False
                            flags["needs_clarification"] = False

                        for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                                   "language_confidence"):
                            if fk in ai_analysis:
                                flags[fk] = ai_analysis.pop(fk)

                        confidence = ai_analysis.get("confidence", 0.5)

                        # Manual/Low confidence flags (secondary to the main rules above)
                        if confidence < 0.60 and not is_spam and not is_fraud:
                            # If it's already Review, keep it Review. Otherwise Clarify.
                            if not flags.get("needs_review"):
                                flags["needs_clarification"] = True
                        elif 0.60 <= confidence < 0.85 and not is_spam and not is_fraud:
                            # If it's already Review, keep it Review. Otherwise Clarify.
                            if not flags.get("needs_review") and not flags.get("needs_clarification"):
                                pass

                    is_fraud = not is_empty_ticket and ai_analysis.get("type") == "Мошеннические действия"
                    bypass_assignment = is_empty_ticket or flags.get("needs_review") or is_fraud

                    if not bypass_assignment:
                        # ---- Geo-routing ----
                        geo_res = GeoRouter.route(ai_analysis, city, region, offices, guid)
                        office_city = geo_res["city"]
                        routed_branch_id = geo_res["branch_id"]
                        office_rule = geo_res["rule"]
                        alternative_branches = geo_res.get("alternative_branches", [])
                        flags.update(geo_res.get("flags", {}))

                        # ---- Skill filter ----
                        candidates, filter_trace = SkillMatcher.filter_managers(
                            db, routed_branch_id, office_city, segment, ai_analysis["type"], ai_analysis["language"]
                        )
                        if filter_trace.get("no_candidate_after_hardskills"):
                            flags["no_candidate_after_hardskills"] = True

                        # ---- Load balance + RR ----
                        manager, lb_trace = LoadBalancer.balance(candidates, routed_branch_id, db)

                        if manager:
                            manager.current_load += 1
                            db.flush()
                            if routed_branch_id is None:
                                routed_branch_id = manager.office_id

                        if lb_trace.get("invalid_load_coerced"):
                            flags["invalid_load"] = True
                    else:
                        manager = None
                        office_city = city
                        routed_branch_id = None
                        office_rule = "assignment_bypassed" if not is_empty_ticket else "empty_ticket_skipped"
                        alternative_branches = []
                        geo_res = {"city": city, "branch_id": None, "rule": office_rule}
                        filter_trace = {"skipped": True}
                        lb_trace = {"skipped": True}

                    routing_trace = {
                        "geo_decision": {
                            "resolved_city": geo_res.get("city"),
                            "branch_id": routed_branch_id,
                            "branch_name": geo_res.get("branch_name"),
                            "rule": office_rule,
                            "distance_km": geo_res.get("distance_km"),
                        },
                        "skill_filter": filter_trace,
                        "load_balance": lb_trace,
                    }

                    ticket_status = TicketStatus.ASSIGNED.value if manager else TicketStatus.ROUTING_FAILED.value

                    # Override status for flags
                    if is_fraud:
                        ticket_status = TicketStatus.ROUTING_FAILED.value # Red
                    elif flags.get("needs_review"):
                        ticket_status = TicketStatus.NEEDS_REVIEW.value
                    elif flags.get("needs_clarification"):
                        ticket_status = TicketStatus.NEEDS_CLARIFICATION.value

                    result = {
                        "ticket_index": idx,
                        "client_guid": guid,
                        "ai_type": ai_analysis["type"],
                        "ai_priority": ai_analysis["priority"],
                        "ai_language": ai_analysis["language"],
                        "ai_sentiment": ai_analysis["sentiment"],
                        "ai_address": ai_analysis.get("normalized_address", "Unknown"),
                        "ai_summary": ai_analysis.get("summary", ""),
                        "ai_confidence": ai_analysis.get("confidence", 0.5),
                        "routed_office": office_city,
                        "routed_branch_id": routed_branch_id,
                        "alternative_branches": alternative_branches,
                        "office_rule": office_rule,
                        "assigned_manager": manager.name if manager else "Unassigned",
                        "assigned_manager_id": manager.id if manager else None,
                        "segment": segment,
                        "status": ticket_status,
                        "flags": flags,
                        "correlation_id": correlation_id,
                    }
                    results.append(result)

                    # ---- Save ticket to DB ----
                    ticket = Ticket(
                        client_guid=guid,
                        correlation_id=correlation_id,
                        description=description[:5000],
                        status=ticket_status,
                        assigned_manager_id=result["assigned_manager_id"],
                        ai_analysis_json=ai_analysis,
                        segment=segment,
                        client_city=city,
                        client_address=f"{row.get('Улица', '')}, {row.get('Дом', '')}".strip(", "),
                        office_rule=office_rule,
                        routed_branch_id=routed_branch_id,
                        alternative_branches=alternative_branches,
                        routing_trace=routing_trace,
                        flags=flags,
                        workload_at_assignment=lb_trace.get("workload_at_assignment"),
                    )
                    db.add(ticket)

                except Exception as e:
                    logger.error(f"Row {idx} failed: {e}")
                    
                    # ---- Save FAILED ticket to DB for visibility ----
                    ticket = Ticket(
                        client_guid=guid,
                        correlation_id=correlation_id,
                        description=description[:5000],
                        status=TicketStatus.DEAD_LETTER.value,
                        segment=segment if 'segment' in locals() else "Mass",
                        flags={"row_processing_error": str(e), "needs_review": True, **flags},
                    )
                    db.add(ticket)
                    
                    if result is None:
                        # Construct a basic result so the UI doesn't crash
                        result = {
                            "ticket_index": idx, "client_guid": guid, "ai_type": "Error",
                            "status": "ERROR", "flags": flags, "correlation_id": correlation_id
                        }
                    results.append(result)

                if progress_callback:
                    progress_callback(idx + 1, total, result)

            db.flush()

        return pd.DataFrame(results)


