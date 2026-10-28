# 24時間ハイブリッド宇宙観測所：衛星スマート足切り × 天の川銀河21cmドリフトスキャン Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 復調見込みのない低仰角衛星パスを自動足切り（Pass Gating）し、1日約22時間の空白時間を天の川銀河の21cm中性水素線（1420.405 MHz）周波数スイッチング分光および銀河連続波ドリフトスキャン観測へ自動配分・可視化する。

**Architecture:** エッジ側の `satellite-tracker-rs` 単一プロセス内で、衛星パス最大仰角 $\text{El}_{\max}$ による足切り判定（METEOR $\ge 25^\circ$, 他 $\ge 20^\circ$）を行い、アイドル時間および低仰角パススキップ中は 5 分サイクルの銀河ドリフトスキャン（天球・銀河座標算出 + ON/OFF 周波数スイッチング FFT 積算）を実行して Garage S3 の `galactic/YYYY-MM-DD/scan_HHMMSS.json` へ保存する。Web 側の `satellite-viewer` は S3 からスキャン結果を 16 スレッド並列フェッチし、現在時刻の天の川ポインティング座標 $(l, b)$・21cm 視線速度スペクトル・24時間ドリフト曲線を『Galactic Monitor』UI に描画する。

**Tech Stack:** Rust (`sgp4`, `rustfft`, `serde`, `tokio`), Python 3.12 (`ThreadPoolExecutor`, `boto3`, `gzip`), HTML5 Canvas, Garage S3

**Spec:** `docs/superpowers/specs/2026-10-28-galactic-drift-scan-design.md`

## Global Constraints

- ハードウェア変更なし（既存の北向きベランダ 137 MHz Vダイポール + RTL-SDR Blog V4 1台のままソフトウェア制御で完結）。
- Pass Gating 閾値：広帯域気象衛星（`METEOR` を含む衛星名）は $\text{El}_{\max} \ge 25.0^\circ$（環境変数 `MIN_MAX_EL_METEOR_DEG`）、その他狭帯域衛星は $\text{El}_{\max} \ge 20.0^\circ$（環境変数 `MIN_MAX_EL_DEFAULT_DEG`）。
- 銀河ポインティング代表方向：方位角 $\text{Az} = 0.0^\circ$（`BEAM_AZ_DEG`）、仰角 $\text{El} = 40.0^\circ$（`BEAM_EL_DEG`）。
- 中性水素静止周波数 $\nu_0 = 1420.405751768\text{ MHz}$、OFF 参照周波数 $f_{\text{OFF}} = 1423.000000\text{ MHz}$、サンプリングレート $2.048\text{ MSPS}$、FFT 1024 点。
- Garage S3 保存キー：`galactic/YYYY-MM-DD/scan_HHMMSS.json`（`raw/` を汚染せず Temporal ワークフローを誤起動させない）。
- `satellite-viewer` の S3 複数オブジェクト取得は `ThreadPoolExecutor(max_workers=16)` を用い、HTTP レスポンスは 1KB 超で Gzip 圧縮（level=6）する。
- `HTML_TEMPLATE` 内の新規 JavaScript グローバル変数・関数・DOM ID はすべて `galactic` プレフィックスを付与し、`node -c` による構文解析テストを通過させる。
- コミットメッセージは日本語、絵文字禁止、Conventional Commits 形式（`feat: ...`, `test: ...`, `docs: ...`）とする。

## Review Focus

- `P_OFF` の特定ビンが `0.0` または極小値になった場合でもゼロ除算（`NaN` / `Infinity`）を起こさず安全に `0.0` へフォールバックし、有効な JSON としてシリアライズできること（Task 2 でテスト）。
- 銀経 $l$・赤経 $\alpha$ の逆三角関数計算において、角度が必ず $[0.0, 360.0)$ の範囲に正規化され、負値にならないこと（Task 2, Task 4 でテスト）。
- 衛星がすでに可視範囲（$\text{El} \ge 10^\circ$）に進入している途中の時刻からパス最大仰角を評価しても、そのパス全体のピーク仰角 $\text{El}_{\max}$ を正しく算出して足切り判定できること（Task 1 でテスト）。
- S3 の `galactic/` 配下にスキャン JSON が 0 件の初期状態でも、`/api/galactic-observations` がエラーにならず現在の理論銀河座標 $(l, b)$ と 24時間ドリフト予測曲線を返すこと（Task 4 でテスト）。
- 単一 HTML テンプレートへの『Galactic Monitor』追加後、既存の `orbit*` / `replay*` / `cal*` スクリプトと変数重複せず `node -c` 構文チェックがパスすること（Task 5 でテスト）。

