import datetime
import logging
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_FUNCUBE_TLE: Tuple[str, str] = (
    "1 39444U 13066AE  26294.20736285  .00008239  00000+0  48957-3 0  9993",
    "2 39444  97.8439 268.5385 0036409 132.3370 228.0953 15.10994851698683",
)


def parse_pass_time_from_path(path_or_str: str) -> Optional[datetime.datetime]:
    """ファイル名や pass_id から YYYYMMDD_HHMMSS 形式の日時をパースして UTC datetime を返す"""
    fname = os.path.basename(path_or_str)
    m = re.search(r"(\d{8})_(\d{6})", fname)
    if m:
        try:
            dt = datetime.datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    return None


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

    def decode(
        self,
        file_path: str,
        sample_rate: int = 240000,
        pass_start_utc: Optional[datetime.datetime] = None,
        tle_lines: Optional[Tuple[str, str]] = None,
    ) -> List[FUNcubeFrame]:
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
            return self.decode_raw(
                file_path,
                sample_rate=sample_rate,
                pass_start_utc=pass_start_utc,
                tle_lines=tle_lines,
            )
        else:
            return self.decode_wav(file_path)

    def decode_raw(
        self,
        raw_path: str,
        sample_rate: int = 240000,
        target_sample_rate: int = 48000,
        pass_start_utc: Optional[datetime.datetime] = None,
        tle_lines: Optional[Tuple[str, str]] = None,
        center_freq_hz: float = 145935000.0,
        f_offset: float = 0.0,
    ) -> List[FUNcubeFrame]:
        """生 IQ (cu8: uint8) ファイルをストリーミングでドップラー補正・48kHz デシメーションし、gr_satellites で復調する"""
        import numpy as np
        import tempfile
        import scipy.signal

        if not self.has_binary:
            logger.warning(
                f"gr_satellites command ('{self.binary_path}') not found. Skipping FUNcube decoding for {raw_path}."
            )
            return []

        try:
            file_size = os.path.getsize(raw_path)
            if file_size < 2048:
                return []

            num_complex_samples = file_size // 2
            duration_sec = num_complex_samples / float(sample_rate)

            # SGP4 軌道伝搬モデルに基づく瞬時ドップラー偏移列の事前計算
            dt_utc = pass_start_utc or parse_pass_time_from_path(raw_path)
            tle = tle_lines or DEFAULT_FUNCUBE_TLE
            d_shifts = None

            if dt_utc and tle:
                try:
                    from iss_dsp import ISSDspDemodulator
                    demod = ISSDspDemodulator()
                    d_shifts = demod.compute_doppler_shifts(
                        tle_line1=tle[0],
                        tle_line2=tle[1],
                        start_time=dt_utc,
                        duration_sec=duration_sec,
                        center_freq_hz=center_freq_hz,
                        step_sec=1.0,
                    )
                except Exception as e:
                    logger.warning(f"Doppler compensation precomputation skipped for {raw_path}: {e}")

            decim = sample_rate // target_sample_rate
            chunk_samples = sample_rate  # 1秒ごとにチャンク処理 (メモリ使用量を数MBに極小化)

            with tempfile.NamedTemporaryFile(suffix=".cs16", delete=True) as tmp_file:
                phase_acc = 0.0
                scale = 10000.0
                scale_determined = False

                with open(raw_path, "rb") as f_in:
                    sample_idx = 0
                    while True:
                        raw_bytes = f_in.read(chunk_samples * 2)
                        if not raw_bytes:
                            break
                        u8 = np.frombuffer(raw_bytes, dtype=np.uint8)
                        chunk_len = len(u8) // 2
                        if chunk_len == 0:
                            break

                        i = (u8[0::2].astype(np.float32) - 128.0)
                        q = (u8[1::2].astype(np.float32) - 128.0)
                        del u8
                        x = i + 1j * q
                        del i, q

                        # ドップラー補正位相回転の適用
                        if d_shifts is not None and len(d_shifts) > 0:
                            t_start_s = sample_idx / float(sample_rate)
                            t_end_s = (sample_idx + chunk_len) / float(sample_rate)
                            t_steps = np.linspace(t_start_s, t_end_s, chunk_len, endpoint=False)
                            shifts = np.interp(t_steps, np.arange(len(d_shifts)), d_shifts)
                            phase = phase_acc + np.cumsum(-2.0 * np.pi * shifts / float(sample_rate))
                            phase_acc = float(phase[-1] % (2.0 * np.pi))
                            x *= np.exp(1j * phase)

                        # デシメーション (アンチエイリアシング LPF 適用)
                        if decim > 1:
                            x_target = scipy.signal.resample_poly(x, 1, decim)
                        else:
                            x_target = x
                        del x

                        # 初回チャンクの振幅から RMS スケーリング係数を決定
                        if not scale_determined:
                            rms = np.sqrt(np.mean(np.abs(x_target) ** 2))
                            if rms > 1e-6:
                                scale = min(10000.0 / rms, 30000.0)
                            scale_determined = True

                        cs16 = np.empty((len(x_target) * 2,), dtype=np.int16)
                        cs16[0::2] = np.clip(np.real(x_target) * scale, -32768, 32767).astype(np.int16)
                        cs16[1::2] = np.clip(np.imag(x_target) * scale, -32768, 32767).astype(np.int16)
                        del x_target
                        tmp_file.write(cs16.tobytes())
                        del cs16

                        sample_idx += chunk_len

                tmp_file.flush()

                cmd = [
                    self.binary_path,
                    "AO-73",
                    "--rawint16", tmp_file.name,
                    "--iq",
                    "--samp_rate", str(target_sample_rate),
                    "--f_offset", str(int(f_offset)),
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
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

        # 実機の gr_satellites は "-> Packet from" 単位で完全な 1 パケットを出力する
        if "-> Packet from" in output:
            blocks = [b.strip() for b in output.split("-> Packet from") if b.strip()]
        else:
            # モックデータや旧フォーマットの後方互換フォールバック
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

            # 各行の key = val または key: val 形式をパース
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
