from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

ANALYTICS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANALYTICS_DIR.parent
ONTOLOGY_DIR = ANALYTICS_DIR / "ontology"
DBT_DIR = ANALYTICS_DIR / "dbt_observatory"
SCRIPTS_DIR = ANALYTICS_DIR / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_review_context as rev_mod  # noqa: E402
import sync_s3_to_duckdb as sync_mod  # noqa: E402


class FakeS3Client:
    def __init__(self, objects: list[dict], payloads: dict[str, dict]) -> None:
        self.objects = objects
        self.payloads = payloads

    def list_objects(self, prefix: str) -> list[dict]:
        return [o for o in self.objects if o["key"].startswith(prefix)]

    def get_json(self, key: str) -> dict:
        return self.payloads[key]


def _make_pass(
    objects: list[dict],
    payloads: dict[str, dict],
    sat_dir: str,
    sat_name: str,
    pass_id: str,
    dt: datetime,
    *,
    packets: int = 0,
    snr_db: float | None = None,
    tca_drift: float | None = None,
    has_img: bool = False,
    signal_type: str = "LRPT (QPSK 72kbps)",
    inpaint_meta: dict | None = None,
) -> None:
    prefix = f"results/{sat_dir}/{pass_id}"
    sum_key = f"{prefix}/summary.json"
    objects.append({"key": sum_key, "last_modified": dt, "size": 100})
    payloads[sum_key] = {
        "satellite": sat_name,
        "pass_id": pass_id,
        "satellite_type": "WeatherSatellite",
        "signal_type": signal_type,
        "packets_count": packets,
        "snr_db": snr_db,
        "actual_tca_drift_seconds": tca_drift,
        "status": "completed",
    }
    if has_img:
        objects.append({"key": f"{prefix}/MSU-MR-1.png", "last_modified": dt, "size": 100})
    if inpaint_meta is not None:
        mkey = f"{prefix}/ml_inpainted/metadata.json"
        objects.append({"key": mkey, "last_modified": dt, "size": 100})
        payloads[mkey] = inpaint_meta


