from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


APP_ID = "org.postmarketos.travelbook"
DEFAULT_RADIUS_M = 1000
MAX_RADIUS_M = 2000
DRIVE_MODE_BASE_RADIUS_M = 5000
DRIVE_MODE_MAX_RADIUS_M = 12000
DRIVE_MODE_AVG_WINDOW_SECS = 300
DRIVE_MODE_MIN_AVG_SPEED_MPS = 4.0
CITY_POI_RADIUS_M = 3000
MIN_CANVAS_SIZE = 1200
CANVAS_PADDING = 600
MIN_ZOOM = 0.35
MAX_ZOOM = 3.0
RADAR_TILE_OPACITY = 0.18
RADAR_TILE_OPACITY_DRIVE = 0.34
RADAR_TILE_SIZE = 256
RADAR_TILE_ZOOM_MIN = 12
RADAR_TILE_ZOOM_MAX = 17
RADAR_TILE_FETCH_TIMEOUT_SECS = 8
RADAR_TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
RADAR_TILE_REQUESTS_PER_DRAW = 2
RADAR_LABEL_LIMIT = 24
RADAR_HEADING_REDRAW_THRESHOLD_DEG = 1.5
OLLAMA_BASE_URL_DEFAULT = "http://192.168.178.48:11434"
OLLAMA_DIARY_MODEL = "qwen3:8b"
OLLAMA_DIARY_MAX_TOKENS = 240
OLLAMA_CONNECT_TIMEOUT_SECS = 5
OLLAMA_READ_TIMEOUT_SECS = 90
OLLAMA_DIARY_SYSTEM_PROMPT_DEFAULT = (
    "Correct spelling and grammar, rewrite as a concise first-person diary entry, "
    "return only the diary text, and keep it under 240 tokens."
)
EXTRA_AMENITY_VALUES = [
    "bar",
    "biergarten",
    "fast_food",
    "food_court",
    "ice_cream",
    "pub",
    "college",
    "dancing_school",
    "driving_school",
    "first_aid_school",
    "kindergarten",
    "language_school",
    "library",
    "surf_school",
    "toy_library",
    "research_institute",
    "training",
    "music_school",
    "school",
    "traffic_park",
    "university",
    "bicycle_parking",
    "bicycle_repair_station",
    "bicycle_rental",
    "bicycle_wash",
    "boat_rental",
    "boat_storage",
    "boat_sharing",
    "bus_station",
    "car_rental",
    "car_sharing",
    "car_wash",
    "compressed_air",
    "vehicle_inspection",
    "charging_station",
    "driver_training",
    "ferry_terminal",
    "grit_bin",
    "motorcycle_parking",
    "parking",
    "parking_entrance",
    "parking_space",
    "taxi",
    "weighbridge",
    "atm",
    "payment_terminal",
    "bank",
    "bureau_de_change",
    "money_transfer",
    "payment_centre",
    "baby_hatch",
    "clinic",
    "dentist",
    "doctors",
    "hospital",
    "nursing_home",
    "social_facility",
    "veterinary",
    "arts_centre",
    "brothel",
    "casino",
    "cinema",
    "community_centre",
    "conference_centre",
    "events_venue",
    "exhibition_centre",
    "fountain",
    "gambling",
    "love_hotel",
    "music_venue",
    "nightclub",
    "planetarium",
    "public_bookcase",
    "social_centre",
    "stage",
    "stripclub",
    "studio",
    "swingerclub",
    "theatre",
    "courthouse",
    "fire_station",
    "police",
    "post_box",
    "post_depot",
    "post_office",
    "prison",
    "ranger_station",
    "townhall",
    "bbq",
    "bench",
    "check_in",
    "dog_toilet",
    "dressing_room",
    "drinking_water",
    "give_box",
    "lounge",
    "mailroom",
    "parcel_locker",
    "shelter",
    "shower",
    "telephone",
    "toilets",
    "water_point",
    "watering_place",
    "sanitary_dump_station",
    "recycling",
    "waste_basket",
    "waste_disposal",
    "waste_transfer_station",
    "animal_boarding",
    "animal_breeding",
    "animal_shelter",
    "animal_training",
    "baking_oven",
    "clock",
    "crematorium",
    "dive_centre",
    "funeral_hall",
    "grave_yard",
    "hunting_stand",
    "internet_cafe",
    "kitchen",
    "kneipp_water_cure",
    "lounger",
    "marketplace",
    "monastery",
    "mortuary",
    "photo_booth",
    "place_of_mourning",
    "place_of_worship",
    "public_bath",
    "refugee_site",
    "vending_machine",
    "hydrant",
]


def amenity_label(value: str) -> str:
    replacements = {
        "atm": "ATM",
        "bbq": "BBQ",
    }
    if value in replacements:
        return replacements[value]
    return value.replace("_", " ").title()


