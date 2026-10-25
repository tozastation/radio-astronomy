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


def test_spectrogram_generation_from_raw_iq_full_pass(tmp_path):
    """生IQ (.raw, cu8 240kSPS) からパス全体・両側複素スペクトル (-120kHz..+120kHz) が正しく解析・描画されることの検証"""
    raw_path = tmp_path / "meteor_pass.raw"
    fs = 240000
    duration_sec = 6.0  # 旧実装の 48000*20 bytes (2秒分) を超える長さ
    n_samples = int(fs * duration_sec)
    rng = np.random.default_rng(42)
    noise_i = rng.normal(0.0, 2.0, n_samples)
    noise_q = rng.normal(0.0, 2.0, n_samples)

    # 後半 (t = 3.0s..6.0s) に +30 kHz の複素正弦波 e^{+j 2\pi f t} を注入 (-30 kHz には出ない)
    t_second_half = np.arange(n_samples // 2) / fs
    tone_freq = 30000.0
    noise_i[n_samples // 2 :] += 40.0 * np.cos(2.0 * np.pi * tone_freq * t_second_half)
    noise_q[n_samples // 2 :] += 40.0 * np.sin(2.0 * np.pi * tone_freq * t_second_half)

    cu8 = np.empty(n_samples * 2, dtype=np.uint8)
    cu8[0::2] = np.clip(np.round(127.5 + noise_i), 0, 255).astype(np.uint8)
    cu8[1::2] = np.clip(np.round(127.5 + noise_q), 0, 255).astype(np.uint8)
    raw_path.write_bytes(cu8.tobytes())

    generator = SpectrogramGenerator()
    freqs_khz, times_sec, sxx_db = generator.compute_complex_iq_spectrogram(
        str(raw_path), sample_rate=fs
    )

    # 1. パス全体 (約 6.0 秒) が切り捨てられずに含まれていること
    assert times_sec[-1] >= 5.5
    # 2. 周波数軸が両側複素ベースバンド (-120 kHz .. +120 kHz) であること
    assert freqs_khz[0] <= -110.0 and freqs_khz[-1] >= 110.0
    # 3. 後半における +30 kHz のパワーが -30 kHz (イメージ周波数) より 15 dB 以上高いこと
    idx_pos30 = int(np.argmin(np.abs(freqs_khz - 30.0)))
    idx_neg30 = int(np.argmin(np.abs(freqs_khz - (-30.0))))
    second_half_mask = times_sec >= 3.5
    pos30_db = float(np.mean(sxx_db[idx_pos30, second_half_mask]))
    neg30_db = float(np.mean(sxx_db[idx_neg30, second_half_mask]))
    assert pos30_db - neg30_db > 15.0

    output_png = tmp_path / "meteor_spectrogram.png"
    result_path = generator.generate(str(raw_path), str(output_png), title="METEOR-M2 4 Pass Test")
    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 5000



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


def test_aprs_decoder_parse_atest_output():
    """atest の実機デコード出力 (DECODED行や <0x0a> 制御文字を含む形式) が正常にパースされることの検証"""
    decoder = APRSDecoder()
    raw_output = """
    44100 samples per second.  16 bits per sample.  1 audio channels.
    42398 audio bytes in file.  Duration = 0.5 seconds.
    Fix Bits level = 0
    Channel 0: 1200 baud, AFSK 1200 & 2200 Hz, A, 44100 sample rate.

    DECODED[1] 0:00.472 WB2OSZ audio level = 99(28/28)     
    [0] WB2OSZ>WORLD:Hello, world!<0x0a>

    1 from /tmp/gen_hello.wav
    1 packets decoded in 0.007 seconds.  70.0 x realtime
    """
    frames = decoder.parse_atest_output(raw_output)
    assert len(frames) == 1
    assert frames[0].source == "WB2OSZ"
    assert frames[0].destination == "WORLD"
    assert frames[0].message == "Hello, world!"


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
