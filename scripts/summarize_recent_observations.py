#!/usr/bin/env python3
"""直近の観測結果 (Garage S3: results/, galactic/, raw/) を集計し Markdown サマリを出力するスクリプト。

外部パッケージ (boto3 等) に依存せず、Python 標準ライブラリのみで AWS SigV4 署名付き S3 リクエストを実行する。
"""

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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any


S3_XML_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


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
        timeout_sec: int = 10,
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


def parse_pass_datetime(key: str, fallback: datetime | None) -> datetime | None:
    m = re.search(r"(\d{8})_(\d{6})", key)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return fallback


def fetch_json_parallel(
    client: SimpleS3Client, keys: list[str], max_workers: int = 16
) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    if not keys:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(client.get_json, key): key for key in keys}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                results.append((key, fut.result()))
            except Exception as exc:
                print(f"[warn] failed to fetch {key}: {exc}", file=sys.stderr)
    return results


def summarize_satellite_results(
    summaries: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    by_satellite: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "with_packets": 0,
            "zero_packets": 0,
            "packets_sum": 0,
            "types": Counter(),
            "samples": [],
        }
    )

    for key, data in summaries:
        sat = str(data.get("satellite", "UNKNOWN"))
        pkt_count = int(data.get("packets_count", data.get("packets_decoded", 0)) or 0)
        sat_type = str(data.get("satellite_type", "Unknown"))
        summary_text = str(data.get("summary_text", "")).strip()

        entry = by_satellite[sat]
        entry["total"] += 1
        entry["packets_sum"] += pkt_count
        entry["types"][sat_type] += 1
        if pkt_count > 0:
            entry["with_packets"] += 1
        else:
            entry["zero_packets"] += 1
        if len(entry["samples"]) < 3 and summary_text:
            entry["samples"].append(f"{data.get('pass_id', key)}: {summary_text[:120]}")

    return dict(by_satellite)


def summarize_galactic_scans(
    scans: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    by_sky_type: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "preempted": 0,
            "snr_db": [],
            "continuum_db": [],
            "peak_v_lsr": [],
        }
    )

    for _, data in scans:
        pointing = data.get("pointing", {})
        rf = data.get("rf_metrics", {})
        sky_type = str(pointing.get("sky_type", "UNKNOWN"))
        entry = by_sky_type[sky_type]
        entry["count"] += 1
        if data.get("preempted"):
            entry["preempted"] += 1
        if "peak_line_snr_db" in rf:
            entry["snr_db"].append(float(rf["peak_line_snr_db"]))
        if "continuum_power_db" in rf:
            entry["continuum_db"].append(float(rf["continuum_power_db"]))
        if "peak_v_lsr_km_s" in rf:
            entry["peak_v_lsr"].append(float(rf["peak_v_lsr_km_s"]))

    return dict(by_sky_type)


