import unittest

from travelbook_providers import is_valid_gps_fix


class TestProviders(unittest.TestCase):
    def test_accepts_precise_gps_fix(self):
        self.assertTrue(is_valid_gps_fix("GPS", 12.0))

    def test_rejects_network_based_fix(self):
        self.assertFalse(is_valid_gps_fix("WiFi location", 40.0))

    def test_rejects_imprecise_fix_even_with_gps_label(self):
        self.assertFalse(is_valid_gps_fix("GPS", 250.0))

    def test_accepts_precise_fix_without_source_description(self):
        self.assertTrue(is_valid_gps_fix("", 25.0))


if __name__ == "__main__":
    unittest.main()
