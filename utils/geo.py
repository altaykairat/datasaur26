"""Geo utilities: Haversine distance calculation, city matching, and Nominatim fallback."""
import math
from utils.logging import get_safe_logger
from difflib import get_close_matches

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    _GEOPY_AVAILABLE = True
except ImportError:
    _GEOPY_AVAILABLE = False

logger = get_safe_logger(__name__)

# Kazakhstan bounding box (generous)
_KZ_LAT_MIN, _KZ_LAT_MAX = 40.0, 56.0
_KZ_LON_MIN, _KZ_LON_MAX = 46.0, 88.0

# Hardcoded coordinates for all Kazakhstan cities referenced in the project.
# These cover the 16 office cities + common variations from ticket addresses.
CITY_COORDINATES = {
    # Office cities
    "Актау": (43.6353, 51.1700),
    "Актобе": (50.2839, 57.1670),
    "Алматы": (43.2380, 76.9457),
    "Астана": (51.1694, 71.4491),
    "Атырау": (47.1065, 51.9228),
    "Караганда": (49.8047, 73.1094),
    "Кокшетау": (53.2833, 69.3833),
    "Костанай": (53.2144, 63.6246),
    "Кызылорда": (44.8488, 65.5228),
    "Павлодар": (52.2873, 76.9674),
    "Петропавловск": (54.8753, 69.1628),
    "Тараз": (42.9000, 71.3667),
    "Уральск": (51.2333, 51.3667),
    "Усть-Каменогорск": (49.9536, 82.6131),
    "Шымкент": (42.3200, 69.5967),
    # Additional cities that may appear in ticket addresses
    "Тургень": (43.4333, 77.0667),
    "Красный Яр": (51.4333, 71.7333),
    "Осакаровка": (50.5833, 72.5500),
    "Шортанды": (51.7000, 70.7333),
    "Кокпек": (43.6000, 76.0000),
    "Аксу": (52.0333, 76.9167),
    "Сарань": (49.8000, 72.8333),
    "Кокпекты": (49.3167, 82.7833),
    "Кыргауылды": (43.2000, 76.6000),
    "Индербор": (48.5500, 51.7833),
    "Бескарагай": (50.0500, 79.1333),
    "Конаев": (43.8694, 77.0642),  # Kapchagai
    "Капчагай": (43.8694, 77.0642),
    "Кентау": (43.5167, 68.5000),
    "Ленгер": (42.1833, 69.8833),
    "Бадам": (42.2000, 69.5000),
    "Шардара": (41.5667, 67.9667),
    "Туркестан": (43.3000, 68.2500),
    "Косшы": (51.3000, 71.4167),
}

# Map for matching oblast/region names to nearest office city
REGION_TO_CITY = {
    "Алматинская": "Алматы",
    "Акмолинская": "Астана",
    "Атырауская": "Атырау",
    "Восточно-Казахстанская": "Усть-Каменогорск",
    "Карагандинская": "Караганда",
    "Костанайская": "Костанай",
    "Павлодарская": "Павлодар",
    "Северо-Казахстанская обл.": "Петропавловск",
    "Туркестанская": "Шымкент",
    "Абайская": "Усть-Каменогорск",
    "ЮКО / Шымкент обл.": "Шымкент",
    "Семипалатинская обл. / ВКО": "Усть-Каменогорск",
    "г. Алматы": "Алматы",
    "г. Шымкент": "Шымкент",
    "Алматинская обл.": "Алматы",
    "Акмолинская обл.": "Астана",
    "Павлодарская обл.": "Павлодар",
    "Mangystau obl.": "Актау",
}

# Office city names (the 16 actual offices)
OFFICE_CITIES = list(CITY_COORDINATES.keys())[:16]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the Haversine distance between two points on Earth.

    Returns distance in kilometers.
    """
    R = 6371  # Earth's radius in km

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return R * c


def is_in_kazakhstan(lat: float, lon: float) -> bool:
    """Check if coordinates fall within Kazakhstan's bounding box."""
    return _KZ_LAT_MIN <= lat <= _KZ_LAT_MAX and _KZ_LON_MIN <= lon <= _KZ_LON_MAX


def geocode_nominatim(city_name: str) -> dict | None:
    """
    Geocode a city name via Nominatim (OpenStreetMap).

    Returns dict with 'lat', 'lon', 'country_code', 'display_name'
    or None if lookup fails.
    """
    if not _GEOPY_AVAILABLE or not city_name:
        return None

    try:
        geolocator = Nominatim(user_agent="fire_routing_system", timeout=5)
        location = geolocator.geocode(city_name, language="ru", exactly_one=True)
        if location:
            country_code = ""
            if hasattr(location, 'raw') and 'display_name' in location.raw:
                # Nominatim returns country as last part of display_name
                parts = location.raw['display_name'].split(',')
                country_code = parts[-1].strip().lower() if parts else ""

            return {
                "lat": location.latitude,
                "lon": location.longitude,
                "country_code": country_code,
                "display_name": location.address,
            }
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.warning(f"Nominatim geocoding failed for '{city_name}': {e}")
    except Exception as e:
        logger.warning(f"Nominatim unexpected error for '{city_name}': {e}")

    return None


