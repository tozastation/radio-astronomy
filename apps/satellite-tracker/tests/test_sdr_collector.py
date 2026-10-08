import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from sdr_collector import SDRCollector, SpectrumResult


def test_sdr_collector_mock_lifecycle():
    collector = SDRCollector(mock_sdr=True, sample_rate=2.4e6, fft_size=2048)
    assert collector.mock_sdr is True
    collector.start()
    collector.stop()


def test_measure_spectrum_with_known_doppler_shift():
    # 2.4 MSPS, 2048 点 FFT (周波数分解能 約 1171 Hz)
    # 期待されるドップラー偏移: +6000 Hz
    collector = SDRCollector(mock_sdr=True, sample_rate=2.4e6, fft_size=4096)
    collector.start()

    center_freq = 437550000.0
    expected_doppler = 6000.0

    result = collector.measure_spectrum(
        center_freq_hz=center_freq,
        expected_doppler_hz=expected_doppler,
    )

    collector.stop()

    assert isinstance(result, SpectrumResult)
    assert result.center_freq_hz == center_freq
    # 二次補間により周波数分解能以内の高精度でドップラーが測定されること
    assert pytest.approx(result.measured_doppler_hz, abs=1500.0) == expected_doppler
    assert result.snr_db >= 10.0  # 10dB 以上の SNR
    assert -120.0 <= result.rssi_dbm <= 0.0


def test_measure_spectrum_negative_doppler_shift():
    # 離脱時の負のドップラー偏移: -8000 Hz
    collector = SDRCollector(mock_sdr=True, sample_rate=2.4e6, fft_size=4096)
    collector.start()

    center_freq = 435220000.0
    expected_doppler = -8000.0

    result = collector.measure_spectrum(
        center_freq_hz=center_freq,
        expected_doppler_hz=expected_doppler,
    )

    collector.stop()

    assert pytest.approx(result.measured_doppler_hz, abs=1500.0) == expected_doppler
    assert result.snr_db >= 10.0
