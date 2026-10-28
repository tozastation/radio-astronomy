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
    is_demod_successful,
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

    def test_mobile_responsive_optimizations(self):
        # 1. Safe-area-insets for modern notch/hole-punch devices (Xiaomi 15 Ultra, Z Flip 7)
        self.assertIn("env(safe-area-inset-left", HTML_TEMPLATE)
        self.assertIn("env(safe-area-inset-right", HTML_TEMPLATE)
        self.assertIn("env(safe-area-inset-top", HTML_TEMPLATE)
        self.assertIn("env(safe-area-inset-bottom", HTML_TEMPLATE)

        # 2. Header actions responsive labels & spacious mobile 2-row layout
        self.assertIn("btn-label-long", HTML_TEMPLATE)
        self.assertIn("btn-label-short", HTML_TEMPLATE)
        self.assertIn("📦 バックアップ", HTML_TEMPLATE)
        self.assertIn("📅 通過予測", HTML_TEMPLATE)
        self.assertIn("🔄 最新取得", HTML_TEMPLATE)
        self.assertIn("grid-column: span 2", HTML_TEMPLATE)
        self.assertIn("modal-title-long", HTML_TEMPLATE)
        self.assertIn("modal-title-short", HTML_TEMPLATE)

        # 3. Filter chips mobile scrollable or wrap container
        self.assertIn("filter-chips-mobile", HTML_TEMPLATE)
        self.assertIn("-webkit-overflow-scrolling: touch", HTML_TEMPLATE)

        # 4. Telemetry grid 1-column layout on mobile without class collision
        self.assertIn(".telemetry-panel .telemetry-grid", HTML_TEMPLATE)
        self.assertIn(".replay-telemetry-grid", HTML_TEMPLATE)
        self.assertIn("width: 100% !important", HTML_TEMPLATE)  # Tooltip safe boundary

        # 5. File row responsive stacking for mobile
        self.assertIn(".file-row", HTML_TEMPLATE)
        self.assertIn("file-row-mobile", HTML_TEMPLATE)
        self.assertIn("overflow-wrap: anywhere", HTML_TEMPLATE)

        # 6. Card rendering output has all required responsive classes and collapsible files accordion
        p = {
            "satellite": "METEOR-M2 3",
            "pass_id": "METEOR-M2 3_20261025_005344",
            "last_modified": "2026-10-25 01:00:00 UTC",
            "files": {
                "spectrogram.png": "results/METEOR-M2 3/pass/spectrogram.png",
                "summary.json": "results/METEOR-M2 3/pass/summary.json",
                "audio.wav": "results/METEOR-M2 3/pass/audio.wav",
                "METEOR-M2 3_20261025_005344_msu_mr_rgb_color_corrected_highres.png": "results/METEOR-M2 3/pass/long.png",
            },
            "summary": {
                "status": "completed",
                "packets_count": 12,
                "max_elevation": 45.2,
            },
            "packets": None,
        }
        card_html = render_card_html(p)
        self.assertIn("files-accordion", card_html)
        self.assertIn("file-row", card_html)
        self.assertIn("file-name", card_html)
        self.assertIn("file-actions", card_html)
        self.assertIn("btn-replay", card_html)
        self.assertIn("btn-grafana", card_html)
        self.assertIn("badge-status", card_html)
        self.assertIn("telemetry-grid", card_html)
        self.assertIn("telemetry-item", card_html)

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

    def test_pass_data_cache_compute_satellite_time_drifts(self):
        self.cache._passes = {
            "p1": {
                "satellite": "METEOR-M2 3",
                "pass_id": "METEOR-M2 3_20261025_005344",
                "summary": {"packets_count": 10, "status": "completed", "actual_tca_drift_seconds": 3.5},
                "files": {"spectrogram.png": "..."},
            }
        }
        drifts = self.cache.compute_satellite_time_drifts()
        self.assertIn("METEOR-M2 3", drifts)
        self.assertEqual(drifts["METEOR-M2 3"], 3.5)

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
        self.assertIn("time_drift_seconds", p)

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

    def test_is_demod_successful(self):
        # 1. WeatherSatellite with images -> True
        p_meteor_success = {
            "satellite": "METEOR-M2 3",
            "summary": {"status": "completed", "packets_count": 0},
            "files": {"spectrogram.png": "path/spec.png", "MSU-MR-1.png": "path/img.png"}
        }
        self.assertTrue(is_demod_successful(p_meteor_success))

        # 2. WeatherSatellite without images (only spectrogram) -> False
        p_meteor_fail = {
            "satellite": "METEOR-M2 3",
            "summary": {"status": "completed", "packets_count": 0, "summary_text": "信号微弱"},
            "files": {"spectrogram.png": "path/spec.png"}
        }
        self.assertFalse(is_demod_successful(p_meteor_fail))

        # 3. CubeSat with packets -> True
        p_cubesat_success = {
            "satellite": "FUNCUBE-1 (AO-73)",
            "summary": {"status": "completed", "packets_count": 3},
            "files": {"spectrogram.png": "path/spec.png"}
        }
        self.assertTrue(is_demod_successful(p_cubesat_success))

        # 4. CubeSat with 0 packets -> False
        p_cubesat_fail = {
            "satellite": "FUNCUBE-1 (AO-73)",
            "summary": {"status": "completed", "packets_count": 0},
            "files": {"spectrogram.png": "path/spec.png"}
        }
        self.assertFalse(is_demod_successful(p_cubesat_fail))

        # 5. Card html data-has-data reflects is_demod_successful
        card_html_success = render_card_html(p_meteor_success)
        self.assertIn('data-has-data="true"', card_html_success)

        card_html_fail = render_card_html(p_meteor_fail)
        self.assertIn('data-has-data="false"', card_html_fail)

    def test_html_template_has_demod_filter_button(self):
        self.assertIn('id="btnDemodOnly"', HTML_TEMPLATE)
        self.assertIn('id="count-demod-success"', HTML_TEMPLATE)