def resolve_city(city_name: str, region: str = None, use_nominatim: bool = True) -> dict | None:
    """
    Try to resolve a city name into a known city with coordinates.

    Resolution order:
    1. Exact match in office cities
    2. Exact match in CITY_COORDINATES
    3. Fuzzy match against office cities
    4. Region-based lookup
    5. Nominatim geocoding (if use_nominatim=True and geopy available)

    Returns a dict with 'city' and 'rule' keys, or None if no match.
    For Nominatim results: also includes 'lat', 'lon', 'is_foreign'.
    """
    if not city_name and not region:
        return None

    # Normalize
    if city_name:
        city_name = city_name.strip()

        # Direct match in office cities
        if city_name in OFFICE_CITIES:
            return {"city": city_name, "rule": "exact_match"}

        # Check if it exists in our coordinates (non-office city)
        if city_name in CITY_COORDINATES:
            return {"city": city_name, "rule": "coordinate_match"}

        # Fuzzy match against office cities
        matches = get_close_matches(city_name, OFFICE_CITIES, n=1, cutoff=0.7)
        if matches:
            return {"city": matches[0], "rule": "fuzzy_match"}

    # Try region-based fallback
    if region:
        region = region.strip()
        if region in REGION_TO_CITY:
            return {"city": REGION_TO_CITY[region], "rule": "region_fallback"}

    # Nominatim fallback — resolve unknown cities via OpenStreetMap
    if use_nominatim and city_name:
        geo_result = geocode_nominatim(city_name)
        if geo_result:
            lat, lon = geo_result["lat"], geo_result["lon"]

            if not is_in_kazakhstan(lat, lon):
                # Foreign address — will be routed to Astana/Almaty
                logger.info(f"Foreign city detected: '{city_name}' → {geo_result['display_name']}")
                return {
                    "city": city_name,
                    "rule": "nominatim_foreign",
                    "lat": lat,
                    "lon": lon,
                    "is_foreign": True,
                }

            # In Kazakhstan — store coords so find_nearest_office can use them
            logger.info(f"Nominatim resolved: '{city_name}' → ({lat}, {lon})")
            # Temporarily add to CITY_COORDINATES for this session
            CITY_COORDINATES[city_name] = (lat, lon)
            return {
                "city": city_name,
                "rule": "nominatim_kz",
                "lat": lat,
                "lon": lon,
                "is_foreign": False,
            }

    return None


def find_nearest_branch(client_city: str, client_address: str, branches: list[dict], use_nominatim: bool = True, enforce_street_level: bool = False) -> tuple[dict | None, list[dict]]:
    """
    Find the nearest branch (and return ranked alternative branches) using street level or city level.

    Args:
        client_city: The customer's resolved city name.
        client_address: The full address (from AI extraction) or None.
        branches: List of office dicts with 'id', 'city', 'address', 'lat', 'lon'.
    
    Returns:
        (nearest_branch_dict, [list of alternative branch dicts with distance])
        Returns (None, []) if distance cannot be calculated.
    """
    # 1. Try to get precise coordinates from Nominatim (street-level)
    coords = None
    search_query = None
    
    if use_nominatim and client_address and client_address != "Unknown":
        # Try full address first
        search_query = client_address
        geo_result = geocode_nominatim(search_query)
        
        # If full address fails, try stripping apartment/building numbers (anything after last comma)
        if not geo_result and "," in search_query:
            parts = [p.strip() for p in search_query.split(",")]
            if len(parts) > 1:
                # Try just City, Street
                search_query_reduced = f"{parts[0]}, {parts[1]}"
                geo_result = geocode_nominatim(search_query_reduced)
                
        if geo_result and is_in_kazakhstan(geo_result["lat"], geo_result["lon"]):
             coords = (geo_result["lat"], geo_result["lon"])
             
    if not coords:
        if enforce_street_level:
            # If we couldn't get a precise street-level coordinate, return None
            # so that the router can distribute load fairly among all city branches.
            return None, []
            
        # 2. Try city-level coordinates if street-level fails (for cross-city routing)
        search_query = client_city
        coords = CITY_COORDINATES.get(search_query)
        
        if not coords and use_nominatim:
            # Try Nominatim as a last resort for the city
            geo_result = geocode_nominatim(search_query)
            if geo_result and is_in_kazakhstan(geo_result["lat"], geo_result["lon"]):
                coords = (geo_result["lat"], geo_result["lon"])
                CITY_COORDINATES[search_query] = coords  # Cache for this session

    if not coords:
        return None, []

    client_lat, client_lon = coords

    # 3. Calculate distance to all active branches in the target city
    # Actually, we should calculate distance to ALL branches to see if a cross-city branch is somehow closer
    branches_with_dist = []
    for branch in branches:
        if branch.get("lat") is None or branch.get("lon") is None:
            continue

        dist = haversine(client_lat, client_lon, branch["lat"], branch["lon"])
        
        branch_copy = branch.copy()
        branch_copy["distance_km"] = round(dist, 1)
        branches_with_dist.append(branch_copy)

    if not branches_with_dist:
        return None, []
        
    # 4. Sort by distance, then id
    sorted_branches = sorted(branches_with_dist, key=lambda x: (x["distance_km"], x.get("id", 0)))
    
    nearest_branch = sorted_branches[0]
    alternatives = sorted_branches[1:]
    
    return nearest_branch, alternatives