def test_step2_dbt_build_and_diagnosis_e2e(tmp_path: Path) -> None:
    base_dt = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    objects: list[dict] = []
    payloads: dict[str, dict] = {}

    # 1. 通常成功 + ML 補完 (Tier C かつ patch_size 超過 + 単一バンド欠落)
    _make_pass(
        objects,
        payloads,
        "METEOR-M2 3",
        "METEOR-M2 3",
        "METEOR-M2 3_20261127_003000",
        base_dt - timedelta(hours=2),
        packets=5,
        snr_db=14.0,
        tca_drift=1.2,
        has_img=True,
        inpaint_meta={
            "schema_version": 1,
            "pass_id": "METEOR-M2 3_20261127_003000",
            "satellite": "METEOR-M2 3",
            "generated_at_utc": "2026-11-27T11:00:00Z",
            "model": {"name": "PrevImageGuidedResSEUNet", "mode": "temporal_prev_11ch", "config_hash": "a84ae13587fa7d7a"},
            "input_stats": {"height": 168, "width": 1568, "available_channels": "14", "missing_pct": 88.6, "partial_valid_pct": 5.0, "max_gap_px": 128},
            "registration": {"mode": "utc_track_scan_match", "clear_agree_pct": 0.0, "used_prev_atlas": True, "prev_source_passes": ["p0"]},
            "quality": {"tier": "Tier C", "seam_jump_l1": 0.0411, "processing_sec": 12.8},
        },
    )
    # 2. HighSNRDecodeFailure x 2 (RF_DECODER_TUNING 発火用)
    _make_pass(
        objects,
        payloads,
        "METEOR-M2 3",
        "METEOR-M2 3",
        "METEOR-M2 3_20261126_003000",
        base_dt - timedelta(days=1),
        packets=0,
        snr_db=11.5,
        tca_drift=2.0,
    )
    _make_pass(
        objects,
        payloads,
        "METEOR-M2 3",
        "METEOR-M2 3",
        "METEOR-M2 3_20261125_003000",
        base_dt - timedelta(days=2),
        packets=0,
        snr_db=9.2,
        tca_drift=-1.5,
    )
    # 3. TcaDriftAnomaly (TLE_OR_CLOCK_DRIFT 確認用)
    _make_pass(
        objects,
        payloads,
        "ISS (ZARYA)",
        "ISS",
        "ISS_20261126_100000",
        base_dt - timedelta(days=1, hours=2),
        packets=0,
        snr_db=4.0,
        tca_drift=22.5,
        signal_type="APRS / AX.25 (1200bps AFSK)",
    )
    # 4. GOLDEN サンプル (集計除外対象)
    _make_pass(
        objects,
        payloads,
        "METEOR-M2 4",
        "METEOR-M2 4",
        "METEOR-M2_GOLDEN_1791698618",
        base_dt - timedelta(days=3),
        packets=0,
        snr_db=20.0,
    )
    # 5. 21cm スキャン
    for i in range(3):
        dt_g = base_dt - timedelta(hours=i + 1)
        gkey = f"galactic/{dt_g.strftime('%Y-%m-%d')}/scan_{dt_g.strftime('%Y%m%d_%H%M%S')}.json"
        objects.append({"key": gkey, "last_modified": dt_g, "size": 100})
        payloads[gkey] = {
            "timestamp_utc": dt_g.isoformat(),
            "integration_sec": 300.0,
            "preempted": i == 0,
            "pointing": {"az_deg": 30.0, "el_deg": 35.0, "sky_type": "GALACTIC_PLANE" if i < 2 else "REFERENCE"},
            "rf_metrics": {"continuum_power_db": 17.0, "peak_line_snr_db": 4.2 if i < 2 else 0.8},
        }

    db_path = tmp_path / "e2e.duckdb"
    sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, FakeS3Client(objects, payloads))

    # dbt build 実行 (モデル構築 + 全 schema テスト)
    env = os.environ.copy()
    env["OBSERVATORY_DUCKDB_PATH"] = str(db_path)
    dbt_vars = rev_mod.load_dbt_vars_from_rules(ONTOLOGY_DIR)
    proc = subprocess.run(
        [
            "dbt",
            "build",
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--vars",
            json.dumps(dbt_vars),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"dbt build failed:\n{proc.stdout}\n{proc.stderr}"

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        causes = dict(conn.execute("SELECT pass_id, failure_cause FROM ont_pass_diagnosis").fetchall())
        assert causes["METEOR-M2 3_20261127_003000"] == "InpaintDegraded"
        assert causes["METEOR-M2 3_20261126_003000"] == "HighSNRDecodeFailure"
        assert causes["METEOR-M2 3_20261125_003000"] == "HighSNRDecodeFailure"
        assert causes["ISS_20261126_100000"] == "TcaDriftAnomaly"
        assert causes["METEOR-M2_GOLDEN_1791698618"] == "GoldenBenchmark"

        signals = {r[0] for r in conn.execute("SELECT signal_id FROM ont_tuning_signals").fetchall()}
        assert "RF_DECODER_TUNING" in signals
        assert "ML_MASK_DISTRIBUTION_SHIFT" in signals
    finally:
        conn.close()

    json_out = tmp_path / "review_ctx.json"
    ctx = rev_mod.build_review_context(db_path, days=7)
    json_out.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    md = rev_mod.format_markdown(ctx)

    assert ctx["available"] is True
    assert "generated_at_utc" in ctx
    assert len(ctx["galactic_daily_trend"]) >= 1
    assert len(ctx["pass_daily_trend"]) >= 1
    lineage = ctx.get("dbt_lineage") or {}
    lineage_names = {n["name"]: n for n in lineage.get("nodes") or []}
    assert "raw_passes" in lineage_names
    assert "fct_passes" in lineage_names
    assert "ont_tuning_signals" in lineage_names
    assert lineage_names["raw_passes"]["row_count"] == 5
    assert len(lineage.get("edges") or []) >= 15
    assert "RF_DECODER_TUNING" in md
    assert "ML_MASK_DISTRIBUTION_SHIFT" in md
    assert "METEOR-M2-3" in md
    assert "GOLDEN" in md
    assert json_out.exists()


def _run_dbt_build(db_path: Path) -> None:
    env = os.environ.copy()
    env["OBSERVATORY_DUCKDB_PATH"] = str(db_path)
    dbt_vars = rev_mod.load_dbt_vars_from_rules(ONTOLOGY_DIR)
    proc = subprocess.run(
        [
            "dbt",
            "build",
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--vars",
            json.dumps(dbt_vars),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"dbt build failed:\n{proc.stdout}\n{proc.stderr}"


def test_tle_drift_requires_min_valid_drift_samples(tmp_path: Path) -> None:
    """total_passes >= 5 でも非 NULL の actual_tca_drift_seconds が 5 件未満なら TLE_OR_CLOCK_DRIFT が発火しないこと。"""
    base_dt = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
    objects: list[dict] = []
    payloads: dict[str, dict] = {}

    # 5 パスのうち 1 件だけ 35.0s のドリフト、残り 4 件は None (SNR 不足等)
    for i in range(5):
        dt = base_dt - timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "METEOR-M2 3",
            "METEOR-M2 3",
            f"METEOR-M2 3_{dt.strftime('%Y%m%d_%H%M%S')}",
            dt,
            packets=0,
            snr_db=3.0,
            tca_drift=35.0 if i == 0 else None,
        )

    db_path = tmp_path / "tle_guard.duckdb"
    sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, FakeS3Client(objects, payloads))
    _run_dbt_build(db_path)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        qor_row = conn.execute(
            "SELECT total_passes, tca_drift_sample_count FROM sem_satellite_qor WHERE window_label = '7d' AND canonical_sat_id = 'METEOR-M2-3'"
        ).fetchone()
        assert qor_row == (5, 1)
        tle_sigs = conn.execute(
            "SELECT signal_id FROM ont_tuning_signals WHERE signal_id = 'TLE_OR_CLOCK_DRIFT'"
        ).fetchall()
        assert tle_sigs == []
    finally:
        conn.close()

    # 有効計測を 5 件に増やした場合は発火すること
    for i in range(5, 9):
        dt = base_dt - timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "METEOR-M2 3",
            "METEOR-M2 3",
            f"METEOR-M2 3_{dt.strftime('%Y%m%d_%H%M%S')}",
            dt,
            packets=0,
            snr_db=6.0,
            tca_drift=25.0,
        )
    sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, FakeS3Client(objects, payloads))
    _run_dbt_build(db_path)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tle_sigs = conn.execute(
            "SELECT target_entity, evidence_summary FROM ont_tuning_signals WHERE signal_id = 'TLE_OR_CLOCK_DRIFT'"
        ).fetchall()
        assert len(tle_sigs) == 1
        assert tle_sigs[0][0] == "METEOR-M2-3"
        assert "有効計測 n=5/9" in tle_sigs[0][1]
    finally:
        conn.close()


