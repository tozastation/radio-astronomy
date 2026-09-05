# Personal Ground Station (`ground-station`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform `noaa-station` into a multi-satellite personal ground station (`ground-station`) capable of 24/7 autonomous reception, asynchronous decoding (Meteor-M, CubeSats, ISS), and startup preflight health checks.

**Architecture:** SGP4 multi-satellite scheduler drives an asynchronous Producer-Consumer pipeline via Tokio MPSC channels: a single SDR I/O receiver records wideband 240kSPS raw IQ per satellite pass and hands jobs off to background decoder workers (`satdump`, `gr-satellites`, native CW) with graceful fallback and Discord/VOICEVOX notifications.

**Tech Stack:** Rust 2021, Tokio (async MPSC), SGP4 0.2, Reqwest (TLS/JSON/multipart), Serde/TOML, RTL-SDR CLI (`rtl_sdr`, `rtl_test`), SatDump CLI, gr-satellites CLI.

**Spec:** `docs/superpowers/specs/2026-09-05-ground-station-design.md`

## Global Constraints
- Commit messages must be in Japanese, no emojis, conventional commits format (`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`).
- All code and tests must compile cleanly with `cargo check` and pass with `cargo test --all`.
- Resilient SRE principles: fail-fast on startup if RTL-SDR is missing; graceful degradation if optional decoders (`satdump`, `gr_satellites`) are missing.

---

### Task 1: Crate and Workspace Migration (`noaa-station` -> `ground-station`)

**Files:**
- Modify: `Cargo.toml`
- Move: `apps/noaa-station` -> `apps/ground-station`
- Modify: `apps/ground-station/Cargo.toml`
- Modify: `apps/ground-station/src/main.rs`
- Modify: `apps/ground-station/tests/unit/config_test.rs`
- Modify: `apps/ground-station/tests/unit/orbit_test.rs`
- Modify: `apps/ground-station/tests/unit/voicevox_test.rs`
- Modify: `apps/ground-station/tests/unit/worker_test.rs`
- Modify: `apps/ground-station/tests/integration/voicevox_integration_test.rs`

**Interfaces:**
- Renames crate from `noaa-station` (and library `noaa_station`) to `ground-station` (and library `ground_station`).
- Binary name becomes `ground-station`.

- [x] **Step 1: Move directory using git mv**

```bash
git mv apps/noaa-station apps/ground-station
```

- [x] **Step 2: Update workspace Cargo.toml and crate Cargo.toml**

Update root `Cargo.toml`:
```toml
[workspace]
members = [
    "apps/ground-station",
]
resolver = "2"
```

Update `apps/ground-station/Cargo.toml`:
```toml
[package]
name = "ground-station"
version = "0.1.0"
edition = "2021"
```

- [x] **Step 3: Update crate imports in src and tests**

Replace all occurrences of `noaa_station::` with `ground_station::` across:
- `apps/ground-station/src/main.rs`
- `apps/ground-station/tests/unit/*.rs`
- `apps/ground-station/tests/integration/*.rs`
- `apps/ground-station/examples/predict_cubesats.rs`

- [x] **Step 4: Run cargo check and cargo test to verify rename**

Run: `cargo test --all`
Expected: All existing tests pass with the new crate name `ground-station`.

- [x] **Step 5: Commit**

```bash
git add .
git commit -m "refactor: noaa-stationからground-stationへの移行"
```

---

### Task 2: Configuration Schema Expansion (`config.rs`, `config.toml`)

**Files:**
- Modify: `apps/ground-station/src/config.rs`
- Modify: `apps/ground-station/config.toml`
- Test: `apps/ground-station/tests/unit/config_test.rs`

**Interfaces:**
- Produces:
  - `CubeSatTargetConfig { name: String, norad_id: u32, freq: u64, r#type: String }`
  - `CubeSatsConfig { enabled: bool, targets: Vec<CubeSatTargetConfig> }`
  - `IssConfig { enabled: bool, norad_id: u32, freq: u64 }`
  - `MeteorConfig { enabled: bool }`
  - `SatellitesConfig` containing `meteor`, `cubesats`, and `iss`.

