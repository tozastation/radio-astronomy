import os
import wave
import numpy as np
import pytest
from pathlib import Path
from audio_spooler import AudioSpooler


@pytest.fixture
def temp_spool_dir(tmp_path):
    spool_dir = tmp_path / "spool"
    spool_dir.mkdir()
    return spool_dir


def test_audio_spooler_record_pass(temp_spool_dir):
    """48kHz モノラル WAV ファイルが正しく生成・録音されることの検証"""
    spooler = AudioSpooler(
        spool_dir=str(temp_spool_dir),
        input_sample_rate=2.4e6,
        target_sample_rate=48000,
    )

    wav_path = spooler.start_pass(satellite="ISS", pass_id="ISS_20261010_090048")
    assert Path(wav_path).exists()

    # 2.4 MSPS で 0.1 秒分 (240,000 サンプル) の複素正弦波 (1kHz オーディオトーン) を生成
    duration_sec = 0.1
    num_samples = int(2.4e6 * duration_sec)
    t = np.arange(num_samples) / 2.4e6
    freq_tone = 1000.0
    iq = np.exp(1j * 2 * np.pi * freq_tone * t).astype(np.complex64)

    # サンプル書き込み
    spooler.write_samples(iq, doppler_hz=0.0)

    finished_path = spooler.finish_pass()
    assert finished_path == wav_path
    assert os.path.exists(finished_path)

    # WAV ファイルヘッダの検証
    with wave.open(finished_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2  # 16-bit
        assert wf.getframerate() == 48000
        n_frames = wf.getnframes()
        # 0.1秒分なので約4800フレーム
        assert abs(n_frames - 4800) < 50


def test_audio_spooler_circuit_breaker(temp_spool_dir):
    """スプール容量超過時に最古ファイルが安全に破棄されることの検証"""
    spooler = AudioSpooler(
        spool_dir=str(temp_spool_dir),
        max_spool_bytes=2000,  # 極小の容量制限 (2KB)
    )

    # ダミーファイルを3つ作成 (それぞれ1KB)
    f1 = temp_spool_dir / "oldest.wav"
    f2 = temp_spool_dir / "middle.wav"
    f3 = temp_spool_dir / "newest.wav"

    f1.write_bytes(b"x" * 1000)
    os.utime(f1, (100, 100))
    f2.write_bytes(b"x" * 1000)
    os.utime(f2, (200, 200))
    f3.write_bytes(b"x" * 1000)
    os.utime(f3, (300, 300))

    # サーキットブレーカー発動
    spooler.enforce_circuit_breaker()

    # 合計3000バイト > 2000バイト なので最古の f1 が削除されていること
    assert not f1.exists()
    assert f2.exists()
    assert f3.exists()