def test_revision_regression_scope_and_min_before_success_guard(tmp_path: Path) -> None:
    """affected_satellites スコープ外および success_count_before < 3 の小母数揺らぎでは REVISION_REGRESSION が発火しないこと。"""
    deploy_dt = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
    objects: list[dict] = []
    payloads: dict[str, dict] = {}

    # METEOR-M2-3: 適用前 5 件中 2 件成功 (QoR 40%, success_count_before=2 < 3)、適用後 5 件中 0 件成功 (QoR 0%)
    for i in range(5):
        dt_b = deploy_dt - timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "METEOR-M2 3",
            "METEOR-M2 3",
            f"METEOR-M2 3_b_{i}",
            dt_b,
            packets=5 if i < 2 else 0,
            snr_db=10.0 if i < 2 else 1.5,
            has_img=i < 2,
        )
        dt_a = deploy_dt + timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "METEOR-M2 3",
            "METEOR-M2 3",
            f"METEOR-M2 3_a_{i}",
            dt_a,
            packets=0,
            snr_db=1.5,
            has_img=False,
        )

    # FUNCUBE-1: 適用前 5 件中 3 件成功 (QoR 60%, success_count_before=3 >= 3)、適用後 5 件中 0 件成功 (QoR 0%)
    for i in range(5):
        dt_b = deploy_dt - timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "FUNCUBE-1",
            "FUNCUBE-1",
            f"FUNCUBE-1_b_{i}",
            dt_b,
            packets=3 if i < 3 else 0,
            snr_db=9.0 if i < 3 else 1.0,
            signal_type="BPSK 1200bps",
        )
        dt_a = deploy_dt + timedelta(hours=i + 1)
        _make_pass(
            objects,
            payloads,
            "FUNCUBE-1",
            "FUNCUBE-1",
            f"FUNCUBE-1_a_{i}",
            dt_a,
            packets=0,
            snr_db=1.0,
            signal_type="BPSK 1200bps",
        )

    db_path = tmp_path / "rev_guard.duckdb"
    sync_mod.sync_all(db_path, ONTOLOGY_DIR, REPO_ROOT, FakeS3Client(objects, payloads))

    # テスト用にリビジョンテーブルを差し替えてスコープと最小成功数ガードを検証
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("DELETE FROM raw_config_revisions")
        now_utc = datetime.now(timezone.utc)
        conn.executemany(
            "INSERT INTO raw_config_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("rev_iss_only", "chore/tuning-review-iss", deploy_dt, "analyzer", "ISS", "fix: iss only", now_utc),
                ("rev_all_scope", "chore/tuning-review-all", deploy_dt, "tracker", "ALL", "fix: tracker gain", now_utc),
            ],
        )
    finally:
        conn.close()

    _run_dbt_build(db_path)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        impacts = conn.execute(
            "SELECT pr_label, canonical_sat_id, success_count_before FROM sem_revision_impact ORDER BY pr_label, canonical_sat_id"
        ).fetchall()
        # rev_iss_only は ISS 以外のパスに紐づかないこと
        assert all(row[0] != "rev_iss_only" for row in impacts)

        sigs = conn.execute(
            "SELECT target_entity FROM ont_tuning_signals WHERE signal_id = 'REVISION_REGRESSION'"
        ).fetchall()
        targets = {r[0] for r in sigs}
        # METEOR-M2-3 は success_count_before=2 (< 3) のため発火せず、FUNCUBE-1 のみ発火すること
        assert "FUNCUBE-1 (chore/tuning-review-all)" in targets
        assert "METEOR-M2-3 (chore/tuning-review-all)" not in targets
    finally:
        conn.close()
