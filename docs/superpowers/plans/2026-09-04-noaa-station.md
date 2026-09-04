# NOAA気象衛星 自律自動受信・デコード地上局 (`apps/noaa-station`) 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GPD Pocket3（エッジノード）上で常駐し、NOAA気象衛星（15/18/19）の通過を自律予測して137MHz帯APT電波を録音・画像デコードし、ずんだもんの音声で事前・事後通知する地上局デーモンをRustで構築する。

**Architecture:** `apps/noaa-station` 配下にRustクレートを作成。CelesTrakからTLEを取得して `sgp4` で観測地基準の仰角・パスを計算し、`tokio` のステートマシンで省電力待機・事前通知・SDR録音・画像デコード・完了通知を一元管理する。

**Tech Stack:** Rust 2021, `tokio`, `sgp4`, `reqwest`, `serde`, `toml`, `chrono`, `clap`, `rtl_fm`, `noaa-apt`, VOICEVOX Engine

**Spec:** [docs/superpowers/specs/2026-09-04-noaa-station-design.md](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/docs/superpowers/specs/2026-09-04-noaa-station-design.md)

## Global Constraints
- 言語: Rust 2021 Edition
- ディレクトリ: `apps/noaa-station/` 配下に自己完結して配置
- コミットルール: 日本語、絵文字禁止、Conventional Commits（`feat: ...`, `test: ...`, `docs: ...`）
- 外部依存: `rtl_fm`（RTL-SDR V4対応ドライバ）、`noaa-apt` CLI（Linux版）
- エラーハンドリング: VOICEVOX や ネットワークが一時的に不通でもデーモンがパニック・終了しないフォールバック設計

---

### Task 1: プロジェクトスキャフォールディング & 設定管理 (`config.rs`)

**Files:**
- Create: `Cargo.toml` (ルート workspace)
- Create: `apps/noaa-station/Cargo.toml`
- Create: `apps/noaa-station/config.toml`
- Create: `apps/noaa-station/src/config.rs`
- Create: `apps/noaa-station/src/main.rs`
- Test: `apps/noaa-station/tests/config_test.rs`

**Interfaces:**
- Consumes: なし
- Produces: `noaa_station::config::Config`, `ObserverConfig`, `SchedulerConfig`, `VoicevoxConfig`, `StorageConfig`

- [ ] **Step 1: ルート `Cargo.toml` と `apps/noaa-station/Cargo.toml` を作成**

ルート `Cargo.toml`:
```toml
[workspace]
members = [
    "apps/noaa-station",
]
resolver = "2"
```

`apps/noaa-station/Cargo.toml`:
```toml
[package]
name = "noaa-station"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.38", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
toml = "0.8"
chrono = { version = "0.4", features = ["serde"] }
clap = { version = "4.5", features = ["derive"] }
reqwest = { version = "0.12", features = ["json"] }
sgp4 = "0.2"
nix = { version = "0.29", features = ["signal", "process"] }
anyhow = "1.0"
log = "0.4"
env_logger = "0.11"
```

- [ ] **Step 2: デフォルト設定ファイル `apps/noaa-station/config.toml` を作成**

```toml
[observer]
latitude = 35.6895
longitude = 139.6917
altitude_m = 40.0

[scheduler]
min_elevation_deg = 20.0
pre_alert_minutes = 3.0
tle_update_interval_hours = 24

[voicevox]
enabled = true
host = "http://localhost:50021"
speaker_id = 3

[storage]
output_dir = "data/noaa"
```

- [ ] **Step 3: 設定パースの失敗テストを作成**

`apps/noaa-station/tests/config_test.rs`:
```rust
use noaa_station::config::Config;

#[test]
fn test_load_default_config() {
    let toml_str = r#"
        [observer]
        latitude = 35.6895
        longitude = 139.6917
        altitude_m = 40.0

        [scheduler]
        min_elevation_deg = 20.0
        pre_alert_minutes = 3.0
        tle_update_interval_hours = 24

        [voicevox]
        enabled = true
        host = "http://localhost:50021"
        speaker_id = 3

        [storage]
        output_dir = "data/noaa"
    "#;
    let config = Config::from_str(toml_str).expect("Failed to parse config");
    assert_eq!(config.observer.latitude, 35.6895);
    assert_eq!(config.scheduler.min_elevation_deg, 20.0);
    assert_eq!(config.voicevox.speaker_id, 3);
}
```

