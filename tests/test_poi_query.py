import unittest

import travelbook


class TestPoiQuery(unittest.TestCase):
    def test_query_includes_all_poi_categories(self):
        query = travelbook.build_overpass_query(48.1, 11.6, 1200)
        for _label, osm_filter, _enabled in travelbook.POI_OPTIONS:
            expected = f"nwr[{osm_filter}](around:1200,48.1,11.6);"
            self.assertIn(expected, query)

    def test_query_uses_radius_argument(self):
        query_small = travelbook.build_overpass_query(50.0, 8.0, 500)
        query_large = travelbook.build_overpass_query(50.0, 8.0, 1800)
        self.assertIn("around:500,50.0,8.0", query_small)
        self.assertIn("around:1800,50.0,8.0", query_large)
        self.assertNotEqual(query_small, query_large)

    def test_query_respects_selected_categories_subset(self):
        subset = ['"amenity"="cafe"', '"tourism"="hotel"']
        query = travelbook.build_overpass_query(51.0, 9.0, 900, subset)
        self.assertIn('nwr["amenity"="cafe"](around:900,51.0,9.0);', query)
        self.assertIn('nwr["tourism"="hotel"](around:900,51.0,9.0);', query)
        self.assertNotIn('nwr["shop"="supermarket"](around:900,51.0,9.0);', query)

    def test_query_can_append_extra_filters_for_drive_mode(self):
        query = travelbook.build_overpass_query(
            52.0,
            13.0,
            5000,
            ['"amenity"="cafe"'],
            extra_filters=['"place"="city"'],
        )
        self.assertIn('nwr["amenity"="cafe"](around:5000,52.0,13.0);', query)
        self.assertIn('nwr["place"="city"](around:5000,52.0,13.0);', query)


if __name__ == "__main__":
    unittest.main()
