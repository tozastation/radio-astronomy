# ☀️ Solar Radio Observatory (`solar-station`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous solar radio burst observatory (`solar-station`) in Rust using the RTL-SDR Blog V4 and the stock telescopic whip antenna (70.0 MHz resonance), featuring real-time DSP integration, robust statistical burst detection (MAD), waterfall PNG generation, JSON Lines persistence, and Discord alerts.

**Architecture:** A standalone multi-threaded binary (`apps/ground-station/src/bin/solar_station.rs`) runs a 3-tier pipeline: a background stdout reader pulls 2.4MSPS raw IQ from `rtl_sdr`, a CPU-bound worker performs 1024-point FFT + 1-second integration via `rustfft` and evaluates dynamic thresholds using a 300-second ring buffer with MAD robust statistics, and an async Tokio worker renders waterfall PNGs and sends Discord Webhook alerts.

**Tech Stack:** Rust 2021, `rustfft` 6.2, `image` 0.25, `tokio` 1.38, `reqwest` 0.12, `chrono` 0.4, `serde` / `serde_json`, `toml` 0.8, RTL-SDR CLI (`rtl_sdr`).

**Spec:** `docs/superpowers/specs/2026-09-27-solar-station-design.md`

## Global Constraints
- Commit messages must be in Japanese, no emojis, conventional commits format (`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`).
- All code and tests must compile cleanly with `cargo check` and pass with `cargo test --all`.
- Non-blocking real-time DSP: heavy I/O (disk writes, PNG encoding, Discord HTTP) must never block the 2.4MSPS DSP stream.
- Zero raw IQ persistence: store only 1-second aggregated metrics (JSON Lines) and event waterfall PNGs to keep disk usage under 10 MB/day.

---

### Task 1: Configuration & Solar Position Calculation (`config.rs` & `solar/sun_pos.rs`)

**Files:**
- Create: `apps/ground-station/src/solar/mod.rs`
- Create: `apps/ground-station/src/solar/sun_pos.rs`
- Modify: `apps/ground-station/src/config.rs:1-150`
- Modify: `apps/ground-station/src/lib.rs:1-30`
- Test: `apps/ground-station/tests/unit/solar_config_test.rs`
- Test: `apps/ground-station/tests/unit/solar_sun_pos_test.rs`

**Interfaces:**
- Consumes: `crate::config::Config`
- Produces:
  - `SolarConfig`: TOML configuration for solar observation (freq, sample_rate, gain, thresholds, mode).
  - `calculate_sun_position(lat_deg: f64, lon_deg: f64, time: DateTime<Utc>) -> SunPosition { azimuth_deg: f64, elevation_deg: f64 }`

- [x] **Step 1: Write failing tests for `SolarConfig` and `calculate_sun_position`**

`apps/ground-station/tests/unit/solar_config_test.rs`:
```rust
use ground_station::config::Config;

#[test]
fn test_solar_config_defaults() {
    let toml_str = r#"
        station_name = "TestStation"
        latitude = 35.68
        longitude = 139.76
        elevation_m = 50.0

        [solar]
        enabled = true
        center_freq = 70.0e6
        sample_rate = 2.4e6
        gain = 28.0
        fft_size = 1024
        integration_secs = 1
        threshold_sigma = 4.0
        min_jump_db = 3.0
        cooldown_secs = 180
        mode = "daylight_only"
        min_elevation = 5.0
        data_dir = "data/solar"
    "#;

    let config: Config = toml::from_str(toml_str).expect("Failed to parse config");
    assert!(config.solar.is_some());
    let solar = config.solar.unwrap();
    assert!(solar.enabled);
    assert_eq!(solar.center_freq, 70.0e6);
    assert_eq!(solar.threshold_sigma, 4.0);
    assert_eq!(solar.min_elevation, 5.0);
}
```

