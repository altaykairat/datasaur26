"""Ticket Routing Engine — geo-routing, skill filtering, load balancing."""
import random
from typing import Optional

import pandas as pd

from database.connection import get_db
from database.models import Manager, Ticket, Office, TicketStatus
from engine.intelligence import IntelligenceEngine
from utils.geo import find_nearest_office, resolve_city, OFFICE_CITIES


class TicketRouter:
    """Routes tickets to the best manager using a 3-step pipeline."""

    def __init__(self, ai_mode: str = "deepseek"):
        self.ai_engine = IntelligenceEngine(mode=ai_mode)
        self._offices_cache = None

    def _load_offices(self, db) -> list[dict]:
        """Load and cache office data."""
        if self._offices_cache is None:
            offices = db.query(Office).all()
            self._offices_cache = [
                {"city": o.city, "lat": o.lat, "lon": o.lon}
                for o in offices
            ]
        return self._offices_cache

    def route_single_ticket(self, ticket_description: str, segment: str = "Mass",
                            client_city: str = None, client_region: str = None,
                            db=None) -> dict:
        """
        Route a single ticket through the full pipeline.

        Returns dict with: ai_analysis, assigned_manager_id, assigned_manager_name, office_city.
        """
        # Step A: AI Enrichment
        ai_analysis = self.ai_engine.analyze_ticket(ticket_description)

        # Step B: Routing
        should_close = db is not None
        if db is None:
            db_ctx = get_db()
            db = db_ctx.__enter__()
        else:
            db_ctx = None

        try:
            offices = self._load_offices(db)

            # 1. Geo-routing
            office_city = self._geo_route(ai_analysis, client_city, client_region, offices)

            # 2. Skill filter
            candidates = self._skill_filter(
                db, office_city, segment, ai_analysis["type"], ai_analysis["language"]
            )

            # 3. Load balancing
            manager = self._load_balance(candidates)

            if manager:
                manager.current_load += 1
                db.flush()

            result = {
                "ai_analysis": ai_analysis,
                "assigned_manager_id": manager.id if manager else None,
                "assigned_manager_name": manager.name if manager else "Unassigned",
                "office_city": office_city,
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

    def _geo_route(self, ai_analysis: dict, client_city: str,
                   client_region: str, offices: list[dict]) -> str:
        """
        Step 1: Determine which office city to route to.

        Rules:
        - Find nearest office by Haversine distance.
        - If distance > 500km or address unknown → random Astana/Almaty.
        """
        # Try to resolve the city from AI analysis or input
        resolved = resolve_city(client_city, client_region)

        # Also try AI-extracted address
        if not resolved:
            ai_addr = ai_analysis.get("normalized_address", "Unknown")
            if ai_addr and ai_addr != "Unknown":
                # Try to extract city from the AI address
                parts = ai_addr.split(",")
                if parts:
                    resolved = resolve_city(parts[0].strip())

        if not resolved:
            # Fallback: random Astana/Almaty
            return random.choice(["Астана", "Алматы"])

        # If the resolved city IS an office city, use it directly
        if resolved in [o["city"] for o in offices]:
            return resolved

        # Otherwise, find nearest office
        nearest_city, distance = find_nearest_office(resolved, offices)

        if nearest_city is None or distance > 500:
            return random.choice(["Астана", "Алматы"])

        return nearest_city

    def _skill_filter(self, db, office_city: str, segment: str,
                      ticket_type: str, language: str) -> list[Manager]:
        """
        Step 2: Filter managers by office + required skills.

        Rules:
        - Must be in the office_city
        - VIP → needs "VIP" skill
        - "Смена данных" → must be "Главный специалист"
        - KZ/ENG language → needs that language skill
        """
        # Get all managers in this office
        candidates = db.query(Manager).filter(
            Manager.office_location == office_city
        ).all()

        if not candidates:
            # Fallback: try Astana + Almaty managers
            candidates = db.query(Manager).filter(
                Manager.office_location.in_(["Астана", "Алматы"])
            ).all()

        filtered = []
        for m in candidates:
            skills = m.skills if isinstance(m.skills, list) else []

            # VIP check
            if segment and segment.upper() == "VIP":
                if "VIP" not in skills:
                    continue

            # "Смена данных" check
            if ticket_type == "Смена данных":
                if m.role != "Главный специалист":
                    continue

            # Language check
            if language == "KZ" and "KZ" not in skills:
                continue
            if language == "ENG" and "ENG" not in skills:
                continue

            filtered.append(m)

        # If no one passed all filters, relax: try just office match
        if not filtered:
            filtered = candidates[:] if candidates else []

        # If still empty, global fallback
        if not filtered:
            filtered = db.query(Manager).order_by(Manager.current_load.asc()).limit(5).all()

        return filtered

    def _load_balance(self, candidates: list[Manager]) -> Optional[Manager]:
        """
        Step 3: Pick the best manager from candidates.

        Sort by current_load ascending, pick randomly from top 2.
        """
        if not candidates:
            return None

        sorted_candidates = sorted(candidates, key=lambda m: m.current_load)

        # Pick from top 2 (or top 1 if only one)
        top = sorted_candidates[:min(2, len(sorted_candidates))]
        return random.choice(top)

    def route_batch(self, df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
        """
        Route an entire CSV DataFrame of tickets.

        Expected columns from tickets.csv:
        - 'GUID клиента', 'Описание ', 'Сегмент клиента',
          'Населённый пункт', 'Область'

        Returns the DataFrame with added columns for routing results.
        """
        results = []
        total = len(df)

        with get_db() as db:
            offices = self._load_offices(db)

            for idx, row in df.iterrows():
                description = str(row.get("Описание ", "") or row.get("Описание", "") or "")
                segment = str(row.get("Сегмент клиента", "Mass") or "Mass")
                city = str(row.get("Населённый пункт", "") or "")
                region = str(row.get("Область", "") or "")

                # AI Enrichment
                ai_analysis = self.ai_engine.analyze_ticket(description)

                # Geo-routing
                office_city = self._geo_route(ai_analysis, city, region, offices)

                # Skill filter
                candidates = self._skill_filter(
                    db, office_city, segment, ai_analysis["type"], ai_analysis["language"]
                )

                # Load balance
                manager = self._load_balance(candidates)

                if manager:
                    manager.current_load += 1
                    db.flush()

                result = {
                    "ticket_index": idx,
                    "ai_type": ai_analysis["type"],
                    "ai_priority": ai_analysis["priority"],
                    "ai_language": ai_analysis["language"],
                    "ai_sentiment": ai_analysis["sentiment"],
                    "ai_address": ai_analysis["normalized_address"],
                    "routed_office": office_city,
                    "assigned_manager": manager.name if manager else "Unassigned",
                    "assigned_manager_id": manager.id if manager else None,
                    "segment": segment,
                    "status": TicketStatus.ASSIGNED.value if manager else TicketStatus.NEW.value,
                }
                results.append(result)

                # Save ticket to DB
                ticket = Ticket(
                    client_guid=str(row.get("GUID клиента", "")),
                    description=description[:5000],
                    status=result["status"],
                    assigned_manager_id=result["assigned_manager_id"],
                    ai_analysis_json=ai_analysis,
                    segment=segment,
                    client_city=city,
                    client_address=f"{row.get('Улица', '')}, {row.get('Дом', '')}".strip(", "),
                )
                db.add(ticket)

                if progress_callback:
                    progress_callback(idx + 1, total, result)

            db.flush()

        results_df = pd.DataFrame(results)
        return results_df
