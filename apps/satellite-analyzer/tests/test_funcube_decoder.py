import os
import tempfile
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from funcube_decoder import FUNcubeDecoder, FUNcubeFrame


def test_funcube_decoder_parse_output():
    decoder = FUNcubeDecoder()
    sample_output = """
fll_band_edge_cc :warning: Using legacy FLL loop filter.
Container:
    frame_type = Realtime
    sequence_number = 1420
    solar_panel_voltage = 8.24
    battery_voltage = 7.85
    eps_temperature = 14.5
    transponder_active = False
Container:
    frame_type = WOD
    sequence_number = 1421
    solar_panel_voltage = 8.21
    battery_voltage = 7.84
    eps_temperature = 14.6
"""
    frames = decoder.parse_output(sample_output)
    assert len(frames) == 2

    assert frames[0].satellite == "FUNcube-1 (AO-73)"
    assert frames[0].frame_type == "Realtime"
    assert frames[0].telemetry["solar_panel_voltage"] == 8.24
    assert frames[0].telemetry["battery_voltage"] == 7.85
    assert frames[0].telemetry["sequence_number"] == 1420

    assert frames[1].frame_type == "WOD"
    assert frames[1].telemetry["sequence_number"] == 1421


def test_funcube_decoder_empty_output():
    decoder = FUNcubeDecoder()
    sample_output = """
fll_band_edge_cc :warning: Using legacy FLL loop filter.
"""
    frames = decoder.parse_output(sample_output)
    assert len(frames) == 0


def test_funcube_decoder_with_golden_wav():
    wav_asset = os.path.join(os.path.dirname(__file__), "../assets/ao73_telemetry_48k.wav")
    if not os.path.exists(wav_asset):
        pytest.skip("Golden AO-73 WAV sample not found")

    decoder = FUNcubeDecoder()
    if not decoder.has_binary:
        pytest.skip("gr_satellites binary not found")

    frames = decoder.decode_wav(wav_asset)
    assert len(frames) > 0
    assert frames[0].satellite == "FUNcube-1 (AO-73)"
    assert "batteryvoltage" in frames[0].telemetry or len(frames[0].telemetry) > 0


def test_funcube_decoder_parse_output_real_format():
    decoder = FUNcubeDecoder()
    sample_output = """
-> Packet from 1k2 BPSK downlink
Container:
    frame_type = Realtime
    sequence_number = 1420
    Container:
        solar_panel_voltage = 8.24
        battery_voltage = 7.85
    Container:
        eps_temperature = 14.5
-> Packet from 1k2 BPSK downlink
Container:
    frame_type = WOD
    sequence_number = 1421
    Container:
        solar_panel_voltage = 8.21
        battery_voltage = 7.84
        eps_temperature = 14.6
"""
    frames = decoder.parse_output(sample_output)
    assert len(frames) == 2

    assert frames[0].satellite == "FUNcube-1 (AO-73)"
    assert frames[0].frame_type == "Realtime"
    assert frames[0].telemetry["solar_panel_voltage"] == 8.24
    assert frames[0].telemetry["battery_voltage"] == 7.85
    assert frames[0].telemetry["eps_temperature"] == 14.5
    assert frames[0].telemetry["sequence_number"] == 1420

    assert frames[1].frame_type == "WOD"
    assert frames[1].telemetry["sequence_number"] == 1421


def test_funcube_decoder_decode_raw_invocation():
    decoder = FUNcubeDecoder(binary_path="gr_satellites")
    decoder.has_binary = True

    mock_stdout = """
Container:
    frame_type = Realtime
    sequence_number = 9999
    solar_panel_voltage = 8.35
    battery_voltage = 7.90
    eps_temperature = 12.3
"""

    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        # 4096 bytes of test cu8 (uint8 centered around 128)
        u8_data = np.full(4096, 128, dtype=np.uint8)
        u8_data[0] = 200
        u8_data[1] = 50
        f.write(u8_data.tobytes())
        raw_path = f.name

    try:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=mock_stdout, returncode=0)

            frames = decoder.decode(raw_path, sample_rate=240000)

            assert len(frames) == 1
            assert frames[0].satellite == "FUNcube-1 (AO-73)"
            assert frames[0].frame_type == "Realtime"
            assert frames[0].telemetry["sequence_number"] == 9999

            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "gr_satellites"
            assert cmd[1] == "AO-73"
            assert cmd[2] == "--rawint16"
            assert cmd[3].endswith(".cs16")
            assert cmd[4] == "--iq"
            assert cmd[5] == "--samp_rate"
            assert cmd[6] == "48000"
            assert cmd[7] == "--f_offset"
            assert cmd[8] == "0"
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)


