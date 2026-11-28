from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

ANALYTICS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANALYTICS_DIR.parent
ONTOLOGY_DIR = ANALYTICS_DIR / "ontology"
SCRIPTS_DIR = ANALYTICS_DIR / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_s3_to_duckdb as sync_mod  # noqa: E402


class FakeS3Client:
    """テスト用のインメモリ S3 クライアントモック。"""

    def __init__(self, objects: list[dict], payloads: dict[str, dict]) -> None:
        self.objects = objects
        self.payloads = payloads
        self.get_calls: list[str] = []

    def list_objects(self, prefix: str) -> list[dict]:
        return [o for o in self.objects if o["key"].startswith(prefix)]

    def get_json(self, key: str) -> dict:
        self.get_calls.append(key)
        return self.payloads[key]


def test_ontology_yamls_valid_and_required_keys() -> None:
    entities = yaml.safe_load((ONTOLOGY_DIR / "entities.yaml").read_text(encoding="utf-8"))
    assert entities["ground_stations"][0]["station_id"] == "tokyo-north-balcony"
    sat_ids = {s["canonical_id"] for s in entities["satellites"]}
    assert {"METEOR-M2-3", "METEOR-M2-4", "FUNCUBE-1", "ISS"}.issubset(sat_ids)
    assert {e["id"] for e in entities["epistemic_statuses"]} == {
        "Observed",
        "PartiallyRecovered",
        "ModelEstimated",
    }

    models = yaml.safe_load((ONTOLOGY_DIR / "models.yaml").read_text(encoding="utf-8"))
    hashes = {m["config_hash"] for m in models["model_versions"]}
    assert {"46546fee33518457", "a84ae13587fa7d7a"}.issubset(hashes)

    metrics = yaml.safe_load((ONTOLOGY_DIR / "metrics.yaml").read_text(encoding="utf-8"))
    sem_names = {m["name"] for m in metrics["semantic_models"]}
    assert sem_names == {
        "sem_satellite_qor",
        "sem_galactic_snr",
        "sem_inpaint_quality",
        "sem_revision_impact",
    }

    rules = yaml.safe_load((ONTOLOGY_DIR / "rules.yaml").read_text(encoding="utf-8"))
    assert rules["pass_diagnosis_thresholds"]["high_snr_db"] == 8.0
    sig_ids = {s["signal_id"] for s in rules["tuning_signals"]}
    assert {
        "RF_DECODER_TUNING",
        "TLE_OR_CLOCK_DRIFT",
        "REVISION_REGRESSION",
        "ML_MASK_DISTRIBUTION_SHIFT",
        "GALACTIC_NOISE_FLOOR_SPIKE",
    } == sig_ids


def test_is_decoded_image_relpath_excludes_spectrogram_and_inpainted() -> None:
    assert sync_mod.is_decoded_image_relpath("MSU-MR-1.png") is True
    assert sync_mod.is_decoded_image_relpath("avhrr_221.jpg") is True
    assert sync_mod.is_decoded_image_relpath("spectrogram.png") is False
    assert sync_mod.is_decoded_image_relpath("ml_inpainted/avhrr_221_inpainted.png") is False
    assert sync_mod.is_decoded_image_relpath("summary.json") is False