- [ ] **Step 4: テストを実行して失敗を確認**

Run: `cargo test --test config_test`
Expected: FAIL (noaa_station::config モジュールが存在しない)

- [ ] **Step 5: `src/config.rs` と `src/lib.rs` を実装**

`apps/noaa-station/src/lib.rs`:
```rust
pub mod config;
```

`apps/noaa-station/src/config.rs`:
```rust
use anyhow::{Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub observer: ObserverConfig,
    pub scheduler: SchedulerConfig,
    pub voicevox: VoicevoxConfig,
    pub storage: StorageConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ObserverConfig {
    pub latitude: f64,
    pub longitude: f64,
    pub altitude_m: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SchedulerConfig {
    pub min_elevation_deg: f64,
    pub pre_alert_minutes: f64,
    pub tle_update_interval_hours: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct VoicevoxConfig {
    pub enabled: bool,
    pub host: String,
    pub speaker_id: u32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StorageConfig {
    pub output_dir: String,
}

impl Config {
    pub fn from_str(s: &str) -> Result<Self> {
        toml::from_str(s).context("Failed to parse TOML configuration")
    }

    pub fn load_from_file<P: AsRef<Path>>(path: P) -> Result<Self> {
        let content = fs::read_to_string(path.as_ref())
            .with_context(|| format!("Failed to read config file: {:?}", path.as_ref()))?;
        Self::from_str(&content)
    }
}
```

- [ ] **Step 6: テストを実行して成功を確認**

Run: `cargo test --test config_test`
Expected: PASS

- [ ] **Step 7: コミット**

```bash
git add Cargo.toml apps/noaa-station/
git commit -m "feat: noaa-stationプロジェクト構造と設定管理モジュールを追加"
```

---

### Task 2: ずんだもん音声通知クライアント (`voicevox.rs`)

**Files:**
- Create: `apps/noaa-station/src/voicevox.rs`
- Modify: `apps/noaa-station/src/lib.rs`
- Test: `apps/noaa-station/tests/voicevox_test.rs`

**Interfaces:**
- Consumes: `noaa_station::config::VoicevoxConfig`
- Produces: `VoicevoxClient::new(config)`, `VoicevoxClient::speak(&self, text: &str) -> Result<()>`

- [ ] **Step 1: 音声クエリURLとリクエスト構築の単体テストを作成**

`apps/noaa-station/tests/voicevox_test.rs`:
```rust
use noaa_station::config::VoicevoxConfig;
use noaa_station::voicevox::VoicevoxClient;

#[test]
fn test_voicevox_url_generation() {
    let config = VoicevoxConfig {
        enabled: true,
        host: "http://localhost:50021".to_string(),
        speaker_id: 3,
    };
    let client = VoicevoxClient::new(config);
    let query_url = client.audio_query_url("こんにちは");
    assert!(query_url.contains("speaker=3"));
    assert!(query_url.contains("audio_query"));
}
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test voicevox_test`
Expected: FAIL (`VoicevoxClient` 未定義)

- [ ] **Step 3: `apps/noaa-station/src/voicevox.rs` を実装**