- [x] **Step 1: Write the failing unit tests for new config fields**

In `apps/ground-station/tests/unit/config_test.rs`, add tests for parsing `[satellites.cubesats]` and `[satellites.iss]`:
```rust
#[test]
fn test_cubesat_and_iss_config_parsing() {
    let toml_str = r#"
        [observer]
        latitude = 35.7903
        longitude = 139.2584
        altitude_m = 200.0

        [satellites.meteor]
        enabled = true

        [satellites.cubesats]
        enabled = true
        targets = [
            { name = "FUNcube-1", norad_id = 39444, freq = 145935000, type = "BpskTelemetry" },
            { name = "UmKA-1", norad_id = 57172, freq = 437625000, type = "CameraSstv" }
        ]

        [satellites.iss]
        enabled = true
        norad_id = 25544
        freq = 145800000
    "#;

    let config: Config = toml::from_str(toml_str).expect("パース成功");
    assert!(config.satellites.meteor.enabled);
    assert!(config.satellites.cubesats.enabled);
    assert_eq!(config.satellites.cubesats.targets.len(), 2);
    assert_eq!(config.satellites.cubesats.targets[0].name, "FUNcube-1");
    assert!(config.satellites.iss.enabled);
    assert_eq!(config.satellites.iss.freq, 145800000);
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cargo test --test unit_config test_cubesat_and_iss_config_parsing`
Expected: FAIL with compilation error (fields do not exist).

- [x] **Step 3: Implement new structs in `src/config.rs` and update `config.toml`**

Add `CubeSatTargetConfig`, `CubeSatsConfig`, `IssConfig`, `MeteorConfig` to `src/config.rs` with `serde::{Serialize, Deserialize}` and sensible defaults. Update `config.toml` with the complete satellite list.

- [x] **Step 4: Run test to verify it passes**

Run: `cargo test --test unit_config`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/config.rs apps/ground-station/config.toml apps/ground-station/tests/unit/config_test.rs
git commit -m "feat: CubeSatおよびISSの設定スキーマを追加"
```

---

### Task 3: Preflight Health Check Module (`src/health.rs`)

**Files:**
- Create: `apps/ground-station/src/health.rs`
- Modify: `apps/ground-station/src/lib.rs`
- Modify: `apps/ground-station/src/main.rs`
- Test: `apps/ground-station/tests/unit/health_test.rs`
- Modify: `apps/ground-station/Cargo.toml` (if needed for test targets)

**Interfaces:**
- Produces:
  - `HealthStatus { Ok, Warn, Error }`
  - `HealthCheckItem { name: String, status: HealthStatus, message: String, remedy: Option<String> }`
  - `HealthReport { items: Vec<HealthCheckItem> }`
  - `run_preflight_checks(config: &Config) -> Result<HealthReport>`
  - `HealthReport::print_table(&self)`
  - `HealthReport::is_fatal(&self) -> bool`

- [x] **Step 1: Write the failing unit test for health report logic**

In `apps/ground-station/tests/unit/health_test.rs`:
```rust
use ground_station::health::{HealthCheckItem, HealthReport, HealthStatus};

