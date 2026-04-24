import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

from travelbook_core import Poi, compute_runtime_indicators, format_fix_age
from travelbook_services import (
    DiaryImproveError,
    PoiFetchError,
    average_speed_mps,
    assign_clusters,
    calculate_speed_mps,
    calculate_navigation_info,
    detect_travel_mode,
    derive_travel_heading,
    effective_query_radius,
    extract_poi_url,
    fetch_pois,
    improve_diary_entry,
    is_city_poi,
    load_diary_entries,
    load_app_settings,
    ollama_generate_url,
    poi_refresh_distance,
    poi_refresh_interval,
    resolve_region,
    save_app_settings,
    save_diary_entries,
    should_refresh_pois,
    trim_location_samples,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.text = ""
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_lines(self, decode_unicode=False):
        return iter(())

    def close(self):
        return None


def extract_query(data):
    if isinstance(data, dict):
        return data.get("data", "")
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return str(data)


class TestServices(unittest.TestCase):
    def test_runtime_indicators_cover_loading_combination(self):
        indicators = compute_runtime_indicators("gps", "online", True, True, False)
        self.assertEqual(("available", "ok"), indicators["GPS"])
        self.assertEqual(("online", "ok"), indicators["Network"])
        self.assertEqual(("loading POIs + region", "info"), indicators["Data"])

    def test_format_fix_age_formats_seconds_minutes_and_hours(self):
        self.assertEqual("-", format_fix_age(None))
        self.assertEqual("12s", format_fix_age(12))
        self.assertEqual("1m 5s", format_fix_age(65))
        self.assertEqual("1h 1m", format_fix_age(3665))

    def test_resolve_region_prefers_state_and_builds_wikipedia_url(self):
        def fake_get(*_args, **_kwargs):
            return FakeResponse(
                {
                    "address": {
                        "city": "Munich",
                        "state": "Bavaria",
                        "country": "Germany",
                    }
                }
            )

        info = resolve_region(48.1, 11.6, http_get=fake_get)
        self.assertEqual("Munich", info["city"])
        self.assertEqual("Bavaria", info["region"])
        self.assertEqual("Germany", info["country"])
        self.assertTrue(info["wiki_url"].endswith("/Bavaria"))

    def test_extract_poi_url_prefers_website_and_normalizes_www(self):
        self.assertEqual("https://example.com", extract_poi_url({"website": "https://example.com"}))
        self.assertEqual("https://www.example.org", extract_poi_url({"contact:website": "www.example.org"}))
        self.assertIsNone(extract_poi_url({"name": "Cafe"}))

    def test_assign_clusters_groups_nearby_pois(self):
        pois = [
            Poi("A", 48.1000, 11.6000, 0, 0, "x", "f", "L"),
            Poi("B", 48.1004, 11.6000, 0, 0, "x", "f", "L"),
            Poi("C", 48.1008, 11.6000, 0, 0, "x", "f", "L"),
            Poi("D", 48.1200, 11.6200, 0, 0, "x", "f", "L"),
        ]

        clusters = assign_clusters(pois, 1000)
        clustered = [poi for poi in pois if poi.cluster_id >= 0]
        noise = [poi for poi in pois if poi.cluster_id < 0]

        self.assertEqual(1, len(clusters))
        self.assertEqual(3, len(clustered))
        self.assertEqual(1, len(noise))

    def test_diary_roundtrip_persists_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            day = date(2026, 4, 4)
            entries = [{"id": "1", "text": "hello"}]

            save_diary_entries(base_dir, day, entries, timestamp="2026-04-04T10:00:00Z")
            loaded = load_diary_entries(base_dir, day)

            self.assertEqual(entries, loaded)

    def test_app_settings_roundtrip_persists_ollama_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            save_app_settings(
                base_dir,
                {
                    "ollama_base_url": "http://192.168.178.48",
                    "ollama_diary_system_prompt": "rewrite diary entries",
                },
            )

            loaded = load_app_settings(base_dir)

            self.assertEqual("http://192.168.178.48", loaded["ollama_base_url"])
            self.assertEqual("rewrite diary entries", loaded["ollama_diary_system_prompt"])

    def test_ollama_generate_url_accepts_plain_and_api_base_urls(self):
        self.assertEqual("http://192.168.178.48/api/generate", ollama_generate_url("http://192.168.178.48"))
        self.assertEqual("http://192.168.178.48/api/generate", ollama_generate_url("http://192.168.178.48/api"))

    def test_improve_diary_entry_accumulates_streamed_response(self):
        calls = {}

        class FakeStreamResponse(FakeResponse):
            def iter_lines(self, decode_unicode=False):
                lines = [
                    '{"response":"Heute bin ich","done":false}',
                    '{"response":" durch die Stadt gelaufen.","done":false}',
                    '{"done":true}',
                ]
                return iter(lines)

        def fake_post(url, json=None, **kwargs):
            calls["url"] = url
            calls["json"] = json
            calls["kwargs"] = kwargs
            return FakeStreamResponse({})

        improved = improve_diary_entry(
            "today i walked through the city",
            base_url="http://192.168.178.48",
            system_prompt="fix diary entry",
            http_post=fake_post,
        )

        self.assertEqual("Heute bin ich durch die Stadt gelaufen.", improved)
        self.assertEqual("http://192.168.178.48/api/generate", calls["url"])
        self.assertEqual(False, calls["json"]["think"])
        self.assertEqual(True, calls["json"]["stream"])
        self.assertEqual(240, calls["json"]["options"]["num_predict"])

    def test_improve_diary_entry_raises_on_empty_stream_response(self):
        class EmptyStreamResponse(FakeResponse):
            def iter_lines(self, decode_unicode=False):
                return iter(['{"done":true}'])

        with self.assertRaises(DiaryImproveError):
            improve_diary_entry(
                "today",
                base_url="http://192.168.178.48",
                system_prompt="fix diary entry",
                http_post=lambda *_args, **_kwargs: EmptyStreamResponse({}),
            )

    def test_fetch_pois_retries_timeout_then_succeeds(self):
        import requests

        calls = {"count": 0}

        def fake_post(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise requests.Timeout()
            return FakeResponse(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 48.1,
                            "lon": 11.6,
                            "tags": {"name": "Cafe", "amenity": "cafe"},
                        }
                    ]
                }
            )

        def fake_sleep(_seconds):
            return None

        categories = {'"amenity"="cafe"': True}
        filter_lookup = {("amenity", "cafe"): '"amenity"="cafe"'}
        labels = {'"amenity"="cafe"': "Cafes"}

        pois = fetch_pois(
            48.1,
            11.6,
            1000,
            categories,
            filter_lookup,
            labels,
            http_post=fake_post,
            sleep_fn=fake_sleep,
        )
        self.assertEqual(2, calls["count"])
        self.assertEqual(1, len(pois))
        self.assertEqual("Cafe", pois[0].name)

    def test_fetch_pois_raises_clear_message_after_retryable_failures(self):
        import requests

        def fake_post(*_args, **_kwargs):
            raise requests.ConnectionError()

        categories = {'"amenity"="cafe"': True}
        filter_lookup = {("amenity", "cafe"): '"amenity"="cafe"'}
        labels = {'"amenity"="cafe"': "Cafes"}

        with self.assertRaises(PoiFetchError) as ctx:
            fetch_pois(
                48.1,
                11.6,
                1000,
                categories,
                filter_lookup,
                labels,
                http_post=fake_post,
                sleep_fn=lambda _seconds: None,
            )
        self.assertIn("Netzwerkverbindung", ctx.exception.user_message)

    def test_fetch_pois_batches_large_filter_sets_and_deduplicates_results(self):
        calls = {"queries": []}
        categories = {f'"amenity"="test_{index}"': True for index in range(30)}
        filter_lookup = {("amenity", f"test_{index}"): f'"amenity"="test_{index}"' for index in range(30)}
        labels = {f'"amenity"="test_{index}"': f"Test {index}" for index in range(30)}

        def fake_post(_url, data=None, **_kwargs):
            calls["queries"].append(extract_query(data))
            return FakeResponse(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 48.1,
                            "lon": 11.6,
                            "tags": {"name": "Cafe", "amenity": "test_0"},
                        }
                    ]
                }
            )

        pois = fetch_pois(48.1, 11.6, 1000, categories, filter_lookup, labels, http_post=fake_post)
        self.assertEqual(2, len(calls["queries"]))
        self.assertEqual(1, len(pois))
        self.assertEqual("Cafe", pois[0].name)

    def test_fetch_pois_only_queries_selected_categories_without_city_expansion(self):
        calls = {"queries": []}
        categories = {
            '"amenity"="cafe"': True,
            '"tourism"="hotel"': False,
            '"place"="city"': False,
        }
        filter_lookup = {("amenity", "cafe"): '"amenity"="cafe"'}
        labels = {'"amenity"="cafe"': "Cafes"}

        def fake_post(_url, data=None, **_kwargs):
            calls["queries"].append(extract_query(data))
            return FakeResponse({"elements": []})

        fetch_pois(48.1, 11.6, 1000, categories, filter_lookup, labels, include_cities=False, http_post=fake_post)

        self.assertEqual(1, len(calls["queries"]))
        self.assertIn('nwr["amenity"="cafe"](around:1000,48.1,11.6);', calls["queries"][0])
        self.assertNotIn('nwr["place"="city"](around:1000,48.1,11.6);', calls["queries"][0])

    def test_fetch_pois_splits_406_batches_and_still_returns_results(self):
        import requests

        calls = {"queries": []}
        categories = {f'"amenity"="test_{index}"': True for index in range(4)}
        filter_lookup = {("amenity", f"test_{index}"): f'"amenity"="test_{index}"' for index in range(4)}
        labels = {f'"amenity"="test_{index}"': f"Test {index}" for index in range(4)}

        class Fake406Response:
            status_code = 406

        def fake_post(_url, data=None, **_kwargs):
            query = extract_query(data)
            calls["queries"].append(query)
            if query.count("nwr[") > 1:
                raise requests.HTTPError(response=Fake406Response())
            test_index = int(query.split('test_')[1].split('"')[0])
            return FakeResponse(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": test_index,
                            "lat": 48.1,
                            "lon": 11.6,
                            "tags": {"name": "Split OK", "amenity": "test_0"},
                        }
                    ]
                }
            )

        pois = fetch_pois(48.1, 11.6, 1000, categories, filter_lookup, labels, http_post=fake_post)

        self.assertGreater(len(calls["queries"]), 1)
        self.assertEqual(4, len(pois))

    def test_fetch_pois_logs_query_text(self):
        categories = {'"amenity"="cafe"': True}
        filter_lookup = {("amenity", "cafe"): '"amenity"="cafe"'}
        labels = {'"amenity"="cafe"': "Cafes"}

        def fake_post(_url, data=None, **_kwargs):
            return FakeResponse({"elements": []})

        with patch("travelbook_services.LOGGER.warning") as warning:
            fetch_pois(48.1, 11.6, 1000, categories, filter_lookup, labels, http_post=fake_post)

        self.assertEqual(2, warning.call_count)
        log_args = warning.call_args_list[0][0]
        self.assertEqual("POI query lat=%s lon=%s radius=%s filter_count=%s filters=%s\n%s", log_args[0])
        self.assertEqual(48.1, log_args[1])
        self.assertEqual(11.6, log_args[2])
        self.assertEqual(1000, log_args[3])
        self.assertEqual(1, log_args[4])
        self.assertEqual(['"amenity"="cafe"'], log_args[5])
        self.assertIn('nwr["amenity"="cafe"](around:1000,48.1,11.6);', log_args[6])
        response_log_args = warning.call_args_list[1][0]
        self.assertEqual("POI response status=%s body=%s", response_log_args[0])

    def test_fetch_pois_posts_query_using_data_form_field_and_headers(self):
        categories = {'"amenity"="cafe"': True}
        filter_lookup = {("amenity", "cafe"): '"amenity"="cafe"'}
        labels = {'"amenity"="cafe"': "Cafes"}
        request_args = {}

        def fake_post(_url, data=None, headers=None, **_kwargs):
            request_args["data"] = data
            request_args["headers"] = headers
            return FakeResponse({"elements": []})

        fetch_pois(48.1, 11.6, 1000, categories, filter_lookup, labels, http_post=fake_post)

        self.assertEqual(
            '[out:json][timeout:20];(nwr["amenity"="cafe"](around:1000,48.1,11.6););out center tags;',
            request_args["data"]["data"],
        )
        self.assertEqual("application/json,text/plain;q=0.9,*/*;q=0.8", request_args["headers"]["Accept"])
        self.assertIn("/poi-fetch", request_args["headers"]["User-Agent"])

    def test_poi_refresh_distance_scales_with_radius_but_stays_bounded(self):
        self.assertEqual(75.0, poi_refresh_distance(100))
        self.assertEqual(250.0, poi_refresh_distance(2000))

    def test_calculate_speed_mps_uses_distance_over_time(self):
        speed = calculate_speed_mps(
            (48.1000, 11.6000),
            100.0,
            (48.1010, 11.6000),
            110.0,
            distance_fn=lambda *_args: 50.0,
        )
        self.assertEqual(5.0, speed)

    def test_trim_location_samples_keeps_recent_window_only(self):
        samples = [
            (100.0, (48.1, 11.6)),
            (250.0, (48.2, 11.6)),
            (410.0, (48.3, 11.6)),
        ]

        trimmed = trim_location_samples(samples, 120.0, now_ts=410.0)
        self.assertEqual([(410.0, (48.3, 11.6))], trimmed)

    def test_average_speed_mps_uses_window_endpoints(self):
        samples = [
            (100.0, (48.1, 11.6)),
            (200.0, (48.2, 11.6)),
            (300.0, (48.3, 11.6)),
        ]
        speed = average_speed_mps(samples, 300.0, distance_fn=lambda *_args: 600.0)
        self.assertEqual(3.0, speed)

    def test_detect_travel_mode_switches_to_drive_on_high_average_speed(self):
        self.assertEqual("drive", detect_travel_mode(1.0, 4.5))
        self.assertEqual("pedestrian", detect_travel_mode(2.0, 2.5))

    def test_poi_refresh_interval_shortens_when_speed_increases(self):
        slow = poi_refresh_interval(1000, 2.0)
        fast = poi_refresh_interval(1000, 20.0)
        stopped = poi_refresh_interval(1000, 0.0)
        self.assertGreater(slow, fast)
        self.assertEqual(45.0, stopped)

    def test_effective_query_radius_extends_with_speed_but_respects_max(self):
        self.assertEqual(1000, effective_query_radius(1000, 2000, 1.0))
        self.assertEqual(1600, effective_query_radius(1000, 2000, 20.0))
        self.assertEqual(2000, effective_query_radius(1000, 2000, 80.0))

    def test_effective_query_radius_uses_larger_drive_mode_base(self):
        self.assertEqual(5000, effective_query_radius(1000, 2000, 1.0, "drive"))

    def test_should_refresh_pois_requires_significant_movement(self):
        close_by = (48.1003, 11.6000)
        far_enough = (48.1023, 11.6000)
        reference = (48.1000, 11.6000)

        self.assertFalse(should_refresh_pois(close_by, reference, 1000))
        self.assertTrue(should_refresh_pois(far_enough, reference, 1000))

    def test_should_refresh_pois_can_refresh_on_time_interval_with_partial_movement(self):
        self.assertTrue(
            should_refresh_pois(
                (48.1008, 11.6000),
                (48.1000, 11.6000),
                1000,
                speed_mps=10.0,
                seconds_since_refresh=30.0,
                distance_fn=lambda *_args: 100.0,
            )
        )

    def test_calculate_navigation_info_uses_compass_heading_for_turn(self):
        poi = Poi("Cafe", 48.1010, 11.6000, 0, 0, "amenity:cafe", '"amenity"="cafe"', "Cafes")
        nav = calculate_navigation_info(
            poi,
            (48.1000, 11.6000),
            30.0,
            distance_fn=lambda *_args: 111.0,
            bearing_fn=lambda *_args: 90.0,
        )

        self.assertIsNotNone(nav)
        selected, distance, bearing, heading, turn = nav
        self.assertEqual(poi, selected)
        self.assertEqual(111.0, distance)
        self.assertEqual(90.0, bearing)
        self.assertEqual(30.0, heading)
        self.assertEqual(60.0, turn)

    def test_calculate_navigation_info_without_heading_uses_bearing_as_turn(self):
        poi = Poi("Museum", 48.1010, 11.6000, 0, 0, "tourism:museum", '"tourism"="museum"', "Museen")
        nav = calculate_navigation_info(
            poi,
            (48.1000, 11.6000),
            None,
            distance_fn=lambda *_args: 111.0,
            bearing_fn=lambda *_args: 270.0,
        )

        self.assertIsNotNone(nav)
        _selected, _distance, bearing, heading, turn = nav
        self.assertEqual(270.0, bearing)
        self.assertIsNone(heading)
        self.assertEqual(270.0, turn)

    def test_calculate_navigation_info_wraps_turn_across_north(self):
        poi = Poi("Hotel", 48.1010, 11.6000, 0, 0, "tourism:hotel", '"tourism"="hotel"', "Hotels")
        nav = calculate_navigation_info(
            poi,
            (48.1000, 11.6000),
            350.0,
            distance_fn=lambda *_args: 111.0,
            bearing_fn=lambda *_args: 10.0,
        )

        self.assertIsNotNone(nav)
        _selected, _distance, bearing, heading, turn = nav
        self.assertEqual(10.0, bearing)
        self.assertEqual(350.0, heading)
        self.assertEqual(20.0, turn)

    def test_fetch_pois_can_include_cities_in_general_results(self):
        def fake_post(*_args, **_kwargs):
            return FakeResponse(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 48.1,
                            "lon": 11.6,
                            "tags": {"name": "Village Center", "place": "village"},
                        }
                    ]
                }
            )

        pois = fetch_pois(
            48.1,
            11.6,
            5000,
            {},
            {},
            {},
            include_cities=True,
            http_post=fake_post,
            sleep_fn=lambda _seconds: None,
        )
        self.assertEqual(1, len(pois))
        self.assertEqual("Staedte", pois[0].category_label)
        self.assertEqual("place:village", pois[0].category)
        self.assertTrue(is_city_poi(pois[0]))

    def test_fetch_pois_can_limit_drive_mode_to_cities_only(self):
        def fake_post(*_args, **_kwargs):
            return FakeResponse(
                {
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 48.1,
                            "lon": 11.6,
                            "tags": {"name": "Village Center", "place": "village"},
                        },
                        {
                            "type": "node",
                            "id": 2,
                            "lat": 48.1002,
                            "lon": 11.6002,
                            "tags": {"name": "Cafe", "amenity": "cafe"},
                        },
                    ]
                }
            )

        pois = fetch_pois(
            48.1,
            11.6,
            5000,
            {'"place"="city"': True, '"place"="town"': True, '"place"="village"': True, '"amenity"="cafe"': True},
            {
                ("place", "city"): '"place"="city"',
                ("place", "town"): '"place"="town"',
                ("place", "village"): '"place"="village"',
                ("amenity", "cafe"): '"amenity"="cafe"',
            },
            {
                '"place"="city"': "Staedte",
                '"place"="town"': "Staedte",
                '"place"="village"': "Staedte",
                '"amenity"="cafe"': "Cafes",
            },
            city_only=True,
            http_post=fake_post,
            sleep_fn=lambda _seconds: None,
        )
        self.assertEqual(1, len(pois))
        self.assertEqual("Village Center", pois[0].name)
        self.assertTrue(is_city_poi(pois[0]))

    def test_derive_travel_heading_returns_bearing_for_significant_movement(self):
        heading = derive_travel_heading(
            (48.1000, 11.6000),
            (48.1010, 11.6000),
            distance_fn=lambda *_args: 111.0,
            bearing_fn=lambda *_args: 0.0,
        )
        self.assertEqual(0.0, heading)

    def test_derive_travel_heading_ignores_small_gps_jitter(self):
        heading = derive_travel_heading(
            (48.1000, 11.6000),
            (48.1001, 11.6000),
            distance_fn=lambda *_args: 8.0,
            bearing_fn=lambda *_args: 0.0,
        )
        self.assertIsNone(heading)


if __name__ == "__main__":
    unittest.main()
