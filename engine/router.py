"""Ticket Router — Orchestrator

This module is the entry point for ticket routing. It initializes the Intelligence Engine
and delegates geographic matching, skill filtering, load balancing, and batch processing
to the specialized classes in the `engine.routing` subpackage.
"""
import uuid
import pandas as pd
from typing import Optional

from utils.logging import get_safe_logger
from database.connection import get_db
from database.models import Office, Ticket, TicketStatus
from engine.intelligence import IntelligenceEngine

# Import OOP subcomponents
from engine.routing.geo import GeoRouter
from engine.routing.skills import SkillMatcher
from engine.routing.load_balancer import LoadBalancer
from engine.routing.batch import BatchProcessor

logger = get_safe_logger("router_orchestrator")

class TicketRouter:
    """Orchestrates the FIRE routing pipeline."""

    def __init__(self, ai_mode: str = "deepseek"):
        self.ai_engine = IntelligenceEngine(mode=ai_mode)
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
                            ticket_id: str = "", db=None) -> dict:
        """
        Route a single ticket through the full pipeline.
        
        Returns dict with: ai_analysis, assigned_manager_id, assigned_manager_name,
        office_city, office_rule, routing_trace, flags, correlation_id.
        """
        flags = {}
        correlation_id = self._generate_correlation_id()

        # Optionally handle external DB session
        close_db = False
        if db is None:
            db_gen = get_db()
            db = next(db_gen)
            close_db = True

        try:
            # 1. AI Enrichment
            ai_analysis = self._enrich(ticket_description)
            for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                       "needs_clarification", "language_confidence"):
                if fk in ai_analysis:
                    flags[fk] = ai_analysis.pop(fk)

            is_spam = ai_analysis["type"] == "Спам"
            if is_spam:
                flags["spam_detected"] = True
                return {
                    "ai_analysis": ai_analysis,
                    "assigned_manager_id": None,
                    "assigned_manager_name": "Spam — not assigned",
                    "office_city": "N/A",
                    "office_rule": "spam_skip",
                    "routed_branch_id": None,
                    "alternative_branches": [],
                    "routing_trace": {"spam": True},
                    "flags": flags,
                    "status": TicketStatus.SPAM.value,
                    "correlation_id": correlation_id,
                    "workload_at_assignment": None,
                }

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

            routing_trace = {
                "geo_decision": geo_res,
                "skill_filter": filter_trace,
                "load_balance": lb_trace,
            }

            ticket_status = TicketStatus.ASSIGNED.value if manager else TicketStatus.ROUTING_FAILED.value

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
            if close_db:
                db.close()

    def route_batch(self, df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
        """
        Route an entire CSV DataFrame of tickets by delegating to BatchProcessor.
        """
        processor = BatchProcessor(self)
        return processor.process_csv(df, progress_callback)