---

### Task 1: エッジ軌道計算のスマート足切り（Pass Gating）実装

**Files:**
- Modify: `apps/satellite-tracker-rs/src/orbit.rs`
- Test: `apps/satellite-tracker-rs/tests/orbit_test.rs`

**Interfaces:**
- Produces:
  - `OrbitPredictor::is_qualifying_pass(sat_name: &str, max_elevation_deg: f64, min_meteor_el_deg: f64, min_default_el_deg: f64) -> bool`
  - `OrbitPredictor::estimate_active_pass_max_el(&self, sat_name: &str, tle_line1: &str, tle_line2: &str, freq_hz: f64, now: DateTime<Utc>) -> Result<f64>`

- [ ] **Step 1: Write the failing test in `apps/satellite-tracker-rs/tests/orbit_test.rs`**

```rust
#[test]
fn test_pass_gating_qualifies_high_el_and_rejects_marginal_passes() {
    // METEOR は 25.0° 以上のみ合格
    assert!(!OrbitPredictor::is_qualifying_pass("METEOR-M2 3", 18.5, 25.0, 20.0));
    assert!(!OrbitPredictor::is_qualifying_pass("METEOR-M2 4", 24.9, 25.0, 20.0));
    assert!(OrbitPredictor::is_qualifying_pass("METEOR-M2 3", 25.0, 25.0, 20.0));
    assert!(OrbitPredictor::is_qualifying_pass("METEOR-M2 4", 52.3, 25.0, 20.0));

    // ISS / FUNCUBE 等は 20.0° 以上で合格
    assert!(!OrbitPredictor::is_qualifying_pass("ISS (ZARYA)", 14.2, 25.0, 20.0));
    assert!(OrbitPredictor::is_qualifying_pass("ISS (ZARYA)", 20.0, 25.0, 20.0));
    assert!(OrbitPredictor::is_qualifying_pass("FUNCUBE-1 (AO-73)", 22.5, 25.0, 20.0));
}

#[test]
fn test_estimate_active_pass_max_el_finds_peak_across_window() {
    let predictor = OrbitPredictor::new(35.6895, 139.6917, 30.0, "NORTH", 10.0);
    let line1 = "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996";
    let line2 = "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263";
    let start = Utc.with_ymd_and_hms(2026, 10, 21, 0, 0, 0).unwrap();
    if let Ok(Some(next_pass)) = predictor.get_next_pass("ISS (ZARYA)", line1, line2, 145_825_000.0, start, 24.0, 10) {
        // AOS 直後の時刻から評価してもパス全体のピーク仰角が得られること
        let est_max = predictor
            .estimate_active_pass_max_el("ISS (ZARYA)", line1, line2, 145_825_000.0, next_pass.aos_time)
            .unwrap();
        assert!((est_max - next_pass.max_elevation_deg).abs() < 2.0);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --package satellite-tracker-rs --test orbit_test`
Expected: FAIL with `no function or associated item named is_qualifying_pass`

- [ ] **Step 3: Implement `is_qualifying_pass` and `estimate_active_pass_max_el` in `apps/satellite-tracker-rs/src/orbit.rs`**

