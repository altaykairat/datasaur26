"""Ticket Router — full pipeline: AI enrichment → geo-routing → skill filter → RR load balance."""
import hashlib
import uuid
from utils.logging import get_safe_logger
from typing import Optional

import pandas as pd

from database.connection import get_db
from database.models import (
    Manager, Ticket, Office, TicketStatus, RoundRobinState,
)
from engine.intelligence import IntelligenceEngine
from utils.geo import find_nearest_branch, resolve_city, OFFICE_CITIES
from utils.ocr import extract_text_from_image, is_ocr_available, combine_description_with_ocr

logger = get_safe_logger(__name__)

# Required columns for CSV batch uploads
REQUIRED_CSV_COLUMNS = {"GUID клиента", "Сегмент клиента", "Населённый пункт", "Область"}
# Description column may have trailing space — we check both variants
DESCRIPTION_VARIANTS = {"Описание ", "Описание"}
# Image/attachment columns that may contain screenshots
IMAGE_COLUMN_VARIANTS = {"Скриншот", "Вложение", "Вложения", "Screenshot", "Attachment", "Image"}

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
                {"id": o.id, "city": o.city, "name": o.name, "address": o.address, "lat": o.lat, "lon": o.lon}
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

        Returns dict with: branch_id, branch_name, city, rule, distance_km, alternative_branches, and flags
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
            
            # Find any branch in the fallback city
            fallback_branch = next((o for o in offices if o["city"] == city), None)
            
            return {
                "branch_id": fallback_branch["id"] if fallback_branch else None,
                "branch_name": fallback_branch["name"] if fallback_branch else city,
                "city": city,
                "rule": "split_50_50",
                "distance_km": None,
                "alternative_branches": [],
                "flags": flags,
            }

        # 1. Try to resolve the city from client input ON LOCAL DICTIONARIES
        resolved_dict = resolve_city(client_city, client_region, use_nominatim=False)

        # 2. Try the clean AI-extracted address ON LOCAL DICTIONARIES
        if not resolved_dict:
            ai_addr = ai_analysis.get("normalized_address", "Unknown")
            ai_city = ai_addr.split(",")[0].strip() if ai_addr != "Unknown" else None
            if ai_city:
                resolved_dict = resolve_city(ai_city, use_nominatim=False)
                if resolved_dict:
                    resolved_dict["rule"] += "_via_ai"

        # 3. If local match fails, fallback to Nominatim (first AI, then client city)
        if not resolved_dict:
            if "ai_city" in locals() and ai_city:
                resolved_dict = resolve_city(ai_city, use_nominatim=True)
                if resolved_dict:
                    resolved_dict["rule"] += "_via_ai"

            if not resolved_dict and client_city:
                # If everything else failed, try the raw client city against external API
                resolved_dict = resolve_city(client_city, client_region, use_nominatim=True)

        if not resolved_dict:
            return fallback_50_50(ticket_id, "address_unknown")

        # If Nominatim detected a foreign address → immediate 50/50 fallback
        if resolved_dict.get("is_foreign"):
            return fallback_50_50(ticket_id, "foreign_country")

        resolved_city_name = resolved_dict["city"]
        resolved_rule = resolved_dict["rule"]

        # If the resolved city IS an office city, filter branches for that city
        city_branches = [o for o in offices if o["city"] == resolved_city_name]
        
        client_address = ai_analysis.get("normalized_address", None)
        
        if city_branches:
            # Ticket is in an office city. Find the nearest branch using street-level precision
            nearest_branch, alternatives = find_nearest_branch(
                resolved_city_name, client_address, city_branches, enforce_street_level=True
            )

            if nearest_branch is None:
                # Could not compute distance locally. Return all offices in the city for consideration.
                return {
                    "branch_id": None,
                    "branch_name": f"Любой филиал ({resolved_city_name})",
                    "city": resolved_city_name,
                    "rule": f"all_city_branches_{resolved_rule}",
                    "distance_km": None,
                    "alternative_branches": [
                        {"branch_id": b["id"], "branch_name": b["name"], "distance_km": None}
                         for b in city_branches
                    ],
                    "flags": flags,
                }
        else:
            # Ticket is far from any office city. Find the absolute nearest branch (cross-city)
            nearest_branch, alternatives = find_nearest_branch(
                resolved_city_name, client_address, offices, enforce_street_level=False
            )
            
            if nearest_branch is None:
                # Total failure to locate even the city, very rare
                return fallback_50_50(ticket_id, "geocode_failed")
                
            if nearest_branch.get("distance_km") and nearest_branch["distance_km"] > 500:
                # Foreign/very far country
                return fallback_50_50(ticket_id, "foreign_country")

        # Create clean alternatives list for DB
        alt_list = [
            {"branch_id": b["id"], "branch_name": b["name"], "distance_km": b.get("distance_km")}
            for b in alternatives
        ]

        return {
            "branch_id": nearest_branch["id"],
            "branch_name": nearest_branch["name"],
            "city": nearest_branch["city"],
            "rule": f"nearest_branch_{resolved_rule}",
            "distance_km": nearest_branch.get("distance_km"),
            "alternative_branches": alt_list,
            "flags": flags,
        }

    # ------------------------------------------------------------------
    # Step C: Skill Filter (graduated cascade)
    # ------------------------------------------------------------------

    def _skill_filter(self, db, branch_id: Optional[int], office_city: str, segment: str,
                      ticket_type: str, language: str) -> tuple[list["Manager"], dict]:
        """
        Filter managers by office branch + required skills with graduated fallback.

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
            "branch_id": branch_id,
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

        def _get_active_managers(query):
            """Get only active managers from a query."""
            managers = query.all()
            return [m for m in managers if getattr(m, 'is_active', True)]

        # ---- Get pools ----
        if branch_id is not None:
            branch_managers = _get_active_managers(
                db.query(Manager).filter(Manager.office_id == branch_id)
            )
        else:
            branch_managers = []

        city_managers = _get_active_managers(
            db.query(Manager).join(Office).filter(Office.city == office_city)
        )

        # ---- Step 1: Strict filter in target branch ----
        if branch_managers:
            candidates = _hard_filter(branch_managers, check_lang=True)
            if candidates:
                filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, needs_lang, language)
                filter_trace["fallback_used"] = None
                return candidates, filter_trace

            # ---- Step 2: Relax language in target branch ----
            if needs_lang:
                candidates = _hard_filter(branch_managers, check_lang=False)
                if candidates:
                    filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, False, language)
                    filter_trace["fallback_used"] = "relaxed_language_in_branch"
                    filter_trace["no_candidate_after_hardskills"] = True
                    return candidates, filter_trace

        # ---- Step 3: Strict filter in target city (all branches) ----
        if city_managers:
            candidates = _hard_filter(city_managers, check_lang=True)
            if candidates:
                filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, needs_lang, language)
                filter_trace["fallback_used"] = "expanded_to_city_strict"
                filter_trace["no_candidate_after_hardskills"] = False
                return candidates, filter_trace

            # ---- Step 4: Relax language in target city ----
            if needs_lang:
                candidates = _hard_filter(city_managers, check_lang=False)
                if candidates:
                    filter_trace["applied_rules"] = self._build_rules_list(is_vip, is_data_change, False, language)
                    filter_trace["fallback_used"] = "relaxed_language_in_city"
                    filter_trace["no_candidate_after_hardskills"] = True
                    return candidates, filter_trace
                    
            # ---- Step 5: Absolute fallback to ANY active manager in the target city ----
            # The client exists in this city, we MUST exhaust city managers before leaving the city.
            # We respect VIP if possible.
            if is_vip:
                vip_city = [m for m in city_managers if isinstance(m.skills, list) and "VIP" in m.skills]
                candidates = vip_city if vip_city else city_managers
            else:
                candidates = city_managers
                
            if candidates:
                filter_trace["applied_rules"] = []
                filter_trace["fallback_used"] = "any_manager_in_same_city"
                filter_trace["no_candidate_after_hardskills"] = True
                return sorted(candidates, key=lambda m: m.current_load)[:5], filter_trace

        # ---- Step 6: Expand to Astana + Almaty, keep VIP + role ----
        # Only reached if the target city has literally ZERO active managers
        if office_city not in ("Астана", "Алматы"):
            main_managers = _get_active_managers(
                db.query(Manager).join(Office).filter(Office.city.in_(["Астана", "Алматы"]))
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

    def _load_balance(self, candidates: list["Manager"], branch_id: Optional[int],
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

        # Look up or create RR state with row-level locking to prevent concurrency races
        rr_office_id = branch_id if branch_id is not None else 0
        rr = db.query(RoundRobinState).filter(
            RoundRobinState.office_id == rr_office_id,
            RoundRobinState.candidate_key == candidate_key,
        ).with_for_update().first()

        if rr is None:
            rr = RoundRobinState(
                office_id=rr_office_id,
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
            routed_branch_id = geo_res["branch_id"]
            office_rule = geo_res["rule"]
            alternative_branches = geo_res.get("alternative_branches", [])
            flags.update(geo_res.get("flags", {}))

            # 2. Skill filter
            candidates, filter_trace = self._skill_filter(
                db, routed_branch_id, office_city, segment, ai_analysis["type"], ai_analysis["language"]
            )
            if filter_trace.get("no_candidate_after_hardskills"):
                flags["no_candidate_after_hardskills"] = True

            # 3. Load balancing + Round Robin
            manager, lb_trace = self._load_balance(candidates, routed_branch_id, db)

            if manager:
                manager.current_load += 1
                db.flush()
                if routed_branch_id is None:
                    routed_branch_id = manager.office_id

            if lb_trace.get("invalid_load_coerced"):
                flags["invalid_load"] = True

            # Build routing trace
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

            result = {
                "ai_analysis": ai_analysis,
                "assigned_manager_id": manager.id if manager else None,
                "assigned_manager_name": manager.name if manager else "Unassigned",
                "office_city": office_city,
                "routed_branch_id": routed_branch_id,
                "alternative_branches": alternative_branches,
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

                    # ---- OCR: always extract text from screenshots if available ----
                    if is_ocr_available():
                        for img_col in IMAGE_COLUMN_VARIANTS:
                            img_source = row.get(img_col, "")
                            if img_source and str(img_source).strip():
                                ocr_result = extract_text_from_image(str(img_source))
                                if ocr_result["success"]:
                                    description = combine_description_with_ocr(description, ocr_result["text"])
                                    flags["ocr_extracted"] = True
                                    flags["ocr_source"] = ocr_result["source_type"]
                                    flags["ocr_chars"] = len(ocr_result["text"])
                                    logger.info(f"Row {idx}: OCR extracted {len(ocr_result['text'])} chars from {img_col}")
                                    break
                                else:
                                    flags["ocr_failed"] = ocr_result.get("error", "unknown")

                    # Flag if description is still empty after OCR attempt
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
                            routed_branch_id=None,
                            alternative_branches=[],
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
                    routed_branch_id = geo_res["branch_id"]
                    office_rule = geo_res["rule"]
                    alternative_branches = geo_res.get("alternative_branches", [])
                    flags.update(geo_res.get("flags", {}))

                    # ---- Skill filter ----
                    candidates, filter_trace = self._skill_filter(
                        db, routed_branch_id, office_city, segment, ai_analysis["type"], ai_analysis["language"]
                    )
                    if filter_trace.get("no_candidate_after_hardskills"):
                        flags["no_candidate_after_hardskills"] = True

                    # ---- Load balance + RR ----
                    manager, lb_trace = self._load_balance(candidates, routed_branch_id, db)

                    if manager:
                        manager.current_load += 1
                        db.flush()
                        if routed_branch_id is None:
                            routed_branch_id = manager.office_id

                    if lb_trace.get("invalid_load_coerced"):
                        flags["invalid_load"] = True

                    # Build routing trace
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