```rust
use crate::config::VoicevoxConfig;
use anyhow::{Context, Result};
use log::{error, info, warn};
use reqwest::Client;
use std::process::Command;

pub struct VoicevoxClient {
    config: VoicevoxConfig,
    http_client: Client,
}

impl VoicevoxClient {
    pub fn new(config: VoicevoxConfig) -> Self {
        Self {
            config,
            http_client: Client::new(),
        }
    }

    pub fn audio_query_url(&self, text: &str) -> String {
        format!(
            "{}/audio_query?text={}&speaker={}",
            self.config.host.trim_end_matches('/'),
            urlencoding::encode(text),
            self.config.speaker_id
        )
    }

    pub fn synthesis_url(&self) -> String {
        format!(
            "{}/synthesis?speaker={}",
            self.config.host.trim_end_matches('/'),
            self.config.speaker_id
        )
    }

    pub async fn speak(&self, text: &str) -> Result<()> {
        if !self.config.enabled {
            info!("[VOICEVOX OFF] {}", text);
            return Ok(());
        }

        info!("ずんだもん発話: {}", text);

        // 1. audio_query を取得
        let query_url = self.audio_query_url(text);
        let query_resp = match self.http_client.post(&query_url).send().await {
            Ok(resp) => resp,
            Err(e) => {
                warn!("VOICEVOX 接続失敗 (フォールバック): {}", e);
                return Ok(());
            }
        };

        if !query_resp.status().is_success() {
            warn!("audio_query エラー: HTTP {}", query_resp.status());
            return Ok(());
        }

        let query_json: serde_json::Value = query_resp.json().await.context("Invalid query json")?;

        // 2. 音声合成 (synthesis)
        let synth_url = self.synthesis_url();
        let synth_resp = match self.http_client.post(&synth_url).json(&query_json).send().await {
            Ok(resp) => resp,
            Err(e) => {
                warn!("VOICEVOX 合成失敗: {}", e);
                return Ok(());
            }
        };

        if !synth_resp.status().is_success() {
            warn!("synthesis エラー: HTTP {}", synth_resp.status());
            return Ok(());
        }

        let wav_bytes = synth_resp.bytes().await.context("Failed to read audio bytes")?;

        // 3. 再生 (一時ファイルまたは aplay / ffplay)
        let tmp_wav = std::env::temp_dir().join("zundamon_voice.wav");
        tokio::fs::write(&tmp_wav, &wav_bytes).await?;

        tokio::task::spawn_blocking(move || {
            let status = Command::new("aplay")
                .arg("-q")
                .arg(&tmp_wav)
                .status()
                .or_else(|_| {
                    Command::new("ffplay")
                        .args(["-nodisp", "-autoexit", "-loglevel", "quiet"])
                        .arg(&tmp_wav)
                        .status()
                });
            if let Err(e) = status {
                warn!("音声再生プレイヤー (aplay/ffplay) 実行失敗: {}", e);
            }
        }).await?;

        Ok(())
    }
}
```

`apps/noaa-station/Cargo.toml` に `urlencoding = "2.1"` と `serde_json = "1.0"` を追加。  
`apps/noaa-station/src/lib.rs` に `pub mod voicevox;` を追加。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test voicevox_test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/noaa-station/
git commit -m "feat: VOICEVOXずんだもん音声通知モジュールを追加"
```

---

### Task 3: 軌道計算 & パス予測エンジン (`orbit.rs`)

**Files:**
- Create: `apps/noaa-station/src/orbit.rs`
- Modify: `apps/noaa-station/src/lib.rs`
- Test: `apps/noaa-station/tests/orbit_test.rs`

**Interfaces:**
- Consumes: `noaa_station::config::ObserverConfig`, `SchedulerConfig`
- Produces:
  ```rust
  pub struct SatellitePass {
      pub satellite_name: String,
      pub frequency_hz: u64,
      pub aos: chrono::DateTime<chrono::Utc>,
      pub los: chrono::DateTime<chrono::Utc>,
      pub max_elevation_deg: f64,
  }
  pub struct OrbitPredictor;
  impl OrbitPredictor {
      pub fn predict_passes(
          tles: &[String],
          observer: &ObserverConfig,
          start_time: chrono::DateTime<chrono::Utc>,
          duration_hours: u64,
          min_el_deg: f64,
      ) -> Result<Vec<SatellitePass>>;
  }
  ```

- [ ] **Step 1: 既知のTLEと観測点を用いたパス検出のテストを作成**

`apps/noaa-station/tests/orbit_test.rs`:
```rust
use chrono::{Duration, Utc};
use noaa_station::config::ObserverConfig;
use noaa_station::orbit::{OrbitPredictor, SatelliteInfo};

