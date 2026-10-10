import os
import wave
import json
import numpy as np
import pytest
from pathlib import Path
from aprs_decoder import APRSDecoder, APRSFrame
from spectrogram_generator import SpectrogramGenerator
from analyzer import PassAnalyzer


@pytest.fixture
def sample_wav(tmp_path):
    wav_path = tmp_path / "test_tone.wav"
    sample_rate = 48000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / sample_rate
    # 1200Hz と 2200Hz (AFSK Bell 202 トーン) の混合波
    audio = 0.5 * np.sin(2 * np.pi * 1200 * t) + 0.5 * np.sin(2 * np.pi * 2200 * t)
    pcm = (audio * 32767).astype(np.int16)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    return str(wav_path)


def test_spectrogram_generation(sample_wav, tmp_path):
    """WAV からスペクトログラム PNG 画像が正常に生成されることの検証"""
    output_png = tmp_path / "spectrogram.png"
    generator = SpectrogramGenerator()
    result_path = generator.generate(sample_wav, str(output_png), title="ISS Pass Test")

    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 1000  # 有効な画像サイズ


def test_aprs_decoder_parse_output():
    """direwolf の標準出力テキストが正しく APRSFrame オブジェクトに変換されることの検証"""
    decoder = APRSDecoder()
    raw_output = """
    [0.1] JA1XXX>CQ,ARISS*::Hello from Tokyo via ISS!
    [0.2] RS0ISS>BEACON:ARISS Packet System Active
    """
    frames = decoder.parse_direwolf_output(raw_output)
    assert len(frames) == 2

    assert frames[0].source == "JA1XXX"
    assert frames[0].destination == "CQ"
    assert frames[0].repeater == "ARISS"
    assert "Hello from Tokyo via ISS!" in frames[0].message

    assert frames[1].source == "RS0ISS"
    assert frames[1].destination == "BEACON"


def test_pass_analyzer_e2e(sample_wav, tmp_path):
    """PassAnalyzer が packets.json, spectrogram.png, summary.json を正しく生成することの検証"""
    analyzer = PassAnalyzer()
    out_dir = tmp_path / "results"

    results = analyzer.analyze_pass(
        wav_path=sample_wav,
        satellite="ISS",
        pass_id="ISS_20261010_090048",
        output_dir=str(out_dir),
    )

    assert (out_dir / "summary.json").exists()
    assert (out_dir / "packets.json").exists()
    assert (out_dir / "spectrogram.png").exists()

    with open(out_dir / "summary.json") as f:
        summary = json.load(f)
        assert summary["satellite"] == "ISS"
        assert summary["pass_id"] == "ISS_20261010_090048"