`apps/ground-station/tests/unit/solar_sun_pos_test.rs`:
```rust
use chrono::{TimeZone, Utc};
use ground_station::solar::sun_pos::calculate_sun_position;

#[test]
fn test_sun_position_tokio_noon_equinox() {
    // 2026-03-20 02:45 UTC (approx 11:45 JST, solar noon in Tokyo lat: 35.68, lon: 139.76)
    let time = Utc.with_ymd_and_hms(2026, 3, 20, 2, 45, 0).unwrap();
    let pos = calculate_sun_position(35.68, 139.76, time);

    // At solar noon near spring equinox in Tokyo, Sun should be approximately South (Az ~ 180°) and El ~ 54° (90 - 35.68)
    assert!(pos.elevation_deg > 45.0 && pos.elevation_deg < 60.0);
    assert!(pos.azimuth_deg > 160.0 && pos.azimuth_deg < 200.0);
}

#[test]
fn test_sun_position_midnight() {
    // Midnight in Tokyo (15:00 UTC)
    let time = Utc.with_ymd_and_hms(2026, 3, 20, 15, 0, 0).unwrap();
    let pos = calculate_sun_position(35.68, 139.76, time);
    // Sun is well below horizon
    assert!(pos.elevation_deg < 0.0);
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cargo test --test unit_solar_config --test unit_solar_sun_pos`
Expected: FAIL (modules not found, config fields not found)

- [x] **Step 3: Implement `SolarConfig` and `sun_pos.rs`**

Update `apps/ground-station/src/config.rs`:
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolarConfig {
    pub enabled: bool,
    #[serde(default = "default_solar_center_freq")]
    pub center_freq: f64,
    #[serde(default = "default_solar_sample_rate")]
    pub sample_rate: f64,
    #[serde(default = "default_solar_gain")]
    pub gain: f64,
    #[serde(default = "default_solar_fft_size")]
    pub fft_size: usize,
    #[serde(default = "default_solar_integration_secs")]
    pub integration_secs: u64,
    #[serde(default = "default_solar_threshold_sigma")]
    pub threshold_sigma: f64,
    #[serde(default = "default_solar_min_jump_db")]
    pub min_jump_db: f64,
    #[serde(default = "default_solar_cooldown_secs")]
    pub cooldown_secs: u64,
    #[serde(default = "default_solar_mode")]
    pub mode: String,
    #[serde(default = "default_solar_min_elevation")]
    pub min_elevation: f64,
    #[serde(default = "default_solar_data_dir")]
    pub data_dir: String,
}
// Add helper default functions...
```

Implement `apps/ground-station/src/solar/sun_pos.rs` using low-precision astronomical solar coordinates formula:
```rust
use chrono::{DateTime, Datelike, Timelike, Utc};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SunPosition {
    pub azimuth_deg: f64,
    pub elevation_deg: f64,
}

pub fn calculate_sun_position(lat_deg: f64, lon_deg: f64, time: DateTime<Utc>) -> SunPosition {
    let lat_rad = lat_deg.to_radians();
    // Julian Date calculation
    let year = time.year() as f64;
    let month = time.month() as f64;
    let day = time.day() as f64;
    let hour = time.hour() as f64 + time.minute() as f64 / 60.0 + time.second() as f64 / 3600.0;
    
    let a = (14.0 - month) / 12.0;
    let y = year + 4800.0 - a.floor();
    let m = month + 12.0 * a.floor() - 3.0;
    let jdn = day + ((153.0 * m + 2.0) / 5.0).floor() + 365.0 * y + (y / 4.0).floor() - (y / 100.0).floor() + (y / 400.0).floor() - 32045.0;
    let jd = jdn + (hour - 12.0) / 24.0;
    let n = jd - 2451545.0;

    // Solar mean anomaly
    let l = (280.460 + 0.9856474 * n).rem_euclid(360.0);
    let g = (357.528 + 0.9856003 * n).rem_euclid(360.0).to_radians();
    let lambda = (l + 1.915 * g.sin() + 0.020 * (2.0 * g).sin()).to_radians();

    // Obliquity of ecliptic
    let eps = (23.439 - 0.0000004 * n).to_radians();
    let alpha = (eps.cos() * lambda.sin()).atan2(lambda.cos());
    let delta = (eps.sin() * lambda.sin()).asin();

    // Greenwich Mean Sidereal Time
    let gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)).rem_euclid(360.0).to_radians();
    let lmst = (gmst + lon_deg.to_radians()).rem_euclid(2.0 * std::f64::consts::PI);
    let ha = lmst - alpha;

    // AltAz conversion
    let sin_el = lat_rad.sin() * delta.sin() + lat_rad.cos() * delta.cos() * ha.cos();
    let el = sin_el.asin();
    let cos_az = (delta.sin() - lat_rad.sin() * sin_el) / (lat_rad.cos() * el.cos());
    let sin_az = -delta.cos() * ha.sin() / el.cos();
    let az = sin_az.atan2(cos_az).rem_euclid(2.0 * std::f64::consts::PI);

    SunPosition {
        azimuth_deg: az.to_degrees(),
        elevation_deg: el.to_degrees(),
    }
}
```

Update `apps/ground-station/Cargo.toml` to register tests `unit_solar_config` and `unit_solar_sun_pos`.

- [x] **Step 4: Run tests to verify they pass**

Run: `cargo test --test unit_solar_config --test unit_solar_sun_pos`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/config.rs apps/ground-station/src/lib.rs apps/ground-station/src/solar/ tests/unit/solar_config_test.rs tests/unit/solar_sun_pos_test.rs apps/ground-station/Cargo.toml
git commit -m "feat: 太陽観測の設定モデルと太陽位置計算モジュールを追加"
```

