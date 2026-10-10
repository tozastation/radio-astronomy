import os
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any
from temporalio import activity

from aprs_decoder import APRSDecoder
from spectrogram_generator import SpectrogramGenerator

logger = logging.getLogger(__name__)

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


def _get_s3_client():
    if not HAS_BOTO3:
        raise RuntimeError("boto3 is required.")
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


@activity.defn(name="download_recording_activity")
async def download_recording_activity(s3_key: str) -> str:
    """Garage S3 から対象の WAV ファイルをダウンロードする"""
    bucket = os.getenv("GARAGE_BUCKET", "satellite-recordings")
    s3 = _get_s3_client()
    tmp_dir = tempfile.mkdtemp(prefix="pass_")
    local_path = os.path.join(tmp_dir, os.path.basename(s3_key))

    logger.info(f"Downloading s3://{bucket}/{s3_key} to {local_path}...")
    s3.download_file(bucket, s3_key, local_path)
    return local_path


def classify_satellite(satellite: str) -> Dict[str, str]:
    sat_upper = satellite.upper()
    if "ISS" in sat_upper:
        return {
            "satellite_type": "SpaceStation",
            "signal_type": "APRS / AX.25 (1200bps AFSK)",
            "frequency_label": "145.825 MHz",
            "category": "amateur_packet",
            "display_icon": "🚀",
        }
    elif "METEOR" in sat_upper:
        return {
            "satellite_type": "WeatherSatellite",
            "signal_type": "LRPT (QPSK 72kbps)",
            "frequency_label": "137.900 MHz",
            "category": "weather_lrpt",
            "display_icon": "🛰️",
        }
    elif "FUNCUBE" in sat_upper or "AO-73" in sat_upper:
        return {
            "satellite_type": "CubeSat",
            "signal_type": "BPSK (1200bps Telemetry)",
            "frequency_label": "145.935 MHz",
            "category": "cubesat_telemetry",
            "display_icon": "📻",
        }
    else:
        return {
            "satellite_type": "GenericSatellite",
            "signal_type": "Audio / RF Spectrum",
            "frequency_label": "Unknown",
            "category": "generic",
            "display_icon": "📡",
        }


@activity.defn(name="decode_packets_activity")
async def decode_packets_activity(wav_path: str, satellite: str = "") -> Dict[str, Any]:
    """WAV ファイルから衛星種別に応じて信号解析またはパケットデコードを実施する"""
    info = classify_satellite(satellite)
    category = info["category"]

    if category == "amateur_packet" or not satellite:
        decoder = APRSDecoder()
        frames = decoder.decode_wav(wav_path)
        packets_count = len(frames)
        packets = [f.to_dict() for f in frames]
        summary_text = f"APRS パケット {packets_count} 件検出"
    elif category == "weather_lrpt":
        packets_count = 0
        packets = []
        summary_text = "METEOR-M2 4 気象衛星 LRPT (QPSK 72kbps) 信号受信完了 (SatDump 連携準備中)"
    elif category == "cubesat_telemetry":
        packets_count = 0
        packets = []
        summary_text = "FUNcube-1 (AO-73) CubeSat BPSK (1200bps) テレメトリ信号受信完了"
    else:
        packets_count = 0
        packets = []
        summary_text = "信号受信完了 (スペクトログラム生成)"

    return {
        "satellite": satellite,
        "satellite_type": info["satellite_type"],
        "signal_type": info["signal_type"],
        "frequency_label": info["frequency_label"],
        "display_icon": info["display_icon"],
        "packets_count": packets_count,
        "packets": packets,
        "summary_text": summary_text,
    }


@activity.defn(name="generate_spectrogram_activity")
async def generate_spectrogram_activity(wav_path: str) -> str:
    """WAV ファイルからスペクトログラム画像を生成する"""
    generator = SpectrogramGenerator()
    out_png = os.path.splitext(wav_path)[0] + "_spectrogram.png"
    generator.generate(wav_path, out_png, title="Satellite Pass Analysis")
    return out_png


@activity.defn(name="save_results_activity")
async def save_results_activity(params: Dict[str, Any]) -> bool:
    """解析成果物 (packets.json, spectrogram.png, summary.json) を Garage S3 に保存する"""
    bucket = os.getenv("GARAGE_BUCKET", "satellite-recordings")
    s3 = _get_s3_client()

    satellite = params["satellite"]
    pass_id = params["pass_id"]
    packets_data = params.get("packets_data", {})
    spectrogram_png_path = params.get("spectrogram_png_path", "")

    prefix = f"results/{satellite}/{pass_id}"

    # 1. packets.json アップロード
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(packets_data, f, indent=2, ensure_ascii=False)
        packets_tmp = f.name
    s3.upload_file(packets_tmp, bucket, f"{prefix}/packets.json")
    os.remove(packets_tmp)

    # 2. spectrogram.png アップロード
    if os.path.exists(spectrogram_png_path):
        s3.upload_file(spectrogram_png_path, bucket, f"{prefix}/spectrogram.png")

    # 3. summary.json アップロード
    summary_data = {
        "satellite": satellite,
        "pass_id": pass_id,
        "satellite_type": packets_data.get("satellite_type", "Unknown"),
        "signal_type": packets_data.get("signal_type", "Audio / RF"),
        "frequency_label": packets_data.get("frequency_label", "-"),
        "display_icon": packets_data.get("display_icon", "📡"),
        "packets_count": packets_data.get("packets_count", 0),
        "summary_text": packets_data.get("summary_text", ""),
        "status": "completed",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
        summary_tmp = f.name
    s3.upload_file(summary_tmp, bucket, f"{prefix}/summary.json")
    os.remove(summary_tmp)

    return True


@activity.defn(name="cleanup_raw_recording_activity")
async def cleanup_raw_recording_activity(s3_key: str) -> bool:
    """S3 上の元 WAV ファイルを削除してストレージをクリーンアップする"""
    bucket = os.getenv("GARAGE_BUCKET", "satellite-recordings")
    s3 = _get_s3_client()
    logger.info(f"Deleting s3://{bucket}/{s3_key}...")
    s3.delete_object(Bucket=bucket, Key=s3_key)
    return True
