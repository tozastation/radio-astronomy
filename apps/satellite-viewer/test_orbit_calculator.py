#!/usr/bin/env python3
"""
Unit tests for SGP4 Orbit Calculator and Balcony Sky Window determination.
"""

import unittest
from datetime import datetime, timezone

from orbit_calculator import OrbitCalculator, TrajectoryPoint


class TestOrbitCalculator(unittest.TestCase):
    def setUp(self):
        # 東京代表座標: 北緯 35.6895, 東経 139.6917, 標高 30.0m
        self.calc = OrbitCalculator(
            lat_deg=35.6895,
            lon_deg=139.6917,
            alt_m=30.0
        )
        self.meteor_tle_l1 = "1 57166U 23091A   26296.25648911 -.00000011  00000+0  13974-4 0  9991"
        self.meteor_tle_l2 = "2 57166  98.5934 348.5131 0004559 145.3247 214.8230 14.24054780172745"

    def test_is_in_sky_window(self):
        """北向きベランダのスカイウィンドウ（方位 250°〜110°、仰角 25°〜60°）判定の検証"""
        # 開口部中央（北 $35^\circ$、仰角 $45^\circ$）
        self.assertTrue(self.calc.is_in_sky_window(35.0, 45.0))

        # 西側境界（方位 $260^\circ$、仰角 $30^\circ$）
        self.assertTrue(self.calc.is_in_sky_window(260.0, 30.0))

        # 東側境界（方位 $105^\circ$、仰角 $55^\circ$）
        self.assertTrue(self.calc.is_in_sky_window(105.0, 55.0))

        # 南側マンション躯体遮蔽（方位 $180^\circ$、仰角 $45^\circ$）
        self.assertFalse(self.calc.is_in_sky_window(180.0, 45.0))

        # 南西遮蔽（方位 $220^\circ$、仰角 $40^\circ$）
        self.assertFalse(self.calc.is_in_sky_window(220.0, 40.0))

        # 上階スラブ庇遮蔽（仰角 $75^\circ > 60^\circ$）
        self.assertFalse(self.calc.is_in_sky_window(0.0, 75.0))

        # ベランダ手すり下限遮蔽（仰角 $15^\circ < 25^\circ$）
        self.assertFalse(self.calc.is_in_sky_window(0.0, 15.0))

    def test_calculate_trajectory_reaches_expected_tca(self):
        """2026-10-25 00:53:44 UTC の METEOR-M2 3 パスで期待される TCA 諸元を検証"""
        start_utc = datetime(2026, 10, 25, 0, 53, 44, tzinfo=timezone.utc)
        duration_sec = 406

        points = self.calc.calculate_trajectory(
            tle_line1=self.meteor_tle_l1,
            tle_line2=self.meteor_tle_l2,
            start_time=start_utc,
            duration_seconds=duration_sec
        )

        self.assertEqual(len(points), duration_sec + 1)
        self.assertIsInstance(points[0], TrajectoryPoint)

        # 最大仰角（TCA）の探索
        tca_point = max(points, key=lambda p: p.el)

        # 00:53:44 AOS から約 4 分半後 (t=260〜300秒) に TCA (仰角約 33°、方位角約 291° 西北西) に達すること
        self.assertGreaterEqual(tca_point.t, 260)
        self.assertLessEqual(tca_point.t, 300)
        self.assertGreaterEqual(tca_point.el, 30.0)
        self.assertLessEqual(tca_point.el, 36.0)
        self.assertGreaterEqual(tca_point.az, 280.0)
        self.assertLessEqual(tca_point.az, 305.0)
        self.assertTrue(tca_point.in_window)

        # AOS 時点 (t=0) は仰角 10.1° のため手すり下限 (25°) 未満で in_window が False
        self.assertFalse(points[0].in_window)
        self.assertLess(points[0].el, 15.0)


if __name__ == "__main__":
    unittest.main()
