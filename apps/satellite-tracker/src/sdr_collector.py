from dataclasses import dataclass
import logging
from typing import Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# RTL-SDR ライブラリの動的ロード（未接続環境・未対応librtlsdrでの安全なフォールバックのため）
try:
    from rtlsdr import RtlSdr
    HAS_RTLSDR = True
except Exception:
    HAS_RTLSDR = False


@dataclass
class SpectrumResult:
    center_freq_hz: float
    peak_freq_hz: float
    measured_doppler_hz: float
    rssi_dbm: float
    snr_db: float


class SDRCollector:
    def __init__(
        self,
        mock_sdr: bool = False,
        sample_rate: float = 2.4e6,
        fft_size: int = 4096,
        gain_db: float = 40.0,
    ):
        self.mock_sdr = mock_sdr
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.gain_db = gain_db
        self.sdr: Optional[object] = None
        self._is_running = False

    def start(self) -> None:
        """SDR デバイスを初期化し、受信待機状態にする"""
        if self._is_running:
            return

        if self.mock_sdr:
            logger.info("SDRCollector: Initialized in MOCK_SDR mode.")
            self._is_running = True
            return

        if not HAS_RTLSDR:
            logger.warning("pyrtlsdr not available. Falling back to MOCK_SDR mode.")
            self.mock_sdr = True
            self._is_running = True
            return

        try:
            self.sdr = RtlSdr()
            self.sdr.sample_rate = self.sample_rate
            self.sdr.gain = self.gain_db
            self._is_running = True
            logger.info("RTL-SDR v4 initialized successfully.")
        except Exception as e:
            logger.warning(
                f"Failed to initialize RTL-SDR hardware ({e}). Falling back to MOCK_SDR mode."
            )
            self.mock_sdr = True
            self.sdr = None
            self._is_running = True

    def stop(self) -> None:
        """SDR デバイスを解放する"""
        if not self._is_running:
            return

        if self.sdr is not None:
            try:
                self.sdr.close()
            except Exception as e:
                logger.error(f"Error closing RTL-SDR device: {e}")
            self.sdr = None

        self._is_running = False
        logger.info("SDRCollector stopped.")

    def _generate_mock_iq(
        self, num_samples: int, target_doppler_hz: float, snr_target_db: float = 18.0
    ) -> np.ndarray:
        """指定したドップラー周波数オフセットを持つ合成 IQ 信号を生成する"""
        t = np.arange(num_samples) / self.sample_rate
        # 搬送波 (複素正弦波)
        signal = np.exp(1j * 2.0 * np.pi * target_doppler_hz * t)

        # ガウス白色雑音
        noise_power = 10.0 ** (-snr_target_db / 10.0)
        noise = (np.random.normal(0, np.sqrt(noise_power / 2.0), num_samples) +
                 1j * np.random.normal(0, np.sqrt(noise_power / 2.0), num_samples))

        return signal + noise

    def measure_spectrum(
        self, center_freq_hz: float, expected_doppler_hz: float = 0.0
    ) -> SpectrumResult:
        """FFT によるパワースペクトル解析を実行し、ピーク周波数・RSSI・実測ドップラーを算出する"""
        if not self._is_running:
            self.start()

        num_samples = self.fft_size * 4

        if self.mock_sdr:
            samples = self._generate_mock_iq(num_samples, expected_doppler_hz)
        else:
            assert self.sdr is not None
            self.sdr.center_freq = center_freq_hz
            samples = self.sdr.read_samples(num_samples)

        # ハニング窓の適用
        window = np.hanning(self.fft_size)
        # 複数セグメントの平均パワースペクトル（ウェルチ法相当）
        num_segments = num_samples // self.fft_size
        power_spectrum = np.zeros(self.fft_size, dtype=np.float64)

        for i in range(num_segments):
            seg = samples[i * self.fft_size : (i + 1) * self.fft_size] * window
            fft_res = np.fft.fftshift(np.fft.fft(seg))
            power_spectrum += (np.abs(fft_res) ** 2) / (self.fft_size)

        power_spectrum /= num_segments

        # DC オフセット（中央の数ビン）を周囲の中央値でマスク
        center_bin = self.fft_size // 2
        dc_mask_radius = 4
        surrounding_median = np.median(
            power_spectrum[
                center_bin - dc_mask_radius * 3 : center_bin + dc_mask_radius * 3
            ]
        )
        power_spectrum[
            center_bin - dc_mask_radius : center_bin + dc_mask_radius + 1
        ] = surrounding_median

        # ピーク検出
        peak_idx = int(np.argmax(power_spectrum))
        peak_val = power_spectrum[peak_idx]

        # 二次補間（Quadratic Interpolation）によるサブビン周波数推定
        delta = 0.0
        if 0 < peak_idx < self.fft_size - 1:
            alpha = float(power_spectrum[peak_idx - 1])
            beta = float(power_spectrum[peak_idx])
            gamma = float(power_spectrum[peak_idx + 1])
            denom = alpha - 2.0 * beta + gamma
            if abs(denom) > 1e-12:
                delta = 0.5 * (alpha - gamma) / denom

        freq_resolution = self.sample_rate / self.fft_size
        measured_offset_hz = (peak_idx + delta - center_bin) * freq_resolution
        measured_peak_freq = center_freq_hz + measured_offset_hz

        # ノイズフロアおよび SNR 算出
        noise_floor = float(np.median(power_spectrum))
        snr_db = float(
            10.0 * np.log10(max(peak_val / max(noise_floor, 1e-12), 1.0))
        )

        # RSSI (dBm 換算の近似モデル)
        # フルスケール 0 dBFS を約 -10 dBm とし、ノイズフロア・ゲインに応じた補正
        rssi_dbm = float(10.0 * np.log10(max(peak_val, 1e-12)) - 50.0)

        return SpectrumResult(
            center_freq_hz=center_freq_hz,
            peak_freq_hz=measured_peak_freq,
            measured_doppler_hz=measured_offset_hz,
            rssi_dbm=rssi_dbm,
            snr_db=snr_db,
        )
