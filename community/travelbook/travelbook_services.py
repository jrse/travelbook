import json
import math
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

from travelbook_core import (
    APP_ID,
    CITY_POI_FILTERS,
    DBSCAN_EPSILON_M,
    DBSCAN_MIN_POINTS,
    DIARY_APP_VERSION,
    DRIVE_MODE_AVG_WINDOW_SECS,
    DRIVE_MODE_BASE_RADIUS_M,
    DRIVE_MODE_MAX_RADIUS_M,
    DRIVE_MODE_MIN_AVG_SPEED_MPS,
    GPS_SPEED_MIN_MOVE_M,
    MAX_VISIBLE_POI_RESULTS,
    POI_FETCH_BACKOFF_SECS,
    POI_FETCH_RETRYABLE_STATUS_CODES,
    POI_FETCH_RETRY_COUNT,
    POI_FETCH_TIMEOUT_SECS,
    POI_REFRESH_INTERVAL_MAX_SECS,
    POI_REFRESH_INTERVAL_MIN_SECS,
    POI_REFRESH_INTERVAL_PARTIAL_MOVE_FACTOR,
    POI_REFRESH_MAX_MOVE_M,
    POI_REFRESH_MIN_MOVE_M,
    POI_REFRESH_RADIUS_FACTOR,
    POI_SPEED_EXTENSION_MIN_MPS,
    POI_SPEED_LOOKAHEAD_SECS,
    TRAVEL_HEADING_MIN_MOVE_M,
    Cluster,
    Poi,
    build_overpass_query,
    parse_filter,
)


class PoiFetchError(RuntimeError):
    def __init__(self, user_message: str, *, retryable: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.retryable = retryable


def resolve_region(lat: float, lon: float, http_get=requests.get) -> Dict[str, str]:
    response = http_get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": lat,
            "lon": lon,
            "zoom": 10,
            "addressdetails": 1,
        },
        headers={"User-Agent": f"{APP_ID}/region-lookup"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    address = data.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or "-"
    )
    region = address.get("state") or address.get("county") or address.get("region") or city
    country = address.get("country") or "-"
    wiki_topic = region if region and region != "-" else city
    if not wiki_topic or wiki_topic == "-":
        wiki_topic = country
    wiki_url = f"https://en.wikipedia.org/wiki/{quote(str(wiki_topic).replace(' ', '_'))}"
    return {"city": str(city), "region": str(region), "country": str(country), "wiki_url": wiki_url}


def diary_file_path(base_dir: Path, day: date) -> Path:
    return base_dir / f"{day.isoformat()}.json"


def load_diary_entries(base_dir: Path, day: date) -> List[Dict]:
    path = diary_file_path(base_dir, day)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def save_diary_entries(
    base_dir: Path,
    day: date,
    entries: List[Dict],
    version: str = DIARY_APP_VERSION,
    timestamp: Optional[str] = None,
) -> None:
    now = timestamp or (datetime.utcnow().isoformat() + "Z")
    payload = {
        "date": day.isoformat(),
        "metadata": {
            "app": "travelbook",
            "version": version,
            "updated_at": now,
            "entry_count": len(entries),
        },
        "entries": entries,
    }
    path = diary_file_path(base_dir, day)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_category(tags: Dict[str, str]) -> str:
    for key in ["amenity", "tourism", "shop", "leisure", "highway", "place"]:
        if key in tags:
            return f"{key}:{tags[key]}"
    return "unknown"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def calculate_navigation_info(
    selected_poi: Optional[Poi],
    current_location,
    heading_deg: Optional[float],
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
    bearing_fn: Callable[[float, float, float, float], float] = bearing_deg,
):
    if selected_poi is None or current_location is None:
        return None

    distance = distance_fn(
        current_location[0],
        current_location[1],
        selected_poi.lat,
        selected_poi.lon,
    )
    bearing = bearing_fn(
        current_location[0],
        current_location[1],
        selected_poi.lat,
        selected_poi.lon,
    )
    turn = bearing if heading_deg is None else (bearing - heading_deg + 360.0) % 360.0
    return selected_poi, distance, bearing, heading_deg, turn