def test_funcube_decoder_decode_raw_no_binary():
    decoder = FUNcubeDecoder(binary_path="nonexistent_binary")
    decoder.has_binary = False

    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        f.write(np.zeros(2048, dtype=np.uint8).tobytes())
        raw_path = f.name

    try:
        frames = decoder.decode(raw_path)
        assert frames == []
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)


def test_funcube_decoder_removes_dc_and_preserves_multichunk_continuity(tmp_path):
    """5秒チャンク境界を跨ぐ BPSK トーンの位相連続性と 0Hz DC オフセット除去を検証"""
    decoder = FUNcubeDecoder(binary_path="gr_satellites")
    decoder.has_binary = True

    sample_rate = 48000
    target_rate = 9600  # decim = 5, chunk_samples = 240000 (5秒)
    duration_sec = 6.0  # 5秒チャンク + 1秒チャンク (境界を跨ぐ)
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    # +15 LSB の大きな DC オフセット + 600 Hz の複素正弦波 (ドップラー補正後を模擬)
    sig_freq_hz = 600.0
    phase = 2.0 * np.pi * sig_freq_hz * t
    i_val = np.clip(np.round(127.5 + 15.0 + 40.0 * np.cos(phase)), 0, 255).astype(np.uint8)
    q_val = np.clip(np.round(127.5 - 12.0 + 40.0 * np.sin(phase)), 0, 255).astype(np.uint8)
    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = i_val
    raw_bytes[1::2] = q_val

    raw_path = str(tmp_path / "fc_multichunk.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    captured_iq = {}

    def fake_run(cmd, capture_output, text, timeout):
        cs16_path = cmd[3]
        raw_i16 = np.fromfile(cs16_path, dtype=np.int16).astype(np.float32)
        captured_iq["iq"] = raw_i16[0::2] + 1j * raw_i16[1::2]
        return MagicMock(stdout="", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        decoder.decode_raw(
            raw_path=raw_path,
            sample_rate=sample_rate,
            target_sample_rate=target_rate,
            pass_start_utc=None,
        )

    iq = captured_iq["iq"]
    assert len(iq) == int(target_rate * duration_sec)
    # DC 成分が除去されていること
    assert abs(np.mean(iq)) < 200.0
    # チャンク境界 (5秒目 = 48000 サンプル付近) で振幅・位相の破綻がないこと
    boundary = 5 * target_rate
    phase_diffs = np.angle(iq[boundary - 10 : boundary + 10] * np.conj(iq[boundary - 11 : boundary + 9]))
    expected_step = 2.0 * np.pi * sig_freq_hz / target_rate
    assert np.all(np.abs(phase_diffs - expected_step) < 0.2)


def test_funcube_decoder_rejects_out_of_band_rfi(tmp_path):
    """48kSPS デシメーション後の ±4kHz LPF が帯域外 (+10kHz) の強力 RFI を抑圧し希望波を保護すること"""
    decoder = FUNcubeDecoder(binary_path="gr_satellites")
    decoder.has_binary = True

    sample_rate = 240000
    target_rate = 48000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate

    # 希望波 (+600 Hz) + 強力な帯域外干渉波 (+10 kHz)
    desired = 20.0 * np.exp(1j * 2.0 * np.pi * 600.0 * t)
    rfi = 80.0 * np.exp(1j * 2.0 * np.pi * 10000.0 * t)
    combined = desired + rfi

    raw_bytes = np.empty(num_samples * 2, dtype=np.uint8)
    raw_bytes[0::2] = np.clip(np.round(127.5 + np.real(combined)), 0, 255).astype(np.uint8)
    raw_bytes[1::2] = np.clip(np.round(127.5 + np.imag(combined)), 0, 255).astype(np.uint8)

    raw_path = str(tmp_path / "fc_rfi.raw")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes.tobytes())

    captured_iq = {}

    def fake_run(cmd, capture_output, text, timeout):
        raw_i16 = np.fromfile(cmd[3], dtype=np.int16).astype(np.float32)
        captured_iq["iq"] = raw_i16[0::2] + 1j * raw_i16[1::2]
        return MagicMock(stdout="", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        decoder.decode_raw(
            raw_path=raw_path,
            sample_rate=sample_rate,
            target_sample_rate=target_rate,
            pass_start_utc=None,
        )

    iq = captured_iq["iq"]
    spec = np.abs(np.fft.fftshift(np.fft.fft(iq * np.hanning(len(iq))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), 1.0 / target_rate))
    pwr_600 = float(np.max(spec[np.abs(freqs - 600.0) < 200.0]))
    pwr_10k = float(np.max(spec[np.abs(freqs - 10000.0) < 500.0]))
    assert pwr_600 > pwr_10k * 100.0  # +10kHz RFI が 20dB 以上抑圧されていること



