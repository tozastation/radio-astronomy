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


def test_demodulate_noisy_iq_with_predetection_filter(tmp_path):
    """検波前 LPF により広帯域雑音下でも FM 復調が破綻せずトーンを再生できることを検証"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    # 1200Hz FM 信号
    mod_freq_hz = 1200.0
    fm_dev_hz = 3000.0
    phase = 2 * np.pi * fm_dev_hz * np.cumsum(np.sin(2 * np.pi * mod_freq_hz * t)) / sample_rate
    sig = 40.0 * np.exp(1j * phase)

    # 240kHz 広帯域複素ガウス雑音
    np.random.seed(42)
    noise = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)) * 15.0
    noisy_iq = sig + noise

    i_val = np.clip(np.round(127.5 + np.real(noisy_iq)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 + np.imag(noisy_iq)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "noisy_iss.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "noisy_out.wav"),
    )

    assert os.path.exists(out_wav)
    with wave.open(out_wav, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)

    fft_mag = np.abs(np.fft.rfft(audio.astype(float)))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[10:]) + 10
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


def test_demodulate_multi_chunk_continuity(tmp_path):
    """10秒チャンク境界を跨いでも位相連続性と復調トーン品質が維持されることを検証"""
    demod = ISSDspDemodulator()
    sample_rate = 48000  # fs_if = 9600 Hz (> 7000 Hz)
    duration_sec = 12.0  # 10s チャンク + 2s チャンク
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    fm_dev_hz = 1500.0
    mod_freq_hz = 1200.0
    doppler_drift_hz = 1000.0 * (1.0 - 2.0 * (t / duration_sec))
    instantaneous_freq = doppler_drift_hz + fm_dev_hz * np.sin(2 * np.pi * mod_freq_hz * t)
    phase = 2 * np.pi * np.cumsum(instantaneous_freq) / sample_rate

    i_val = np.clip(np.round(127.5 + 100.0 * np.cos(phase)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 + 100.0 * np.sin(phase)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "multi_chunk.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "multi_chunk.wav"),
        doppler_override_hz=doppler_drift_hz,
    )
    assert os.path.exists(out_wav)
    with wave.open(out_wav, "rb") as wf:
        assert wf.getframerate() == sample_rate // 5
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    fft_mag = np.abs(np.fft.rfft(audio.astype(float)))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / (sample_rate // 5))
    peak_idx = np.argmax(fft_mag[10:]) + 10
    assert abs(freqs[peak_idx] - 1200.0) < 30.0


def test_resolve_fallback_tle_for_all_satellites():
    from activities import _resolve_fallback_tle
    assert _resolve_fallback_tle("ISS (ZARYA)") is not None
    assert _resolve_fallback_tle("FUNCUBE-1 (AO-73)") is not None
    assert _resolve_fallback_tle("METEOR-M2 3") is not None
    assert _resolve_fallback_tle("METEOR-M2 4") is not None
    assert _resolve_fallback_tle("UNKNOWN-SAT") is None


def test_demodulate_rejects_dc_spike_and_residual_carrier_offset(tmp_path):
    """LO漏洩 DC スパイク(+25 LSB)および残留キャリアオフセット(+800Hz)があってもゼロDC・1200Hzトーンを復元できること"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    # +800 Hz 残留オフセット + 1200 Hz AFSK FM 変調
    residual_offset_hz = 800.0
    fm_dev_hz = 3000.0
    mod_freq_hz = 1200.0
    inst_freq = residual_offset_hz + fm_dev_hz * np.sin(2.0 * np.pi * mod_freq_hz * t)
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / sample_rate

    # 強い DC バイアス (+25 / -20 LSB) を重畳
    i_val = np.clip(np.round(127.5 + 25.0 + 40.0 * np.cos(phase)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 - 20.0 + 40.0 * np.sin(phase)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "dc_offset_iss.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "dc_offset_out.wav"),
    )
    assert os.path.exists(out_wav)
    with wave.open(out_wav, "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float64)

    # 音声バンドパス (300-3500 Hz) により残留オフセット由来の DC バイアスがほぼゼロであること
    assert abs(np.mean(audio)) < 300.0

    fft_mag = np.abs(np.fft.rfft(audio))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[10:]) + 10
    assert abs(freqs[peak_idx] - 1200.0) < 40.0


def test_demodulate_predetection_filter_rejects_out_of_band_interferer(tmp_path):
    """検波前 ±8kHz フィルタが帯域外 (+16kHz) の強力妨害波を抑圧して 1200Hz 信号を保護することを検証"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    fm_phase = 2.0 * np.pi * 3000.0 * np.cumsum(np.sin(2.0 * np.pi * 1200.0 * t)) / sample_rate
    desired = 25.0 * np.exp(1j * fm_phase)
    # 帯域外 (+16 kHz) に希望波より強い妨害キャリアを重畳
    interferer = 60.0 * np.exp(1j * 2.0 * np.pi * 16000.0 * t)
    combined = desired + interferer

    i_val = np.clip(np.round(127.5 + np.real(combined)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 + np.imag(combined)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "interferer_iss.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "interferer_out.wav"),
    )
    with wave.open(out_wav, "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float64)

    fft_mag = np.abs(np.fft.rfft(audio))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[10:]) + 10
    assert abs(freqs[peak_idx] - 1200.0) < 40.0


def test_estimate_tca_drift_rejects_out_of_band_rfi_and_detects_inband(tmp_path):
    """帯域外 (+100kHz) の強力 RFI による偽 TCA/SNR 誤検出を防ぎ、帯域内信号ピークのみ正しく検出すること"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 20
    num_samples = sample_rate * duration_sec
    t = np.arange(num_samples) / sample_rate

    line1 = "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996"
    line2 = "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263"
    start_time = datetime(2026, 10, 20, 0, 54, 50, tzinfo=timezone.utc)

    # ケース1: 帯域外 (+100kHz) に時間変化する強大な RFI のみ存在 (帯域内は白色雑音)
    rng = np.random.default_rng(7)
    noise = (rng.normal(0.0, 2.0, num_samples) + 1j * rng.normal(0.0, 2.0, num_samples))
    rfi_env = np.exp(-0.5 * ((t - 5.0) / 1.5) ** 2) * 40.0
    rfi = rfi_env * np.exp(1j * 2.0 * np.pi * 100000.0 * t)
    sig_rfi_only = noise + rfi

    rfi_path = str(tmp_path / "iss_oob_rfi.raw")
    cu8 = np.empty(num_samples * 2, dtype=np.uint8)
    cu8[0::2] = np.clip(np.round(127.5 + np.real(sig_rfi_only)), 0, 255).astype(np.uint8)
    cu8[1::2] = np.clip(np.round(127.5 + np.imag(sig_rfi_only)), 0, 255).astype(np.uint8)
    with open(rfi_path, "wb") as f:
        f.write(cu8.tobytes())

    drift_rfi, snr_rfi = demod.estimate_tca_drift(
        file_path=rfi_path,
        pass_start_utc=start_time,
        tle_lines=(line1, line2),
        center_freq_hz=145825000.0,
        sample_rate=sample_rate,
    )
    assert drift_rfi is None
    assert snr_rfi is None or snr_rfi < 2.0

    # ケース2: 帯域内 (+1kHz) に t=10s ピークの衛星信号が存在
    sat_env = np.exp(-0.5 * ((t - 10.0) / 2.0) ** 2) * 15.0
    sat_sig = noise + sat_env * np.exp(1j * 2.0 * np.pi * 1000.0 * t)
    inband_path = str(tmp_path / "iss_inband.raw")
    cu8_in = np.empty(num_samples * 2, dtype=np.uint8)
    cu8_in[0::2] = np.clip(np.round(127.5 + np.real(sat_sig)), 0, 255).astype(np.uint8)
    cu8_in[1::2] = np.clip(np.round(127.5 + np.imag(sat_sig)), 0, 255).astype(np.uint8)
    with open(inband_path, "wb") as f:
        f.write(cu8_in.tobytes())

    drift_in, snr_in = demod.estimate_tca_drift(
        file_path=inband_path,
        pass_start_utc=start_time,
        tle_lines=(line1, line2),
        center_freq_hz=145825000.0,
        sample_rate=sample_rate,
    )
    assert snr_in is not None and snr_in >= 5.0
    assert drift_in is not None


def test_demodulate_preserves_packet_level_against_fm_noise_clicks(tmp_path):
    """長時間パスの無信号区間 FM クリック雑音でパケット音声の PCM スケーリングが圧迫されないことを検証"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 4.0
    num_samples = sample_rate * int(duration_sec)
    t = np.arange(num_samples) / sample_rate

    rng = np.random.default_rng(42)
    rx = rng.normal(0.0, 2.0, num_samples) + 1j * rng.normal(0.0, 2.0, num_samples)

    # t=2.0..2.5s にのみ 3kHz 偏移の 1200Hz AFSK トーンバーストを注入 (残りは無信号白色雑音)
    burst_start = sample_rate * 2
    burst_len = sample_rate // 2
    t_b = np.arange(burst_len) / sample_rate
    phase_b = 2.0 * np.pi * 3000.0 * np.cumsum(np.sin(2.0 * np.pi * 1200.0 * t_b)) / sample_rate
    rx[burst_start : burst_start + burst_len] += 30.0 * np.exp(1j * phase_b)

    cu8 = np.empty(num_samples * 2, dtype=np.uint8)
    cu8[0::2] = np.clip(np.round(127.5 + np.real(rx)), 0, 255).astype(np.uint8)
    cu8[1::2] = np.clip(np.round(127.5 + np.imag(rx)), 0, 255).astype(np.uint8)
    raw_path = str(tmp_path / "iss_clicks.raw")
    with open(raw_path, "wb") as f:
        f.write(cu8.tobytes())

    out_wav = demod.demodulate_raw_iq(
        raw_path=raw_path,
        sample_rate=sample_rate,
        output_wav_path=str(tmp_path / "iss_clicks.wav"),
    )
    with wave.open(out_wav, "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    pkt_slice = audio[48000 * 2 + 2400 : 48000 * 2 + 21600]
    pkt_rms = float(np.sqrt(np.mean(pkt_slice.astype(np.float64) ** 2)))
    # FM クリックで圧迫されず、16-bit PCM として十分な信号レベル (> 6000 RMS) を維持すること
    assert pkt_rms > 6000.0

    fft_mag = np.abs(np.fft.rfft(pkt_slice.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(pkt_slice), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[5:]) + 5
    assert abs(freqs[peak_idx] - 1200.0) < 40.0


def test_estimate_tca_drift_suppresses_low_snr_drift(tmp_path):
    """2.0 <= SNR < 5.0 dB の低 SNR パスでは TCA ドリフトを None として誤推定を抑止し、min_tca_snr_db=2.0 明示時のみ返すこと"""
    demod = ISSDspDemodulator()
    sample_rate = 240000
    duration_sec = 20
    num_samples = sample_rate * duration_sec
    t = np.arange(num_samples) / sample_rate

    line1 = "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996"
    line2 = "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263"
    start_time = datetime(2026, 10, 20, 0, 54, 50, tzinfo=timezone.utc)

    # チャネル LPF 通過後に SNR が約 3〜4 dB になるよう帯域内微弱信号を重畳
    rng = np.random.default_rng(123)
    noise = rng.normal(0.0, 2.0, num_samples) + 1j * rng.normal(0.0, 2.0, num_samples)
    weak_env = np.exp(-0.5 * ((t - 10.0) / 2.0) ** 2) * 1.4
    sig = noise + weak_env * np.exp(1j * 2.0 * np.pi * 1000.0 * t)

    cu8 = np.empty(num_samples * 2, dtype=np.uint8)
    cu8[0::2] = np.clip(np.round(127.5 + np.real(sig)), 0, 255).astype(np.uint8)
    cu8[1::2] = np.clip(np.round(127.5 + np.imag(sig)), 0, 255).astype(np.uint8)
    raw_path = str(tmp_path / "iss_low_snr.raw")
    with open(raw_path, "wb") as f:
        f.write(cu8.tobytes())

    drift_default, snr_default = demod.estimate_tca_drift(
        file_path=raw_path,
        pass_start_utc=start_time,
        tle_lines=(line1, line2),
        center_freq_hz=145825000.0,
        sample_rate=sample_rate,
    )
    assert snr_default is not None and 2.0 <= snr_default < 5.0
    assert drift_default is None

    drift_relaxed, snr_relaxed = demod.estimate_tca_drift(
        file_path=raw_path,
        pass_start_utc=start_time,
        tle_lines=(line1, line2),
        center_freq_hz=145825000.0,
        sample_rate=sample_rate,
        min_tca_snr_db=2.0,
    )
    assert snr_relaxed == snr_default
    assert drift_relaxed is not None