#[test]
fn test_health_report_fatal_check() {
    let report = HealthReport {
        items: vec![
            HealthCheckItem {
                name: "RTL-SDR Device".to_string(),
                status: HealthStatus::Error,
                message: "デバイス未検出".to_string(),
                remedy: Some("usbipd attach を実行してください".to_string()),
            },
            HealthCheckItem {
                name: "VOICEVOX".to_string(),
                status: HealthStatus::Warn,
                message: "未起動".to_string(),
                remedy: None,
            },
        ],
    };

    assert!(report.is_fatal());
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cargo test --test unit_health`
Expected: FAIL (module does not exist).

- [x] **Step 3: Implement `src/health.rs` and register CLI command `check`**

Implement probes:
1. RTL-SDR check (`which rtl_test`, test open via `rtl_test -t` with 1 sec timeout).
2. Binaries check (`which satdump`, `which gr_satellites`, `which rtl_sdr`).
3. Services check (HTTP GET `http://localhost:50021/version` for VOICEVOX).
4. Storage check (`std::fs::create_dir_all` and write test to `output_dir`).
Expose `Commands::Check` in `src/main.rs`.

- [x] **Step 4: Run test to verify it passes**

Run: `cargo test --test unit_health`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/health.rs apps/ground-station/src/lib.rs apps/ground-station/src/main.rs apps/ground-station/tests/unit/health_test.rs apps/ground-station/Cargo.toml
git commit -m "feat: 起動時事前ヘルスチェック機能とcheckコマンドを追加"
```

---

### Task 4: Multi-Satellite SGP4 Orbit & TLE Fetching (`src/orbit.rs`)

**Files:**
- Modify: `apps/ground-station/src/orbit.rs`
- Test: `apps/ground-station/tests/unit/orbit_test.rs`

**Interfaces:**
- Produces:
  - `SignalType::MeteorLrpt`, `SignalType::CubeSatTelemetry`, `SignalType::CubeSatSsdv`, `SignalType::MorseCw`, `SignalType::IssSstv`
  - `fetch_all_tles(client: &Client, config: &SatellitesConfig) -> Result<Vec<SatelliteInfo>>`
  - Fetches both weather group and amateur group TLEs from CelesTrak.
  - Matches configured targets (`Meteor-M`, `targets` from `cubesats`, and `iss`).

- [x] **Step 1: Write the failing unit test for multi-satellite TLE matching**

In `apps/ground-station/tests/unit/orbit_test.rs`:
```rust
#[test]
fn test_signal_type_display_and_parsing() {
    let sig = SignalType::CubeSatSsdv;
    assert_eq!(sig.name(), "CubeSat SSDV (カメラ画像)");
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cargo test --test unit_orbit test_signal_type_display_and_parsing`
Expected: FAIL (enum variant does not exist).

- [x] **Step 3: Implement expanded SignalType and fetch_all_tles in `src/orbit.rs`**

- Add variants to `SignalType`: `CubeSatSsdv`, `CubeSatTelemetry`, `MorseCw`, `IssSstv`.
- Implement `fetch_all_tles`:
  - If `meteor.enabled`: fetch weather group (`gp.php?GROUP=weather&FORMAT=tle`).
  - If `cubesats.enabled` or `iss.enabled`: fetch amateur group (`gp.php?GROUP=amateur&FORMAT=tle`).
  - Parse 3-line TLE format and construct `SatelliteInfo`.
- Keep existing SGP4 ENU coordinate and conflict resolution logic.

- [x] **Step 4: Run tests to verify it passes**

Run: `cargo test --test unit_orbit`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/orbit.rs apps/ground-station/tests/unit/orbit_test.rs
git commit -m "feat: 多種衛星TLE取得とSignalTypeの拡張"
```

---

### Task 5: Asynchronous Worker Pipeline and Pluggable Decoders (`src/worker.rs`, `src/decoder.rs`)

**Files:**
- Modify: `apps/ground-station/src/decoder.rs`
- Modify: `apps/ground-station/src/worker.rs`
- Modify: `apps/ground-station/src/receiver.rs`
- Test: `apps/ground-station/tests/unit/worker_test.rs`

**Interfaces:**
- Produces:
  - `DecodeResult { image_path: Option<PathBuf>, telemetry_summary: Option<String> }`
  - `DecoderEngine::decode(&self, pass: &SatellitePass, raw_path: &Path) -> Result<DecodeResult>`
  - `worker::run_worker(rx: mpsc::Receiver<DecodeJob>, discord: Arc<DiscordClient>, voicevox: Arc<VoicevoxClient>)`
  - `receiver::record_pass_raw(pass: &SatellitePass, config: &SdrConfig, output_dir: &Path) -> Result<PathBuf>`

- [x] **Step 1: Write the failing unit test for DecoderEngine routing**

In `apps/ground-station/tests/unit/worker_test.rs`:
```rust
#[tokio::test]
async fn test_decoder_routing_for_cubesat_ssdv() {
    let pass = SatellitePass {
        satellite_name: "UmKA-1".to_string(),
        frequency_hz: 437_625_000,
        signal_type: SignalType::CubeSatSsdv,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 50.0,
        peak_azimuth_deg: 90.0,
    };
    // Verify engine routes to correct decoder without panicking
    let engine = DecoderEngine::new("data/test");
    let result = engine.decode_mock(&pass);
    assert!(result.is_ok());
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cargo test --test unit_worker`
Expected: FAIL (types or routing missing).

- [x] **Step 3: Implement pluggable decoder routing and async worker**

In `src/decoder.rs`:
- Implement `DecoderEngine::decode`:
  - `MeteorLrpt` -> calls `SatDump` CLI.
  - `CubeSatSsdv` / `CubeSatTelemetry` -> calls `gr_satellites` / `satdump`.
  - `MorseCw` -> parses CW audio/IQ or produces summary.
  - Graceful degradation: If CLI command is not found in PATH, logs `WARN`, leaves raw IQ file in `data/ground-station/raw/`, returns `DecodeResult { image_path: None, telemetry_summary: Some("生データ保存済み (デコーダ未検出)") }`.
In `src/worker.rs`:
- Asynchronously pulls `DecodeJob` from `mpsc::Receiver`, calls `DecoderEngine`, then notifies Discord and VOICEVOX.

- [x] **Step 4: Run tests to verify it passes**

Run: `cargo test --test unit_worker`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add apps/ground-station/src/decoder.rs apps/ground-station/src/worker.rs apps/ground-station/src/receiver.rs apps/ground-station/tests/unit/worker_test.rs
git commit -m "feat: 非同期デコードワーカとプラグイン型デコードエンジンの実装"
```

---

### Task 6: CLI Integration & End-to-End Verification (`src/main.rs`, `src/scheduler.rs`)

**Files:**
- Modify: `apps/ground-station/src/scheduler.rs`
- Modify: `apps/ground-station/src/main.rs`
- Modify: `README.md`

**Interfaces:**
- Integrates all subcommands:
  - `ground-station check`: Runs preflight checks and exits with code 0 or 1.
  - `ground-station schedule`: Pulls real TLEs and renders passes in table.
  - `ground-station daemon`: Runs preflight checks; if non-fatal, starts scheduler & worker.
  - `ground-station test-voice`: Tests VOICEVOX.
  - `ground-station test-discord`: Tests Discord Webhook.

- [x] **Step 1: Wire Preflight Check into `daemon` and `check` command**

In `src/main.rs` and `src/scheduler.rs`:
- When `Commands::Check` is invoked: call `health::run_preflight_checks(&config)` and print formatted summary table.
- When `Commands::Daemon` is invoked: run preflight check first. If `is_fatal()`, print clear remedy and abort before acquiring SDR.

- [x] **Step 2: Update `schedule` command to output multi-satellite pass table**

Ensure `show_schedule` prints satellite category, frequency, pass time (JST), max elevation, and direction.

- [x] **Step 3: Run cargo check and run CLI commands**

```bash
cargo run --bin ground-station -- check
cargo run --bin ground-station -- schedule
```
Expected:
1. `check` displays status of RTL-SDR, tools, VOICEVOX, Discord, storage.
2. `schedule` displays upcoming Meteor-M, CubeSat, and ISS passes.

- [x] **Step 4: Run full test suite**

Run: `cargo test --all`
Expected: All unit and integration tests PASS.

- [x] **Step 5: Commit and update README.md**

```bash
git add apps/ground-station/src/main.rs apps/ground-station/src/scheduler.rs README.md
git commit -m "feat: ground-stationのCLI統合と起動前ヘルスチェックの実装"
```
