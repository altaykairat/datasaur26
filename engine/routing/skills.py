"""Engine Subpackage: Skill Matching

Encapsulates all logic for filtering managers based on VIP status, 
ticket types, and language capabilities, utilizing a graduated fallback mechanism.
"""
from typing import Optional
from database.models import Manager, Office


class SkillMatcher:
    """Handles skill-based filtering of managers for a ticket."""

    @staticmethod
    def _hard_filter(managers: list, is_vip: bool, is_data_change: bool, 
                     needs_lang: bool, language: str, check_lang: bool = True) -> list:
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

    @staticmethod
    def _get_active_managers(query) -> list:
        """Get only active managers from a SQLAlchemy query."""
        managers = query.all()
        return [m for m in managers if getattr(m, 'is_active', True)]

    @staticmethod
    def _build_rules_list(is_vip: bool, is_data_change: bool, check_lang: bool, language: str) -> list[str]:
        rules = []
        if is_vip:
            rules.append("VIP_required")
        if is_data_change:
            rules.append("role_Главный_специалист_required")
        if check_lang and language in ("KZ", "ENG"):
            rules.append(f"language_{language}_required")
        return rules

    @classmethod
    def filter_managers(cls, db, branch_id: Optional[int], office_city: str, segment: str,
                        ticket_type: str, language: str) -> tuple[list[Manager], dict]:
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
        is_vip = bool(segment and segment.upper() in ("VIP", "PRIORITY"))
        is_data_change = (ticket_type == "Смена данных")
        needs_lang = (language in ("KZ", "ENG"))

        filter_trace = {
            "branch_id": branch_id,
            "office_city": office_city,
            "applied_rules": [],
            "fallback_used": None,
            "no_candidate_after_hardskills": False,
        }

        # ---- Get pools ----
        if branch_id is not None:
            branch_managers = cls._get_active_managers(
                db.query(Manager).filter(Manager.office_id == branch_id)
            )
        else:
            branch_managers = []

        city_managers = cls._get_active_managers(
            db.query(Manager).join(Office).filter(Office.city == office_city)
        )

        # ---- Step 1: Strict filter in target branch ----
        if branch_managers:
            candidates = cls._hard_filter(branch_managers, is_vip, is_data_change, needs_lang, language, check_lang=True)
            if candidates:
                filter_trace["applied_rules"] = cls._build_rules_list(is_vip, is_data_change, needs_lang, language)
                filter_trace["fallback_used"] = None
                return candidates, filter_trace

            # ---- Step 2: Relax language in target branch ----
            if needs_lang:
                candidates = cls._hard_filter(branch_managers, is_vip, is_data_change, needs_lang, language, check_lang=False)
                if candidates:
                    filter_trace["applied_rules"] = cls._build_rules_list(is_vip, is_data_change, False, language)
                    filter_trace["fallback_used"] = "relaxed_language_in_branch"
                    filter_trace["no_candidate_after_hardskills"] = True
                    return candidates, filter_trace

        # ---- Step 3: Strict filter in target city (all branches) ----
        if city_managers:
            candidates = cls._hard_filter(city_managers, is_vip, is_data_change, needs_lang, language, check_lang=True)
            if candidates:
                filter_trace["applied_rules"] = cls._build_rules_list(is_vip, is_data_change, needs_lang, language)
                filter_trace["fallback_used"] = "expanded_to_city_strict"
                filter_trace["no_candidate_after_hardskills"] = False
                return candidates, filter_trace

            # ---- Step 4: Relax language in target city ----
            if needs_lang:
                candidates = cls._hard_filter(city_managers, is_vip, is_data_change, needs_lang, language, check_lang=False)
                if candidates:
                    filter_trace["applied_rules"] = cls._build_rules_list(is_vip, is_data_change, False, language)
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
                return sorted(candidates, key=lambda m: getattr(m, 'current_load', 0))[:5], filter_trace

        # ---- Step 6: Expand to Astana + Almaty, keep VIP + role ----
        # Only reached if the target city has literally ZERO active managers
        if office_city not in ("Астана", "Алматы"):
            main_managers = cls._get_active_managers(
                db.query(Manager).join(Office).filter(Office.city.in_(["Астана", "Алматы"]))
            )
            candidates = cls._hard_filter(main_managers, is_vip, is_data_change, needs_lang, language, check_lang=False)
            if candidates:
                filter_trace["applied_rules"] = cls._build_rules_list(is_vip, is_data_change, False, language)
                filter_trace["fallback_used"] = "expanded_to_main_offices"
                filter_trace["no_candidate_after_hardskills"] = True
                return candidates, filter_trace

        # ---- Step 4: VIP with no candidates → escalation (never relax VIP) ----
        if is_vip:
            filter_trace["fallback_used"] = "vip_escalation"
            filter_trace["no_candidate_after_hardskills"] = True
            return [], filter_trace

        # ---- Step 5: Global Глав спец fallback ----
        glav_managers = cls._get_active_managers(
            db.query(Manager).filter(Manager.role == "Главный специалист")
                .order_by(Manager.current_load.asc())
        )
        if glav_managers:
            candidates = glav_managers[:5]  # Top 5 lowest-load
            filter_trace["fallback_used"] = "global_glav_spec_pool"
            filter_trace["no_candidate_after_hardskills"] = True
            return candidates, filter_trace

        # ---- Absolute fallback: any active manager ----
        all_managers = cls._get_active_managers(
            db.query(Manager).order_by(Manager.current_load.asc())
        )
        candidates = all_managers[:5] if all_managers else []
        filter_trace["fallback_used"] = "global_any_manager"
        filter_trace["no_candidate_after_hardskills"] = True
        return candidates, filter_trace
