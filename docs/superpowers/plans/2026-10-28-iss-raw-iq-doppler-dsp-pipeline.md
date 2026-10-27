# ISS 生 IQ 録音とクラウド側 DSP ドップラー相殺復調パイプライン 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ISS（145.825MHz）の観測パイプラインを Meteor/FUNcube と同様の「生 IQ 録音 ＋ クラウド側 DSP ドップラー相殺復調」に刷新し、衛星の高速移動に伴うドップラー偏移（最大 $\pm 3.5\ \text{kHz}$）を完全キャンセルして Dire Wolf による高精度な APRS パケット復号を実現する。

**Architecture:** エッジ側（`satellite-tracker-rs`）は `rtl_sdr` による 240kSPS 生 IQ（`.raw`）の記録に責務を統一。クラウド側（`satellite-analyzer`）で SGP4 軌道から計算した瞬時ドップラー偏移 $\Delta f(t)$ を複素ミキシングで相殺し、位相差分 FM 復調と FIR デシメーションを経て 48kHz WAV を生成して `APRSDecoder`（`atest -B 1200`）へ渡す。

**Tech Stack:** Rust (Tokio), Python 3.10+, NumPy, SciPy, SGP4, Dire Wolf (`atest`), Docker, Kubernetes (k3s/EdgeCore), Garage S3

**Spec:** [docs/superpowers/specs/2026-10-28-iss-raw-iq-doppler-dsp-pipeline-design.md](../../specs/2026-10-28-iss-raw-iq-doppler-dsp-pipeline-design.md)

## Global Constraints

- ISS ダウンリンク周波数: $145.825\ \text{MHz}$
- 生 IQ サンプリング周波数: $240\ \text{kSPS}$ (`uint8` / `cu8` 形式)
- 復調音声出力仕様: $48\ \text{kSPS}$ 16-bit リニア PCM モノラル WAV
- 変調方式: 1200bps AFSK (Bell 202: Mark $1200\ \text{Hz}$, Space $2200\ \text{Hz}$)
- データリンク層: AX.25 UI フレーム (CRC-16-CCITT 検証)
- 地上局観測点座標: 北緯 35.6895°、東経 139.6917°、標高 30m

## Review Focus

1. **破損・極小生 IQ ファイル**: ファイルサイズが 100KB 未満の場合にクラッシュせず安全にスキップして `packets_count = 0` を返すこと。
2. **TLE 不在 / 時刻パース失敗**: パス ID から時刻が特定できない場合や TLE が欠落している場合でも、ドップラー補正なし（中心固定）で安全にフォールバックすること。
3. **後方互換性**: S3 から渡されたファイルが `.raw` ではなく `.wav` の場合でも、既存の `APRSDecoder.decode_wav` を直接呼んで正常処理できること。
4. **ドップラー逆回転方向**: 接近時（$v_r < 0 \rightarrow \Delta f > 0$）に正しく負の周波数シフト $e^{-j 2\pi \Delta f t}$ でミキシングされること。
5. **メモリ効率**: 5分間の生データ（約 144MB）を処理する際、不要な配列コピーを作らず数秒以内に高速にベクトル演算完了すること。

---

### Task 1: `satellite-tracker-rs` の生 IQ 録音対象に ISS を追加

**Files:**
- Modify: `apps/satellite-tracker-rs/src/receiver.rs:73-77`
- Test: `apps/satellite-tracker-rs/tests/receiver_test.rs`

**Interfaces:**
- Produces: `is_raw_iq_target(sat_name: &str) -> bool`

- [ ] **Step 1: Write the failing test**

```rust
// apps/satellite-tracker-rs/tests/receiver_test.rs
use satellite_tracker_rs::receiver::is_raw_iq_target;

#[test]
fn test_is_raw_iq_target_includes_iss() {
    assert!(is_raw_iq_target("ISS (ZARYA)"));
    assert!(is_raw_iq_target("ISS"));
    assert!(is_raw_iq_target("METEOR-M2 4"));
    assert!(is_raw_iq_target("FUNCUBE-1 (AO-73)"));
    assert!(!is_raw_iq_target("NOAA 19"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --test receiver_test test_is_raw_iq_target_includes_iss`
