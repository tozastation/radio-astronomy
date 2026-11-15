#!/usr/bin/env python3
"""Prometheus と Kubernetes からシステム健全性メトリクスを収集し Markdown サマリを出力するスクリプト。

外部パッケージに依存せず Python 標準ライブラリのみで動作する。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


def resolve_prometheus_url() -> str:
    env_url = os.environ.get("PROMETHEUS_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    try:
        ip = subprocess.check_output(
            [
                "kubectl",
                "get",
                "svc",
                "-n",
                "monitoring",
                "kube-prometheus-stack-prometheus",
                "-o",
                "jsonpath={.spec.clusterIP}",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        if ip:
            return f"http://{ip}:9090"
    except Exception:
        pass
    return "http://10.43.126.153:9090"


def prom_get(base_url: str, path: str, timeout: int = 10) -> dict[str, Any]:
    url = f"{base_url}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def prom_query(base_url: str, expr: str) -> list[dict[str, Any]]:
    qs = urllib.parse.urlencode({"query": expr})
    data = prom_get(base_url, f"/api/v1/query?{qs}")
    if data.get("status") != "success":
        return []
    return data.get("data", {}).get("result", [])


def run_cmd(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=timeout).strip()
    except Exception as exc:
        return f"[error] {' '.join(cmd)}: {exc}"


def collect_k8s_summary() -> dict[str, Any]:
    nodes_out = run_cmd(["kubectl", "get", "nodes", "-o", "wide"])
    pods_json_raw = run_cmd(["kubectl", "get", "pods", "-A", "-o", "json"])
    unhealthy_pods: list[str] = []
    restarted_pods: list[str] = []

    try:
        pods_data = json.loads(pods_json_raw)
        for item in pods_data.get("items", []):
            meta = item.get("metadata", {})
            ns = meta.get("namespace", "-")
            name = meta.get("name", "-")
            status = item.get("status", {})
            phase = status.get("phase", "Unknown")

            if phase not in ("Running", "Succeeded"):
                unhealthy_pods.append(f"{ns}/{name} (phase={phase})")

            for cs in status.get("containerStatuses", []):
                restarts = int(cs.get("restartCount", 0) or 0)
                ready = bool(cs.get("ready", False))
                if not ready and phase == "Running":
                    unhealthy_pods.append(f"{ns}/{name}/{cs.get('name')} (not ready)")
                if restarts > 0:
                    restarted_pods.append(f"{ns}/{name}/{cs.get('name')} (restarts={restarts})")
    except Exception as exc:
        unhealthy_pods.append(f"failed to parse pods json: {exc}")

    return {
        "nodes": nodes_out,
        "unhealthy_pods": unhealthy_pods,
        "restarted_pods": restarted_pods,
    }


def format_markdown(prom_url: str) -> str:
    now_utc = datetime.now(timezone.utc)
    lines: list[str] = [
        "# システム健全性・メトリクスサマリ",
        "",
        f"- 収集時刻 (UTC): `{now_utc.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Prometheus: `{prom_url}`",
        "",
    ]

    # 1. Kubernetes ノード & Pod
    k8s = collect_k8s_summary()
    lines.append("## 1. Kubernetes ノード・Pod 状態")
    lines.append("")
    lines.append("```text")
    lines.append(k8s["nodes"])
    lines.append("```")
    lines.append("")
    if k8s["unhealthy_pods"]:
        lines.append("### 異常 / NotReady Pod")
        for p in k8s["unhealthy_pods"]:
            lines.append(f"- `{p}`")
    else:
        lines.append("- 異常 / NotReady Pod: なし")
    lines.append("")
    if k8s["restarted_pods"]:
        lines.append("### 再起動履歴のあるコンテナ (`restartCount > 0`)")
        for p in k8s["restarted_pods"]:
            lines.append(f"- `{p}`")
        lines.append("")

    # 2. ホスト CPU 負荷・メモリ・ディスク使用率
    lines.append("## 2. ホストリソース (`uptime`, `free -h`, `df -h / /tmp`)")
    lines.append("")
    lines.append("```text")
    lines.append(run_cmd(["uptime"]))
    lines.append(run_cmd(["free", "-h"]))
    lines.append(run_cmd(["df", "-h", "/", "/tmp"]))
    lines.append("```")
    lines.append("")

    # 3. Prometheus アラート & ターゲット
    lines.append("## 3. Prometheus アラートとターゲット")
    lines.append("")
    try:
        alerts_resp = prom_get(prom_url, "/api/v1/alerts")
        alerts = alerts_resp.get("data", {}).get("alerts", [])
        noise_count = 0
        active_alerts: list[dict[str, Any]] = []
        for a in alerts:
            labels = a.get("labels", {})
            if labels.get("severity") == "none" and labels.get("alertname") in ("Watchdog", "InfoInhibitor"):
                noise_count += 1
                continue
            active_alerts.append(a)

        lines.append(f"- 常時監視用アラート (`Watchdog` / `InfoInhibitor`, `severity=none`): {noise_count} 件")
        if not active_alerts:
            lines.append("- その他の発火中アラート: なし")
        else:
            lines.append("")
            lines.append("| state | severity | alertname | target / detail |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for a in active_alerts:
                labels = a.get("labels", {})
                state = a.get("state", "-")
                sev = labels.get("severity", "-")
                name = labels.get("alertname", "-")
                detail_parts = [
                    f"{k}={v}"
                    for k, v in sorted(labels.items())
                    if k not in ("alertname", "severity")
                ]
                detail = ", ".join(detail_parts[:5])
                lines.append(f"| {state} | {sev} | {name} | `{detail}` |")
    except Exception as exc:
        lines.append(f"- アラート取得エラー: `{exc}`")
    lines.append("")

    # ダウン中のスクレイプターゲット (up == 0)
    try:
        down_targets = prom_query(prom_url, "up == 0")
        if not down_targets:
            lines.append("- ダウン中のスクレイプターゲット (`up == 0`): なし")
        else:
            lines.append("### ダウン中のスクレイプターゲット (`up == 0`)")
            for t in down_targets:
                m = t.get("metric", {})
                lines.append(
                    f"- job=`{m.get('job', '-')}`, instance=`{m.get('instance', '-')}`, node=`{m.get('node', '-')}`"
                )
    except Exception as exc:
        lines.append(f"- ターゲット取得エラー: `{exc}`")
    lines.append("")

    # 4. ノード CPU / メモリ使用率 (Prometheus node-exporter)
    lines.append("## 4. ノードリソース指標 (Prometheus)")
    lines.append("")
    try:
        cpu_res = prom_query(
            prom_url,
            '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
        )
        mem_res = prom_query(
            prom_url,
            '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
        )
        for item in cpu_res:
            inst = item.get("metric", {}).get("instance", "-")
            val = float(item["value"][1])
            lines.append(f"- CPU 使用率 (`{inst}`): {val:.1f}%")
        for item in mem_res:
            inst = item.get("metric", {}).get("instance", "-")
            val = float(item["value"][1])
            lines.append(f"- メモリ使用率 (`{inst}`): {val:.1f}%")
        if not cpu_res and not mem_res:
            lines.append("- node-exporter メトリクスは取得できませんでした。")
    except Exception as exc:
        lines.append(f"- リソース指標取得エラー: `{exc}`")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Prometheus and Kubernetes system health metrics.")
    parser.parse_args()
    prom_url = resolve_prometheus_url()
    print(format_markdown(prom_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
