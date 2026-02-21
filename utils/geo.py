"""Geo utilities: Haversine distance calculation and city matching."""
import math
from difflib import get_close_matches

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


def resolve_city(city_name: str, region: str = None) -> str | None:
    """
    Try to resolve a city name into an office city.

    First tries exact match, then fuzzy match, then region-based lookup.
    Returns the office city name or None if no match.
    """
    if not city_name and not region:
        return None

    # Normalize
    if city_name:
        city_name = city_name.strip()

        # Direct match in office cities
        if city_name in OFFICE_CITIES:
            return city_name

        # Check if it exists in our coordinates (non-office city)
        if city_name in CITY_COORDINATES:
            return city_name  # We know the coords, can find nearest office

        # Fuzzy match against office cities
        matches = get_close_matches(city_name, OFFICE_CITIES, n=1, cutoff=0.7)
        if matches:
            return matches[0]

    # Try region-based fallback
    if region:
        region = region.strip()
        if region in REGION_TO_CITY:
            return REGION_TO_CITY[region]

    return None


def find_nearest_office(city_name: str, offices: list[dict]) -> tuple[str, float]:
    """
    Find the nearest office to a given city.

    Args:
        city_name: The customer's city
        offices: List of office dicts with 'city', 'lat', 'lon'

    Returns:
        Tuple of (office_city, distance_km). If city unknown, returns (None, -1).
    """
    coords = CITY_COORDINATES.get(city_name)
    if not coords:
        return None, -1

    client_lat, client_lon = coords

    best_office = None
    best_dist = float("inf")

    for office in offices:
        dist = haversine(client_lat, client_lon, office["lat"], office["lon"])
        if dist < best_dist:
            best_dist = dist
            best_office = office["city"]

    return best_office, best_dist