Expected: FAIL with assertion failed: `is_raw_iq_target("ISS (ZARYA)")`

- [ ] **Step 3: Implement `is_raw_iq_target` in `apps/satellite-tracker-rs/src/receiver.rs`**

```rust
pub fn is_raw_iq_target(sat_name: &str) -> bool {
    let name_upper = sat_name.to_uppercase();
    name_upper.contains("METEOR")
        || name_upper.contains("FUNCUBE")
        || name_upper.contains("AO-73")
        || name_upper.contains("ISS")
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --test receiver_test test_is_raw_iq_target_includes_iss`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-tracker-rs/src/receiver.rs apps/satellite-tracker-rs/tests/receiver_test.rs
git commit -m "feat: satellite-tracker-rsでISSを生IQ録音対象に追加"
```

---

### Task 2: `satellite-analyzer` の DSP 復調器（`ISSDspDemodulator`）の実装（TDD）

**Files:**
- Create: `apps/satellite-analyzer/src/iss_dsp.py`
- Test: `apps/satellite-analyzer/tests/test_iss_dsp.py`

**Interfaces:**
- Produces:
  ```python
  class ISSDspDemodulator:
      def __init__(self, lat: float = 35.6895, lon: float = 139.6917, alt_m: float = 30.0): ...
      def compute_doppler_shifts(self, tle_line1: str, tle_line2: str, start_time: datetime, duration_sec: float, center_freq_hz: float = 145825000.0, step_sec: float = 1.0) -> np.ndarray: ...
      def demodulate_raw_iq(self, raw_path: str, pass_start_utc: Optional[datetime] = None, tle_lines: Optional[Tuple[str, str]] = None, center_freq_hz: float = 145825000.0, sample_rate: int = 240000, output_wav_path: Optional[str] = None) -> str: ...
  ```

- [ ] **Step 1: Write the failing test**

```python
# apps/satellite-analyzer/tests/test_iss_dsp.py
import os
import wave
import numpy as np
import pytest
from datetime import datetime, timezone
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

    # 2. ドップラー相殺付き復調の実行 (手動指定のドップラー補正関数または TLE モック)
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
        assert wf.getsampwidth() == 2 # 16-bit
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)

    # 3. FFT 解析で 1200Hz に明確なピークがあることを確認
    fft_mag = np.abs(np.fft.rfft(audio.astype(float)))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / 48000)
    peak_idx = np.argmax(fft_mag[10:]) + 10 # DC 除外
    peak_freq = freqs[peak_idx]
    assert abs(peak_freq - 1200.0) < 50.0

def test_demodulate_empty_or_small_file(tmp_path):
    demod = ISSDspDemodulator()
    empty_raw = str(tmp_path / "empty.raw")
    with open(empty_raw, "wb") as f:
        f.write(b"")
    result = demod.demodulate_raw_iq(raw_path=empty_raw, output_wav_path=str(tmp_path / "empty.wav"))
    assert result == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-analyzer/tests/test_iss_dsp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'iss_dsp'`

- [ ] **Step 3: Implement `ISSDspDemodulator` in `apps/satellite-analyzer/src/iss_dsp.py`**

- SGP4 視線速度計算（1秒ごと計算 ＋ `np.interp`）
- uint8 IQ から複素数変換
- 複素ミキシング：$y[n] = x[n] e^{-j \theta[n]}$
- 位相差分 FM 復調：$\Delta \phi[n] = \arg(y[n] y^*[n-1])$
- FIR ローパス（4kHz）および `scipy.signal.resample_poly(..., 1, 5)` で 48kSPS へ変換
- 16-bit PCM WAV 書き出し

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-analyzer/tests/test_iss_dsp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-analyzer/src/iss_dsp.py apps/satellite-analyzer/tests/test_iss_dsp.py
git commit -m "feat: satellite-analyzerにISS用ドップラー相殺FM復調器を追加"
```

---

### Task 3: `satellite-analyzer` ワークフロー統合（`activities.py`）