#[test]
fn test_tle_parsing_and_pass_prediction() {
    // NOAA 19 のサンプルTLE
    let tle_line1 = "1 33591U 09005A   26246.41725515  .00000085  00000-0  62483-4 0  9993";
    let tle_line2 = "2 33591  99.1915 288.6652 0014285  97.6432 262.6288 14.12461749881180";
    let sat = SatelliteInfo {
        name: "NOAA 19".to_string(),
        norad_id: 33591,
        frequency_hz: 137_100_000,
        line1: tle_line1.to_string(),
        line2: tle_line2.to_string(),
    };

    let observer = ObserverConfig {
        latitude: 35.6895,
        longitude: 139.6917,
        altitude_m: 40.0,
    };

    let now = Utc::now();
    let passes = OrbitPredictor::predict_passes_for_satellite(&sat, &observer, now, 24, 15.0)
        .expect("Prediction failed");

    // 24時間あれば最低でも2回以上は日本上空をパスする
    assert!(!passes.is_empty(), "At least one pass should be detected within 24 hours");
    for pass in &passes {
        assert!(pass.max_elevation_deg >= 15.0);
        assert!(pass.los > pass.aos);
    }
}
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test orbit_test`
Expected: FAIL (`SatelliteInfo`, `OrbitPredictor` 未定義)

- [ ] **Step 3: `apps/noaa-station/src/orbit.rs` を実装**

`apps/noaa-station/src/orbit.rs`:
- SGP4 を初期化し、時間刻み（60秒刻み）で衛星の地球中心慣性座標系（ECI）位置を算出。
- 観測地（緯度・経度・標高）のECEF位置およびグリニッジ恒星時（GST）を用いて Topocentric 水平座標（Elevation, Azimuth）を算出。
- 仰角が `min_el_deg` を超えた区間を `SatellitePass` として検出し、`max_elevation_deg` を特定。
- 重複するパスがある場合は最大仰角が高い方を調停。
- CelesTrak からの最新 TLE HTTP 取得関数 `fetch_weather_tles() -> Result<Vec<SatelliteInfo>>` を実装。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test orbit_test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/noaa-station/
git commit -m "feat: SGP4による衛星軌道予測とパス検出モジュールを追加"
```

---

### Task 4: 受信ワーカー & デコーダワーカー (`receiver.rs`, `decoder.rs`)

**Files:**
- Create: `apps/noaa-station/src/receiver.rs`
- Create: `apps/noaa-station/src/decoder.rs`
- Modify: `apps/noaa-station/src/lib.rs`
- Test: `apps/noaa-station/tests/worker_test.rs`

**Interfaces:**
- Consumes: `SatellitePass`, 出力先パス
- Produces:
  - `ReceiverSession::start(pass, output_wav_path) -> Result<ReceiverSession>`
  - `ReceiverSession::stop(self) -> Result<()>` (SIGINT送信 & 正常終了待機)
  - `Decoder::decode_apt(wav_path, png_path) -> Result<()>`

- [ ] **Step 1: コマンドライン引数構築の単体テストを作成**

`apps/noaa-station/tests/worker_test.rs`:
```rust
use noaa_station::receiver::build_rtl_fm_args;
use noaa_station::decoder::build_noaa_apt_args;
use std::path::Path;

#[test]
fn test_command_args_construction() {
    let wav = Path::new("/tmp/test.wav");
    let png = Path::new("/tmp/test.png");

    let fm_args = build_rtl_fm_args(137_100_000, wav);
    assert!(fm_args.contains(&"-f".to_string()));
    assert!(fm_args.contains(&"137100000".to_string()));
    assert!(fm_args.contains(&"-M".to_string()));
    assert!(fm_args.contains(&"wfm".to_string()));

    let apt_args = build_noaa_apt_args(wav, png);
    assert_eq!(apt_args[0], "/tmp/test.wav");
    assert_eq!(apt_args[1], "-o");
    assert_eq!(apt_args[2], "/tmp/test.png");
}
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test worker_test`
Expected: FAIL (`build_rtl_fm_args` 未定義)

- [ ] **Step 3: `src/receiver.rs` と `src/decoder.rs` を実装**

`apps/noaa-station/src/receiver.rs`:
- `build_rtl_fm_args`: `["-M", "wfm", "-f", &freq.to_string(), "-s", "60k", "-r", "11025", "-E", "wav", "-F", "9", wav_path]`
- `ReceiverSession`: `tokio::process::Child` を保持。
- `stop()`: `nix::sys::signal::kill(Pid::from_raw(child_id), Signal::SIGINT)` を送り、プロセスがWAVヘッダを書き終えて終了するのを待機。

