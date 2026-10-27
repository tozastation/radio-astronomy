import os
import sys
import wave
import numpy as np
import pytest
from datetime import datetime, timezone

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from iss_dsp import ISSDspDemodulator


def test_demodulate_synthetic_doppler_tone(tmp_path):
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 1.0
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    # 1. 人工的な周波数ドリフト (+3000Hz -> -3000Hz) を伴う 1200Hz AFSK トーンの FM 信号を生成
    fm_dev_hz = 3000.0
    mod_freq_hz = 1200.0
    m_t = np.sin(2 * np.pi * mod_freq_hz * t)
    # ドップラー周波数: +3000Hz から -3000Hz へ線形スイープ
    doppler_drift_hz = 3000.0 * (1.0 - 2.0 * (t / duration_sec))
    instantaneous_freq = doppler_drift_hz + fm_dev_hz * m_t
    phase = 2 * np.pi * np.cumsum(instantaneous_freq) / sample_rate

    # 複素ベースバンド -> uint8 I/Q
    i_val = np.clip(np.round(127.5 + 100.0 * np.cos(phase)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 + 100.0 * np.sin(phase)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "test_synth.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    # 2. ドップラー相殺付き復調の実行 (手動指定のドップラー補正配列)
    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "out.wav"),
        doppler_override_hz=doppler_drift_hz
    )

    assert os.path.exists(out_wav)
    with wave.open(out_wav, "rb") as wf:
        assert wf.getframerate() == 48000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # 16-bit
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)

    # 3. FFT 解析で 1200Hz に明確なピークがあることを確認
    fft_mag = np.abs(np.fft.rfft(audio.astype(float)))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[10:]) + 10  # DC 除外
    peak_freq = freqs[peak_idx]
    assert abs(peak_freq - 1200.0) < 50.0


def test_demodulate_empty_or_small_file(tmp_path):
    demod = ISSDspDemodulator()
    empty_raw = str(tmp_path / "empty.raw")
    with open(empty_raw, "wb") as f:
        f.write(b"")
    result = demod.demodulate_raw_iq(raw_path=empty_raw, output_wav_path=str(tmp_path / "empty.wav"))
    assert result == ""


def test_compute_doppler_shifts_with_tle():
    demod = ISSDspDemodulator()
    # ISS (ZARYA) TLE
    line1 = "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996"
    line2 = "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263"
    start_time = datetime(2026, 10, 20, 0, 50, 0, tzinfo=timezone.utc)
    duration_sec = 600.0

    shifts = demod.compute_doppler_shifts(
        tle_line1=line1,
        tle_line2=line2,
        start_time=start_time,
        duration_sec=duration_sec,
        center_freq_hz=145825000.0,
        step_sec=1.0,
    )

    assert len(shifts) == int(duration_sec) + 1
    # ドップラー偏移は通過開始時正（接近）、終了時負（離脱）
    assert shifts[0] > 1000.0
    assert shifts[-1] < -1000.0


def test_decode_packets_activity_with_raw_iq(tmp_path):
    from activities import decode_packets_activity
    # 正常な raw ファイルを準備し、decode_packets_activity がクラッシュせず辞書を返すことを検証
    raw_path = str(tmp_path / "ISS (ZARYA)_20261028_120000.raw")
    # 240kSPS 1秒分の無音 IQ (128)
    with open(raw_path, "wb") as f:
        f.write(bytes([128] * (240000 * 2)))

    import asyncio
    res = asyncio.run(decode_packets_activity(raw_path, satellite="ISS (ZARYA)"))
    assert res["satellite"] == "ISS (ZARYA)"
    assert res["satellite_type"] == "SpaceStation"
    assert "packets_count" in res
    # 変換後の WAV ファイルが生成されていること
    assert os.path.exists(str(tmp_path / "ISS (ZARYA)_20261028_120000_48k.wav"))


def test_demodulate_with_none_pass_time(tmp_path):
    demod = ISSDspDemodulator()
    raw_path = str(tmp_path / "no_time.raw")
    with open(raw_path, "wb") as f:
        f.write(bytes([128] * (240000 * 2)))

    out_wav = demod.demodulate_raw_iq(raw_path=raw_path, pass_start_utc=None, tle_lines=None)
    assert os.path.exists(out_wav)


def test_decode_packets_activity_with_corrupt_raw(tmp_path):
    from activities import decode_packets_activity
    # 50バイトの破損 raw ファイル (100KB 未満)
    corrupt_raw = str(tmp_path / "corrupt.raw")
    with open(corrupt_raw, "wb") as f:
        f.write(b"CORRUPT" * 5)

    import asyncio
    res = asyncio.run(decode_packets_activity(corrupt_raw, satellite="ISS (ZARYA)"))
    assert res["packets_count"] == 0
    assert res["packets"] == []


