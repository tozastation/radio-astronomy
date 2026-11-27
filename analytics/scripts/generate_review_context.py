#!/usr/bin/env python3
"""DuckDB の dbt Mart / Semantic / Ontology テーブルから毎朝レビュー用の診断レポートを生成する。"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import yaml

ANALYTICS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ANALYTICS_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_s3_to_duckdb as sync_mod  # noqa: E402

DEFAULT_DB_PATH = ANALYTICS_DIR / "state" / "observatory.duckdb"
DEFAULT_ONTOLOGY_DIR = ANALYTICS_DIR / "ontology"
DEFAULT_DBT_DIR = ANALYTICS_DIR / "dbt_observatory"


def load_dbt_vars_from_rules(ontology_dir: Path) -> dict[str, Any]:
    """rules.yaml から dbt --vars 用の閾値辞書を組み立てる。"""
    rules = yaml.safe_load((ontology_dir / "rules.yaml").read_text(encoding="utf-8"))
    thresholds = rules.get("pass_diagnosis_thresholds") or {}
    signals = {s["signal_id"]: s for s in (rules.get("tuning_signals") or []) if "signal_id" in s}

    rf = signals.get("RF_DECODER_TUNING", {})
    tle = signals.get("TLE_OR_CLOCK_DRIFT", {})
    rev = signals.get("REVISION_REGRESSION", {})
    ml = signals.get("ML_MASK_DISTRIBUTION_SHIFT", {})
    gal = signals.get("GALACTIC_NOISE_FLOOR_SPIKE", {})

    return {
        "high_snr_db": thresholds.get("high_snr_db", 8.0),
        "tca_drift_sec": thresholds.get("tca_drift_sec", 15.0),
        "seam_jump_l1_degraded": thresholds.get("seam_jump_l1_degraded", 0.035),
        "rf_decoder_min_count": rf.get("min_count", 2),
        "tle_min_samples": tle.get("min_samples", 5),
        "min_samples_per_window": rev.get("min_samples_per_window", 5),
        "qor_drop_pt_threshold": rev.get("qor_drop_pt_threshold", -15.0),
        "large_gap_rate_pct_threshold": ml.get("large_gap_rate_pct_threshold", 30.0),
        "single_band_dropout_rate_pct_threshold": ml.get("single_band_dropout_rate_pct_threshold", 25.0),
        "continuum_delta_db_threshold": gal.get("continuum_delta_db_threshold", 3.0),
        "galactic_min_scans": gal.get("min_scans", 10),
    }


def run_dbt_build(dbt_dir: Path, db_path: Path, ontology_dir: Path) -> None:
    """rules.yaml の閾値を渡して dbt build を実行する。"""
    dbt_vars = load_dbt_vars_from_rules(ontology_dir)
    env = os.environ.copy()
    env["OBSERVATORY_DUCKDB_PATH"] = str(db_path.resolve())
    cmd = [
        "dbt",
        "build",
        "--project-dir",
        str(dbt_dir),
        "--profiles-dir",
        str(dbt_dir),
        "--vars",
        json.dumps(dbt_vars),
    ]
    subprocess.run(cmd, env=env, check=True)


def _fetch_dicts(conn: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    cur = conn.execute(query)
    cols = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for col, val in zip(cols, row):
            if hasattr(val, "isoformat"):
                item[col] = val.isoformat()
            else:
                item[col] = val
        out.append(item)
    return out


def build_review_context(db_path: Path, days: int = 7) -> dict[str, Any]:
    """DuckDB から診断サマリ構造体を取得する。"""
    window_label = "7d" if days <= 7 else ("30d" if days <= 30 else "all")
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        signals = _fetch_dicts(
            conn,
            "SELECT signal_id, severity, target_entity, evidence_summary, recommended_action FROM ont_tuning_signals ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, signal_id",
        )
        qor = _fetch_dicts(
            conn,
            f"""
            SELECT canonical_sat_id, total_passes, success_count, failure_count, unknown_count,
                   effective_qor_pct, mean_snr_db, p95_abs_tca_drift_sec, avg_packets_per_success
            FROM sem_satellite_qor
            WHERE window_label = '{window_label}'
            ORDER BY canonical_sat_id
            """,
        )
        causes = _fetch_dicts(
            conn,
            f"""
            WITH anchor AS (
                SELECT COALESCE(MAX(observed_at_utc), CURRENT_TIMESTAMP) AS ref_ts
                FROM ont_pass_diagnosis
                WHERE NOT is_golden
            )
            SELECT d.failure_cause, d.is_tunable, d.is_ml_retrain_signal, COUNT(*) AS pass_count
            FROM ont_pass_diagnosis d
            CROSS JOIN anchor a
            WHERE NOT d.is_golden
              AND d.observed_at_utc >= a.ref_ts - ({int(days)} * INTERVAL '1 day')
            GROUP BY d.failure_cause, d.is_tunable, d.is_ml_retrain_signal
            ORDER BY pass_count DESC, d.failure_cause
            """,
        )
        tunable_passes = _fetch_dicts(
            conn,
            f"""
            WITH anchor AS (
                SELECT COALESCE(MAX(observed_at_utc), CURRENT_TIMESTAMP) AS ref_ts
                FROM ont_pass_diagnosis
                WHERE NOT is_golden
            )
            SELECT d.pass_id, d.canonical_sat_id, d.observed_at_utc, d.failure_cause,
                   d.snr_db, d.actual_tca_drift_seconds, d.config_pr_label
            FROM ont_pass_diagnosis d
            CROSS JOIN anchor a
            WHERE d.is_tunable
              AND d.observed_at_utc >= a.ref_ts - ({int(days)} * INTERVAL '1 day')
            ORDER BY d.observed_at_utc DESC
            LIMIT 15
            """,
        )
        revision_impact = _fetch_dicts(
            conn,
            """
            SELECT COALESCE(pr_label, LEFT(commit_sha, 8)) AS revision,
                   deployed_at_utc, changed_scope, canonical_sat_id,
                   sample_count_before, sample_count_after,
                   qor_before_pct, qor_after_pct, qor_delta_pt,
                   snr_before_db, snr_after_db, snr_delta_db
            FROM sem_revision_impact
            ORDER BY deployed_at_utc DESC, canonical_sat_id
            LIMIT 15
            """,
        )
        inpaint_quality = _fetch_dicts(
            conn,
            """
            SELECT config_hash, model_name, model_mode, notebook_ref, patch_size,
                   run_count, tier_a_count, tier_b_count, tier_c_count,
                   mean_seam_jump_l1, mean_uncertainty,
                   single_band_dropout_rate_pct, large_gap_rate_pct,
                   val_psnr_db, val_sobel_pct
            FROM sem_inpaint_quality
            ORDER BY run_count DESC
            """,
        )
        galactic_snr = _fetch_dicts(
            conn,
            f"""
            SELECT sky_type, scan_count, preempted_count, preemption_rate_pct,
                   mean_peak_line_snr_db, p95_peak_line_snr_db, mean_continuum_power_db
            FROM sem_galactic_snr
            WHERE window_label = '{window_label}'
            ORDER BY scan_count DESC
            """,
        )
        galactic_daily_trend = _fetch_dicts(
            conn,
            """
            WITH anchor AS (
                SELECT COALESCE(MAX(timestamp_utc), CURRENT_TIMESTAMP) AS ref_ts
                FROM fct_galactic_scans
            )
            SELECT CAST(g.scan_date_utc AS VARCHAR) AS scan_date_utc,
                   g.sky_type,
                   COUNT(*) AS scan_count,
                   ROUND(AVG(g.peak_line_snr_db), 2) AS mean_peak_line_snr_db,
                   ROUND(AVG(g.continuum_power_db), 2) AS mean_continuum_power_db
            FROM fct_galactic_scans g
            CROSS JOIN anchor a
            WHERE g.timestamp_utc >= a.ref_ts - INTERVAL '14 day'
            GROUP BY g.scan_date_utc, g.sky_type
            ORDER BY g.scan_date_utc ASC, g.sky_type ASC
            """,
        )
        pass_daily_trend = _fetch_dicts(
            conn,
            """
            WITH anchor AS (
                SELECT COALESCE(MAX(observed_at_utc), CURRENT_TIMESTAMP) AS ref_ts
                FROM fct_passes
                WHERE NOT is_golden
            )
            SELECT CAST(CAST(p.observed_at_utc AS DATE) AS VARCHAR) AS pass_date_utc,
                   COUNT(*) AS total_passes,
                   SUM(CASE WHEN p.outcome = 'success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN p.outcome = 'failure' THEN 1 ELSE 0 END) AS failure_count,
                   ROUND(
                       100.0 * SUM(CASE WHEN p.outcome = 'success' THEN 1 ELSE 0 END)
                       / NULLIF(SUM(CASE WHEN p.outcome IN ('success', 'failure') THEN 1 ELSE 0 END), 0),
                       2
                   ) AS effective_qor_pct
            FROM fct_passes p
            CROSS JOIN anchor a
            WHERE NOT p.is_golden
              AND p.observed_at_utc >= a.ref_ts - INTERVAL '14 day'
            GROUP BY CAST(p.observed_at_utc AS DATE)
            ORDER BY pass_date_utc ASC
            """,
        )
    finally:
        conn.close()

    return {
        "available": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "window_label": window_label,
        "tuning_signals": signals,
        "satellite_qor": qor,
        "diagnosis_causes": causes,
        "tunable_anomalies": tunable_passes,
        "revision_impact": revision_impact,
        "inpaint_quality": inpaint_quality,
        "galactic_snr": galactic_snr,
        "galactic_daily_trend": galactic_daily_trend,
        "pass_daily_trend": pass_daily_trend,
    }


def format_markdown(ctx: dict[str, Any]) -> str:
    """構造化診断コンテキストを定期レビュー向け Markdown へ整形する。"""
    lines: list[str] = []
    days = ctx["window_days"]
    lines.append(f"# 観測オントロジー ＆ セマンティック診断サマリ (直近 {days} 日 / window={ctx['window_label']})")
    lines.append("")

    lines.append("## 1. 優先改善シグナル (`ont_tuning_signals`)")
    lines.append("")
    signals = ctx["tuning_signals"]
    if not signals:
        lines.append("- 発火した優先改善シグナルはありません。")
    else:
        lines.append("| Severity | Signal | 対象 | 根拠サマリ | 推奨アクション |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for s in signals:
            lines.append(
                f"| `{s['severity']}` | `{s['signal_id']}` | {s['target_entity']} | {s['evidence_summary']} | {s['recommended_action']} |"
            )
    lines.append("")

    lines.append("## 2. 衛星別公式 QoR (`sem_satellite_qor` / `GOLDEN` 除外)")
    lines.append("")
    qor = ctx["satellite_qor"]
    if not qor:
        lines.append("- 対象期間内の衛星パスデータはありません。")
    else:
        lines.append("| 衛星 | 総数 | 成功 | 失敗 | 保留 | 公式 QoR | 平均 SNR (dB) | p95 TCA ドリフト (s) |")
        lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in qor:
            q_str = f"{r['effective_qor_pct']}%" if r["effective_qor_pct"] is not None else "-"
            snr_str = f"{r['mean_snr_db']}" if r["mean_snr_db"] is not None else "-"
            tca_str = f"{r['p95_abs_tca_drift_sec']}" if r["p95_abs_tca_drift_sec"] is not None else "-"
            lines.append(
                f"| {r['canonical_sat_id']} | {r['total_passes']} | {r['success_count']} | {r['failure_count']} | {r['unknown_count']} | {q_str} | {snr_str} | {tca_str} |"
            )
    lines.append("")

    lines.append("## 3. 失敗・品質低下の原因分類 (`ont_pass_diagnosis`)")
    lines.append("")
    causes = ctx["diagnosis_causes"]
    if causes:
        lines.append("| 原因分類 | チューニング対象 | ML学習シグナル | 件数 |")
        lines.append("| :--- | :---: | :---: | ---: |")
        for c in causes:
            lines.append(
                f"| `{c['failure_cause']}` | {c['is_tunable']} | {c['is_ml_retrain_signal']} | {c['pass_count']} |"
            )
        lines.append("")

    anomalies = ctx["tunable_anomalies"]
    if anomalies:
        lines.append("### チューニング対象異常パス (直近最大15件)")
        for a in anomalies:
            lines.append(
                f"- `{a['pass_id']}` ({a['canonical_sat_id']}): `{a['failure_cause']}` / SNR={a['snr_db']} dB / TCA_drift={a['actual_tca_drift_seconds']} s"
            )
        lines.append("")

    lines.append("## 4. 直近チューニング PR の前後比較 (`sem_revision_impact`)")
    lines.append("")
    revs = ctx["revision_impact"]
    if not revs:
        lines.append("- 前後比較可能なチューニング PR 履歴はありません。")
    else:
        lines.append("| リビジョン | 衛星 | 件数 (前/後) | QoR (前 -> 後) | QoR 変化 | SNR 変化 (dB) |")
        lines.append("| :--- | :--- | :---: | :---: | ---: | ---: |")
        for rv in revs:
            qb = f"{rv['qor_before_pct']}%" if rv["qor_before_pct"] is not None else "-"
            qa = f"{rv['qor_after_pct']}%" if rv["qor_after_pct"] is not None else "-"
            qd = f"{rv['qor_delta_pt']:+} pt" if rv["qor_delta_pt"] is not None else "-"
            sd = f"{rv['snr_delta_db']:+}" if rv["snr_delta_db"] is not None else "-"
            lines.append(
                f"| `{rv['revision']}` | {rv['canonical_sat_id']} | {rv['sample_count_before']}/{rv['sample_count_after']} | {qb} -> {qa} | {qd} | {sd} |"
            )
    lines.append("")

    lines.append("## 5. ML 補完品質 ＆ 学習フィードバック指標 (`sem_inpaint_quality`)")
    lines.append("")
    inps = ctx["inpaint_quality"]
    if not inps:
        lines.append("- 対象となる ML 補完実行データはありません。")
    else:
        lines.append("| モデル (`notebook`) | 件数 | Tier A/B/C | Seam L1 | 不確実性 | 単一Ch欠落率 | Patch超過率 |")
        lines.append("| :--- | ---: | :---: | ---: | ---: | ---: | ---: |")
        for m in inps:
            unc = f"{m['mean_uncertainty']}" if m["mean_uncertainty"] is not None else "-"
            l1 = f"{m['mean_seam_jump_l1']}" if m["mean_seam_jump_l1"] is not None else "-"
            lines.append(
                f"| `{m['notebook_ref']}` (`{m['config_hash']}`) | {m['run_count']} | {m['tier_a_count']}/{m['tier_b_count']}/{m['tier_c_count']} | {l1} | {unc} | {m['single_band_dropout_rate_pct']}% | {m['large_gap_rate_pct']}% |"
            )
    lines.append("")

    lines.append("## 6. 天の川銀河 21cm ドリフトスキャン (`sem_galactic_snr`)")
    lines.append("")
    gals = ctx["galactic_snr"]
    if not gals:
        lines.append("- 対象期間内の 21cm スキャンデータはありません。")
    else:
        lines.append("| sky_type | スキャン数 | 中断率 | SNR 平均 / p95 (dB) | Continuum 平均 (dB) |")
        lines.append("| :--- | ---: | ---: | :---: | ---: |")
        for g in gals:
            lines.append(
                f"| {g['sky_type']} | {g['scan_count']} | {g['preemption_rate_pct']}% | {g['mean_peak_line_snr_db']} / {g['p95_peak_line_snr_db']} | {g['mean_continuum_power_db']} |"
            )
    lines.append("")
    return "\n".join(lines)


def put_json_to_s3(
    endpoint_url: str,
    bucket: str,
    s3_key: str,
    payload: dict[str, Any],
    access_key: str,
    secret_key: str,
    region: str = "garage",
    timeout_sec: int = 15,
) -> None:
    """AWS SigV4 PUT で JSON ペイロードを Garage S3 へアップロードする。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    parsed = urllib.parse.urlparse(endpoint_url.rstrip("/"))
    host = parsed.netloc
    canonical_uri = urllib.parse.quote(f"/{bucket}/{s3_key.lstrip('/')}", safe="/-_.~")

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    canonical_headers = (
        f"content-type:application/json; charset=utf-8\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = (
        f"PUT\n{canonical_uri}\n\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    signing_key = sync_mod._get_signature_key(secret_key, date_stamp, region, "s3")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"{endpoint_url.rstrip('/')}{canonical_uri}"
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Host": host,
            "Content-Type": "application/json; charset=utf-8",
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ontology/semantic review context from DuckDB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB file path")
    parser.add_argument("--ontology-dir", type=Path, default=DEFAULT_ONTOLOGY_DIR, help="Ontology YAML dir")
    parser.add_argument("--dbt-dir", type=Path, default=DEFAULT_DBT_DIR, help="dbt project dir")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--run-dbt", action="store_true", help="Run dbt build before querying")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path to save structured JSON")
    parser.add_argument("--publish-s3", action="store_true", help="Upload report JSON to Garage S3 for satellite-viewer")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("GARAGE_S3_ENDPOINT", "http://localhost:30900"),
        help="Garage S3 endpoint URL",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("GARAGE_BUCKET", "satellite-recordings"),
        help="Garage S3 bucket name",
    )
    parser.add_argument("--s3-key", default="analytics/latest_report.json", help="Target S3 key for report JSON")
    args = parser.parse_args()

    if args.run_dbt:
        run_dbt_build(args.dbt_dir, args.db, args.ontology_dir)

    ctx = build_review_context(args.db, days=args.days)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.publish_s3:
        ak = os.environ.get("GARAGE_ACCESS_KEY", "")
        sk = os.environ.get("GARAGE_SECRET_KEY", "")
        if not ak or not sk:
            ak, sk = sync_mod.load_credentials_from_k8s()
        if not ak or not sk:
            print(
                "[warn] --publish-s3 skipped: GARAGE_ACCESS_KEY / GARAGE_SECRET_KEY unavailable.",
                file=sys.stderr,
            )
        else:
            put_json_to_s3(
                endpoint_url=args.endpoint,
                bucket=args.bucket,
                s3_key=args.s3_key,
                payload=ctx,
                access_key=ak,
                secret_key=sk,
                region=os.environ.get("GARAGE_REGION", "garage"),
            )
            print(f"[info] Published analytics report to s3://{args.bucket}/{args.s3_key}", file=sys.stderr)

    print(format_markdown(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