def test_sync_all_incremental_and_schema(tmp_path: Path) -> None:
    t0 = datetime(2026, 11, 27, 1, 0, 0, tzinfo=timezone.utc)
    objects = [
        {"key": "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/summary.json", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/spectrogram.png", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/MSU-MR-1.png", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/ml_inpainted/avhrr_221_inpainted.png", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/ml_inpainted/metadata.json", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 4/METEOR-M2_GOLDEN_1791698618/summary.json", "last_modified": t0, "size": 100},
        {"key": "results/METEOR-M2 4/METEOR-M2_GOLDEN_1791698618/ml_inpainted/avhrr_221_inpainted.png", "last_modified": t0, "size": 100},
        {"key": "galactic/2026-11-27/scan_20261127_231828.json", "last_modified": t0, "size": 100},
    ]
    payloads = {
        "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/summary.json": {
            "satellite": "METEOR-M2 3",
            "pass_id": "METEOR-M2 3_20261127_003000",
            "satellite_type": "WeatherSatellite",
            "signal_type": "LRPT (QPSK 72kbps)",
            "frequency_label": "137.900 MHz",
            "packets_count": 3,
            "snr_db": 12.4,
            "actual_tca_drift_seconds": -2.1,
            "duration_seconds": 540.0,
            "summary_text": "ok",
            "status": "completed",
        },
        "results/METEOR-M2 3/METEOR-M2 3_20261127_003000/ml_inpainted/metadata.json": {
            "schema_version": 1,
            "pass_id": "METEOR-M2 3_20261127_003000",
            "satellite": "METEOR-M2 3",
            "generated_at_utc": "2026-11-27T02:00:00Z",
            "device": "mps",
            "model": {
                "name": "PrevImageGuidedResSEUNet",
                "mode": "temporal_prev_11ch",
                "quality": "standard",
                "bundle_file": "nb11_temporal_prev_bundle.pt",
                "config_hash": "a84ae13587fa7d7a",
                "base_bundle_hash": "46546fee33518457",
            },
            "input_stats": {
                "height": 360,
                "width": 1568,
                "available_channels": "124",
                "all_valid_pct": 40.0,
                "partial_valid_pct": 10.0,
                "missing_pct": 50.0,
                "any_valid_pct": 50.0,
                "max_gap_px": 96,
            },
            "registration": {
                "mode": "clear_orbital_tps",
                "clear_agree_pct": 94.2,
                "used_prev_atlas": True,
                "prev_source_passes": ["p1", "p2"],
            },
            "quality": {
                "tier": "Tier B",
                "tier_label": "test",
                "seam_jump_l1": 0.0214,
                "processing_sec": 28.5,
            },
        },
        "results/METEOR-M2 4/METEOR-M2_GOLDEN_1791698618/summary.json": {
            "satellite": "METEOR-M2 4",
            "pass_id": "METEOR-M2_GOLDEN_1791698618",
            "packets_count": 0,
            "status": "completed",
        },
        "galactic/2026-11-27/scan_20261127_231828.json": {
            "timestamp_utc": "2026-11-27T23:18:28+00:00",
            "integration_sec": 264.0,
            "preempted": True,
            "pointing": {
                "az_deg": 30.0,
                "el_deg": 35.0,
                "sky_type": "INTERMEDIATE",
                "gal_l_deg": 97.37,
                "gal_b_deg": 20.15,
            },
            "lsr_correction_km_s": 8.59,
            "rf_metrics": {
                "continuum_power_db": 16.8,
                "peak_line_snr_db": 1.78,
                "peak_v_lsr_km_s": 136.49,
            },
        },
    }

    fake = FakeS3Client(objects, payloads)
    db_path = tmp_path / "obs.duckdb"

    res1 = sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, fake)
    assert res1["s3"]["passes_synced"] == 2
    assert res1["s3"]["inpaint_synced"] == 1
    assert res1["s3"]["galactic_synced"] == 1
    assert res1["git_revisions"] > 0
    assert res1["ref"]["satellites"] == 4

    # 2 回目は増分キャッシュにより S3 からの再取得は 0 件
    fake.get_calls.clear()
    res2 = sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, fake)
    assert res2["s3"]["passes_synced"] == 0
    assert res2["s3"]["inpaint_synced"] == 0
    assert res2["s3"]["galactic_synced"] == 0
    assert fake.get_calls == []

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            "SELECT pass_id, has_decoded_image, decoded_image_count, is_golden, snr_db FROM raw_passes ORDER BY pass_id"
        ).fetchall()
        assert len(rows) == 2
        # METEOR-M2 3_20261127_003000: MSU-MR-1.png のみカウントされ、spectrogram / ml_inpainted は除外
        assert rows[0] == ("METEOR-M2 3_20261127_003000", True, 1, False, 12.4)
        # GOLDEN: ml_inpainted 配下のみなので has_decoded_image は False、is_golden は True
        assert rows[1] == ("METEOR-M2_GOLDEN_1791698618", False, 0, True, None)

        inp = conn.execute(
            "SELECT config_hash, prev_source_passes_count, mean_uncertainty FROM raw_inpaint_runs"
        ).fetchone()
        assert inp == ("a84ae13587fa7d7a", 2, None)

        gal = conn.execute(
            "SELECT scan_id, sky_type, preempted, peak_line_snr_db FROM raw_galactic_scans"
        ).fetchone()
        assert gal == ("scan_20261127_231828", "INTERMEDIATE", True, 1.78)

        rev_cols = {r[0] for r in conn.execute("DESCRIBE raw_config_revisions").fetchall()}
        assert "affected_satellites" in rev_cols
    finally:
        conn.close()


def test_classify_affected_satellites_and_first_parent_revisions() -> None:
    assert sync_mod._classify_affected_satellites(["apps/satellite-analyzer/src/aprs_decoder.py"]) == "ISS"
    assert sync_mod._classify_affected_satellites(["apps/satellite-analyzer/src/iss_dsp.py"]) == "ISS"
    assert sync_mod._classify_affected_satellites(["apps/satellite-analyzer/src/meteor_decoder.py"]) == "METEOR-M2-3,METEOR-M2-4"
    assert sync_mod._classify_affected_satellites(["apps/satellite-analyzer/src/funcube_decoder.py"]) == "FUNCUBE-1"
    assert sync_mod._classify_affected_satellites([
        "apps/satellite-analyzer/src/aprs_decoder.py",
        "apps/satellite-analyzer/src/funcube_decoder.py",
    ]) == "FUNCUBE-1,ISS"
    assert sync_mod._classify_affected_satellites(["apps/satellite-tracker-rs/src/main.rs"]) == "ALL"
    assert sync_mod._classify_affected_satellites(["apps/satellite-analyzer/src/activities.py"]) == "ALL"
    assert sync_mod._classify_affected_satellites(["apps/satellite-viewer/app.py", "README.md"]) == ""

    revs = sync_mod.collect_git_revisions(REPO_ROOT, limit=30)
    shas = [r["commit_sha"] for r in revs]
    assert len(shas) == len(set(shas))
    assert all("affected_satellites" in r for r in revs)
