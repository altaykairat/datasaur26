"""Engine Subpackage: Batch Processor

Encapsulates the high-performance logic for routing entire DataFrames of tickets,
integrating OCR extraction, idempotency checks, AI enrichment, and DB storage.
"""
import pandas as pd
from typing import Callable, Optional

from database.connection import get_db
from database.models import Ticket, TicketStatus
from utils.ocr import is_ocr_available, extract_text_from_image, combine_description_with_ocr
from utils.logging import get_safe_logger
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

                try:
                    # ---- Extract fields with safe defaults ----
                    description = str(row.get("Описание ", "") or row.get("Описание", "") or "")
                    segment = str(row.get("Сегмент клиента", "Mass") or "Mass")
                    city = str(row.get("Населённый пункт", "") or "")
                    region = str(row.get("Область", "") or "")
                    guid = str(row.get("GUID клиента", ""))

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

                    # OCR extraction
                    if is_ocr_available():
                        for img_col in IMAGE_COLUMN_VARIANTS:
                            img_source = row.get(img_col, "")
                            if img_source and str(img_source).strip():
                                source_path = str(img_source).strip()
                                import os
                                if not os.path.isabs(source_path) and not source_path.startswith("input/attachments"):
                                    possible_path = os.path.join("input", "attachments", source_path)
                                    if os.path.exists(possible_path):
                                        source_path = possible_path
                                
                                flags["attachment_path"] = source_path
                                ocr_result = extract_text_from_image(source_path)
                                if ocr_result["success"]:
                                    description = combine_description_with_ocr(description, ocr_result["text"])
                                    flags["ocr_extracted"] = True
                                    flags["ocr_source"] = ocr_result["source_type"]
                                    flags["ocr_chars"] = len(ocr_result["text"])
                                    logger.info(f"Row {idx}: OCR extracted {len(ocr_result['text'])} chars from {img_col}")
                                    break
                                else:
                                    flags["ocr_failed"] = ocr_result.get("error", "unknown")

                    if not description.strip():
                        flags["empty_description"] = True

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
                    ai_analysis = self.router._enrich(description)

                    for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                               "needs_clarification", "language_confidence"):
                        if fk in ai_analysis:
                            flags[fk] = ai_analysis.pop(fk)

                    # ---- Spam check (early exit) ----
                    is_spam = ai_analysis["type"] == "Спам"
                    if is_spam:
                        flags["spam_detected"] = True
                        result = self._handle_spam(db, idx, guid, segment, ai_analysis, city, row, flags, correlation_id, description)
                        results.append(result)
                        if progress_callback:
                            progress_callback(idx + 1, total, result)
                        continue

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
                        client_guid=guid if guid else None,
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
                    result = {
                        "ticket_index": idx,
                        "client_guid": guid,
                        "ai_type": "",
                        "ai_priority": 0,
                        "ai_language": "",
                        "ai_sentiment": "",
                        "ai_address": "",
                        "ai_summary": "",
                        "routed_office": "",
                        "office_rule": "row_error",
                        "assigned_manager": "Error",
                        "assigned_manager_id": None,
                        "segment": "",
                        "status": TicketStatus.DEAD_LETTER.value,
                        "flags": {"row_processing_error": str(e)},
                        "correlation_id": correlation_id,
                    }
                    results.append(result)

                if progress_callback:
                    progress_callback(idx + 1, total, result)

            db.flush()

        return pd.DataFrame(results)

    def _handle_spam(self, db, idx, guid, segment, ai_analysis, city, row, flags, correlation_id, description):
        """Handle saving of spam tickets."""
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
            "routed_office": "N/A",
            "office_rule": "spam_skip",
            "assigned_manager": "Spam — not assigned",
            "assigned_manager_id": None,
            "segment": segment,
            "status": TicketStatus.SPAM.value,
            "flags": flags,
            "correlation_id": correlation_id,
        }
        ticket = Ticket(
            client_guid=guid if guid else None,
            correlation_id=correlation_id,
            description=description[:5000],
            status=TicketStatus.SPAM.value,
            assigned_manager_id=None,
            ai_analysis_json=ai_analysis,
            segment=segment,
            client_city=city,
            client_address=f"{row.get('Улица', '')}, {row.get('Дом', '')}".strip(", "),
            office_rule="spam_skip",
            routed_branch_id=None,
            alternative_branches=[],
            routing_trace={"spam": True},
            flags=flags,
        )
        db.add(ticket)
        return result