`is_qualifying_pass` checks if `sat_name.to_uppercase().contains("METEOR")` to compare against `min_meteor_el_deg`, otherwise `min_default_el_deg`. `estimate_active_pass_max_el` scans `now - 10 min` to `now + 20 min` in `10s` steps while `is_in_view` is true around `now` to return the maximum elevation in degrees. Also update `get_next_qualifying_pass` (or filter inside `get_next_pass` / caller) so non-qualifying passes are skipped when computing next AOS.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --package satellite-tracker-rs --test orbit_test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-tracker-rs/src/orbit.rs apps/satellite-tracker-rs/tests/orbit_test.rs
git commit -m "feat: 衛星パスの最大仰角によるスマート足切り判定ロジックを追加"
```

---

### Task 2: 銀河座標・LSR 視線速度補正および 21cm 周波数スイッチング分光エンジン実装

**Files:**
- Create: `apps/satellite-tracker-rs/src/galactic.rs`
- Modify: `apps/satellite-tracker-rs/src/lib.rs`
- Modify: `apps/satellite-tracker-rs/src/s3.rs`
- Test: `apps/satellite-tracker-rs/tests/galactic_test.rs`

**Interfaces:**
- Produces in `satellite_tracker_rs::galactic`:
  - `pub const HI_REST_FREQ_HZ: f64 = 1_420_405_751.768;`
  - `pub const HI_OFF_FREQ_HZ: f64 = 1_423_000_000.0;`
  - `pub struct GalacticPointing { pub az_deg: f64, pub el_deg: f64, pub lst_deg: f64, pub ra_deg: f64, pub dec_deg: f64, pub gal_l_deg: f64, pub gal_b_deg: f64, pub region_label: String, pub sky_type: String }`
  - `pub struct GalacticRfMetrics { pub center_freq_on_hz: f64, pub center_freq_off_hz: f64, pub continuum_power_db: f64, pub peak_line_snr_db: f64, pub peak_v_lsr_km_s: f64 }`
  - `pub struct GalacticSpectrum { pub v_lsr_km_s: Vec<f64>, pub relative_temp_k: Vec<f64> }`
  - `pub struct GalacticScanResult { pub timestamp_utc: String, pub integration_sec: f64, pub preempted: bool, pub pointing: GalacticPointing, pub lsr_correction_km_s: f64, pub rf_metrics: GalacticRfMetrics, pub spectrum: GalacticSpectrum }`
  - `pub fn compute_galactic_pointing(lat_deg: f64, lon_deg: f64, az_deg: f64, el_deg: f64, timestamp: DateTime<Utc>) -> GalacticPointing`
  - `pub fn compute_lsr_velocity_correction(gal_l_deg: f64, gal_b_deg: f64, ra_deg: f64, dec_deg: f64, timestamp: DateTime<Utc>) -> f64`
  - `pub fn accumulate_welch_psd(iq_samples: &[(f32, f32)], fft_size: usize) -> Vec<f64>`
  - `pub fn calibrate_frequency_switched_spectrum(psd_on: &[f64], psd_off: &[f64], sample_rate_hz: f64, center_freq_on_hz: f64, lsr_corr_km_s: f64, t_sys_k: f64) -> (GalacticSpectrum, GalacticRfMetrics)`

- [ ] **Step 1: Write the failing test in `apps/satellite-tracker-rs/tests/galactic_test.rs`**

```rust
use chrono::{TimeZone, Utc};
use satellite_tracker_rs::galactic::{
    accumulate_welch_psd, calibrate_frequency_switched_spectrum, compute_galactic_pointing,
    compute_lsr_velocity_correction, HI_OFF_FREQ_HZ, HI_REST_FREQ_HZ,
};

#[test]
fn test_galactic_pointing_normalizes_angles_and_detects_plane_vs_halo() {
    let dt = Utc.with_ymd_and_hms(2026, 10, 28, 12, 0, 0).unwrap();
    let p = compute_galactic_pointing(35.6895, 139.6917, 0.0, 40.0, dt);
    assert!((0.0..360.0).contains(&p.ra_deg));
    assert!((-90.0..=90.0).contains(&p.dec_deg));
    assert!((0.0..360.0).contains(&p.gal_l_deg));
    assert!((-90.0..=90.0).contains(&p.gal_b_deg));
    assert!(!p.region_label.is_empty());

    // 24時間（1時間刻み）回したとき、天の川銀河面 (|b| <= 15°) と銀河ハロー (|b| > 30°) の両方を通過すること
    let mut saw_plane = false;
    let mut saw_halo = false;
    for h in 0..24 {
        let t = dt + chrono::Duration::hours(h);
        let pt = compute_galactic_pointing(35.6895, 139.6917, 0.0, 40.0, t);
        if pt.gal_b_deg.abs() <= 15.0 {
            saw_plane = true;
            assert_eq!(pt.sky_type, "WARM_GALACTIC_PLANE");
        }
        if pt.gal_b_deg.abs() > 30.0 {
            saw_halo = true;
            assert_eq!(pt.sky_type, "COLD_HALO");
        }
    }
    assert!(saw_plane, "North balcony drift scan must cross the Galactic plane");
    assert!(saw_halo, "North balcony drift scan must cross the Galactic halo");
}

