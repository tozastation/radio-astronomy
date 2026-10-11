import os
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
