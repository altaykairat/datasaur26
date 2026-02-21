"""Ticket Router — full pipeline: AI enrichment → geo-routing → skill filter → RR load balance."""
import hashlib
import uuid
import logging
from typing import Optional

import pandas as pd

from database.connection import get_db
from database.models import (
    Manager, Ticket, Office, TicketStatus, RoundRobinState,
)
from engine.intelligence import IntelligenceEngine
from utils.geo import find_nearest_office, resolve_city, OFFICE_CITIES

logger = logging.getLogger("fire.router")

# Required columns for CSV batch uploads
REQUIRED_CSV_COLUMNS = {"GUID клиента", "Сегмент клиента", "Населённый пункт", "Область"}
# Description column may have trailing space — we check both variants
DESCRIPTION_VARIANTS = {"Описание ", "Описание"}

class TicketRouter:
    """Routes tickets to the best manager using the full FIRE pipeline."""

    def __init__(self, ai_mode: str = "deepseek"):
        self.ai_engine = IntelligenceEngine(mode=ai_mode)
        self._offices_cache = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_offices(self, db) -> list[dict]:
        """Load and cache office data."""
        if self._offices_cache is None:
            offices = db.query(Office).all()
            self._offices_cache = [
                {"city": o.city, "lat": o.lat, "lon": o.lon}
                for o in offices
            ]
        return self._offices_cache

    def _generate_correlation_id(self) -> str:
        return f"COR-{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Step A: AI Enrichment
    # ------------------------------------------------------------------

    def _enrich(self, description: str) -> dict:
        """Run AI enrichment with fallback handling."""
        try:
            ai_analysis = self.ai_engine.analyze_ticket(description)
        except Exception as e:
            logger.error(f"AI enrichment critical failure: {e}")
            ai_analysis = {
                "type": "Консультация",
                "priority": 5,
                "language": "RU",
                "sentiment": "Нейтральный",
                "normalized_address": "Unknown",
                "summary": "AI недоступен; требуется ручная проверка обращения.",
                "ai_fallback": True,
            }
        return ai_analysis

    # ------------------------------------------------------------------
    # Step B: Geo-Routing
    # ------------------------------------------------------------------

    def _geo_route(self, ai_analysis: dict, client_city: str,
                   client_region: str, offices: list[dict], ticket_id: str) -> dict:
        """
        Determine which office city to route to.

        Returns dict with: city, rule, distance_km, and flags
        (address_unknown, foreign_country, geocode_failed).
        """
        flags = {
            "address_unknown": False,
            "foreign_country": False,
            "geocode_failed": False,
        }

        def fallback_50_50(tid: str, flag_key: str) -> dict:
            tid_str = str(tid) if tid else "default"
            num = int(hashlib.md5(tid_str.encode()).hexdigest(), 16)
            city = "Астана" if num % 2 == 0 else "Алматы"
            flags[flag_key] = True
            return {
                "city": city,
                "rule": "split_50_50",
                "distance_km": None,
                "flags": flags,
            }

        # Try to resolve the city from client input
        resolved_dict = resolve_city(client_city, client_region)

        # Also try AI-extracted address
        if not resolved_dict:
            ai_addr = ai_analysis.get("normalized_address", "Unknown")
            if ai_addr and ai_addr != "Unknown":
                parts = ai_addr.split(",")
                if parts:
                    resolved_dict = resolve_city(parts[0].strip())

        if not resolved_dict:
            return fallback_50_50(ticket_id, "address_unknown")

        # If Nominatim detected a foreign address → immediate 50/50 fallback
        if resolved_dict.get("is_foreign"):
            return fallback_50_50(ticket_id, "foreign_country")

        resolved_city_name = resolved_dict["city"]
        resolved_rule = resolved_dict["rule"]

        # If the resolved city IS an office city, use it directly
        if resolved_city_name in [o["city"] for o in offices]:
            return {
                "city": resolved_city_name,
                "rule": resolved_rule,
                "distance_km": 0,
                "flags": flags,
            }

        # Otherwise, find nearest office
        nearest_city, distance = find_nearest_office(resolved_city_name, offices)

        if nearest_city is None:
            # Could not compute distance (unknown coords or all offices missing coords)
            return fallback_50_50(ticket_id, "geocode_failed")

        if distance > 500:
            return fallback_50_50(ticket_id, "foreign_country")

        return {
            "city": nearest_city,
            "rule": f"nearest_office_dist_{int(distance)}",
            "distance_km": round(distance, 1),
            "flags": flags,
        }

    # ------------------------------------------------------------------
    # Step C: Skill Filter (graduated cascade)
    # ------------------------------------------------------------------

    def _skill_filter(self, db, office_city: str, segment: str,
                      ticket_type: str, language: str) -> tuple[list["Manager"], dict]:
        """
        Filter managers by office + required skills with graduated fallback.

        Cascade:
        1. Strict: all hard rules in target office
        2. Relax language only (keep VIP + role)
        3. Expand to Astana + Almaty (keep VIP + role)
        4. VIP with no candidates → return empty (escalation)
        5. Global Глав спец fallback (lowest-load)

        Returns (candidates, filter_trace).
        """
        is_vip = segment and segment.upper() in ("VIP", "PRIORITY")
        is_data_change = ticket_type == "Смена данных"
        needs_lang = language in ("KZ", "ENG")

        filter_trace = {
            "office_city": office_city,
            "applied_rules": [],
            "fallback_used": None,
            "no_candidate_after_hardskills": False,
        }

        def _hard_filter(managers: list, check_lang: bool = True) -> list:
            """Apply hard skill rules to a list of managers."""
            filtered = []
            for m in managers:
                # Skip inactive managers
                if not getattr(m, 'is_active', True):
                    continue

                skills = m.skills if isinstance(m.skills, list) else []

                # VIP check — NEVER relaxed
                if is_vip and "VIP" not in skills:
                    continue

                # Role check for data change
                if is_data_change and m.role != "Главный специалист":
                    continue

                # Language check (can be relaxed in fallback)
                if check_lang and needs_lang:
                    if language == "KZ" and "KZ" not in skills:
                        continue
                    if language == "ENG" and "ENG" not in skills:
                        continue

                filtered.append(m)
            return filtered

        def _get_active_managers(query):
            """Get only active managers from a query."""
            managers = query.all()
            return [m for m in managers if getattr(m, 'is_active', True)]

        # ---- Step 1: Strict filter in target office ----
        office_managers = _get_active_managers(
            db.query(Manager).filter(Manager.office_location == office_city)
        )
        candidates = _hard_filter(office_managers, check_lang=True)

        if candidates:
            filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, needs_lang, language)
            filter_trace["fallback_used"] = None
            return candidates, filter_trace

        # ---- Step 2: Relax language, keep VIP + role ----
        if needs_lang:
            candidates = _hard_filter(office_managers, check_lang=False)
            if candidates:
                filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, False, language)
                filter_trace["fallback_used"] = "relaxed_language"
                filter_trace["no_candidate_after_hardskills"] = True
                return candidates, filter_trace

        # ---- Step 3: Expand to Astana + Almaty, keep VIP + role ----
        if office_city not in ("Астана", "Алматы"):
            main_managers = _get_active_managers(
                db.query(Manager).filter(Manager.office_location.in_(["Астана", "Алматы"]))
            )
            candidates = _hard_filter(main_managers, check_lang=False)
            if candidates:
                filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, False, language)
                filter_trace["fallback_used"] = "expanded_to_main_offices"
                filter_trace["no_candidate_after_hardskills"] = True
                return candidates, filter_trace

        # ---- Step 4: VIP with no candidates → escalation (never relax VIP) ----
        if is_vip:
            filter_trace["fallback_used"] = "vip_escalation"
            filter_trace["no_candidate_after_hardskills"] = True
            return [], filter_trace

        # ---- Step 5: Global Глав спец fallback ----
        glav_managers = _get_active_managers(
            db.query(Manager).filter(Manager.role == "Главный специалист")
                .order_by(Manager.current_load.asc())
        )
        if glav_managers:
            candidates = glav_managers[:5]  # Top 5 lowest-load
            filter_trace["fallback_used"] = "global_glav_spec_pool"
            filter_trace["no_candidate_after_hardskills"] = True
            return candidates, filter_trace

        # ---- Absolute fallback: any active manager ----
        all_managers = _get_active_managers(
            db.query(Manager).order_by(Manager.current_load.asc())
        )
        candidates = all_managers[:5] if all_managers else []
        filter_trace["fallback_used"] = "global_any_manager"
        filter_trace["no_candidate_after_hardskills"] = True
        return candidates, filter_trace

    @staticmethod
    def _build_rules_list(is_vip, is_data_change, check_lang, language) -> list[str]:
        rules = []
        if is_vip:
            rules.append("VIP_required")
        if is_data_change:
            rules.append("role_Главный_специалист_required")
        if check_lang and language in ("KZ", "ENG"):
            rules.append(f"language_{language}_required")
        return rules

    # ------------------------------------------------------------------
    # Step D: Load Balance + Round Robin
    # ------------------------------------------------------------------

    def _load_balance(self, candidates: list["Manager"], office_city: str,
                      db) -> tuple[Optional["Manager"], dict]:
        """
        Pick the best manager from candidates using top-2 + round robin.

        Returns (manager, lb_trace).
        """
        lb_trace = {
            "candidate_ids": [],
            "candidate_loads": [],
            "rr_pointer": None,
            "chosen_manager_id": None,
            "workload_at_assignment": None,
            "invalid_load_coerced": False,
        }

        if not candidates:
            return None, lb_trace

        # Validate and coerce load values
        for m in candidates:
            if m.current_load is None or m.current_load < 0:
                m.current_load = 9999
                lb_trace["invalid_load_coerced"] = True
                logger.warning(f"Manager {m.id} had invalid load, coerced to 9999")

        # Sort by (current_load, id) for determinism
        sorted_candidates = sorted(candidates, key=lambda m: (m.current_load, m.id))

        lb_trace["candidate_ids"] = [m.id for m in sorted_candidates]
        lb_trace["candidate_loads"] = [m.current_load for m in sorted_candidates]

        if len(sorted_candidates) == 1:
            chosen = sorted_candidates[0]
            lb_trace["chosen_manager_id"] = chosen.id
            lb_trace["workload_at_assignment"] = chosen.current_load
            return chosen, lb_trace

        # Take top 2 with minimal workload
        top_two = sorted_candidates[:2]
        candidate_key = f"{min(top_two[0].id, top_two[1].id)}_{max(top_two[0].id, top_two[1].id)}"

        # Look up or create RR state
        rr = db.query(RoundRobinState).filter(
            RoundRobinState.office_city == office_city,
            RoundRobinState.candidate_key == candidate_key,
        ).first()

        if rr is None:
            rr = RoundRobinState(
                office_city=office_city,
                candidate_key=candidate_key,
                pointer=0,
            )
            db.add(rr)
            db.flush()

        # Select based on pointer
        chosen = top_two[rr.pointer % 2]
        lb_trace["rr_pointer"] = rr.pointer
        lb_trace["chosen_manager_id"] = chosen.id
        lb_trace["workload_at_assignment"] = chosen.current_load

        # Increment pointer atomically
        rr.pointer += 1
        db.flush()

        return chosen, lb_trace

    # ------------------------------------------------------------------
    # Public API: Route Single Ticket
    # ------------------------------------------------------------------

    def route_single_ticket(self, ticket_description: str, segment: str = "Mass",
                            client_city: str = None, client_region: str = None,
                            ticket_id: str = "", db=None) -> dict:
        """
        Route a single ticket through the full pipeline.

        Returns dict with: ai_analysis, assigned_manager_id, assigned_manager_name,
        office_city, office_rule, routing_trace, flags, correlation_id.
        """
        correlation_id = self._generate_correlation_id()

        # Step A: AI Enrichment
        ai_analysis = self._enrich(ticket_description)

        # Collect flags from AI
        flags = {}
        for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                    "needs_clarification", "language_confidence"):
            if fk in ai_analysis:
                flags[fk] = ai_analysis.pop(fk)

        # Step B: Routing
        should_close = db is None
        if db is None:
            db_ctx = get_db()
            db = db_ctx.__enter__()
        else:
            db_ctx = None

        try:
            offices = self._load_offices(db)

            # Spam check: skip routing entirely
            if ai_analysis["type"] == "Спам":
                result = {
                    "ai_analysis": ai_analysis,
                    "assigned_manager_id": None,
                    "assigned_manager_name": "Spam — not assigned",
                    "office_city": "N/A",
                    "office_rule": "spam_skip",
                }
                if db_ctx:
                    db.commit()
                return result

            # 1. Geo-routing
            geo_res = self._geo_route(ai_analysis, client_city, client_region, offices, ticket_id)
            office_city = geo_res["city"]
            office_rule = geo_res["rule"]
            flags.update(geo_res.get("flags", {}))

            # 2. Skill filter
            candidates, filter_trace = self._skill_filter(
                db, office_city, segment, ai_analysis["type"], ai_analysis["language"]
            )
            if filter_trace.get("no_candidate_after_hardskills"):
                flags["no_candidate_after_hardskills"] = True

            # 3. Load balancing + Round Robin
            manager, lb_trace = self._load_balance(candidates, office_city, db)

            if manager:
                manager.current_load += 1
                db.flush()

            if lb_trace.get("invalid_load_coerced"):
                flags["invalid_load"] = True

            # Build routing trace
            routing_trace = {
                "geo_decision": {
                    "resolved_city": geo_res.get("city"),
                    "rule": office_rule,
                    "distance_km": geo_res.get("distance_km"),
                },
                "skill_filter": filter_trace,
                "load_balance": lb_trace,
            }

            result = {
                "ai_analysis": ai_analysis,
                "assigned_manager_id": manager.id if manager else None,
                "assigned_manager_name": manager.name if manager else "Unassigned",
                "office_city": office_city,
                "office_rule": office_rule,
                "routing_trace": routing_trace,
                "flags": flags,
                "correlation_id": correlation_id,
                "workload_at_assignment": lb_trace.get("workload_at_assignment"),
                "status": TicketStatus.ASSIGNED.value if manager else TicketStatus.ROUTING_FAILED.value,
            }

            if db_ctx:
                db.commit()

            return result
        except Exception:
            if db_ctx:
                db.rollback()
            raise
        finally:
            if db_ctx:
                db.close()

    # ------------------------------------------------------------------
    # Public API: Route Batch
    # ------------------------------------------------------------------

    def route_batch(self, df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
        """
        Route an entire CSV DataFrame of tickets.

        Expected columns from tickets.csv:
        - 'GUID клиента', 'Описание ' (or 'Описание'), 'Сегмент клиента',
          'Населённый пункт', 'Область'

        Returns the DataFrame with added columns for routing results.
        """
        # ---- Schema Validation ----
        actual_columns = set(df.columns)

        # Check for description column
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
            offices = self._load_offices(db)

            for idx, row in df.iterrows():
                correlation_id = self._generate_correlation_id()

                try:
                    # ---- Extract fields with safe defaults ----
                    description = str(row.get("Описание ", "") or row.get("Описание", "") or "")
                    segment = str(row.get("Сегмент клиента", "Mass") or "Mass")
                    city = str(row.get("Населённый пункт", "") or "")
                    region = str(row.get("Область", "") or "")
                    guid = str(row.get("GUID клиента", ""))

                    flags = {}

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
                    ai_analysis = self._enrich(description)

                    # Extract AI flags
                    for fk in ("ai_fallback", "ai_schema_error", "ai_type_low_confidence",
                               "needs_clarification", "language_confidence"):
                        if fk in ai_analysis:
                            flags[fk] = ai_analysis.pop(fk)

                    # ---- Spam check: skip routing entirely ----
                    is_spam = ai_analysis["type"] == "Спам"

                    if is_spam:
                        flags["spam_detected"] = True
                        result = {
                            "ticket_index": idx,
                            "client_guid": guid,
                            "ai_type": ai_analysis["type"],
                            "ai_priority": ai_analysis["priority"],
                            "ai_language": ai_analysis["language"],
                            "ai_sentiment": ai_analysis["sentiment"],
                            "ai_address": ai_analysis.get("normalized_address", "Unknown"),
                            "ai_summary": ai_analysis.get("summary", ""),
                            "routed_office": "N/A",
                            "office_rule": "spam_skip",
                            "assigned_manager": "Spam — not assigned",
                            "assigned_manager_id": None,
                            "segment": segment,
                            "status": TicketStatus.SPAM.value,
                            "flags": flags,
                            "correlation_id": correlation_id,
                        }
                        results.append(result)

                        # Save spam ticket to DB for analytics
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
                            routing_trace={"spam": True},
                            flags=flags,
                        )
                        db.add(ticket)

                        if progress_callback:
                            progress_callback(idx + 1, total, result)
                        continue

                    # ---- Geo-routing ----
                    geo_res = self._geo_route(ai_analysis, city, region, offices, guid)
                    office_city = geo_res["city"]
                    office_rule = geo_res["rule"]
                    flags.update(geo_res.get("flags", {}))

                    # ---- Skill filter ----
                    candidates, filter_trace = self._skill_filter(
                        db, office_city, segment, ai_analysis["type"], ai_analysis["language"]
                    )
                    if filter_trace.get("no_candidate_after_hardskills"):
                        flags["no_candidate_after_hardskills"] = True

                    # ---- Load balance + RR ----
                    manager, lb_trace = self._load_balance(candidates, office_city, db)

                    if manager:
                        manager.current_load += 1
                        db.flush()

                    if lb_trace.get("invalid_load_coerced"):
                        flags["invalid_load"] = True

                    # Build routing trace
                    routing_trace = {
                        "geo_decision": {
                            "resolved_city": geo_res.get("city"),
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
                        "routed_office": office_city,
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
                        routing_trace=routing_trace,
                        flags=flags,
                        workload_at_assignment=lb_trace.get("workload_at_assignment"),
                    )
                    db.add(ticket)

                except Exception as e:
                    # Row-level error handling: skip bad row, continue
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

        results_df = pd.DataFrame(results)
        return results_df
