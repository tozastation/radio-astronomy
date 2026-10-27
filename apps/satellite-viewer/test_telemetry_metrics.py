#!/usr/bin/env python3
import unittest
from datetime import datetime, timezone
import math

from orbit_calculator import (
    OrbitCalculator,
    compute_fspl_db,
    compute_doppler_shift_hz,
    compute_pass_telemetry_metrics,
    DEFAULT_TARGET_TLES,
)


class TestTelemetryMetrics(unittest.TestCase):
    def test_compute_fspl(self):
        # f = 145.825 MHz, d = 600 km
        # FSPL = 20*log10(600) + 20*log10(145.825) + 32.44
        # 20*log10(600) = 55.563
        # 20*log10(145.825) = 43.277
        # Total = 55.563 + 43.277 + 32.44 = 131.28 dB
        fspl = compute_fspl_db(freq_mhz=145.825, distance_km=600.0)
        self.assertAlmostEqual(fspl, 131.28, delta=0.2)

    def test_compute_doppler_shift(self):
        # v_r = 3000 m/s (接近中 = -3000 m/s 相対速度), f = 145.825 MHz
        # c = 299792458 m/s
        # delta_f = -f0 * (v_r / c) = - 145.825e6 * (-3000 / 299792458) = +1459.25 Hz
        shift = compute_doppler_shift_hz(freq_hz=145.825e6, radial_velocity_m_s=-3000.0)
        self.assertAlmostEqual(shift, 1459.25, delta=1.0)

    def test_compute_pass_telemetry_metrics_iss(self):
        tle = DEFAULT_TARGET_TLES["ISS (ZARYA)"]
        calc = OrbitCalculator()
        # 2026-10-20 00:50:00 UTC は東京上空を通過する実際のパス (Max El ~50°)
        start_time = datetime(2026, 10, 20, 0, 50, 0, tzinfo=timezone.utc)
        points = calc.calculate_trajectory(
            tle_line1=tle[0],
            tle_line2=tle[1],
            start_time=start_time,
            duration_seconds=600,
            step_seconds=1,
        )

        metrics = compute_pass_telemetry_metrics(
            satellite_name="ISS (ZARYA)",
            points=points,
            freq_mhz=145.825,
            norad_id=25544,
            inclination_deg=51.64,
        )

        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["norad_id"], 25544)
        self.assertIn("tca_time_iso", metrics)
        self.assertIn("max_elevation", metrics)
        self.assertIn("min_range_km", metrics)
        self.assertIn("min_fspl_db", metrics)
        self.assertIn("max_fspl_db", metrics)
        self.assertIn("delta_fspl_db", metrics)
        self.assertIn("max_doppler_hz", metrics)
        self.assertIn("tca_df_dt_hz_s", metrics)
        self.assertIn("window_duration_seconds", metrics)

        # 物理量の妥当性検証
        self.assertGreater(metrics["min_fspl_db"], 120.0)
        self.assertLess(metrics["min_fspl_db"], 160.0)
        self.assertGreater(metrics["max_doppler_hz"], 1000.0)
        self.assertLess(metrics["max_doppler_hz"], 6000.0)

    def test_render_card_html_with_telemetry_and_tooltips(self):
        from app import render_card_html

        sample_pass = {
            "satellite": "ISS (ZARYA)",
            "pass_id": "20261020_005000_ISS",
            "last_modified": "2026-10-20 00:55:00 UTC",
            "last_modified_dt": datetime(2026, 10, 20, 0, 50, 0, tzinfo=timezone.utc),
            "files": {
                "spectrogram.png": "results/ISS/20261020_005000_ISS/spectrogram.png",
                "summary.json": "results/ISS/20261020_005000_ISS/summary.json",
            },
            "summary": {
                "status": "completed",
                "packets_count": 2,
                "duration_seconds": 600,
            },
            "packets": {
                "packets": [
                    {
                        "from": "RS0ISS",
                        "to": "CQ",
                        "route": ["ARISS"],
                        "comment": "=3541.20N/13941.50E-Hello Tokyo Ground Station!",
                    }
                ]
            },
        }

        html_out = render_card_html(sample_pass)
        self.assertIn("telemetry-panel", html_out)
        self.assertIn("geek-tooltip", html_out)
        self.assertIn("FSPL (伝搬損失)", html_out)
        self.assertIn("DOPPLER (理論偏移)", html_out)
        self.assertIn("packet-preview-container", html_out)
        self.assertIn("RS0ISS", html_out)
        self.assertIn("Hello Tokyo Ground Station!", html_out)

    def test_render_card_html_fallback_unknown_satellite(self):
        from app import render_card_html

        sample_pass = {
            "satellite": "UNKNOWN-SAT-99",
            "pass_id": "20261020_120000_UNKNOWN",
            "last_modified": "2026-10-20 12:00:00 UTC",
            "files": {},
            "summary": {"status": "completed", "packets_count": 0},
            "packets": None,
        }

        html_out = render_card_html(sample_pass)
        self.assertIn("telemetry-panel", html_out)
        self.assertIn("geek-tooltip", html_out)
        self.assertNotIn("packet-preview-container", html_out)


if __name__ == "__main__":
    unittest.main()
