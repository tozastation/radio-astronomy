import argparse
import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from analyzer import PassAnalyzer

logger = logging.getLogger("satellite-analyzer-worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


def get_s3_client():
    if not HAS_BOTO3:
        raise RuntimeError("boto3 is not available.")
    endpoint_url = os.getenv("GARAGE_S3_ENDPOINT", "http://garage-s3.storage.svc.cluster.local:3900")
    access_key = os.getenv("GARAGE_ACCESS_KEY", "")
    secret_key = os.getenv("GARAGE_SECRET_KEY", "")
    region = os.getenv("GARAGE_REGION", "garage")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def process_pass_from_s3(
    s3_key: str,
    satellite: str,
    pass_id: str,
    bucket_name: Optional[str] = None,
    keep_raw: bool = False,
) -> dict:
    """Garage S3 から WAV をダウンロードし、解析成果物をアップロードして元ファイルを削除する"""
    bucket = bucket_name or os.getenv("GARAGE_BUCKET", "satellite-recordings")
    s3 = get_s3_client()
    analyzer = PassAnalyzer()

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_wav = Path(tmp_dir) / Path(s3_key).name
        logger.info(f"Downloading s3://{bucket}/{s3_key} to {local_wav}...")
        s3.download_file(bucket, s3_key, str(local_wav))

        # 解析実行
        results_dir = Path(tmp_dir) / "results"
        summary = analyzer.analyze_pass(
            wav_path=str(local_wav),
            satellite=satellite,
            pass_id=pass_id,
            output_dir=str(results_dir),
        )

        # 成果物の S3 アップロード (results/{satellite}/{pass_id}/...)
        s3_results_prefix = f"results/{satellite}/{pass_id}"
        for artifact_path in results_dir.glob("*"):
            target_key = f"{s3_results_prefix}/{artifact_path.name}"
            logger.info(f"Uploading artifact {artifact_path.name} to s3://{bucket}/{target_key}...")
            s3.upload_file(str(artifact_path), bucket, target_key)

        # 元の raw WAV ファイルの削除 (ストレージクリーンアップ)
        if not keep_raw:
            logger.info(f"Cleaning up raw WAV: s3://{bucket}/{s3_key}...")
            s3.delete_object(Bucket=bucket, Key=s3_key)

        logger.info(f"Successfully processed pass {pass_id} for {satellite}.")
        return summary


async def run_temporal_worker():
    """Temporal Worker モードで起動し、Task Queue をリッスンする"""
    from temporalio.client import Client
    from temporalio.worker import Worker
    from workflows import AnalyzeSatellitePassWorkflow
    from activities import (
        download_recording_activity,
        decode_packets_activity,
        generate_spectrogram_activity,
        save_results_activity,
        cleanup_raw_recording_activity,
    )

    temporal_host = os.getenv("TEMPORAL_HOST", "temporal-server.temporal.svc.cluster.local:7233")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "satellite-analysis")

    logger.info(f"Connecting to Temporal Server at {temporal_host}...")
    client = await Client.connect(temporal_host)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[AnalyzeSatellitePassWorkflow],
        activities=[
            download_recording_activity,
            decode_packets_activity,
            generate_spectrogram_activity,
            save_results_activity,
            cleanup_raw_recording_activity,
        ],
    )
    logger.info(f"Temporal Worker started. Listening on task queue: '{task_queue}'...")
    await worker.run()


def main():
    parser = argparse.ArgumentParser(description="Satellite Analyzer Worker")
    parser.add_argument("--mode", choices=["standalone", "temporal"], default=os.getenv("MODE", "standalone"))
    parser.add_argument("--s3-key", help="Raw WAV S3 object key (standalone mode)")
    parser.add_argument("--satellite", help="Satellite name (standalone mode)")
    parser.add_argument("--pass-id", help="Pass ID (standalone mode)")
    parser.add_argument("--keep-raw", action="store_true", help="Keep raw recording in S3")

    args = parser.parse_args()

    if args.mode == "temporal":
        asyncio.run(run_temporal_worker())
    else:
        if not args.s3_key or not args.satellite or not args.pass_id:
            logger.error("--s3-key, --satellite, and --pass-id are required in standalone mode.")
            return
        process_pass_from_s3(
            s3_key=args.s3_key,
            satellite=args.satellite,
            pass_id=args.pass_id,
            keep_raw=args.keep_raw,
        )


if __name__ == "__main__":
    main()
