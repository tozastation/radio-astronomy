#!/usr/bin/env python3
"""
Unit tests for satellite-viewer backend optimizations (caching, pagination, card rendering)
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone
import json
import io

from app import PassDataCache, render_card_html, get_satellite_type


class TestViewerOptimizations(unittest.TestCase):
    def setUp(self):
        self.mock_s3 = MagicMock()
        self.cache = PassDataCache(s3_client=self.mock_s3, s3_bucket="test-bucket")

    def test_get_satellite_type(self):
        p_iss = {"satellite": "ISS (ZARYA)", "summary": {}}
        self.assertEqual(get_satellite_type(p_iss), "SpaceStation")

        p_meteor = {"satellite": "METEOR-M2 4", "summary": {}}
        self.assertEqual(get_satellite_type(p_meteor), "WeatherSatellite")

        p_cube = {"satellite": "FUNCUBE-1 (AO-73)", "summary": {}}
        self.assertEqual(get_satellite_type(p_cube), "CubeSat")

        p_custom = {"satellite": "MY-SAT", "summary": {"satellite_type": "CustomType"}}
        self.assertEqual(get_satellite_type(p_custom), "CustomType")

    def test_render_card_html_has_lazy_loading(self):
        p = {
            "satellite": "METEOR-M2 4",
            "pass_id": "pass_12345",
            "last_modified": "2026-10-22 10:00:00 UTC",
            "files": {
                "spectrogram.png": "results/METEOR-M2 4/pass_12345/spectrogram.png",
                "msu_mr_rgb.png": "results/METEOR-M2 4/pass_12345/msu_mr_rgb.png",
            },
            "summary": {
                "status": "completed",
                "packets_count": 0,
                "summary_text": "LRPT decoded",
            },
            "packets": None,
        }
        card_html = render_card_html(p)
        self.assertIn('loading="lazy"', card_html)
        self.assertIn("pass_12345", card_html)
        self.assertIn("WeatherSatellite", card_html)
        self.assertIn("spectrogram.png", card_html)

    def test_pass_data_cache_avoids_repeated_s3_calls(self):
        # 1回目の list_objects_v2 レスポンス
        paginator = MagicMock()
        self.mock_s3.get_paginator.return_value = paginator
        now_dt = datetime.now(timezone.utc)
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "results/ISS (ZARYA)/pass_1/summary.json",
                        "LastModified": now_dt,
                    },
                    {
                        "Key": "results/ISS (ZARYA)/pass_1/packets.json",
                        "LastModified": now_dt,
                    },
                ]
            }
        ]

        def fake_get_object(Bucket, Key):
            if "summary.json" in Key:
                return {"Body": io.BytesIO(b'{"satellite_type":"SpaceStation","packets_count":1}')}
            elif "packets.json" in Key:
                return {"Body": io.BytesIO(b'{"packets":[]}')}
            raise FileNotFoundError(Key)

        self.mock_s3.get_object.side_effect = fake_get_object

        # 初回ロード
        passes_1 = self.cache.get_all_passes()
        self.assertEqual(len(passes_1), 1)
        self.assertEqual(self.mock_s3.get_object.call_count, 2)

        # 2回目ロード (S3の内容に変更がない場合、get_object は呼ばれずキャッシュを再利用)
        self.cache.last_sync_time = 0  # 強制チェック
        passes_2 = self.cache.get_all_passes()
        self.assertEqual(len(passes_2), 1)
        # get_object call_count が2のまま増えていないこと（キャッシュヒット）
        self.assertEqual(self.mock_s3.get_object.call_count, 2)

    def test_cache_sorts_descending_by_date(self):
        paginator = MagicMock()
        self.mock_s3.get_paginator.return_value = paginator
        dt_old = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
        dt_new = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)

        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "results/ISS (ZARYA)/pass_old/spectrogram.png", "LastModified": dt_old},
                    {"Key": "results/METEOR-M2 4/pass_new/spectrogram.png", "LastModified": dt_new},
                ]
            }
        ]
        self.cache.last_sync_time = 0
        passes = self.cache.get_all_passes()
        self.assertEqual(len(passes), 2)
        self.assertEqual(passes[0]["pass_id"], "pass_new")
        self.assertEqual(passes[1]["pass_id"], "pass_old")


if __name__ == "__main__":
    unittest.main()
