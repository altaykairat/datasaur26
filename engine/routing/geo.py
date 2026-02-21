"""Engine Subpackage: Geo-Routing

Encapsulates all logic resolving client locations to the nearest available office.
"""
import hashlib
from typing import Optional

from utils.geo import resolve_city, find_nearest_branch


class GeoRouter:
    """Handles geographic ticket routing to the optimal office branch."""

    @staticmethod
    def fallback_50_50(ticket_id: str, flag_key: str, offices: list[dict], flags: dict) -> dict:
        """Fallback to Astana or Almaty strictly based on a 50/50 hash of the ticket ID."""
        tid_str = str(ticket_id) if ticket_id else "default"
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

    @staticmethod
    def route(ai_analysis: dict, client_city: str, client_region: str, offices: list[dict], ticket_id: str) -> dict:
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
            return GeoRouter.fallback_50_50(ticket_id, "address_unknown", offices, flags)

        # If Nominatim detected a foreign address → immediate 50/50 fallback
        if resolved_dict.get("is_foreign"):
            return GeoRouter.fallback_50_50(ticket_id, "foreign_country", offices, flags)

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
                return GeoRouter.fallback_50_50(ticket_id, "geocode_failed", offices, flags)
                
            if nearest_branch.get("distance_km") and nearest_branch["distance_km"] > 500:
                # Foreign/very far country
                return GeoRouter.fallback_50_50(ticket_id, "foreign_country", offices, flags)

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