def format_markdown_report(
    days: int,
    since_utc: datetime,
    now_utc: datetime,
    sat_stats: dict[str, Any],
    gal_stats: dict[str, Any],
    raw_objects: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"# 直近 {days} 日間の観測サマリ")
    lines.append("")
    lines.append(
        f"- 集計期間 (UTC): `{since_utc.strftime('%Y-%m-%d %H:%M')}` 〜 `{now_utc.strftime('%Y-%m-%d %H:%M')}`"
    )
    lines.append(f"- 未処理 `raw/` オブジェクト数: {len(raw_objects)}")
    lines.append("")

    lines.append("## 1. 衛星パス解析 (`results/` の `summary.json`)")
    lines.append("")
    if not sat_stats:
        lines.append("対象期間内の解析結果はありませんでした。")
    else:
        lines.append("| 衛星 | パス数 | 復号成功 (`packets > 0`) | 0 パケット | 成功率 | 総パケット数 | 主な種別 |")
        lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | :--- |")
        for sat in sorted(sat_stats.keys()):
            st = sat_stats[sat]
            total = st["total"]
            ok = st["with_packets"]
            rate = (ok / total * 100.0) if total > 0 else 0.0
            top_type = st["types"].most_common(1)[0][0] if st["types"] else "-"
            lines.append(
                f"| {sat} | {total} | {ok} | {st['zero_packets']} | {rate:.1f}% | {st['packets_sum']} | {top_type} |"
            )

        lines.append("")
        lines.append("### サマリテキスト抜粋")
        for sat in sorted(sat_stats.keys()):
            for sample in sat_stats[sat]["samples"]:
                lines.append(f"- `{sat}`: {sample}")

    lines.append("")
    lines.append("## 2. 天の川銀河 21cm ドリフトスキャン (`galactic/`)")
    lines.append("")
    if not gal_stats:
        lines.append("対象期間内の `galactic/` スキャン結果はありませんでした。")
    else:
        lines.append("| sky_type | スキャン数 | 中断 (`preempted`) | SNR 平均 / 最大 (dB) | Continuum 平均 (dB) |")
        lines.append("| :--- | ---: | ---: | :--- | ---: |")
        for sky_type in sorted(gal_stats.keys()):
            st = gal_stats[sky_type]
            snr_list = st["snr_db"]
            cont_list = st["continuum_db"]
            snr_str = (
                f"{mean(snr_list):.2f} / {max(snr_list):.2f}" if snr_list else "-"
            )
            cont_str = f"{mean(cont_list):.2f}" if cont_list else "-"
            lines.append(
                f"| {sky_type} | {st['count']} | {st['preempted']} | {snr_str} | {cont_str} |"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize recent radio-astronomy observations from Garage S3.")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
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
    parser.add_argument("--max-galactic", type=int, default=200, help="Max galactic JSONs to fetch")
    args = parser.parse_args()

    access_key = os.environ.get("GARAGE_ACCESS_KEY", "")
    secret_key = os.environ.get("GARAGE_SECRET_KEY", "")
    if not access_key or not secret_key:
        access_key, secret_key = load_credentials_from_k8s()

    if not access_key or not secret_key:
        print(
            "Error: GARAGE_ACCESS_KEY / GARAGE_SECRET_KEY are not set and kubectl secret lookup failed.",
            file=sys.stderr,
        )
        return 1

    client = SimpleS3Client(
        endpoint_url=args.endpoint,
        bucket=args.bucket,
        access_key=access_key,
        secret_key=secret_key,
        region=os.environ.get("GARAGE_REGION", "garage"),
    )

    now_utc = datetime.now(timezone.utc)
    since_utc = now_utc - timedelta(days=args.days)

    # 1. results/ の summary.json を列挙して期間内のみ取得
    result_objs = client.list_objects("results/")
    summary_keys = []
    for obj in result_objs:
        if not obj["key"].endswith("/summary.json"):
            continue
        dt = parse_pass_datetime(obj["key"], obj["last_modified"])
        if dt is None or dt >= since_utc:
            summary_keys.append(obj["key"])

    summaries = fetch_json_parallel(client, summary_keys)
    sat_stats = summarize_satellite_results(summaries)

    # 2. galactic/ の直近 N 日分の日付プレフィックスから JSON を取得
    galactic_keys: list[str] = []
    for offset in range(args.days + 1):
        day_str = (now_utc - timedelta(days=offset)).strftime("%Y-%m-%d")
        day_objs = client.list_objects(f"galactic/{day_str}/")
        for obj in day_objs:
            if obj["key"].endswith(".json"):
                galactic_keys.append(obj["key"])

    galactic_keys = sorted(galactic_keys, reverse=True)[: args.max_galactic]
    scans = fetch_json_parallel(client, galactic_keys)
    gal_stats = summarize_galactic_scans(scans)

    # 3. raw/ の滞留確認
    raw_objects = client.list_objects("raw/")

    print(
        format_markdown_report(
            days=args.days,
            since_utc=since_utc,
            now_utc=now_utc,
            sat_stats=sat_stats,
            gal_stats=gal_stats,
            raw_objects=raw_objects,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