class TestPerformanceOptimizations(unittest.TestCase):
    def test_pass_cache_parallel_fetching(self):
        mock_s3 = MagicMock()
        cache = PassDataCache(s3_client=mock_s3, s3_bucket="test-bucket")

        # Mock list_objects_v2 with 3 passes
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{
            "Contents": [
                {"Key": "results/ISS (ZARYA)/ISS_01/summary.json", "LastModified": datetime.now(timezone.utc)},
                {"Key": "results/ISS (ZARYA)/ISS_01/packets.json", "LastModified": datetime.now(timezone.utc)},
                {"Key": "results/METEOR-M2 4/METEOR_02/summary.json", "LastModified": datetime.now(timezone.utc)},
            ]
        }]

        def mock_get_object(Bucket, Key):
            if "summary.json" in Key:
                return {"Body": io.BytesIO(b'{"status":"completed","packets_count":1}')}
            elif "packets.json" in Key:
                return {"Body": io.BytesIO(b'{"packets":[{"id":1}]}')}
            raise ValueError(f"Unknown key {Key}")

        mock_s3.get_object.side_effect = mock_get_object

        passes = cache.get_all_passes()
        self.assertEqual(len(passes), 2)
        iss_pass = next(p for p in passes if p["pass_id"] == "ISS_01")
        self.assertIsNotNone(iss_pass["summary"])
        self.assertEqual(iss_pass["summary"]["status"], "completed")
        self.assertIsNotNone(iss_pass["packets"])
        self.assertEqual(len(iss_pass["packets"]["packets"]), 1)

    def test_gzip_compression_helper(self):
        import gzip
        from app import compress_body_if_supported

        large_body = b"hello " * 500  # 3000 bytes > 1024
        compressed, is_gzip = compress_body_if_supported("gzip, deflate", large_body)
        self.assertTrue(is_gzip)
        self.assertLess(len(compressed), len(large_body))
        self.assertEqual(gzip.decompress(compressed), large_body)

        # Without gzip in accept-encoding
        uncompressed, is_gzip2 = compress_body_if_supported("identity", large_body)
        self.assertFalse(is_gzip2)
        self.assertEqual(uncompressed, large_body)

    def test_prewarm_cache_logic(self):
        from app import warm_cache_background
        # Should execute safely without uncaught exception
        try:
            warm_cache_background()
        except Exception as e:
            self.fail(f"warm_cache_background raised unexpected exception: {e}")

    def test_galactic_pointing_and_empty_s3_fallback(self):
        from orbit_calculator import compute_galactic_pointing, compute_galactic_24h_drift_curve
        from app import fetch_galactic_observations

        dt = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
        pt = compute_galactic_pointing(35.6895, 139.6917, 30.0, 35.0, dt)
        self.assertTrue(0.0 <= pt["gal_l_deg"] < 360.0)
        self.assertTrue(-90.0 <= pt["gal_b_deg"] <= 90.0)
        self.assertIn("region_label", pt)
        self.assertIn("lsr_correction_km_s", pt)

        curve = compute_galactic_24h_drift_curve(35.6895, 139.6917, 30.0, 35.0, dt, step_minutes=30)
        self.assertEqual(len(curve), 49)

        # S3 に galactic/ データが0件のときでも理論ポインティングと予測スペクトル・24hカーブが返ること
        mock_s3 = MagicMock()
        mock_s3.list_objects_v2.return_value = {"Contents": []}
        obs = fetch_galactic_observations(mock_s3, "test-bucket", now_dt=dt)
        self.assertEqual(obs["scan_count"], 0)
        self.assertIn("current_pointing", obs)
        self.assertIn("drift_curve_24h", obs)
        self.assertIn("latest_spectrum", obs)

    def test_galactic_monitor_ui_and_js_syntax(self):
        import subprocess
        import tempfile
        import re

        self.assertIn("openGalacticModal()", HTML_TEMPLATE)
        self.assertIn('id="galacticModal"', HTML_TEMPLATE)
        self.assertIn('id="galacticInsightCard"', HTML_TEMPLATE)
        self.assertIn('id="galacticSpectrumCanvas"', HTML_TEMPLATE)
        self.assertIn('id="galacticTopDownCanvas"', HTML_TEMPLATE)
        self.assertIn('id="galacticDarkMatterPanel"', HTML_TEMPLATE)
        self.assertIn('id="galacticDriftCanvas"', HTML_TEMPLATE)
        self.assertIn("/api/galactic-observations", HTML_TEMPLATE)
        self.assertIn("galacticRenderTopDownMap", HTML_TEMPLATE)
        self.assertIn("galacticUpdateInsights", HTML_TEMPLATE)

        # node -c によるテンプレート内全 JavaScript 構文解析
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", HTML_TEMPLATE, flags=re.DOTALL)
        self.assertTrue(len(scripts) > 0)
        combined_js = "\n".join(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(combined_js)
            js_path = f.name
        res = subprocess.run(["node", "-c", js_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JS syntax error: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
