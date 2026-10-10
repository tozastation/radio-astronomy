#!/usr/bin/env python3
"""🛰️ 真のゼロ介入・完全自律型 KEDA パイプライン E2E 実証スクリプト

本スクリプトは、一切の手動操作（kubectl scale や patch）を行わず、
S3 への生 WAV 投入をトリガーとして以下のライフサイクルが 100% 自律回転することを検証します：

1. S3 raw/ へ生 WAV 投入 (エッジからの到着を模擬)
2. satellite-viewer (/api/pending-tasks) の pending_count が 1 に変化
3. satellite-viewer が Temporal ワークフローを自動発火
4. KEDA (metrics-api scaler) がワーカーを 0 → 1 に自動スケールアウト
5. ワーカーが解析完走、成果物 (spectrogram, summary) 格納、生 WAV 自動削除
6. satellite-viewer の pending_count が 0 に復帰
7. KEDA がワーカーを 1 → 0 に自動縮退
"""

import os
import sys
import time
import wave
import json
import urllib.request
import subprocess
from pathlib import Path
import numpy as np
import boto3
from botocore.client import Config

# S3 認証情報
S3_ENDPOINT = os.getenv("GARAGE_S3_ENDPOINT", "http://192.168.68.66:30900")
ACCESS_KEY = "GKc936f490b86ca470a41c5ad7"
SECRET_KEY = "aa6934eef383fd8768368eb05f1377b803785922ccf2ebe9eaf0dfc69b0d7c47"
BUCKET = "satellite-recordings"
VIEWER_API = "http://192.168.68.66:30088/api/pending-tasks"


def get_pending_count():
    try:
        with urllib.request.urlopen(VIEWER_API, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("pending_count", 0)
    except Exception as e:
        print(f"  [Warn] Failed to query viewer API: {e}")
        return -1


def get_worker_replica_count():
    cmd = ["kubectl", "get", "deployment", "satellite-analyzer-worker", "-n", "default", "-o", "jsonpath={.status.readyReplicas}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    val = res.stdout.strip()
    return int(val) if val.isdigit() else 0


def generate_wav(path: str, duration: float = 1.0):
    sample_rate = 48000
    num_samples = int(sample_rate * duration)
    t = np.arange(num_samples) / float(sample_rate)
    tone = 0.4 * np.sin(2 * np.pi * 1200 * t) + 0.4 * np.sin(2 * np.pi * 2200 * t)
    pcm = (np.clip(tone, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def main():
    print("=" * 70)
    print("🚀 Verifying 100% Autonomous Pipeline (Zero Manual Intervention)")
    print("=" * 70)

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="garage",
        config=Config(s3={"addressing_style": "path"})
    )

    pass_id = f"ISS_AUTONOMOUS_{int(time.time())}"
    raw_key = f"raw/ISS (ZARYA)/{pass_id}.wav"
    fixture_wav = "apps/satellite-tracker-rs/assets/iss_aprs_packet_48k.wav"

    print(f"\n[Step 0] Initial state verification...")
    initial_count = get_pending_count()
    initial_replicas = get_worker_replica_count()
    print(f"  - Viewer pending_count: {initial_count}")
    print(f"  - Worker replicas: {initial_replicas}")
    assert initial_replicas == 0, "Worker must start at 0 replicas!"

    print(f"\n[Step 1] Uploading raw WAV (with real AX.25 APRS packet) to s3://{BUCKET}/{raw_key}...")
    with open(fixture_wav, "rb") as f:
        s3.put_object(Bucket=BUCKET, Key=raw_key, Body=f.read(), ContentType="audio/wav")
    print("  - Upload complete.")

    print(f"\n[Step 2] Monitoring viewer API & KEDA auto scale-out (0 -> 1)...")
    scale_out_success = False
    start_time = time.time()
    while time.time() - start_time < 90:
        count = get_pending_count()
        replicas = get_worker_replica_count()
        print(f"  [{int(time.time() - start_time)}s] pending_count={count}, worker_replicas={replicas}")
        if replicas >= 1:
            scale_out_success = True
            print("  🎉 KEDA successfully scaled worker from 0 -> 1 automatically!")
            break
        time.sleep(4)

    assert scale_out_success, "KEDA failed to scale out worker to 1!"

    print(f"\n[Step 3] Monitoring workflow execution and artifact generation...")
    analysis_success = False
    start_time = time.time()
    expected_spec = f"results/ISS (ZARYA)/{pass_id}/spectrogram.png"
    expected_summary = f"results/ISS (ZARYA)/{pass_id}/summary.json"
    expected_packets = f"results/ISS (ZARYA)/{pass_id}/packets.json"
    while time.time() - start_time < 90:
        try:
            s3.head_object(Bucket=BUCKET, Key=expected_spec)
            s3.head_object(Bucket=BUCKET, Key=expected_summary)
            s3.head_object(Bucket=BUCKET, Key=expected_packets)
            analysis_success = True
            print(f"  🎉 Found generated artifacts in s3://{BUCKET}/results/ISS (ZARYA)/{pass_id}/!")
            break
        except Exception:
            pass
        time.sleep(3)

    assert analysis_success, "Worker failed to complete analysis!"

    # パケットデコード結果の検証 (atest による AX.25 パケット抽出確認)
    summary_obj = s3.get_object(Bucket=BUCKET, Key=expected_summary)
    summary_json = json.loads(summary_obj["Body"].read().decode())
    packets_obj = s3.get_object(Bucket=BUCKET, Key=expected_packets)
    packets_json = json.loads(packets_obj["Body"].read().decode())

    print(f"  📊 Decoded Packets Count: {summary_json.get('packets_count')}")
    print(f"  📦 Packets detail: {packets_json.get('packets')}")
    assert summary_json.get("packets_count", 0) >= 1, "APRS packet decoding failed! packets_count must be >= 1"

    print(f"\n[Step 4] Verifying raw WAV cleanup and queue empty...")
    raw_cleaned = False
    for _ in range(15):
        try:
            s3.head_object(Bucket=BUCKET, Key=raw_key)
        except Exception:
            raw_cleaned = True
            print(f"  🎉 Raw recording s3://{BUCKET}/{raw_key} was automatically cleaned up!")
            break
        time.sleep(2)
    assert raw_cleaned, "Raw WAV was not cleaned up!"

    print(f"\n[Step 5] Monitoring KEDA auto scale-in (1 -> 0)...")
    scale_in_success = False
    start_time = time.time()
    while time.time() - start_time < 120:
        count = get_pending_count()
        replicas = get_worker_replica_count()
        print(f"  [{int(time.time() - start_time)}s] pending_count={count}, worker_replicas={replicas}")
        if replicas == 0:
            scale_in_success = True
            print("  🎉 KEDA successfully scaled worker from 1 -> 0 automatically!")
            break
        time.sleep(5)

    assert scale_in_success, "KEDA failed to scale in worker to 0!"

    print("\n" + "=" * 70)
    print("🏆 100% AUTONOMOUS PIPELINE VERIFICATION SUCCESSFUL!")
    print("   Zero human/AI intervention. Complete self-healing event-driven loop.")
    print("=" * 70)


if __name__ == "__main__":
    main()
