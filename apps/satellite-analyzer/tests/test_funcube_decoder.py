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
            assert cmd[6] == "240000"
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

