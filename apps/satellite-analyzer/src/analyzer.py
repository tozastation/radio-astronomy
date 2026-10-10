import json
import logging
import os
import wave
from pathlib import Path
from typing import Dict, Any, Optional

from aprs_decoder import APRSDecoder
from spectrogram_generator import SpectrogramGenerator

logger = logging.getLogger(__name__)


class PassAnalyzer:
    """衛星通過時の音声データを解析し、パケットJSON、サマリーJSON、スペクトログラム画像を生成するアナライザー"""

    def __init__(self, direwolf_binary: str = "direwolf"):
        self.decoder = APRSDecoder(direwolf_binary=direwolf_binary)
        self.spectrogram_gen = SpectrogramGenerator()

    def analyze_pass(
        self,
        wav_path: str,
        satellite: str,
        pass_id: str,
        output_dir: str,
    ) -> Dict[str, Any]:
        """WAV ファイルを解析し、成果物一式を指定ディレクトリに出力する"""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"PassAnalyzer: Starting analysis for {satellite} ({pass_id}) from {wav_path}")

        # 1. WAV 諸元の取得
        file_size = os.path.getsize(wav_path) if os.path.exists(wav_path) else 0
        duration_sec = 0.0
        sample_rate = 48000
        try:
            with wave.open(wav_path, "rb") as wf:
                sample_rate = wf.getframerate()
                duration_sec = wf.getnframes() / float(sample_rate)
        except Exception as e:
            logger.warning(f"Could not read wave header for {wav_path}: {e}")

        # 2. APRS パケットデコード
        frames = self.decoder.decode_wav(wav_path)
        packets_data = {
            "satellite": satellite,
            "pass_id": pass_id,
            "packets_count": len(frames),
            "packets": [f.to_dict() for f in frames],
        }
        packets_json_path = out_path / "packets.json"
        with open(packets_json_path, "w", encoding="utf-8") as f:
            json.dump(packets_data, f, indent=2, ensure_ascii=False)

        # 3. スペクトログラム画像生成
        spectrogram_png_path = out_path / "spectrogram.png"
        self.spectrogram_gen.generate(
            wav_path=wav_path,
            output_png_path=str(spectrogram_png_path),
            title=f"{satellite} Pass Spectrogram ({pass_id})",
        )

        # 4. パスサマリーの保存
        summary_data = {
            "satellite": satellite,
            "pass_id": pass_id,
            "recording_duration_sec": duration_sec,
            "sample_rate": sample_rate,
            "raw_file_size_bytes": file_size,
            "packets_decoded": len(frames),
            "spectrogram_image": spectrogram_png_path.name,
        }
        summary_json_path = out_path / "summary.json"
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)

        logger.info(f"PassAnalyzer: Completed analysis for {satellite}. Results in {output_dir}")
        return summary_data
