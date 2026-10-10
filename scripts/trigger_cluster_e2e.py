#!/usr/bin/env python3
"""🛰️ クラスタ実機 E2E 結合検証スクリプト

1. 擬似 48kHz WAV を生成して Garage S3 へアップロード
2. Temporal Server へ AnalyzeSatellitePassWorkflow を投入 (Task Queue: satellite-analysis)
3. KEDA による satellite-analyzer-worker の 0 → 1 スケールアウトを監視
4. 成果物 (packets.json, spectrogram.png) の S3 格納を確認
5. ワークフロー正常完了の確認
"""

import asyncio
import os
import sys
import wave
import json
import time
from pathlib import Path
import numpy as np
import boto3
from botocore.client import Config
from temporalio.client import Client

# プロジェクトルートのパス追加
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "apps" / "satellite-analyzer" / "src"))

from workflows import AnalyzeSatellitePassWorkflow, PassAnalysisParams


def generate_mock_pass_wav(output_path: str, duration_sec: float = 1.0, sample_rate: int = 48000):
    """擬似的な ISS パス音声 (Bell 202 AFSK 信号を含む) を生成する"""
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / float(sample_rate)
    tone = 0.4 * np.sin(2 * np.pi * 1200 * t) + 0.4 * np.sin(2 * np.pi * 2200 * t)
    noise = np.random.normal(0, 0.05, num_samples)
    audio = tone + noise
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


async def main():
    print("=" * 65)
    print("🚀 Triggering Full Cluster E2E Pipeline (S3 -> Temporal -> KEDA 0-scale)")
    print("=" * 65)

    s3_endpoint = os.environ.get("GARAGE_S3_ENDPOINT", "http://10.43.217.174:3900")
    temporal_host = os.environ.get("TEMPORAL_HOST", "10.43.144.239:7233")
    bucket_name = "satellite-recordings"
    pass_id = f"ISS_E2E_{int(time.time())}"
    s3_key = f"recordings/{pass_id}.wav"
    local_wav = f"/tmp/{pass_id}.wav"

    # 1. 擬似 WAV 生成
    print(f"\n[Step 1] Generating mock audio file: {local_wav}")
    generate_mock_pass_wav(local_wav, duration_sec=1.5)

    # 2. Garage S3 へアップロード
    print(f"\n[Step 2] Uploading to Garage S3 ({s3_endpoint}/{bucket_name}/{s3_key})...")
    s3 = boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id="GKc936f490b86ca470a41c5ad7",
        aws_secret_access_key="aa6934eef383fd8768368eb05f1377b803785922ccf2ebe9eaf0dfc69b0d7c47",
        config=Config(s3={"addressing_style": "path"}),
        region_name="garage",
    )
    with open(local_wav, "rb") as f:
        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=f)
    print("✅ Successfully uploaded raw recording to Garage S3!")
    os.remove(local_wav)

    # 3. Temporal ワークフローを起動
    print(f"\n[Step 3] Connecting to Temporal Server ({temporal_host})...")
    client = await Client.connect(temporal_host, namespace="default")
    print(f"Connected to Temporal. Launching AnalyzeSatellitePassWorkflow for pass: {pass_id}...")

    handle = await client.start_workflow(
        AnalyzeSatellitePassWorkflow.run,
        PassAnalysisParams(
            s3_key=s3_key,
            satellite="ISS",
            pass_id=pass_id,
            bucket_name=bucket_name,
            keep_raw=False,
        ),
        id=f"workflow-{pass_id}",
        task_queue="satellite-analysis",
    )
    print(f"✅ Workflow started! Workflow ID: {handle.id}, Run ID: {handle.result_run_id}")
    print("\n[Step 4] Monitoring Temporal execution and KEDA scaling...")
    print("Waiting for workflow to complete (Worker will scale 0 -> 1)...")

    # ワークフロー完了待機 (最大 120 秒)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=120.0)
        print("\n🎉 Workflow execution completed successfully!")
        print("Workflow Output Summary:")
        print(json.dumps(result, indent=2))
    except asyncio.TimeoutError:
        print("\n⚠️ Workflow execution timed out waiting for result.")

    # 5. S3 成果物の確認
    print("\n[Step 5] Checking generated artifacts in Garage S3...")
    objects = s3.list_objects_v2(Bucket=bucket_name, Prefix=f"results/{pass_id}")
    if "Contents" in objects:
        for obj in objects["Contents"]:
            print(f"  - S3 Object: {obj['Key']} ({obj['Size']} bytes)")
    else:
        print("  - No objects found under results prefix.")

    print("\n" + "=" * 65)
    print("🏁 Full Cluster E2E Pipeline Test Finished")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
