import unittest
from datetime import datetime, timezone
import sys
from pathlib import Path

# Add satellite-viewer to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from orbit_calculator import OrbitCalculator, DEFAULT_TARGET_TLES
from app import get_month_passes_cached, get_satellites_tle_data, HTML_TEMPLATE


class TestCalendarPasses(unittest.TestCase):
    def setUp(self):
        self.calc = OrbitCalculator()

    def test_find_month_passes_october_2026(self):
        targets = [("METEOR-M2 3", DEFAULT_TARGET_TLES["METEOR-M2 3"][0], DEFAULT_TARGET_TLES["METEOR-M2 3"][1])]
        start_t = datetime.now()
        data = self.calc.find_month_passes(year=2026, month=10, targets=targets)
        elapsed = (datetime.now() - start_t).total_seconds()

        self.assertLess(elapsed, 2.0, "Month pass generation must complete within 2.0 seconds")
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["month"], 10)
        self.assertIn("days", data)
        self.assertIn("2026-10-28", data["days"])
        self.assertGreater(len(data["days"]["2026-10-28"]), 0)
        p = data["days"]["2026-10-28"][0]
        self.assertIn("aos_time", p)
        self.assertIn("max_elevation", p)
        self.assertIn("quality_score", p)

    def test_find_month_passes_leap_year_february(self):
        targets = [("ISS (ZARYA)", DEFAULT_TARGET_TLES["ISS (ZARYA)"][0], DEFAULT_TARGET_TLES["ISS (ZARYA)"][1])]
        # 2024 is a leap year (29 days)
        data_leap = self.calc.find_month_passes(year=2024, month=2, targets=targets)
        self.assertEqual(len(data_leap["days"]), 29)
        self.assertIn("2024-02-29", data_leap["days"])

        # 2025 is a non-leap year (28 days)
        data_normal = self.calc.find_month_passes(year=2025, month=2, targets=targets)
        self.assertEqual(len(data_normal["days"]), 28)
        self.assertIn("2025-02-28", data_normal["days"])
        self.assertNotIn("2025-02-29", data_normal["days"])

    def test_sky_window_azimuth_wraparound_and_boundaries(self):
        # Azimuth across North (0 deg): 250 -> 360 -> 110
        self.assertTrue(self.calc.is_in_sky_window(355.0, 45.0))
        self.assertTrue(self.calc.is_in_sky_window(5.0, 45.0))
        self.assertTrue(self.calc.is_in_sky_window(260.0, 30.0))
        self.assertTrue(self.calc.is_in_sky_window(100.0, 50.0))

        # South side (Blocked by building: 110 -> 250)
        self.assertFalse(self.calc.is_in_sky_window(180.0, 45.0))
        self.assertFalse(self.calc.is_in_sky_window(200.0, 55.0))

        # Elevation boundaries (25 - 60 deg)
        self.assertFalse(self.calc.is_in_sky_window(0.0, 15.0))  # too low
        self.assertFalse(self.calc.is_in_sky_window(0.0, 75.0))  # too high (ceiling slab cutoff)


class TestCalendarAPI(unittest.TestCase):
    def test_get_satellites_tle_data(self):
        data = get_satellites_tle_data()
        self.assertIn("satellites", data)
        self.assertGreaterEqual(len(data["satellites"]), 4)
        sat_names = [s["name"] for s in data["satellites"]]
        self.assertIn("METEOR-M2 3", sat_names)
        self.assertIn("tle1", data["satellites"][0])
        self.assertIn("tle2", data["satellites"][0])
        self.assertIn("color", data["satellites"][0])

    def test_get_month_passes_cached(self):
        data1 = get_month_passes_cached(2026, 10)
        self.assertIn("days", data1)
        # 2回目はキャッシュから瞬時に取得
        start_t = datetime.now()
        data2 = get_month_passes_cached(2026, 10)
        self.assertLess((datetime.now() - start_t).total_seconds(), 0.02)
        self.assertEqual(data1["days"], data2["days"])


class TestCalendarUI(unittest.TestCase):
    def test_template_contains_calendar_elements(self):
        self.assertIn('id="orbitCalendarModal"', HTML_TEMPLATE)
        self.assertIn('id="calendarGrid"', HTML_TEMPLATE)
        self.assertIn('id="calendarDayDetails"', HTML_TEMPLATE)
        self.assertIn('renderMonthlyCalendar', HTML_TEMPLATE)


class TestWorldMapCanvas(unittest.TestCase):
    def test_template_contains_world_canvas(self):
        self.assertIn('id="orbitWorldCanvas"', HTML_TEMPLATE)
        self.assertIn('class WorldMapRenderer', HTML_TEMPLATE)
        self.assertIn('drawTerminator', HTML_TEMPLATE)


class TestRadioPhysicsAnimation(unittest.TestCase):
    def test_template_contains_radio_physics_logic(self):
        self.assertIn('class MultiSatellitePropagator', HTML_TEMPLATE)
        self.assertIn('drawWavefrontRipples', HTML_TEMPLATE)
        self.assertIn('drawSlantRangeBeam', HTML_TEMPLATE)
        self.assertIn('drawLockedCapturePulse', HTML_TEMPLATE)


class TestTimeController(unittest.TestCase):
    def test_template_contains_time_controller(self):
        self.assertIn('id="orbitTimeSlider"', HTML_TEMPLATE)
        self.assertIn('id="orbitSpeedSelect"', HTML_TEMPLATE)
        self.assertIn('id="btnPlayPause"', HTML_TEMPLATE)
        self.assertIn('id="btnNextPass"', HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
