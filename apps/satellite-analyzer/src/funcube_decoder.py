from dataclasses import dataclass, asdict
import os
import datetime
import logging
import re
import shutil
import subprocess
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class FUNcubeFrame:
    timestamp: str
    satellite: str
    frame_type: str
    telemetry: Dict[str, Any]
    raw_frame: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FUNcubeDecoder:
    """gr-satellites による FUNcube-1 (AO-73) BPSK 1200bps / AO-40 テレメトリデコーダー"""

    def __init__(self, binary_path: Optional[str] = None):
        candidate = binary_path or os.getenv("GR_SATELLITES_BINARY") or "gr_satellites"
        self.binary_path = candidate
        self.has_binary = shutil.which(self.binary_path) is not None

    def decode(self, file_path: str, sample_rate: int = 240000) -> List[FUNcubeFrame]:
        """WAV または生 IQ (.raw/.u8) ファイルから FUNcube-1 (AO-73) の BPSK テレメトリフレームをデコードする"""
        if not self.has_binary:
            logger.warning(
                f"gr_satellites command ('{self.binary_path}') not found. Skipping FUNcube decoding for {file_path}."
            )
            return []

        if not os.path.exists(file_path) or os.path.getsize(file_path) < 1024:
            return []

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".raw", ".u8", ".cu8"):
            return self.decode_raw(file_path, sample_rate=sample_rate)
        else:
            return self.decode_wav(file_path)

    def decode_raw(self, raw_path: str, sample_rate: int = 240000) -> List[FUNcubeFrame]:
        """生 IQ (cu8: uint8) ファイルを cs16 (int16) に変換し、gr_satellites で復調する"""
        import numpy as np
        import tempfile

        if not self.has_binary:
            logger.warning(
                f"gr_satellites command ('{self.binary_path}') not found. Skipping FUNcube decoding for {raw_path}."
            )
            return []

        try:
            with tempfile.NamedTemporaryFile(suffix=".cs16", delete=True) as tmp_file:
                chunk_size = 1024 * 1024  # 1MB ずつストリーミング変換
                with open(raw_path, "rb") as f_in, open(tmp_file.name, "wb") as f_out:
                    while chunk := f_in.read(chunk_size):
                        u8 = np.frombuffer(chunk, dtype=np.uint8)
                        cs16 = ((u8.astype(np.int16) - 128) * 256).astype(np.int16)
                        f_out.write(cs16.tobytes())

                cmd = [
                    self.binary_path,
                    "AO-73",
                    "--rawint16", tmp_file.name,
                    "--iq",
                    "--samp_rate", str(sample_rate),
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                return self.parse_output(result.stdout)
        except Exception as e:
            logger.error(f"Error running gr_satellites on raw IQ {raw_path}: {e}")
            return []

    def decode_wav(self, wav_path: str) -> List[FUNcubeFrame]:
        """WAV ファイルから FUNcube-1 (AO-73) の BPSK テレメトリフレームをデコードする"""
        if not self.has_binary:
            logger.warning(
                f"gr_satellites command ('{self.binary_path}') not found. Skipping FUNcube decoding for {wav_path}."
            )
            return []

        ext = os.path.splitext(wav_path)[1].lower()
        if ext in (".raw", ".u8", ".cu8"):
            return self.decode_raw(wav_path)

        try:
            cmd = [
                self.binary_path,
                "AO-73",
                "--wavfile", wav_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return self.parse_output(result.stdout)
        except Exception as e:
            logger.error(f"Error running gr_satellites on {wav_path}: {e}")
            return []

    def parse_output(self, output: str) -> List[FUNcubeFrame]:
        """gr_satellites の標準出力テキストからテレメトリフレームを抽出する"""
        frames: List[FUNcubeFrame] = []
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # gr-satellites の出力ブロック分割 (Container: または frame 開始行)
        blocks = re.split(r"(?:Container:|------+\s+AO-73\s+------*)", output)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # 警告行やエラーメッセージのみの場合はスキップ
            if any(p in block.lower() for p in ("fll_band_edge_cc", "wavfile_source", "error in wav", "runtimeerror")):
                continue

            telemetry: Dict[str, Any] = {}
            frame_type = "AO-73 Telemetry"

            # 各行の key = val 形式をパース
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("fll_") or line.startswith("wavfile_source") or "error:" in line.lower():
                    continue
                if "=" in line:
                    parts = line.split("=", 1)
                    k = parts[0].strip().replace(" ", "_").lower()
                    v_str = parts[1].strip()
                    try:
                        if "." in v_str:
                            v = float(v_str)
                        else:
                            v = int(v_str)
                    except ValueError:
                        v = v_str
                    telemetry[k] = v
                elif ":" in line:
                    parts = line.split(":", 1)
                    k = parts[0].strip().replace(" ", "_").lower()
                    v = parts[1].strip()
                    telemetry[k] = v

            # 意味のあるテレメトリ項目が存在する場合のみフレームとして採用
            valid_keys = {"frame_type", "sequence_number", "battery_voltage", "solar_panel_voltage", "eps_temperature"}
            if telemetry and (any(vk in telemetry for vk in valid_keys) or len(telemetry) >= 3):
                if "frame_type" in telemetry:
                    frame_type = str(telemetry["frame_type"])

                frames.append(
                    FUNcubeFrame(
                        timestamp=now_iso,
                        satellite="FUNcube-1 (AO-73)",
                        frame_type=frame_type,
                        telemetry=telemetry,
                        raw_frame=block[:1000],
                    )
                )

        return frames