---

### Task 2: Real-time DSP & Dynamic MAD Burst Detector (`solar/dsp.rs`)

**Files:**
- Create: `apps/ground-station/src/solar/dsp.rs`
- Modify: `apps/ground-station/src/solar/mod.rs`
- Test: `apps/ground-station/tests/unit/solar_dsp_test.rs`

**Interfaces:**
- Produces:
  - `SolarDsp`: struct with FFT planner, Hamming window, 1-second power accumulator, and 300-second ring buffer.
  - `SolarDsp::process_chunk(&mut self, iq_samples: &[u8]) -> Option<SolarSecondMetrics>`
  - `SolarSecondMetrics { timestamp: i64, total_power_db: f32, baseline_median_db: f32, snr_db: f32, is_burst: bool, spectrum_db: Vec<f32> }`
  - `calculate_median_and_mad(values: &[f32]) -> (f32, f32)`

- [x] **Step 1: Write failing tests for MAD calculation and burst detection**

`apps/ground-station/tests/unit/solar_dsp_test.rs`:
```rust
use ground_station::solar::dsp::{calculate_median_and_mad, SolarDsp};

#[test]
fn test_median_and_mad() {
    let mut data = vec![10.0, 12.0, 11.0, 10.5, 11.5, 50.0]; // 50.0 is an outlier
    let (median, mad) = calculate_median_and_mad(&mut data);
    assert!((median - 11.0).abs() < 0.5);
    assert!(mad > 0.0 && mad < 2.0); // robust to outlier 50.0
}

#[test]
fn test_dsp_burst_detection_trigger() {
    let mut dsp = SolarDsp::new(1024, 2_400_000, 4.0, 3.0);
    // Feed 300 seconds of baseline noise (I=127, Q=127 + small noise)
    // Then feed 3 seconds of high amplitude burst
    // Verify is_burst transitions from false to true with snr_db > 10.0
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cargo test --test unit_solar_dsp`
Expected: FAIL (module not found)

- [x] **Step 3: Implement `SolarDsp` and robust statistics**

Implement `apps/ground-station/src/solar/dsp.rs`:
- Window function generation: Hamming 1024 points.
- FFT processing: `rustfft::FftPlanner` (1024-point complex FFT).
- Chunk-to-complex conversion: u8 to complex f32 centered at 0: `(sample as f32 - 127.5) / 127.5`.
- 1-second integration: accumulate $|X[k]|^2$, compute total power in dB, push to `RingBuffer<f32, 300>`.
- Trigger logic: compute median and MAD over ring buffer, check $P > \text{median} + 4\sigma$ and $dP/dt \ge 3.0\text{ dB/s}$.

- [x] **Step 4: Run tests to verify they pass**