**Files:**
- Modify: `apps/satellite-analyzer/src/activities.py:90-133, 199-265`
- Test: `apps/satellite-analyzer/tests/test_iss_dsp.py`

**Interfaces:**
- Consumes: `ISSDspDemodulator.demodulate_raw_iq` from Task 2
- Modifies: `decode_packets_activity` and `analyze_satellite_pass_activity` to handle `.raw` / `.u8` for `amateur_packet`

- [ ] **Step 1: Write integration test case in `tests/test_iss_dsp.py`**

```python
def test_decode_packets_activity_with_raw_iq(tmp_path):
    from activities import decode_packets_activity
    # 正常な raw ファイルを準備し、decode_packets_activity がクラッシュせず辞書を返すことを検証
    raw_path = str(tmp_path / "ISS (ZARYA)_20261028_120000.raw")
    with open(raw_path, "wb") as f:
        f.write(bytes([128] * (240000 * 2))) # 1秒分の無音 IQ

    import asyncio
    res = asyncio.run(decode_packets_activity(raw_path, satellite="ISS (ZARYA)"))
    assert res["satellite"] == "ISS (ZARYA)"
    assert res["satellite_type"] == "SpaceStation"
    assert "packets_count" in res
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-analyzer/tests/test_iss_dsp.py::test_decode_packets_activity_with_raw_iq -v`
Expected: FAIL or warning because `.raw` is not yet converted to `.wav` for APRSDecoder

- [ ] **Step 3: Modify `apps/satellite-analyzer/src/activities.py`**

- `category == "amateur_packet"` のブロックにおいて：
  - 入力ファイル拡張子が `.raw` または `.u8` の場合、`ISSDspDemodulator().demodulate_raw_iq(...)` を呼び出して同ディレクトリに一時 WAV を生成。
  - 生成された WAV を `APRSDecoder.decode_wav` に渡す。
  - スペクトログラム生成時も生成された WAV を使用。
  - 入力ファイルが `.wav` の場合は既存通りそのまま処理。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-analyzer/tests/test_iss_dsp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-analyzer/src/activities.py apps/satellite-analyzer/tests/test_iss_dsp.py
git commit -m "feat: satellite-analyzerのアクティビティを生IQ入力ドップラー復調に対応"
```

---

### Task 4: コンテナイメージのビルド、レジストリプッシュ、およびクラスタ更新

**Files:**
- None (Build & Deployment Commands)

- [ ] **Step 1: Build & push `satellite-tracker-rs`**

```bash
docker build -t 127.0.0.1:30500/satellite-tracker:latest apps/satellite-tracker-rs
docker push 127.0.0.1:30500/satellite-tracker:latest
kubectl rollout restart deploy/satellite-tracker
kubectl rollout status deploy/satellite-tracker
```

- [ ] **Step 2: Build & push `satellite-analyzer`**

```bash
docker build -t 127.0.0.1:30500/satellite-analyzer:latest apps/satellite-analyzer
docker push 127.0.0.1:30500/satellite-analyzer:latest
kubectl rollout restart deploy/satellite-analyzer-worker
kubectl rollout status deploy/satellite-analyzer-worker
```

- [ ] **Step 3: Verify all pods are Running**

Run: `kubectl get pods -A | grep -E "(satellite-tracker|satellite-analyzer)"`
Expected: All pods Running and 1/1 Ready.

---

### Task 5: 質疑応答とドキュメントの記録（GEMINI.md）

**Files:**
- Modify: `docs/04_qa.md`

- [ ] **Step 1: Add Q&A section to `docs/04_qa.md`**

Q&A 項目として以下を追記：
- ISS 復調率が見かけ上高かった理由（過去の結合テスト用モックデータ 40 件の残存とパージ）
- 観測実績メトリクス（直近 25 パスすべてパケット未検出）の正常化
- Meteor / FUNcube と共通化された「案 B（生 IQ 録音 ＋ クラウド側 DSP 完全ドップラー相殺）」の電波工学的背景と Carson の規則

- [ ] **Step 2: Verify git status and commit**

```bash
git add docs/04_qa.md
git commit -m "docs: ISS復調率の真相と生IQドップラー補正パイプラインの質疑応答を追加"
```