def derive_travel_heading(
    previous_location,
    current_location,
    min_move_m: float = TRAVEL_HEADING_MIN_MOVE_M,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
    bearing_fn: Callable[[float, float, float, float], float] = bearing_deg,
) -> Optional[float]:
    if previous_location is None or current_location is None:
        return None
    moved = distance_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )
    if moved < min_move_m:
        return None
    return bearing_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )


def calculate_speed_mps(
    previous_location,
    previous_ts: Optional[float],
    current_location,
    current_ts: Optional[float],
    min_move_m: float = GPS_SPEED_MIN_MOVE_M,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> Optional[float]:
    if previous_location is None or current_location is None or previous_ts is None or current_ts is None:
        return None
    delta_t = current_ts - previous_ts
    if delta_t <= 0:
        return None
    moved = distance_fn(
        previous_location[0],
        previous_location[1],
        current_location[0],
        current_location[1],
    )
    if moved < min_move_m:
        return 0.0
    return moved / delta_t


def trim_location_samples(
    samples: Sequence[Tuple[float, Tuple[float, float]]],
    max_age_secs: float = DRIVE_MODE_AVG_WINDOW_SECS,
    now_ts: Optional[float] = None,
) -> List[Tuple[float, Tuple[float, float]]]:
    if not samples:
        return []
    reference_ts = samples[-1][0] if now_ts is None else now_ts
    cutoff = reference_ts - max_age_secs
    return [(sample_ts, loc) for sample_ts, loc in samples if sample_ts >= cutoff]


def average_speed_mps(
    samples: Sequence[Tuple[float, Tuple[float, float]]],
    window_secs: float = DRIVE_MODE_AVG_WINDOW_SECS,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> Optional[float]:
    windowed = trim_location_samples(samples, window_secs)
    if len(windowed) < 2:
        return None
    start_ts, start_loc = windowed[0]
    end_ts, end_loc = windowed[-1]
    delta_t = end_ts - start_ts
    if delta_t <= 0:
        return None
    moved = distance_fn(start_loc[0], start_loc[1], end_loc[0], end_loc[1])
    return moved / delta_t


def detect_travel_mode(
    instant_speed_mps: Optional[float],
    avg_speed_window_mps: Optional[float],
    pedestrian_threshold_mps: float = DRIVE_MODE_MIN_AVG_SPEED_MPS,
) -> str:
    if avg_speed_window_mps is not None and avg_speed_window_mps >= pedestrian_threshold_mps:
        return "drive"
    if instant_speed_mps is not None:
        return "pedestrian"
    return "pedestrian"


def assign_clusters(
    pois: List[Poi],
    radius_m: int,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> List[Cluster]:
    if len(pois) < DBSCAN_MIN_POINTS:
        for poi in pois:
            poi.cluster_id = -1
        return []

    epsilon = min(DBSCAN_EPSILON_M, max(80.0, radius_m * 0.2))
    labels = [-2] * len(pois)

    def region_query(index: int) -> List[int]:
        result = []
        for other in range(len(pois)):
            if distance_fn(pois[index].lat, pois[index].lon, pois[other].lat, pois[other].lon) <= epsilon:
                result.append(other)
        return result

    cluster_id = 0
    for index in range(len(pois)):
        if labels[index] != -2:
            continue
        neighbors = region_query(index)
        if len(neighbors) < DBSCAN_MIN_POINTS:
            labels[index] = -1
            continue

        labels[index] = cluster_id
        seeds = neighbors[:]
        cursor = 0
        while cursor < len(seeds):
            seed_index = seeds[cursor]
            if labels[seed_index] == -1:
                labels[seed_index] = cluster_id
            if labels[seed_index] == -2:
                labels[seed_index] = cluster_id
                expanded = region_query(seed_index)
                if len(expanded) >= DBSCAN_MIN_POINTS:
                    for neighbor in expanded:
                        if neighbor not in seeds:
                            seeds.append(neighbor)
            cursor += 1
        cluster_id += 1

    grouped: Dict[int, List[Poi]] = {}
    for index, poi in enumerate(pois):
        poi.cluster_id = labels[index]
        if labels[index] >= 0:
            grouped.setdefault(labels[index], []).append(poi)

    clusters: List[Cluster] = []
    for cid, members in grouped.items():
        center_lat = sum(p.lat for p in members) / float(len(members))
        center_lon = sum(p.lon for p in members) / float(len(members))
        radius = 0.0
        for poi in members:
            radius = max(radius, distance_fn(center_lat, center_lon, poi.lat, poi.lon))
        clusters.append(
            Cluster(
                cluster_id=cid,
                center_lat=center_lat,
                center_lon=center_lon,
                radius_m=radius,
                size=len(members),
            )
        )
    return clusters


def match_filter(filter_lookup: Dict, tags: Dict[str, str]) -> Optional[str]:
    for key, value in tags.items():
        found = filter_lookup.get((key, value))
        if found is not None:
            return found
    return None


def extract_poi_url(tags: Dict[str, str]) -> Optional[str]:
    for key in ("website", "contact:website", "url", "contact:url"):
        value = str(tags.get(key, "")).strip()
        if not value:
            continue
        if value.startswith(("http://", "https://")):
            return value
        if value.startswith("www."):
            return f"https://{value}"
    return None


def poi_refresh_distance(radius_m: int) -> float:
    return min(POI_REFRESH_MAX_MOVE_M, max(POI_REFRESH_MIN_MOVE_M, float(radius_m) * POI_REFRESH_RADIUS_FACTOR))


def effective_query_radius(
    base_radius_m: int,
    max_radius_m: int,
    speed_mps: Optional[float],
    travel_mode: str = "pedestrian",
) -> int:
    if travel_mode == "drive":
        base_radius_m = max(base_radius_m, DRIVE_MODE_BASE_RADIUS_M)
        max_radius_m = max(max_radius_m, DRIVE_MODE_MAX_RADIUS_M)
    if speed_mps is None or speed_mps < POI_SPEED_EXTENSION_MIN_MPS:
        return min(base_radius_m, max_radius_m)
    extension = int(speed_mps * POI_SPEED_LOOKAHEAD_SECS)
    return min(max_radius_m, max(base_radius_m, base_radius_m + extension))


def poi_refresh_interval(radius_m: int, speed_mps: Optional[float]) -> float:
    if speed_mps is None or speed_mps <= 0:
        return POI_REFRESH_INTERVAL_MAX_SECS
    derived = poi_refresh_distance(radius_m) / speed_mps
    return min(POI_REFRESH_INTERVAL_MAX_SECS, max(POI_REFRESH_INTERVAL_MIN_SECS, derived))


def should_refresh_pois(
    current_location,
    reference_location,
    radius_m: int,
    speed_mps: Optional[float] = None,
    seconds_since_refresh: Optional[float] = None,
    distance_fn: Callable[[float, float, float, float], float] = distance_m,
) -> bool:
    if current_location is None or reference_location is None:
        return True
    moved = distance_fn(
        current_location[0],
        current_location[1],
        reference_location[0],
        reference_location[1],
    )
    threshold = poi_refresh_distance(radius_m)
    if moved >= threshold:
        return True
    if seconds_since_refresh is None:
        return False
    partial_threshold = max(15.0, threshold * POI_REFRESH_INTERVAL_PARTIAL_MOVE_FACTOR)
    return seconds_since_refresh >= poi_refresh_interval(radius_m, speed_mps) and moved >= partial_threshold


def fetch_pois(
    lat: float,
    lon: float,
    radius: int,
    categories: Dict[str, bool],
    filter_lookup: Dict,
    category_labels: Dict[str, str],
    include_cities: bool = False,
    city_only: bool = False,
    http_post=requests.post,
    sleep_fn=time.sleep,
) -> List[Poi]:
    active_filters = [filter_key for filter_key, enabled in categories.items() if enabled]
    city_filter_lookup = {}
    for _label, filter_text in CITY_POI_FILTERS:
        parsed = parse_filter(filter_text)
        if parsed is not None:
            city_filter_lookup[parsed] = filter_text
    city_labels = {filter_text: label for label, filter_text in CITY_POI_FILTERS}
    city_filters = [filter_key for filter_key in active_filters if filter_key in city_labels]

    if city_only:
        active_filters = city_filters
        extra_filters = []
    else:
        extra_filters = [
            filter_key for _label, filter_key in CITY_POI_FILTERS if include_cities and filter_key not in active_filters
        ]

    if not active_filters and not extra_filters:
        return []

    query = build_overpass_query(lat, lon, radius, active_filters, extra_filters=extra_filters)
    data = None
    last_error: Optional[PoiFetchError] = None
    attempts = POI_FETCH_RETRY_COUNT + 1
    for attempt in range(attempts):
        try:
            response = http_post(
                "https://overpass-api.de/api/interpreter",
                data=query.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                timeout=POI_FETCH_TIMEOUT_SECS,
            )
            response.raise_for_status()
            data = response.json()
            break
        except requests.Timeout:
            last_error = PoiFetchError(
                "POI-Abfrage hat das Zeitlimit erreicht. Bitte erneut versuchen.",
                retryable=True,
            )
        except requests.ConnectionError:
            last_error = PoiFetchError(
                "Keine Netzwerkverbindung für POI-Abfrage.",
                retryable=True,
            )
        except requests.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in POI_FETCH_RETRYABLE_STATUS_CODES:
                last_error = PoiFetchError(
                    f"POI-Dienst antwortet gerade nicht stabil (HTTP {status_code}).",
                    retryable=True,
                )
            elif status_code is not None:
                last_error = PoiFetchError(
                    f"POI-Abfrage wurde vom Dienst abgelehnt (HTTP {status_code}).",
                    retryable=False,
                )
            else:
                last_error = PoiFetchError("POI-Abfrage ist mit einem HTTP-Fehler fehlgeschlagen.", retryable=False)
        except (ValueError, json.JSONDecodeError):
            last_error = PoiFetchError("POI-Dienst lieferte eine ungueltige Antwort.", retryable=False)
        except requests.RequestException:
            last_error = PoiFetchError("POI-Abfrage ist aufgrund eines Netzwerkfehlers fehlgeschlagen.", retryable=True)

        if last_error is None:
            break
        if not last_error.retryable or attempt >= attempts - 1:
            raise last_error
        if attempt < len(POI_FETCH_BACKOFF_SECS):
            sleep_fn(POI_FETCH_BACKOFF_SECS[attempt])

    if data is None:
        raise last_error or PoiFetchError("POIs konnten nicht geladen werden.", retryable=False)
    if not isinstance(data, dict):
        raise PoiFetchError("POI-Dienst lieferte ein ungueltiges Datenformat.", retryable=False)

    results: List[Poi] = []
    for element in data.get("elements", []):
        if "lat" in element and "lon" in element:
            poi_lat, poi_lon = float(element["lat"]), float(element["lon"])
        elif "center" in element:
            poi_lat, poi_lon = float(element["center"]["lat"]), float(element["center"]["lon"])
        else:
            continue

        distance = distance_m(lat, lon, poi_lat, poi_lon)
        if distance > radius:
            continue

        bearing = bearing_deg(lat, lon, poi_lat, poi_lon)
        tags = element.get("tags", {})
        filter_key = match_filter(filter_lookup, tags) or match_filter(city_filter_lookup, tags) or "unknown"
        if city_only and filter_key not in city_labels:
            continue
        name = tags.get("name", f"{element.get('type', 'poi')} #{element.get('id', '?')}")
        results.append(
            Poi(
                name=name,
                lat=poi_lat,
                lon=poi_lon,
                distance_m=distance,
                bearing_deg=bearing,
                category=infer_category(tags),
                category_filter=filter_key,
                category_label=category_labels.get(filter_key, city_labels.get(filter_key, "Andere")),
                url=extract_poi_url(tags),
            )
        )

    results.sort(key=lambda poi: poi.distance_m)
    return results[:MAX_VISIBLE_POI_RESULTS]


def is_city_poi(poi: Optional[Poi]) -> bool:
    if poi is None:
        return False
    return poi.category.startswith("place:") and poi.category_filter in {filter_text for _label, filter_text in CITY_POI_FILTERS}