`apps/noaa-station/src/decoder.rs`:
- `build_noaa_apt_args`: `[wav_path, "-o", png_path]`
- `decode_apt`: `tokio::process::Command::new("noaa-apt")` を実行し、終了コードを検証。画像ファイルが正しく生成されたかをチェック。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test worker_test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/noaa-station/
git commit -m "feat: rtl_fm受信制御とnoaa-aptデコードモジュールを追加"
```

---

### Task 5: スケジューラステートマシン & CLI 統合 (`scheduler.rs`, `main.rs`)

**Files:**
- Create: `apps/noaa-station/src/scheduler.rs`
- Modify: `apps/noaa-station/src/main.rs`
- Modify: `apps/noaa-station/src/lib.rs`

**Interfaces:**
- Consumes: Task 1〜4 で作成した全モジュール
- Produces:
  - `cargo run -- schedule`
  - `cargo run -- test-voice`
  - `cargo run -- daemon`

- [ ] **Step 1: CLI サブコマンド定義と `main.rs` の骨格を作成**

`apps/noaa-station/src/main.rs`:
```rust
use clap::{Parser, Subcommand};
use noaa_station::config::Config;
use noaa_station::voicevox::VoicevoxClient;
use noaa_station::scheduler::run_daemon;
use noaa_station::orbit::{fetch_weather_tles, OrbitPredictor};
use anyhow::Result;

#[derive(Parser)]
#[command(name = "noaa-station")]
#[command(about = "NOAA気象衛星 自律自動受信・デコード地上局デーモン")]
struct Cli {
    #[arg(short, long, default_value = "config.toml")]
    config: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// 今後24時間の通過予定一覧を表示
    Schedule,
    /// ずんだもん音声発話テスト
    TestVoice,
    /// 自律監視デーモン起動
    Daemon,
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let cli = Cli::parse();
    let config = Config::load_from_file(&cli.config)?;

    match cli.command {
        Commands::Schedule => {
            // TLE取得 & パス予測テーブル表示
        }
        Commands::TestVoice => {
            let client = VoicevoxClient::new(config.voicevox);
            client.speak("テストなのだ！正常に通信できているのだ！").await?;
        }
        Commands::Daemon => {
            run_daemon(config).await?;
        }
    }
    Ok(())
}
```

- [ ] **Step 2: `apps/noaa-station/src/scheduler.rs` を実装**

- `tokio::select!` を用いたメインループ:
  1. `OrbitPredictor` からパス一覧を取得。
  2. 直近パスの事前通知時刻（`pass.aos - pre_alert_minutes`）までタイマー待機。
  3. 事前通知発話: 「まもなく {衛星名} が通過するのだ！最大仰角は {角度}度、受信を試みるのだ！」
  4. `pass.aos` までタイマー待機。
  5. 録音開始: `ReceiverSession::start`
  6. `pass.los` までタイマー待機。
  7. 録音停止: `session.stop()`
  8. デコード実行: `noaa-apt`
  9. 成否に応じた事後発話: 「{衛星名} の受信とデコードに成功したのだ！」
  10. 次のパスへループ。
  - OSシグナル（Ctrl+C）受信時は安全に録音プロセスを停止して Graceful Shutdown。

- [ ] **Step 3: `cargo check` と `cargo build` を実行してコンパイル成功を確認**

Run: `cargo check --workspace`
Expected: SUCCESS

- [ ] **Step 4: サブコマンド `schedule` のドライラン動作確認**

Run: `cargo run -p noaa-station -- schedule`
Expected: 衛星通過予定テーブルが標準出力に表示される。

- [ ] **Step 5: コミット**

```bash
git add apps/noaa-station/
git commit -m "feat: 自律スケジューラステートマシンとCLIサブコマンドを実装"
```

---

### Task 6: リポジトリ構成更新 & ドキュメント反映

**Files:**
- Modify: `README.md`
- Modify: `docs/04_qa.md`
- Modify: `.gitignore`

- [ ] **Step 1: `README.md` のディレクトリ構成セクションを `apps/` 構成に更新**
- [ ] **Step 2: `data/noaa/` を `.gitignore` に追加**
- [ ] **Step 3: `docs/04_qa.md` に NOAA衛星自律受信・軌道計算・VOICEVOX連携の知見を記録**
- [ ] **Step 4: コミット**

```bash
git add README.md docs/04_qa.md .gitignore
git commit -m "docs: ディレクトリ構成の更新とNOAA自律受信地上局の知見を記録"
```