POI_OPTIONS = [
    ("Staedte", '"place"="city"', True),
    ("Staedte", '"place"="town"', True),
    ("Staedte", '"place"="village"', True),
    ("Restaurants", '"amenity"="restaurant"', True),
    ("Cafes", '"amenity"="cafe"', True),
    ("Museen", '"tourism"="museum"', True),
    ("Sehenswuerdigkeiten", '"tourism"="attraction"', True),
    ("Hotels", '"tourism"="hotel"', True),
    ("Supermaerkte", '"shop"="supermarket"', True),
    ("Apotheken", '"amenity"="pharmacy"', False),
    ("Parks", '"leisure"="park"', True),
    ("Tankstellen", '"amenity"="fuel"', False),
    ("Bus-Haltestellen", '"highway"="bus_stop"', False),
] + [(amenity_label(value), f'"amenity"="{value}"', False) for value in EXTRA_AMENITY_VALUES]
CITY_POI_FILTERS = [
    ("Staedte", '"place"="city"'),
    ("Staedte", '"place"="town"'),
    ("Staedte", '"place"="village"'),
]
CATEGORY_COLORS = [
    (0.95, 0.31, 0.30),
    (0.98, 0.65, 0.18),
    (0.22, 0.74, 0.91),
    (0.67, 0.45, 0.93),
    (0.98, 0.85, 0.20),
    (0.22, 0.84, 0.58),
    (0.36, 0.80, 0.38),
    (0.82, 0.49, 0.21),
    (0.75, 0.75, 0.75),
]
UNKNOWN_COLOR = (0.85, 0.85, 0.85)
CLUSTER_COLOR_FILL = (0.95, 0.23, 0.72, 0.18)
CLUSTER_COLOR_STROKE = (1.00, 0.45, 0.88, 0.90)
DBSCAN_MIN_POINTS = 3
DBSCAN_EPSILON_M = 180.0
DIARY_APP_VERSION = "0.1.0"
COMPASS_HEADING_OFFSET_DEG = 0.0
REGION_REFRESH_MOVE_M = 500.0
REGION_REFRESH_SECS = 120.0
MAX_VISIBLE_POI_RESULTS = 120
POI_REFRESH_MIN_MOVE_M = 75.0
POI_REFRESH_MAX_MOVE_M = 250.0
POI_REFRESH_RADIUS_FACTOR = 0.25
TRAVEL_HEADING_MIN_MOVE_M = 25.0
GEOCLUE_ACCURACY_LEVEL_EXACT = 8
GPS_FIX_MAX_ACCURACY_M = 100.0
GPS_SPEED_MIN_MOVE_M = 3.0
POI_SPEED_EXTENSION_MIN_MPS = 3.0
POI_SPEED_LOOKAHEAD_SECS = 30.0
POI_REFRESH_INTERVAL_MIN_SECS = 4.0
POI_REFRESH_INTERVAL_MAX_SECS = 45.0
POI_REFRESH_INTERVAL_PARTIAL_MOVE_FACTOR = 0.35
POI_FETCH_TIMEOUT_SECS = 20
POI_FETCH_RETRY_COUNT = 2
POI_FETCH_RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
POI_FETCH_BACKOFF_SECS = (0.6, 1.2)
INDICATOR_COLORS = {
    "ok": "#2e7d32",
    "warn": "#b26a00",
    "error": "#b00020",
    "muted": "#6b7280",
    "info": "#005a9c",
}


@dataclass
class Poi:
    name: str
    lat: float
    lon: float
    distance_m: float
    bearing_deg: float
    category: str
    category_filter: str
    category_label: str
    url: Optional[str] = None
    cluster_id: int = -1


@dataclass
class Cluster:
    cluster_id: int
    center_lat: float
    center_lon: float
    radius_m: float
    size: int


def parse_filter(filter_text: str) -> Optional[Tuple[str, str]]:
    try:
        parts = filter_text.split("=")
        if len(parts) != 2:
            return None
        key = parts[0].strip().replace('"', "")
        value = parts[1].strip().replace('"', "")
        return key, value
    except Exception:
        return None


def build_overpass_query(
    lat: float,
    lon: float,
    radius: int,
    selected_filters: Optional[List[str]] = None,
    extra_filters: Optional[List[str]] = None,
) -> str:
    active_filters = selected_filters
    if active_filters is None:
        active_filters = [osm_filter for _label, osm_filter, _enabled in POI_OPTIONS]
    filters = [f"nwr[{osm_filter}](around:{radius},{lat},{lon});" for osm_filter in active_filters]
    if extra_filters:
        filters.extend(f"nwr[{osm_filter}](around:{radius},{lat},{lon});" for osm_filter in extra_filters)
    return "[out:json][timeout:20];(" + "".join(filters) + ");out center tags;"


def compute_runtime_indicators(
    location_source: str,
    network_state: str,
    fetch_in_progress: bool,
    region_fetch_in_progress: bool,
    reload_requested: bool,
) -> Dict[str, Tuple[str, str]]:
    if location_source == "gps":
        gps_state = ("available", "ok")
    elif location_source == "gps_cached":
        gps_state = ("last gps fix", "warn")
    elif location_source == "manual":
        gps_state = ("manual fallback", "warn")
    elif location_source == "none":
        gps_state = ("unavailable", "error")
    else:
        gps_state = ("starting", "muted")

    if network_state == "online":
        network = ("online", "ok")
    elif network_state == "error":
        network = ("request failed", "error")
    else:
        network = ("unknown", "muted")

    if fetch_in_progress and region_fetch_in_progress:
        data = ("loading POIs + region", "info")
    elif fetch_in_progress:
        data = ("loading POIs", "info")
    elif region_fetch_in_progress:
        data = ("loading region", "info")
    elif reload_requested:
        data = ("reload queued", "warn")
    else:
        data = ("idle", "ok")

    return {"GPS": gps_state, "Network": network, "Data": data}


def format_fix_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 0:
        seconds = 0
    total_seconds = int(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m"
