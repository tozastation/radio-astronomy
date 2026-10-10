import os
import shutil
import wave
import logging
from pathlib import Path
from typing import Optional
import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)


class AudioSpooler:
    """衛星通過時の狭帯域ベースバンド録音およびディスク容量保護 (Circuit Breaker) を担うスプーラー"""

    def __init__(
        self,
        spool_dir: str = "/tmp/spool",
        input_sample_rate: float = 2.4e6,
        target_sample_rate: int = 48000,
        max_spool_bytes: int = 500 * 1024 * 1024,  # デフォルト 500 MB
        min_free_percent: float = 10.0,            # 空き容量 10%
    ):
        self.spool_dir = Path(spool_dir)
        self.input_sample_rate = input_sample_rate
        self.target_sample_rate = target_sample_rate
        self.max_spool_bytes = max_spool_bytes
        self.min_free_percent = min_free_percent

        self.current_wav_path: Optional[Path] = None
        self._wave_writer: Optional[wave.Wave_write] = None
        self._last_sample: complex = 0.0 + 0.0j
        self._phase: float = 0.0

        self.spool_dir.mkdir(parents=True, exist_ok=True)

    def start_pass(self, satellite: str, pass_id: str) -> str:
        """パス開始時に WAV ファイルを新規作成し、初期化する"""
        self.enforce_circuit_breaker()

        filename = f"{satellite}_{pass_id}.wav"
        self.current_wav_path = self.spool_dir / filename

        self._wave_writer = wave.open(str(self.current_wav_path), "wb")
        self._wave_writer.setnchannels(1)        # モノラル
        self._wave_writer.setsampwidth(2)        # 16-bit PCM
        self._wave_writer.setframerate(self.target_sample_rate)

        self._last_sample = 0.0 + 0.0j
        self._phase = 0.0
        logger.info(f"AudioSpooler: Started recording pass to {self.current_wav_path}")
        return str(self.current_wav_path)

    def write_samples(self, iq_samples: np.ndarray, doppler_hz: float = 0.0) -> None:
        """IQ サンプルのドップラー補正・FM復調・デシメーションを行い、WAV へ追記する"""
        if self._wave_writer is None or len(iq_samples) == 0:
            return

        # 1. 理論ドップラー偏移の逆位相ミキシング (ベースバンドへの周波数引き戻し)
        if abs(doppler_hz) > 1e-3:
            t = (np.arange(len(iq_samples))) / self.input_sample_rate
            correction = np.exp(-1j * 2.0 * np.pi * doppler_hz * t)
            iq_corrected = iq_samples * correction
        else:
            iq_corrected = iq_samples

        # 2. クワドラチャ検波 / FM 復調 (角周波数差分の抽出)
        # 前フレームの最終サンプルを先頭に連結
        extended = np.empty(len(iq_corrected) + 1, dtype=np.complex64)
        extended[0] = self._last_sample
        extended[1:] = iq_corrected
        self._last_sample = iq_corrected[-1]

        # 位相差分 (FM検波出力)
        product = extended[1:] * np.conj(extended[:-1])
        demodulated = np.angle(product).astype(np.float32)

        # 3. 48kHz へのデシメーション (ダウンサンプリング)
        # 2.4MSPS / 48kHz = 50倍のダウンサンプリング
        decimation_factor = int(round(self.input_sample_rate / self.target_sample_rate))
        if decimation_factor > 1:
            # 高速かつエリアシングを防ぐ 2段階デシメーション (例: 10倍 -> 5倍)
            try:
                step1 = signal.decimate(demodulated, 10, zero_phase=True)
                audio_48k = signal.decimate(step1, decimation_factor // 10, zero_phase=True)
            except Exception:
                # サンプル数が少ない場合のシンプルな間引きフォールバック
                audio_48k = demodulated[::decimation_factor]
        else:
            audio_48k = demodulated

        # 4. 16-bit PCM への正規化・クリッピング
        # angle の範囲は -pi ~ +pi
        scaled = np.clip(audio_48k * (32767.0 / np.pi), -32768, 32767).astype(np.int16)
        self._wave_writer.writeframes(scaled.tobytes())

    def finish_pass(self) -> Optional[str]:
        """録音を完了して WAV ファイルをフラッシュ・クローズする"""
        if self._wave_writer is not None:
            self._wave_writer.close()
            self._wave_writer = None

        path_str = str(self.current_wav_path) if self.current_wav_path else None
        logger.info(f"AudioSpooler: Finished pass recording: {path_str}")
        self.enforce_circuit_breaker()
        return path_str

    def enforce_circuit_breaker(self) -> None:
        """スプール容量またはホスト空き容量の閾値を超過した場合、最古のファイルを自動破棄する"""
        if not self.spool_dir.exists():
            return

        # 1. ホストファイルシステムの空き容量チェック
        try:
            total, used, free = shutil.disk_usage(str(self.spool_dir))
            free_percent = (free / total) * 100.0
            if free_percent < self.min_free_percent:
                logger.warning(
                    f"Circuit Breaker Triggered: Disk free space ({free_percent:.1f}%) < threshold ({self.min_free_percent}%)."
                )
                self._purge_oldest_files()
        except Exception as e:
            logger.warning(f"Error checking disk usage: {e}")

        # 2. スプールディレクトリ内の合計バイト数チェック
        wav_files = sorted(self.spool_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        total_spool_bytes = sum(f.stat().st_size for f in wav_files)

        while total_spool_bytes > self.max_spool_bytes and wav_files:
            oldest = wav_files.pop(0)
            logger.warning(
                f"Circuit Breaker: Purging oldest spool file {oldest.name} to enforce max_spool_bytes."
            )
            try:
                oldest.unlink(missing_ok=True)
                total_spool_bytes = sum(f.stat().st_size for f in wav_files)
            except Exception as e:
                logger.error(f"Failed to delete {oldest}: {e}")
                break

    def _purge_oldest_files(self) -> None:
        wav_files = sorted(self.spool_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if wav_files:
            oldest = wav_files[0]
            logger.warning(f"Circuit Breaker: Emergency purge of {oldest.name} due to low disk space.")
            oldest.unlink(missing_ok=True)
