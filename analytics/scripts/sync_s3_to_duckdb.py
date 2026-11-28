#!/usr/bin/env python3
"""Garage S3 の観測・ML補完メタデータ、Git変更履歴、およびオントロジーYAMLを DuckDB へ増分同期するスクリプト。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import duckdb
import yaml

S3_XML_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")

ANALYTICS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANALYTICS_DIR.parent
DEFAULT_DB_PATH = ANALYTICS_DIR / "state" / "observatory.duckdb"
DEFAULT_ONTOLOGY_DIR = ANALYTICS_DIR / "ontology"


class S3ClientProtocol(Protocol):
    def list_objects(self, prefix: str) -> list[dict[str, Any]]: ...
    def get_json(self, key: str) -> dict[str, Any]: ...


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


class SimpleS3Client:
    """Python 標準ライブラリのみで実装した最小限の S3 (SigV4) クライアント。"""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "garage",
        timeout_sec: int = 15,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.timeout_sec = timeout_sec

    def _request(self, path: str, query_params: dict[str, str] | None = None) -> bytes:
        parsed = urllib.parse.urlparse(self.endpoint_url)
        host = parsed.netloc
        canonical_uri = urllib.parse.quote(f"/{self.bucket}{path}", safe="/-_.~")

        params = dict(query_params or {})
        sorted_items = sorted(params.items())
        canonical_querystring = "&".join(
            f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
            for k, v in sorted_items
        )

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(b"").hexdigest()

        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = (
            f"GET\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )
        signing_key = _get_signature_key(self.secret_key, date_stamp, self.region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        url = f"{self.endpoint_url}{canonical_uri}"
        if canonical_querystring:
            url = f"{url}?{canonical_querystring}"

        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Host": host,
                "x-amz-date": amz_date,
                "x-amz-content-sha256": payload_hash,
                "Authorization": authorization,
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            return resp.read()

    def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        """ListObjectsV2 で指定プレフィックス配下のオブジェクト一覧を全件取得する。"""
        objects: list[dict[str, Any]] = []
        continuation_token: str | None = None

        while True:
            params: dict[str, str] = {"list-type": "2", "prefix": prefix}
            if continuation_token:
                params["continuation-token"] = continuation_token

            body = self._request("", params)
            root = ET.fromstring(body)
            for contents in root.findall("s3:Contents", S3_XML_NS):
                key_el = contents.find("s3:Key", S3_XML_NS)
                mod_el = contents.find("s3:LastModified", S3_XML_NS)
                size_el = contents.find("s3:Size", S3_XML_NS)
                if key_el is None or key_el.text is None:
                    continue
                last_modified = None
                if mod_el is not None and mod_el.text:
                    last_modified = datetime.fromisoformat(mod_el.text.replace("Z", "+00:00"))
                objects.append(
                    {
                        "key": key_el.text,
                        "last_modified": last_modified,
                        "size": int(size_el.text) if size_el is not None and size_el.text else 0,
                    }
                )

            truncated_el = root.find("s3:IsTruncated", S3_XML_NS)
            is_truncated = truncated_el is not None and truncated_el.text == "true"
            if not is_truncated:
                break
            token_el = root.find("s3:NextContinuationToken", S3_XML_NS)
            if token_el is None or not token_el.text:
                break
            continuation_token = token_el.text

        return objects

    def get_json(self, key: str) -> dict[str, Any]:
        raw = self._request("/" + key.lstrip("/"))
        return json.loads(raw.decode("utf-8"))


def load_credentials_from_k8s(secret_name: str = "garage-satellite-credentials") -> tuple[str, str]:
    """環境変数が未設定のとき kubectl から Garage S3 の認証情報を取得する。"""
    try:
        out = subprocess.check_output(
            ["kubectl", "get", "secret", secret_name, "-o", "jsonpath={.data}"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        data = json.loads(out)
        access_key = base64.b64decode(data["access_key"]).decode("utf-8")
        secret_key = base64.b64decode(data["secret_key"]).decode("utf-8")
        return access_key, secret_key
    except Exception:
        return "", ""


def parse_pass_datetime(key_or_id: str, fallback: datetime | None = None) -> datetime | None:
    """pass_id や S3 キー中の YYYYMMDD_HHMMSS から UTC 時刻を抽出する。"""
    m = re.search(r"(\d{8})_(\d{6})", key_or_id)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return fallback


def is_decoded_image_relpath(rel_path: str) -> bool:
    """spectrogram と ml_inpainted/ 配下を除いた復調画像ファイルかを判定する。"""
    lower = rel_path.lower()
    if lower.startswith("ml_inpainted/") or "/ml_inpainted/" in lower:
        return False
    if "spectrogram" in lower:
        return False
    return lower.endswith(IMAGE_EXTENSIONS)


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_dt(val: Any) -> datetime | None:
    if not val or not isinstance(val, str):
        return None
    try:
        return _normalize_dt(datetime.fromisoformat(val.replace("Z", "+00:00")))
    except ValueError:
        return None


def ensure_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """DuckDB 内に raw_* および ref_* テーブルを作成する。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_passes (
            s3_key VARCHAR PRIMARY KEY,
            pass_prefix VARCHAR,
            pass_id VARCHAR,
            satellite VARCHAR,
            satellite_type VARCHAR,
            signal_type VARCHAR,
            frequency_label VARCHAR,
            status VARCHAR,
            packets_count INTEGER,
            has_decoded_image BOOLEAN,
            decoded_image_count INTEGER,
            snr_db DOUBLE,
            actual_tca_drift_seconds DOUBLE,
            duration_seconds DOUBLE,
            summary_text VARCHAR,
            observed_at_utc TIMESTAMPTZ,
            last_modified_utc TIMESTAMPTZ,
            is_golden BOOLEAN,
            synced_at_utc TIMESTAMPTZ
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_inpaint_runs (
            s3_key VARCHAR PRIMARY KEY,
            pass_id VARCHAR,
            satellite VARCHAR,
            schema_version INTEGER,
            generated_at_utc TIMESTAMPTZ,
            device VARCHAR,
            model_name VARCHAR,
            model_mode VARCHAR,
            quality_preset VARCHAR,
            bundle_file VARCHAR,
            config_hash VARCHAR,
            base_bundle_hash VARCHAR,
            image_height INTEGER,
            image_width INTEGER,
            available_channels VARCHAR,
            all_valid_pct DOUBLE,
            partial_valid_pct DOUBLE,
            missing_pct DOUBLE,
            any_valid_pct DOUBLE,
            max_gap_px INTEGER,
            reg_mode VARCHAR,
            clear_agree_pct DOUBLE,
            used_prev_atlas BOOLEAN,
            prev_source_passes_count INTEGER,
            tier VARCHAR,
            tier_label VARCHAR,
            seam_jump_l1 DOUBLE,
            mean_uncertainty DOUBLE,
            processing_sec DOUBLE,
            last_modified_utc TIMESTAMPTZ,
            synced_at_utc TIMESTAMPTZ
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_galactic_scans (
            s3_key VARCHAR PRIMARY KEY,
            scan_id VARCHAR,
            timestamp_utc TIMESTAMPTZ,
            integration_sec DOUBLE,
            preempted BOOLEAN,
            az_deg DOUBLE,
            el_deg DOUBLE,
            lst_deg DOUBLE,
            ra_deg DOUBLE,
            dec_deg DOUBLE,
            gal_l_deg DOUBLE,
            gal_b_deg DOUBLE,
            region_label VARCHAR,
            sky_type VARCHAR,
            lsr_correction_km_s DOUBLE,
            center_freq_on_hz DOUBLE,
            center_freq_off_hz DOUBLE,
            continuum_power_db DOUBLE,
            peak_line_snr_db DOUBLE,
            peak_v_lsr_km_s DOUBLE,
            last_modified_utc TIMESTAMPTZ,
            synced_at_utc TIMESTAMPTZ
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_config_revisions (
            commit_sha VARCHAR PRIMARY KEY,
            pr_label VARCHAR,
            deployed_at_utc TIMESTAMPTZ,
            changed_scope VARCHAR,
            affected_satellites VARCHAR,
            summary VARCHAR,
            synced_at_utc TIMESTAMPTZ
        )
    """)
    conn.execute("""
        ALTER TABLE raw_config_revisions
        ADD COLUMN IF NOT EXISTS affected_satellites VARCHAR
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ref_ground_stations (
            station_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            lat_deg DOUBLE,
            lon_deg DOUBLE,
            elevation_m DOUBLE,
            clear_az_start_deg DOUBLE,
            clear_az_end_deg DOUBLE,
            obstruction_note VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ref_satellites (
            canonical_id VARCHAR PRIMARY KEY,
            satellite_type VARCHAR,
            canonical_signal VARCHAR,
            expected_artifact VARCHAR,
            nominal_freq_mhz DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ref_satellite_aliases (
            alias_name VARCHAR PRIMARY KEY,
            canonical_id VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ref_model_versions (
            config_hash VARCHAR PRIMARY KEY,
            model_name VARCHAR,
            model_mode VARCHAR,
            notebook_ref VARCHAR,
            input_channels INTEGER,
            patch_size INTEGER,
            gate_channels INTEGER,
            loss_profile VARCHAR,
            extreme_mask_ratio DOUBLE,
            train_patch_count INTEGER,
            val_psnr_db DOUBLE,
            val_sobel_pct DOUBLE,
            blind80_sobel_pct DOUBLE,
            unc_corr_r DOUBLE
        )
    """)


def sync_reference_tables(conn: duckdb.DuckDBPyConnection, ontology_dir: Path) -> dict[str, int]:
    """entities.yaml と models.yaml を読み込み ref_* テーブルを最新化する。"""
    entities_data = yaml.safe_load((ontology_dir / "entities.yaml").read_text(encoding="utf-8"))
    models_data = yaml.safe_load((ontology_dir / "models.yaml").read_text(encoding="utf-8"))

    conn.execute("DELETE FROM ref_ground_stations")
    stations = entities_data.get("ground_stations") or []
    for st in stations:
        conn.execute(
            "INSERT INTO ref_ground_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                st["station_id"],
                st.get("name"),
                st.get("lat_deg"),
                st.get("lon_deg"),
                st.get("elevation_m"),
                st.get("clear_az_start_deg"),
                st.get("clear_az_end_deg"),
                st.get("obstruction_note"),
            ],
        )

    conn.execute("DELETE FROM ref_satellites")
    conn.execute("DELETE FROM ref_satellite_aliases")
    satellites = entities_data.get("satellites") or []
    alias_count = 0
    for sat in satellites:
        cid = sat["canonical_id"]
        conn.execute(
            "INSERT INTO ref_satellites VALUES (?, ?, ?, ?, ?)",
            [
                cid,
                sat.get("satellite_type"),
                sat.get("canonical_signal"),
                sat.get("expected_artifact"),
                sat.get("nominal_freq_mhz"),
            ],
        )
        aliases = set(sat.get("aliases") or [])
        aliases.add(cid)
        for al in sorted(aliases):
            conn.execute("INSERT INTO ref_satellite_aliases VALUES (?, ?)", [al, cid])
            alias_count += 1

    conn.execute("DELETE FROM ref_model_versions")
    model_versions = models_data.get("model_versions") or []
    for mv in model_versions:
        conn.execute(
            "INSERT INTO ref_model_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                mv["config_hash"],
                mv.get("model_name"),
                mv.get("model_mode"),
                mv.get("notebook_ref"),
                mv.get("input_channels"),
                mv.get("patch_size"),
                mv.get("gate_channels"),
                mv.get("loss_profile"),
                mv.get("extreme_mask_ratio"),
                mv.get("train_patch_count"),
                mv.get("val_psnr_db"),
                mv.get("val_sobel_pct"),
                mv.get("blind80_sobel_pct"),
                mv.get("unc_corr_r"),
            ],
        )

    return {
        "ground_stations": len(stations),
        "satellites": len(satellites),
        "satellite_aliases": alias_count,
        "model_versions": len(model_versions),
    }


def _classify_changed_scope(paths: list[str]) -> str:
    scopes: set[str] = set()
    for p in paths:
        if p.startswith("apps/satellite-tracker"):
            scopes.add("tracker")
        elif p.startswith("apps/satellite-analyzer"):
            scopes.add("analyzer")
        elif p.startswith("apps/satellite-viewer"):
            scopes.add("viewer")
        elif p.startswith("infrastructure/"):
            scopes.add("infra")
        elif p.startswith("analytics/"):
            scopes.add("analytics")
        else:
            scopes.add("other")
    if not scopes:
        return "other"
    if len(scopes) == 1:
        return next(iter(scopes))
    for priority in ("tracker", "analyzer", "infra", "viewer", "analytics"):
        if priority in scopes:
            return priority
    return "other"


def _classify_affected_satellites(paths: list[str]) -> str:
    """変更ファイル一覧から影響を受ける正規化衛星ID群を判定する。

    - 受信全体や共通処理に影響する場合は ``"ALL"`` を返す。
    - 衛星固有のデコーダ・DSP のみの場合はカンマ区切りの衛星 ID を返す。
    - テスト・ドキュメント・ビューア・監視設定のみ等で衛星受信に影響しない場合は ``""`` を返す。
    """
    all_prefixes = (
        "apps/satellite-tracker",
        "infrastructure/apps/satellite-tracker",
        "apps/satellite-analyzer/src/activities.py",
        "apps/satellite-analyzer/src/tle_resolver.py",
        "apps/satellite-analyzer/assets/satdump_tles.txt",
        "apps/satellite-analyzer/src/main.py",
        "apps/satellite-analyzer/Dockerfile",
        "apps/satellite-analyzer/requirements.txt",
    )
    for p in paths:
        if any(p.startswith(prefix) for prefix in all_prefixes):
            return "ALL"

    sats: set[str] = set()
    for p in paths:
        if p.endswith("/aprs_decoder.py") or p.endswith("/iss_dsp.py"):
            sats.add("ISS")
        elif p.endswith("/meteor_decoder.py"):
            sats.update(("METEOR-M2-3", "METEOR-M2-4"))
        elif p.endswith("/funcube_decoder.py"):
            sats.add("FUNCUBE-1")
    return ",".join(sorted(sats))


def _extract_pr_label(subject: str) -> str | None:
    m = re.search(r"from\s+[^/\s]+/(\S+)", subject)
    if m:
        return m.group(1)
    m_pr = re.search(r"#(\d+)", subject)
    if m_pr:
        return f"pr-{m_pr.group(1)}"
    return None


def collect_git_revisions(repo_root: Path, limit: int = 100) -> list[dict[str, Any]]:
    """git log から直近コミット・PR マージ履歴と変更スコープを収集する。"""
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "--first-parent",
                "--diff-merges=first-parent",
                f"-n{limit}",
                "--name-only",
                "--format=__COMMIT__%H%x1f%aI%x1f%s",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return []

    revisions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    paths: list[str] = []

    def _flush() -> None:
        if current is None:
            return
        current["changed_scope"] = _classify_changed_scope(paths)
        current["affected_satellites"] = _classify_affected_satellites(paths)
        revisions.append(current)

    for line in out.splitlines():
        if line.startswith("__COMMIT__"):
            _flush()
            paths = []
            payload = line[len("__COMMIT__") :]
            parts = payload.split("\x1f", 2)
            if len(parts) < 3:
                current = None
                continue
            sha, iso_dt, subject = parts
            current = {
                "commit_sha": sha,
                "pr_label": _extract_pr_label(subject),
                "deployed_at_utc": _parse_iso_dt(iso_dt),
                "summary": subject.strip(),
            }
        elif line.strip() and current is not None:
            paths.append(line.strip())

    _flush()
    return revisions


def sync_git_revisions(conn: duckdb.DuckDBPyConnection, repo_root: Path, limit: int = 100) -> int:
    revs = collect_git_revisions(repo_root, limit=limit)
    if not revs:
        return 0
    now_utc = datetime.now(timezone.utc)
    conn.execute("DELETE FROM raw_config_revisions")
    conn.executemany(
        """
        INSERT OR REPLACE INTO raw_config_revisions
        (commit_sha, pr_label, deployed_at_utc, changed_scope, affected_satellites, summary, synced_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["commit_sha"],
                r["pr_label"],
                r["deployed_at_utc"],
                r["changed_scope"],
                r["affected_satellites"],
                r["summary"],
                now_utc,
            )
            for r in revs
        ],
    )
    return len(revs)


def _fetch_json_batch(
    client: S3ClientProtocol,
    items: list[dict[str, Any]],
    max_workers: int,
    label: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not items:
        return []

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    total = len(items)
    started = time.monotonic()
    last_report = started

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as pool:
        future_map = {pool.submit(client.get_json, item["key"]): item for item in items}
        done_count = 0
        for fut in as_completed(future_map):
            done_count += 1
            item = future_map[fut]
            try:
                results.append((item, fut.result()))
            except Exception as exc:
                print(f"[warn] failed to fetch {item['key']}: {exc}", file=sys.stderr, flush=True)

            now = time.monotonic()
            if total >= 50 and (now - last_report >= 5.0 or done_count == total):
                elapsed = now - started
                rate = done_count / elapsed if elapsed > 0 else 0.0
                eta = (total - done_count) / rate if rate > 0 else 0.0
                pct = 100.0 * done_count / total
                print(
                    f"[sync:{label}] {done_count}/{total} ({pct:.1f}%) Elapsed={elapsed:.1f}s ETA={eta:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                last_report = now

    return results


def _existing_last_modified_map(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, datetime | None]:
    rows = conn.execute(f"SELECT s3_key, last_modified_utc FROM {table}").fetchall()
    return {row[0]: _normalize_dt(row[1]) for row in rows}


def sync_results_from_s3(
    conn: duckdb.DuckDBPyConnection,
    client: S3ClientProtocol,
    *,
    full: bool = False,
    max_workers: int = 16,
) -> dict[str, int]:
    """results/ 配下の summary.json と ml_inpainted/metadata.json を増分同期する。"""
    objs = client.list_objects("results/")
    pass_relpaths: dict[str, list[str]] = defaultdict(list)
    summary_objs: list[dict[str, Any]] = []
    inpaint_objs: list[dict[str, Any]] = []

    for obj in objs:
        key = obj["key"]
        parts = key.split("/")
        if len(parts) >= 4:
            pass_prefix = "/".join(parts[:3])
            rel = "/".join(parts[3:])
            pass_relpaths[pass_prefix].append(rel)
            if rel == "summary.json":
                summary_objs.append(obj)
            elif rel == "ml_inpainted/metadata.json":
                inpaint_objs.append(obj)

    existing_passes = {} if full else _existing_last_modified_map(conn, "raw_passes")
    existing_inpaint = {} if full else _existing_last_modified_map(conn, "raw_inpaint_runs")

    passes_to_fetch = [
        o
        for o in summary_objs
        if o["key"] not in existing_passes
        or _normalize_dt(o.get("last_modified")) != existing_passes.get(o["key"])
    ]
    inpaint_to_fetch = [
        o
        for o in inpaint_objs
        if o["key"] not in existing_inpaint
        or _normalize_dt(o.get("last_modified")) != existing_inpaint.get(o["key"])
    ]

    fetched_passes = _fetch_json_batch(client, passes_to_fetch, max_workers, "passes")
    fetched_inpaint = _fetch_json_batch(client, inpaint_to_fetch, max_workers, "inpaint")

    now_utc = datetime.now(timezone.utc)
    pass_rows = []
    for obj, data in fetched_passes:
        key = obj["key"]
        pass_prefix = key.rsplit("/", 1)[0]
        rels = pass_relpaths.get(pass_prefix, [])
        decoded_imgs = [r for r in rels if is_decoded_image_relpath(r)]
        pass_id = str(data.get("pass_id") or pass_prefix.split("/")[-1])
        obs_dt = parse_pass_datetime(pass_id, _normalize_dt(obj.get("last_modified")))
        pkt_raw = data.get("packets_count", data.get("packets_decoded", 0))
        pass_rows.append(
            (
                key,
                pass_prefix,
                pass_id,
                data.get("satellite"),
                data.get("satellite_type"),
                data.get("signal_type"),
                data.get("frequency_label"),
                data.get("status"),
                int(pkt_raw or 0),
                len(decoded_imgs) > 0,
                len(decoded_imgs),
                float(data["snr_db"]) if data.get("snr_db") is not None else None,
                float(data["actual_tca_drift_seconds"])
                if data.get("actual_tca_drift_seconds") is not None
                else None,
                float(data["duration_seconds"])
                if data.get("duration_seconds") is not None
                else (
                    float(data["recording_duration_sec"])
                    if data.get("recording_duration_sec") is not None
                    else None
                ),
                data.get("summary_text"),
                obs_dt,
                _normalize_dt(obj.get("last_modified")),
                "GOLDEN" in pass_id.upper() or "GOLDEN" in key.upper(),
                now_utc,
            )
        )

    if pass_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO raw_passes VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            pass_rows,
        )

    inpaint_rows = []
    for obj, data in fetched_inpaint:
        key = obj["key"]
        model = data.get("model") or {}
        stats = data.get("input_stats") or {}
        reg = data.get("registration") or {}
        qual = data.get("quality") or {}
        prev_sources = reg.get("prev_source_passes") or []
        mean_unc = qual.get("mean_uncertainty")
        if mean_unc is None:
            mean_unc = qual.get("uncertainty_mean")
        inpaint_rows.append(
            (
                key,
                data.get("pass_id"),
                data.get("satellite"),
                data.get("schema_version"),
                _parse_iso_dt(data.get("generated_at_utc")),
                data.get("device"),
                model.get("name"),
                model.get("mode"),
                model.get("quality"),
                model.get("bundle_file"),
                model.get("config_hash"),
                model.get("base_bundle_hash"),
                stats.get("height"),
                stats.get("width"),
                stats.get("available_channels"),
                stats.get("all_valid_pct"),
                stats.get("partial_valid_pct"),
                stats.get("missing_pct"),
                stats.get("any_valid_pct"),
                stats.get("max_gap_px"),
                reg.get("mode"),
                reg.get("clear_agree_pct"),
                reg.get("used_prev_atlas"),
                len(prev_sources) if isinstance(prev_sources, list) else 0,
                qual.get("tier"),
                qual.get("tier_label"),
                qual.get("seam_jump_l1"),
                float(mean_unc) if mean_unc is not None else None,
                qual.get("processing_sec"),
                _normalize_dt(obj.get("last_modified")),
                now_utc,
            )
        )

    if inpaint_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO raw_inpaint_runs VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            inpaint_rows,
        )

    return {
        "passes_total": len(summary_objs),
        "passes_synced": len(pass_rows),
        "inpaint_total": len(inpaint_objs),
        "inpaint_synced": len(inpaint_rows),
    }


def sync_galactic_from_s3(
    conn: duckdb.DuckDBPyConnection,
    client: S3ClientProtocol,
    *,
    full: bool = False,
    galactic_days: int | None = None,
    max_workers: int = 16,
) -> dict[str, int]:
    """galactic/ 配下の 21cm ドリフトスキャン JSON を増分同期する。"""
    json_objs: list[dict[str, Any]] = []
    if galactic_days is not None and galactic_days > 0:
        now_utc = datetime.now(timezone.utc)
        for offset in range(galactic_days + 1):
            day_str = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
            for obj in client.list_objects(f"galactic/{day_str}/"):
                if obj["key"].endswith(".json"):
                    json_objs.append(obj)
    else:
        for obj in client.list_objects("galactic/"):
            if obj["key"].endswith(".json"):
                json_objs.append(obj)

    existing = {} if full else _existing_last_modified_map(conn, "raw_galactic_scans")
    to_fetch = [o for o in json_objs if o["key"] not in existing]
    fetched = _fetch_json_batch(client, to_fetch, max_workers, "galactic")

    now_utc = datetime.now(timezone.utc)
    rows = []
    for obj, data in fetched:
        key = obj["key"]
        pointing = data.get("pointing") or {}
        rf = data.get("rf_metrics") or {}
        scan_id = key.rsplit("/", 1)[-1].removesuffix(".json")
        ts = _parse_iso_dt(data.get("timestamp_utc")) or parse_pass_datetime(
            scan_id, _normalize_dt(obj.get("last_modified"))
        )
        rows.append(
            (
                key,
                scan_id,
                ts,
                data.get("integration_sec"),
                bool(data.get("preempted", False)),
                pointing.get("az_deg"),
                pointing.get("el_deg"),
                pointing.get("lst_deg"),
                pointing.get("ra_deg"),
                pointing.get("dec_deg"),
                pointing.get("gal_l_deg"),
                pointing.get("gal_b_deg"),
                pointing.get("region_label"),
                pointing.get("sky_type"),
                data.get("lsr_correction_km_s"),
                rf.get("center_freq_on_hz"),
                rf.get("center_freq_off_hz"),
                rf.get("continuum_power_db"),
                rf.get("peak_line_snr_db"),
                rf.get("peak_v_lsr_km_s"),
                _normalize_dt(obj.get("last_modified")),
                now_utc,
            )
        )

    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO raw_galactic_scans VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )

    return {
        "galactic_total": len(json_objs),
        "galactic_synced": len(rows),
    }


def sync_all(
    db_path: Path,
    ontology_dir: Path,
    repo_root: Path,
    client: S3ClientProtocol | None,
    *,
    full: bool = False,
    galactic_days: int | None = None,
    skip_s3: bool = False,
    max_workers: int = 16,
) -> dict[str, Any]:
    """DuckDB への同期一式を実行する。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    try:
        ensure_tables(conn)
        ref_stats = sync_reference_tables(conn, ontology_dir)
        git_count = sync_git_revisions(conn, repo_root)
        s3_stats: dict[str, int] = {}
        if not skip_s3 and client is not None:
            s3_stats.update(sync_results_from_s3(conn, client, full=full, max_workers=max_workers))
            s3_stats.update(
                sync_galactic_from_s3(
                    conn,
                    client,
                    full=full,
                    galactic_days=galactic_days,
                    max_workers=max_workers,
                )
            )
        return {
            "db_path": str(db_path),
            "ref": ref_stats,
            "git_revisions": git_count,
            "s3": s3_stats,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Garage S3 observation metadata, git revisions, and ontology YAML into DuckDB."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB file path")
    parser.add_argument("--ontology-dir", type=Path, default=DEFAULT_ONTOLOGY_DIR, help="Ontology YAML dir")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Git repository root")
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
    parser.add_argument("--full", action="store_true", help="Re-fetch all objects ignoring incremental cache")
    parser.add_argument(
        "--galactic-days",
        type=int,
        default=None,
        help="Only list galactic scans from the last N days (default: sync all missing keys)",
    )
    parser.add_argument("--skip-s3", action="store_true", help="Sync only ontology YAML and git revisions")
    parser.add_argument("--max-workers", type=int, default=16, help="Parallel S3 fetch workers")
    args = parser.parse_args()

    client: SimpleS3Client | None = None
    if not args.skip_s3:
        ak = os.environ.get("GARAGE_ACCESS_KEY", "")
        sk = os.environ.get("GARAGE_SECRET_KEY", "")
        if not ak or not sk:
            ak, sk = load_credentials_from_k8s()
        if not ak or not sk:
            print(
                "Error: GARAGE_ACCESS_KEY / GARAGE_SECRET_KEY are not set and kubectl secret lookup failed.",
                file=sys.stderr,
            )
            return 1
        client = SimpleS3Client(
            endpoint_url=args.endpoint,
            bucket=args.bucket,
            access_key=ak,
            secret_key=sk,
            region=os.environ.get("GARAGE_REGION", "garage"),
        )

    summary = sync_all(
        db_path=args.db,
        ontology_dir=args.ontology_dir,
        repo_root=args.repo_root,
        client=client,
        full=args.full,
        galactic_days=args.galactic_days,
        skip_s3=args.skip_s3,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
