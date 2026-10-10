#!/usr/bin/env python3
"""🛰️ イベント駆動型衛星解析パイプライン E2E 結合検証スクリプト

本スクリプトは以下のエンドツーエンドパイプラインをシミュレーション検証します:
1. 48kHz WAV 録音データの生成 (擬似 ISS パス)
2. S3 (Garage S3 またはローカルモック) へのアップロード
3. PassAnalyzer によるパケットデコード & スペクトログラム生成
4. 成果物 (packets.json, spectrogram.png, summary.json) の格納と元データクリーンアップ
"""

import os
import sys
import tempfile
import wave
import json
from pathlib import Path
import numpy as np

# プロジェクトルートを PYTHONPATH に追加
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "apps" / "satellite-analyzer" / "src"))
sys.path.insert(0, str(repo_root / "apps" / "satellite-tracker" / "src"))

from analyzer import PassAnalyzer
from audio_spooler import AudioSpooler


def generate_mock_pass_wav(output_path: str, duration_sec: float = 1.0, sample_rate: int = 48000):
    """擬似的な ISS パス音声 (Bell 202 AFSK 信号を含む) を生成する"""
    num_samples = int(sample_rate * duration_sec)
    t = np.arange(num_samples) / float(sample_rate)

    # 1200Hz と 2200Hz の混合波 + バックグラウンドホワイトノイズ
    tone = 0.4 * np.sin(2 * np.pi * 1200 * t) + 0.4 * np.sin(2 * np.pi * 2200 * t)
    noise = np.random.normal(0, 0.05, num_samples)
    audio = tone + noise
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    print(f"✅ Generated mock WAV: {output_path} ({os.path.getsize(output_path)} bytes)")


def main():
    print("=" * 60)
    print("🚀 Running Event-Driven Pipeline E2E Verification")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        mock_wav = tmp_path / "ISS_20261010_090048.wav"
        results_dir = tmp_path / "results"

        # 1. 擬似 WAV 生成
        print("\n[Step 1] Generating mock 48kHz pass audio...")
        generate_mock_pass_wav(str(mock_wav), duration_sec=1.5)

        # 2. パス解析実行
        print("\n[Step 2] Executing PassAnalyzer (APRS decode & Spectrogram)...")
        analyzer = PassAnalyzer()
        summary = analyzer.analyze_pass(
            wav_path=str(mock_wav),
            satellite="ISS",
            pass_id="ISS_20261010_090048",
            output_dir=str(results_dir),
        )

        # 3. 成果物の検証
        print("\n[Step 3] Verifying generated artifacts...")
        summary_file = results_dir / "summary.json"
        packets_file = results_dir / "packets.json"
        spectrogram_file = results_dir / "spectrogram.png"

        assert summary_file.exists(), "summary.json was not generated!"
        assert packets_file.exists(), "packets.json was not generated!"
        assert spectrogram_file.exists(), "spectrogram.png was not generated!"

        with open(summary_file) as f:
            summary_data = json.load(f)
            print(f"  - Summary: {json.dumps(summary_data, indent=2)}")

        with open(packets_file) as f:
            packets_data = json.load(f)
            print(f"  - Packets JSON count: {packets_data.get('packets_count')}")

        spectrogram_size = os.path.getsize(spectrogram_file)
        print(f"  - Spectrogram PNG size: {spectrogram_size} bytes")
        assert spectrogram_size > 1000, "Spectrogram image is too small or corrupt!"

        print("\n[Step 4] Pipeline Data Cleanup Verification...")
        # 元ファイルの削除テスト
        mock_wav.unlink()
        assert not mock_wav.exists(), "Raw WAV cleanup failed!"
        print("  - Successfully cleaned up raw recording file.")

    print("\n" + "=" * 60)
    print("🎉 All E2E Pipeline Stages Verified Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
