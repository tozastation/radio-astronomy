#!/usr/bin/env python3
"""
Unit tests for satellite-viewer backend optimizations (caching, pagination, card rendering)
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone
import json
import io

from app import (
    HTML_TEMPLATE,
    PassDataCache,
    render_card_html,
    get_satellite_type,
    parse_pass_id,
    generate_trajectory_data,
    generate_future_passes_data,
    generate_s3_backup_stream,
    get_s3_backup_stats,
)


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
        self.assertIn("openReplayModal", card_html)
        self.assertIn("天球リプレイ", card_html)

    def test_mobile_responsive_template_and_card(self):
        # 1. Viewport & media queries
        self.assertIn("viewport-fit=cover", HTML_TEMPLATE)
        self.assertIn("@media (max-width: 640px)", HTML_TEMPLATE)
        self.assertIn("radar-canvas", HTML_TEMPLATE)
        self.assertIn("header-actions", HTML_TEMPLATE)
        self.assertIn("clipper-controls", HTML_TEMPLATE)

        # 2. Card header responsive classes
        p = {
            "satellite": "ISS (ZARYA)",
            "pass_id": "ISS_20261025_120000",
            "last_modified": "2026-10-25 12:00:00 UTC",
            "files": {},
            "summary": {"status": "completed", "packets_count": 5},
            "packets": None,
        }
        card_html = render_card_html(p)
        self.assertIn("sat-header-left", card_html)
        self.assertIn("sat-header-right", card_html)
        self.assertIn("btn-replay", card_html)

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

    def test_parse_pass_id(self):
        sat, dt = parse_pass_id("METEOR-M2 3_20261025_005344")
        self.assertEqual(sat, "METEOR-M2 3")
        self.assertEqual(dt, datetime(2026, 10, 25, 0, 53, 44, tzinfo=timezone.utc))

        sat2, dt2 = parse_pass_id("20261025_005344")
        self.assertIsNone(sat2)
        self.assertEqual(dt2, datetime(2026, 10, 25, 0, 53, 44, tzinfo=timezone.utc))

    def test_generate_trajectory_data_success(self):
        # パスキャッシュに観測データをセット
        pass_id = "METEOR-M2 3_20261025_005344"
        self.cache._passes[pass_id] = {
            "satellite": "METEOR-M2 3",
            "pass_id": pass_id,
            "last_modified": "2026-10-25 01:00:00 UTC",
            "last_modified_dt": datetime(2026, 10, 25, 1, 0, 0, tzinfo=timezone.utc),
            "files": {
                "spectrogram.png": f"results/METEOR-M2 3/{pass_id}/spectrogram.png",
                "msu_mr_rgb.png": f"results/METEOR-M2 3/{pass_id}/msu_mr_rgb.png",
            },
            "summary": {
                "status": "completed",
                "packets_count": 11,
                "duration_seconds": 406.0,
            },
            "packets": {
                "packets": [{"images": ["msu_mr_rgb.png"]}]
            },
        }

        data = generate_trajectory_data(pass_id, self.cache)
        self.assertIsNotNone(data)
        self.assertEqual(data["pass_id"], pass_id)
        self.assertEqual(data["satellite"], "METEOR-M2 3")
        self.assertEqual(data["total_seconds"], 406)
        self.assertGreater(len(data["points"]), 100)
        p0 = data["points"][0]
        self.assertIn("az", p0)
        self.assertIn("el", p0)
        self.assertIn("in_window", p0)
        self.assertIn("has_image", p0)

        # 画像リストに msu_mr_rgb.png が含まれ、spectrogram.png は除外されること
        image_names = [img["name"] for img in data["images"]]
        self.assertIn("msu_mr_rgb.png", image_names)
        self.assertNotIn("spectrogram.png", image_names)

    def test_pass_data_cache_satellite_history_stats(self):
        # 過去パスデータを模擬
        self.cache._passes = {
            "p1": {
                "satellite": "METEOR-M2 3",
                "pass_id": "METEOR-M2 3_20261025_005344",
                "summary": {"packets_count": 10, "status": "completed"},
                "files": {"spectrogram.png": "...", "msu_mr.png": "..."},
            },
            "p2": {
                "satellite": "METEOR-M2 3",
                "pass_id": "METEOR-M2 3_20261026_010000",
                "summary": {"packets_count": 0, "status": "completed"},
                "files": {"spectrogram.png": "..."},
            },
            "p3": {
                "satellite": "ISS (ZARYA)",
                "pass_id": "ISS_20261025_120000",
                "summary": {"packets_count": 5, "status": "completed"},
                "files": {},
            }
        }
        stats = self.cache.get_satellite_history_stats()
        self.assertIn("METEOR-M2 3", stats)
        self.assertIn("ISS (ZARYA)", stats)

        meteor_stats = stats["METEOR-M2 3"]
        self.assertEqual(meteor_stats["total_passes"], 2)
        self.assertEqual(meteor_stats["successful_passes"], 1)
        self.assertAlmostEqual(meteor_stats["overall_success_rate"], 0.5)

        iss_stats = stats["ISS (ZARYA)"]
        self.assertEqual(iss_stats["total_passes"], 1)
        self.assertEqual(iss_stats["successful_passes"], 1)
        self.assertAlmostEqual(iss_stats["overall_success_rate"], 1.0)

    def test_pass_data_cache_compute_sky_heatmap(self):
        # 復調成功パスを模擬
        self.cache._passes = {
            "p1": {
                "satellite": "METEOR-M2 3",
                "pass_id": "METEOR-M2 3_20261025_005344",
                "summary": {"packets_count": 10, "status": "completed", "duration_seconds": 406.0},
                "files": {"spectrogram.png": "...", "msu_mr.png": "..."},
            }
        }
        heatmap_data = self.cache.compute_sky_heatmap()
        self.assertIn("cells", heatmap_data)
        self.assertIn("horizon_polygon", heatmap_data)
        self.assertIn("total_successful_passes", heatmap_data)
        self.assertEqual(heatmap_data["total_successful_passes"], 1)
        self.assertGreater(len(heatmap_data["cells"]), 0)
        c0 = heatmap_data["cells"][0]
        self.assertIn("az", c0)
        self.assertIn("el", c0)
        self.assertIn("count", c0)

    def test_generate_future_passes_data(self):
        now_dt = datetime(2026, 10, 25, 0, 0, 0, tzinfo=timezone.utc)
        result = generate_future_passes_data(now_dt=now_dt, horizon_hours=24)
        self.assertIn("generated_at", result)
        self.assertIn("passes", result)
        self.assertGreater(len(result["passes"]), 0)

        p = result["passes"][0]
        self.assertIn("satellite", p)
        self.assertIn("aos_time", p)
        self.assertIn("tca_time", p)
        self.assertIn("max_elevation", p)
        self.assertIn("max_elevation_azimuth", p)
        self.assertIn("window_duration_seconds", p)
        self.assertIn("quality_score", p)
        self.assertIn("success_probability", p)
        self.assertIn("history_summary", p)

    def test_static_gifenc_file_exists_and_valid(self):
        import os
        static_path = os.path.join(os.path.dirname(__file__), "static", "gifenc.js")
        self.assertTrue(os.path.isfile(static_path))
        with open(static_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("GIFEncoder", content)
        self.assertIn("quantize", content)
        self.assertIn("applyPalette", content)

    def test_get_s3_backup_stats(self):
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        def fake_paginate(Bucket, Prefix):
            if Prefix == "results/":
                return [{"Contents": [{"Key": "results/p1/summary.json", "Size": 100}]}]
            elif Prefix == "golden/":
                return [{"Contents": [{"Key": "golden/p1/sample.raw", "Size": 500}]}]
            return [{"Contents": []}]

        mock_paginator.paginate.side_effect = fake_paginate

        stats = get_s3_backup_stats(mock_s3, "test-bucket")
        self.assertEqual(stats["total_count"], 2)
        self.assertEqual(stats["total_bytes"], 600)
        self.assertEqual(stats["results_count"], 1)
        self.assertEqual(stats["golden_count"], 1)

    def test_generate_s3_backup_stream_and_tar_integrity(self):
        import tarfile
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        content_sample = b"hello satellite backup stream!"
        def fake_paginate(Bucket, Prefix):
            if Prefix == "results/":
                return [{"Contents": [{"Key": "results/test/sample.txt", "Size": len(content_sample), "LastModified": datetime.now(timezone.utc)}]}]
            return [{"Contents": []}]

        mock_paginator.paginate.side_effect = fake_paginate

        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(content_sample)
        }

        stream_chunks = list(generate_s3_backup_stream(mock_s3, "test-bucket"))
        tar_bytes = b"".join(stream_chunks)

        self.assertGreater(len(tar_bytes), 1024)

        # Verify reading tar archive with standard tarfile
        tar_stream = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_stream, mode="r:") as tf:
            members = tf.getmembers()
            self.assertEqual(len(members), 1)
            self.assertEqual(members[0].name, "results/test/sample.txt")
            self.assertEqual(members[0].size, len(content_sample))
            extracted_data = tf.extractfile(members[0]).read()
            self.assertEqual(extracted_data, content_sample)


if __name__ == "__main__":
    unittest.main()