Run: `cargo test --test unit_solar_dsp`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/solar/dsp.rs apps/ground-station/src/solar/mod.rs tests/unit/solar_dsp_test.rs apps/ground-station/Cargo.toml
git commit -m "feat: リアルタイムFFT積算とMAD動的しきい値検知DSPモジュールを追加"
```

---

### Task 3: Waterfall Spectrogram PNG Generator (`solar/waterfall.rs`)

**Files:**
- Create: `apps/ground-station/src/solar/waterfall.rs`
- Modify: `apps/ground-station/src/solar/mod.rs`
- Test: `apps/ground-station/tests/unit/solar_waterfall_test.rs`

**Interfaces:**
- Produces:
  - `render_waterfall_png(spectra: &[Vec<f32>], width: u32, height: u32, output_path: &Path, title: &str) -> anyhow::Result<()>`
  - Color palette: Inferno or Viridis mapping from power range [min_db, max_db] to RGB.

- [x] **Step 1: Write failing test for waterfall PNG generation**

`apps/ground-station/tests/unit/solar_waterfall_test.rs`:
```rust
use ground_station::solar::waterfall::render_waterfall_png;
use std::fs;
use std::path::Path;

#[test]
fn test_render_waterfall_png() {
    let output_dir = Path::new("target/tmp_test_waterfall");
    fs::create_dir_all(output_dir).unwrap();
    let output_path = output_dir.join("test_waterfall.png");

    // 60 seconds of 1024-bin spectrum data
    let mut spectra = Vec::new();
    for t in 0..60 {
        let mut row = vec![-50.0; 1024];
        if t >= 25 && t <= 35 {
            // Simulated solar burst diagonal drift
            let peak_bin = 500 + (t - 25) * 20;
            row[peak_bin] = -20.0;
        }
        spectra.push(row);
    }

    render_waterfall_png(&spectra, 800, 400, &output_path, "70.0 MHz Solar Radio Burst").unwrap();
    assert!(output_path.exists());
    let metadata = fs::metadata(&output_path).unwrap();
    assert!(metadata.len() > 1000); // Valid PNG file produced
}
```

- [x] **Step 2: Run tests to verify it fails**

Run: `cargo test --test unit_solar_waterfall`
Expected: FAIL (module not found)

- [x] **Step 3: Implement `render_waterfall_png`**

Implement `apps/ground-station/src/solar/waterfall.rs`:
- Map 2D float array to `image::RgbImage` using bilinear interpolation or pixel nearest-neighbor.
- Apply colormap (Inferno heatmap: black $\to$ purple $\to$ red $\to$ yellow).
- Save to PNG with compression.

- [x] **Step 4: Run tests to verify it passes**

Run: `cargo test --test unit_solar_waterfall`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/solar/waterfall.rs apps/ground-station/src/solar/mod.rs tests/unit/solar_waterfall_test.rs apps/ground-station/Cargo.toml
git commit -m "feat: 太陽電波スペクトログラムPNG生成モジュールを追加"
```

---

### Task 4: Storage Persistence & Discord Alerts (`solar/storage.rs` & `solar/notification.rs`)

**Files:**
- Create: `apps/ground-station/src/solar/storage.rs`
- Create: `apps/ground-station/src/solar/notification.rs`
- Modify: `apps/ground-station/src/solar/mod.rs`
- Test: `apps/ground-station/tests/unit/solar_storage_test.rs`
- Test: `apps/ground-station/tests/unit/solar_notification_test.rs`

**Interfaces:**
- Produces:
  - `SolarStorage`: writes 1-second JSON Lines records (`metrics_YYYY-MM-DD.jsonl`) atomically.
  - `SolarNotifier`: constructs rich Discord embed with attached waterfall PNG and sends to configured Webhook with cooldown management.

- [x] **Step 1: Write failing tests for storage and notification builder**

`apps/ground-station/tests/unit/solar_storage_test.rs`:
```rust
use ground_station::solar::storage::SolarStorage;
use ground_station::solar::dsp::SolarSecondMetrics;
use std::fs;
use std::path::Path;

#[test]
fn test_solar_storage_append() {
    let tmp_dir = Path::new("target/tmp_solar_storage");
    let storage = SolarStorage::new(tmp_dir);
    let metrics = SolarSecondMetrics {
        timestamp: 1790510535,
        total_power_db: -24.3,
        baseline_median_db: -38.5,
        snr_db: 14.2,
        peak_freq_hz: 70_125_000,
        is_burst: true,
        spectrum_db: vec![-30.0; 1024],
        sun_az_deg: 152.4,
        sun_el_deg: 52.1,
    };
    storage.record_metric(&metrics).unwrap();
    // Check line written in metrics_YYYY-MM-DD.jsonl
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cargo test --test unit_solar_storage --test unit_solar_notification`
Expected: FAIL

