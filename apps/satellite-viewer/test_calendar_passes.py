import unittest
from datetime import datetime, timezone
import sys
from pathlib import Path

# Add satellite-viewer to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from orbit_calculator import OrbitCalculator, DEFAULT_TARGET_TLES


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


if __name__ == "__main__":
    unittest.main()
