"""Ticket Router — Orchestrator

This module is the entry point for ticket routing. It initializes the Intelligence Engine
and delegates geographic matching, skill filtering, load balancing, and batch processing
to the specialized classes in the `engine.routing` subpackage.
"""
import uuid
import pandas as pd
from typing import Optional
import re

from utils.logging import get_safe_logger
from database.connection import get_db
from database.models import Office, Ticket, TicketStatus
from engine.intelligence import IntelligenceEngine, VisionEngine
from utils.concurrency import ModelConcurrency

# Import OOP subcomponents
from engine.routing.geo import GeoRouter
from engine.routing.skills import SkillMatcher
from engine.routing.load_balancer import LoadBalancer
from engine.routing.batch import BatchProcessor

logger = get_safe_logger("router_orchestrator")

class TicketRouter:
    """Orchestrates the FIRE routing pipeline."""

    def __init__(self, ai_mode: str = "qwen"):
        self.ai_engine = IntelligenceEngine(mode=ai_mode)
        self.vision_engine = VisionEngine(mode="minicpm-v")
        self._offices_cache = None

    def _load_offices(self, db) -> list[dict]:
        """Load and cache office data."""
        if self._offices_cache is None:
            offices = db.query(Office).all()
            self._offices_cache = [
                {"id": o.id, "city": o.city, "name": o.name, "lat": o.lat, "lon": o.lon}
                for o in offices
            ]
        return self._offices_cache

    def _generate_correlation_id(self) -> str:
        return str(uuid.uuid4())

    def _enrich(self, description: str) -> dict:
        """Run AI enrichment with fallback handling."""
        try:
            with ModelConcurrency.get_text_semaphore():
                return self.ai_engine.analyze_ticket(description)
        except Exception as e:
            logger.error(f"AI enrichment structural failure: {e}", exc_info=True)
            return {
                "type": "Неизвестно",
                "priority": 5,
                "language": "RU",
                "sentiment": "neutral",
                "normalized_address": "Unknown",
                "summary": "Processing failed.",
                "confidence": 0.0,
                "ai_fallback": True,
                "ai_schema_error": str(e)
            }

    def route_single_ticket(self, ticket_description: str, segment: str = "Mass",
                            client_city: str = None, client_region: str = None,
                            ticket_id: str = "", db=None, attachment_path: str = None) -> dict:
        """
        Route a single ticket through the full pipeline.
        
        Returns dict with: ai_analysis, assigned_manager_id, assigned_manager_name,
        office_city, office_rule, routing_trace, flags, correlation_id.
        """
        flags = {}
        correlation_id = self._generate_correlation_id()

        # Optionally handle external DB session
        close_db = False
        db_context = None
        if db is None:
            db_context = get_db()
            db = db_context.__enter__()
            close_db = True

        try:
            if attachment_path:
                flags["attachment_path"] = attachment_path
                
            flags["is_empty_text"] = not ticket_description.strip()
            is_empty_ticket = flags["is_empty_text"] and not attachment_path
            if is_empty_ticket:
                flags["empty_ticket"] = True
                flags["needs_review"] = True

            has_link = False
            if not is_empty_ticket:
                has_link = bool(re.search(r"(?i)\b(?:https?://|www\.)\S+|\b[a-zA-Z0-9.-]+\.(?:com|ru|kz|net|org|info|biz|su|cc|io|me|co)\b(?:/\S*)?", ticket_description))
                if has_link:
                    flags["contains_link"] = True
                    flags["needs_review"] = True

            # 1. AI Enrichment
            if is_empty_ticket:
                ai_analysis = {
                    "type": "Неизвестно", "priority": 5, "language": "RU",
                    "sentiment": "Нейтральный", "confidence": 0.0,
                    "summary": "No description or attachment provided."
                }
            else:
                ai_analysis = self._enrich(ticket_description)
                
                # Safety prefix for links
                if has_link:
                    ai_analysis["summary"] = f"⚠️ [В ТЕКСТЕ ОБНАРУЖЕНА ССЫЛКА] " + ai_analysis.get("summary", "")
                
                # Vision processing step
                # Run vision ONLY for qwen mode (user request)
                can_run_vision = (self.ai_engine.mode == "qwen")
                if attachment_path and can_run_vision:
                    # If ai_fallback is true, use a generic instruction
                    instruction = ai_analysis.get("image_extraction_instruction", "Опиши детали на изображении.")
                    try:
                        with ModelConcurrency.get_vision_semaphore():
                            vision_res = self.vision_engine.analyze_image(attachment_path, instruction)
                            
                        # Merge vision results deterministically
                        if vision_res.get("has_error_message"):
                            flags["image_has_error"] = True
                        if not vision_res.get("vision_fallback"):
                            ai_analysis["summary"] += f" [Вложение: {vision_res.get('image_summary')}]"
                            if vision_res.get("extracted_text"):
                                ai_analysis["summary"] += f" [Извлеченный текст: {vision_res.get('extracted_text')}]"
                    except Exception as ve:
                        logger.error(f"Vision enrichment failure: {ve}", exc_info=True)
                        flags["vision_processing_failed"] = True
                # ---- FLAG LOGIC (Mutually Exclusive) ----
                is_spam = (ai_type == "Спам")
                is_fraud = (ai_type == "Мошеннические действия")
                has_attachment = bool(attachment_path)
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
                    flags["needs_review"] = False
                    flags["needs_clarification"] = False

                for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                           "language_confidence"):
                    if fk in ai_analysis:
                        flags[fk] = ai_analysis.pop(fk)

                confidence = ai_analysis.get("confidence", 0.5)

                is_spam = ai_analysis["type"] == "Спам"
                if is_spam and confidence >= 0.60:
                    flags["spam_detected"] = True
                elif is_spam:
                    flags["needs_review"] = True

                # Fraud check override
                if ai_analysis["type"] == "Мошеннические действия" and confidence < 0.8:
                    flags["needs_review"] = True
                elif confidence < 0.60 and not is_spam:
                    flags["needs_clarification"] = True
                elif 0.60 <= confidence < 0.85 and not is_spam:
                    flags["needs_review"] = True

            is_fraud = not is_empty_ticket and ai_analysis.get("type") == "Мошеннические действия"
            bypass_assignment = is_empty_ticket or flags.get("needs_review") or is_fraud

            if not bypass_assignment:
                # 2. Geo-routing
                offices = self._load_offices(db)
                geo_res = GeoRouter.route(ai_analysis, client_city, client_region, offices, ticket_id)
                office_city = geo_res["city"]
                routed_branch_id = geo_res["branch_id"]
                flags.update(geo_res.get("flags", {}))

                # 3. Skill filter
                candidates, filter_trace = SkillMatcher.filter_managers(
                    db, routed_branch_id, office_city, segment, ai_analysis["type"], ai_analysis["language"]
                )
                if filter_trace.get("no_candidate_after_hardskills"):
                    flags["no_candidate_after_hardskills"] = True

                # 4. Load balance + RR
                manager, lb_trace = LoadBalancer.balance(candidates, routed_branch_id, db)

                if manager:
                    manager.current_load += 1
                    db.flush()

                if lb_trace.get("invalid_load_coerced"):
                    flags["invalid_load"] = True
            else:
                manager = None
                office_city = client_city
                routed_branch_id = None
                office_rule = "assignment_bypassed" if not is_empty_ticket else "empty_ticket_skipped"
                geo_res = {"city": office_city, "branch_id": None, "rule": office_rule}
                filter_trace = {"skipped": True}
                lb_trace = {"skipped": True}

            routing_trace = {
                "geo_decision": geo_res,
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

            return {
                "ai_analysis": ai_analysis,
                "assigned_manager_id": manager.id if manager else None,
                "assigned_manager_name": manager.name if manager else "Unassigned",
                "office_city": office_city,
                "office_rule": geo_res["rule"],
                "routed_branch_id": routed_branch_id,
                "alternative_branches": geo_res.get("alternative_branches", []),
                "routing_trace": routing_trace,
                "flags": flags,
                "status": ticket_status,
                "correlation_id": correlation_id,
                "workload_at_assignment": lb_trace.get("workload_at_assignment"),
            }

        finally:
            if close_db and db_context:
                db_context.__exit__(None, None, None)

    def route_batch(self, df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
        """
        Route an entire CSV DataFrame of tickets by delegating to BatchProcessor.
        """
        processor = BatchProcessor(self)
        return processor.process_csv(df, progress_callback)