- [x] **Step 3: Implement `storage.rs` and `notification.rs`**

- `storage.rs`: Atomic append to daily JSON Lines file using `std::fs::OpenOptions`.
- `notification.rs`: Formats embed with event metrics, integrates cooldown timer (180s), and uses `crate::discord::DiscordClient::send_embed_with_file`.

- [x] **Step 4: Run tests to verify they pass**

Run: `cargo test --test unit_solar_storage --test unit_solar_notification`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/solar/storage.rs apps/ground-station/src/solar/notification.rs apps/ground-station/src/solar/mod.rs tests/unit/solar_storage_test.rs tests/unit/solar_notification_test.rs apps/ground-station/Cargo.toml
git commit -m "feat: 太陽電波メトリクスのJSON Lines保存とDiscord通知モジュールを追加"
```

---

### Task 5: Solar Station Manager & Standalone Binary (`solar/manager.rs` & `src/bin/solar_station.rs`)

**Files:**
- Create: `apps/ground-station/src/solar/manager.rs`
- Create: `apps/ground-station/src/bin/solar_station.rs`
- Modify: `apps/ground-station/Cargo.toml`
- Test: `apps/ground-station/tests/integration/solar_station_test.rs`

**Interfaces:**
- Produces:
  - `SolarStationManager`: launches `rtl_sdr`, manages stdout reader thread, DSP thread, and async event worker.
  - Binary `solar-station` CLI options (`--config`, `--dry-run`, `--continuous`).

- [x] **Step 1: Write integration test with simulated IQ stream**

`apps/ground-station/tests/integration/solar_station_test.rs`:
```rust
#[tokio::test]
async fn test_solar_station_pipeline_simulated() {
    // Pipe synthetic IQ data into SolarStationManager
    // Verify 1-second metrics are produced without deadlock or buffer overflow
}
```

- [x] **Step 2: Run tests to verify it fails**

Run: `cargo test --test integration_solar_station`
Expected: FAIL (binary / manager not found)

- [x] **Step 3: Implement `manager.rs` and `bin/solar_station.rs`**

Implement pipeline:
1. Parse CLI arguments (`clap`).
2. Load `config.toml`.
3. Check solar elevation (`sun_pos`). If `mode == "daylight_only"` and $El < 5.0^\circ$, sleep until sunrise.
4. Spawn `rtl_sdr -f 70.0M -s 2.4M -g 28.0 -`.
5. Run 3-tier pipeline with graceful Ctrl+C shutdown.

Register binary in `apps/ground-station/Cargo.toml`:
```toml
[[bin]]
name = "solar-station"
path = "src/bin/solar_station.rs"
```

- [x] **Step 4: Run integration test and cargo check**

Run: `cargo test --test integration_solar_station && cargo check --bin solar-station`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/solar/manager.rs apps/ground-station/src/bin/solar_station.rs apps/ground-station/Cargo.toml tests/integration/solar_station_test.rs
git commit -m "feat: 太陽電波観測ステーションのメインデーモンとCLIバイナリを実装"
```

---

### Task 6: Documentation, Q&A Update & End-to-End Verification

**Files:**
- Modify: `docs/04_qa.md`
- Create: `docs/qa/12_solar_station_architecture_and_burst_detection.md`
- Modify: `README.md`

- [x] **Step 1: Write detailed Q&A technical document**

Create `docs/qa/12_solar_station_architecture_and_burst_detection.md`:
- Detailed explanation of 70MHz 1/4 wavelength monopole resonance.
- Mathematical derivation of MAD robust thresholding.
- 3-tier thread concurrency and storage optimization.

- [x] **Step 2: Update `docs/04_qa.md` and `README.md`**

Add links and summary for solar observation pipeline.

- [x] **Step 3: Run full test suite across all crates**

Run: `cargo test --all`
Expected: ALL PASS

- [x] **Step 4: Commit**

```bash
git add docs/04_qa.md docs/qa/12_solar_station_architecture_and_burst_detection.md README.md docs/superpowers/plans/2026-09-27-solar-station.md
git commit -m "docs: 太陽電波観測ステーションの運用ガイドと技術解説を追加"
```