#[test]
fn test_frequency_switching_cancels_bandpass_and_handles_zero_off_power() {
    let fft_size = 1024;
    let sample_rate = 2_048_000.0;
    // パラボラ型のベースライン歪み + 中心付近の模擬21cmガウス信号
    let mut psd_off = vec![0.0; fft_size];
    let mut psd_on = vec![0.0; fft_size];
    for i in 0..fft_size {
        let x = (i as f64 - 512.0) / 512.0;
        let bandpass = 1.0 - 0.3 * x * x;
        psd_off[i] = bandpass;
        // ビン 532 (+40 kHz ≈ -8.4 km/s) に 10% の水素輝線ピークを重畳
        let line = 0.10 * (-((i as f64 - 532.0) / 6.0).powi(2)).exp();
        psd_on[i] = bandpass * (1.0 + line);
    }
    // ゼロ除算耐性の検証: 端の1ビンを 0.0 に設定
    psd_off[0] = 0.0;

    let (spec, metrics) = calibrate_frequency_switched_spectrum(
        &psd_on,
        &psd_off,
        sample_rate,
        HI_REST_FREQ_HZ,
        0.0,
        300.0,
    );
    assert!(!spec.v_lsr_km_s.is_empty());
    assert_eq!(spec.v_lsr_km_s.len(), spec.relative_temp_k.len());
    assert!(spec.relative_temp_k.iter().all(|v| v.is_finite()));
    // 10% * 300K ≈ 30K 付近のピークが検出されること
    let max_temp = spec.relative_temp_k.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    assert!(max_temp > 15.0 && max_temp < 45.0, "max_temp was {}", max_temp);
    assert!((metrics.peak_v_lsr_km_s - (-8.4)).abs() < 3.0);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --package satellite-tracker-rs --test galactic_test`
Expected: FAIL with `unresolved import satellite_tracker_rs::galactic`

- [ ] **Step 3: Implement `apps/satellite-tracker-rs/src/galactic.rs`, register `pub mod galactic;` in `src/lib.rs`, and support `.json` Content-Type in `src/s3.rs`**

In `src/galactic.rs`, implement the spherical trigonometry transformations (`compute_galactic_pointing`), LSR velocity correction (`compute_lsr_velocity_correction`), 1024-pt Hanning-windowed Welch PSD accumulation (`accumulate_welch_psd`), frequency-switched baseline subtraction with linear slope detrending (`calibrate_frequency_switched_spectrum` restricted to $v_{\text{LSR}} \in [-150.0, +150.0]\text{ km/s}$), and a helper `generate_synthetic_galactic_iq(count: usize, sample_rate: f64, is_on_band: bool, gal_l_deg: f64, gal_b_deg: f64, seed: &mut u64) -> Vec<(f32, f32)>` so mock mode produces physically realistic Galactic plane profiles when $|b| \le 15^\circ$.
In `src/s3.rs`, set `content-type` to `"application/json"` when `local_path` ends with `.json` (defaulting to `"audio/wav"` otherwise).

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --package satellite-tracker-rs --test galactic_test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-tracker-rs/src/galactic.rs apps/satellite-tracker-rs/src/lib.rs apps/satellite-tracker-rs/src/s3.rs apps/satellite-tracker-rs/tests/galactic_test.rs
git commit -m "feat: 天の川銀河座標変換と21cm周波数スイッチング分光DSPモジュールを追加"
```

---

### Task 3: エッジメインループへの銀河ドリフトスキャン & プリエンプション統合

**Files:**
- Modify: `apps/satellite-tracker-rs/src/main.rs`
- Test: `apps/satellite-tracker-rs/tests/integration_test.rs`

**Interfaces:**
- Consumes: `OrbitPredictor::is_qualifying_pass`, `OrbitPredictor::estimate_active_pass_max_el`, `galactic::*`
- Produces: Autonomous switching between Qualifying Satellite Pass recording and 300-second Galactic 21cm Drift Scan cycles with S3 upload to `galactic/YYYY-MM-DD/scan_HHMMSS.json`.

- [ ] **Step 1: Write the failing integration test in `apps/satellite-tracker-rs/tests/integration_test.rs`**

Add a test `test_galactic_scan_cycle_and_s3_key_generation` verifying that accumulating ON/OFF slices and flushing produces a valid `GalacticScanResult` JSON file at `spool_dir/scan_YYYYMMDD_HHMMSS.json` and uploads to `galactic/YYYY-MM-DD/scan_HHMMSS.json` via `S3Uploader` in mock mode.

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --package satellite-tracker-rs --test integration_test`
Expected: FAIL

- [ ] **Step 3: Update `apps/satellite-tracker-rs/src/main.rs` and `src/galactic.rs` with `GalacticScanSession`**

Add `GalacticScanSession` in `src/galactic.rs` that accumulates ON/OFF PSD slices every tick during idle time (and during skipped marginal satellite passes), flushes after `300.0` seconds (or early-flushes when a qualifying satellite pass is within `30.0` seconds of AOS and at least `60.0` seconds have been integrated), writes the JSON to `spool_dir`, and uploads to `galactic/YYYY-MM-DD/scan_HHMMSS.json`. In `main.rs`, filter `visible_candidates` and `get_next_pass` using `OrbitPredictor::is_qualifying_pass` with `MIN_MAX_EL_METEOR_DEG` (`25.0`) and `MIN_MAX_EL_DEFAULT_DEG` (`20.0`).

- [ ] **Step 4: Run all Rust tests to verify they pass**

Run: `cargo test --package satellite-tracker-rs`
Expected: PASS (all unit and integration tests green)

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-tracker-rs/src/galactic.rs apps/satellite-tracker-rs/src/main.rs apps/satellite-tracker-rs/tests/integration_test.rs
git commit -m "feat: メインループに低仰角パス足切りと銀河ドリフトスキャン自動切替を統合"
```

---

### Task 4: Web Viewer バックエンド銀河座標計算・S3 並列フェッチ API 実装

**Files:**
- Modify: `apps/satellite-viewer/orbit_calculator.py`
- Modify: `apps/satellite-viewer/app.py`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Produces in `orbit_calculator.py`:
  - `compute_galactic_pointing(lat_deg: float = 35.6895, lon_deg: float = 139.6917, az_deg: float = 0.0, el_deg: float = 40.0, dt: Optional[datetime] = None) -> Dict[str, Any]`
  - `compute_galactic_24h_drift_curve(lat_deg: float = 35.6895, lon_deg: float = 139.6917, az_deg: float = 0.0, el_deg: float = 40.0, start_dt: Optional[datetime] = None, step_minutes: int = 15) -> List[Dict[str, Any]]`
- Produces in `app.py`:
  - `fetch_galactic_observations(s3_client, bucket: str, limit: int = 96) -> Dict[str, Any]`
  - HTTP `GET /api/galactic-observations` endpoint (Gzip-compressed JSON)

- [ ] **Step 1: Write the failing test in `apps/satellite-viewer/test_viewer.py`**

```python
    def test_galactic_pointing_and_empty_s3_fallback(self):
        from orbit_calculator import compute_galactic_pointing, compute_galactic_24h_drift_curve
        from app import fetch_galactic_observations

        dt = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
        pt = compute_galactic_pointing(35.6895, 139.6917, 0.0, 40.0, dt)
        self.assertTrue(0.0 <= pt["gal_l_deg"] < 360.0)
        self.assertTrue(-90.0 <= pt["gal_b_deg"] <= 90.0)
        self.assertIn("region_label", pt)
        self.assertIn("lsr_correction_km_s", pt)

        curve = compute_galactic_24h_drift_curve(35.6895, 139.6917, 0.0, 40.0, dt, step_minutes=30)
        self.assertEqual(len(curve), 49)

        # S3 に galactic/ データが0件のときでも理論ポインティングと予測スペクトル・24hカーブが返ること
        self.mock_s3.list_objects_v2.return_value = {"Contents": []}
        obs = fetch_galactic_observations(self.mock_s3, "test-bucket", now_dt=dt)
        self.assertEqual(obs["scan_count"], 0)
        self.assertIn("current_pointing", obs)
        self.assertIn("drift_curve_24h", obs)
        self.assertIn("latest_spectrum", obs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest apps/satellite-viewer/test_viewer.py -k test_galactic_pointing_and_empty_s3_fallback -v`
Expected: FAIL with `ImportError: cannot import name 'compute_galactic_pointing'`

- [ ] **Step 3: Implement `compute_galactic_pointing`, `compute_galactic_24h_drift_curve` in `orbit_calculator.py` and `fetch_galactic_observations` + `/api/galactic-observations` in `app.py`**

In `orbit_calculator.py`, implement the exact J2000.0 spherical coordinate transformations matching the Rust implementation, including `lsr_correction_km_s` and expected spiral arm velocities (`expected_arms_km_s`).
In `app.py`, implement `fetch_galactic_observations` which lists `galactic/` keys from S3, fetches up to `limit` latest scan JSON files concurrently using `concurrent.futures.ThreadPoolExecutor(max_workers=16)`, and returns `current_pointing`, `scan_count`, `scans`, `latest_spectrum`, and `drift_curve_24h`. Wire `GET /api/galactic-observations` using `self.send_bytes_response(200, "application/json; charset=utf-8", body, cache_control="no-cache")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest apps/satellite-viewer/test_viewer.py -k test_galactic_pointing_and_empty_s3_fallback -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/orbit_calculator.py apps/satellite-viewer/app.py apps/satellite-viewer/test_viewer.py
git commit -m "feat: Viewerに天の川銀河ポインティング計算とS3並列取得APIを追加"
```

---

### Task 5: Web Viewer『Galactic Monitor（天の川銀河観測モニター）』UI・Canvas 描画実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (`HTML_TEMPLATE`)
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `GET /api/galactic-observations`
- Produces: Header button `openGalacticModal()`, modal `#galacticModal` with `#galacticSpectrumCanvas` and `#galacticDriftCanvas`, and `galactic*` JavaScript namespace functions.

- [ ] **Step 1: Write the failing UI and JS syntax regression test in `apps/satellite-viewer/test_viewer.py`**

```python
    def test_galactic_monitor_ui_and_js_syntax(self):
        import subprocess
        import tempfile
        import re

        self.assertIn("openGalacticModal()", HTML_TEMPLATE)
        self.assertIn('id="galacticModal"', HTML_TEMPLATE)
        self.assertIn('id="galacticSpectrumCanvas"', HTML_TEMPLATE)
        self.assertIn('id="galacticDriftCanvas"', HTML_TEMPLATE)
        self.assertIn("/api/galactic-observations", HTML_TEMPLATE)

        # node -c によるテンプレート内全 JavaScript 構文解析
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", HTML_TEMPLATE, flags=re.DOTALL)
        self.assertTrue(len(scripts) > 0)
        combined_js = "\n".join(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(combined_js)
            js_path = f.name
        res = subprocess.run(["node", "-c", js_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JS syntax error: {res.stderr}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest apps/satellite-viewer/test_viewer.py -k test_galactic_monitor_ui_and_js_syntax -v`
Expected: FAIL with `'openGalacticModal()' not found in HTML_TEMPLATE`

- [ ] **Step 3: Add the『Galactic Monitor』button, modal, and `galactic*` Canvas rendering scripts to `HTML_TEMPLATE` in `apps/satellite-viewer/app.py`**

Add a header action button (`🌌 銀河観測` / `🌌 銀河`) that opens `#galacticModal`. Inside `#galacticModal`, render:
1. Real-time pointing telemetry grid: Galactic $(l, b)$, Equatorial $(\alpha, \delta)$, LST, LSR correction $\Delta v_{\text{LSR}}$, current region badge (`WARM_GALACTIC_PLANE` vs `COLD_HALO`), and Pass Gating filter policy badge (`METEOR >= 25° / ISS・CubeSat >= 20°`).
2. `#galacticSpectrumCanvas`: 21cm HI line velocity spectrum ($v_{\text{LSR}} \in [-150, +150]\text{ km/s}$ vs $\Delta T_A\text{ [K]}$) with vertical dashed markers for Orion Arm ($0\text{ km/s}$) and Perseus Arm ($-45\text{ km/s}$).
3. `#galacticDriftCanvas`: 24-hour Galactic drift scan curve plotting $|b|$ (highlighting $|b| \le 15^\circ$ Galactic plane crossings in cyan/amber) alongside observed continuum power points.

- [ ] **Step 4: Run all Python Viewer tests to verify they pass**

Run: `.venv/bin/pytest apps/satellite-viewer/`
Expected: PASS (all tests including `node -c` JS check green)

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_viewer.py
git commit -m "feat: Web Viewerに天の川銀河21cm分光・24時間ドリフト監視UIを追加"
```
