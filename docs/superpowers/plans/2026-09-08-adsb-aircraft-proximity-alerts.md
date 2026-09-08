# ADS-B 航空機近接監視・実機写真付き自動通知システム 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 衛星通過の合間（アイドル時間帯）に、自宅上空を通過する航空機（ADS-B: 1090MHz）を自動検知し、hexdb.io（発着地ルート）および Planespotters.net（実機写真）をキャッシュ付きで照会して、Discord リッチ Embed およびずんだもん音声（VOICEVOX）で通知する機能を `ground-station` に追加する。

**Architecture:** Ultrafeeder (readsb) がローカル提供する `http://localhost:8080/data/aircraft.json` を数秒周期で非同期 HTTP ポーリング。自宅座標からの球面大圏距離（Haversine）を計算し、半径 X km（デフォルト 8km）以内に入った機体を抽出。メモリ内キャッシュ（30分クールダウン、実機写真・ルート情報キャッシュ）を用いて重複通知と外部負荷を防ぎ、Discord と VOICEVOX へ並行通知する。

**Tech Stack:** Rust 2021, Tokio, Reqwest, Serde, Serde_json, Chrono, Log/Env_logger

**Spec:** [docs/superpowers/specs/2026-09-08-adsb-aircraft-proximity-alerts-design.md](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/docs/superpowers/specs/2026-09-08-adsb-aircraft-proximity-alerts-design.md)

## Global Constraints

- 日本語で記述すること
- 絵文字はコミットメッセージには含めないこと（Conventional Commits 準拠: `feat: ...`, `fix: ...`, `docs: ...`）
- 外部クレートの追加は不要（既存の `reqwest`, `serde`, `serde_json`, `tokio` で完結）
- 既存の衛星追跡（Meteor-M, CubeSat, ISS）機能に悪影響を与えないこと（非同期独立タスク化）
- 外部 API（Planespotters, hexdb）の失敗や未登録機体でもパニックせず、Graceful Degradation すること

---

### Task 1: 設定スキーマの拡張 (`AdsbConfig`) と単体テスト

**Files:**
- Modify: `apps/ground-station/src/config.rs:50-120`
- Modify: `apps/ground-station/config.toml:50-55`
- Test: `apps/ground-station/tests/unit/config_test.rs`

**Interfaces:**
- Produces: `ground_station::config::AdsbConfig`
  ```rust
  #[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq)]
  pub struct AdsbConfig {
      pub enabled: bool,
      pub data_url: String,
      pub tar1090_url: String,
      pub poll_interval_secs: u64,
      pub max_distance_km: f64,
      pub min_altitude_m: f64,
      pub max_altitude_m: f64,
      pub cooldown_minutes: u64,
      pub fetch_routes: bool,
      pub fetch_photos: bool,
      pub discord_alert: bool,
      pub voice_alert: bool,
  }
  ```

- [ ] **Step 1: 失敗する単体テストの作成**

`apps/ground-station/tests/unit/config_test.rs` に `test_adsb_config_parsing` を追加：

```rust
#[test]
fn test_adsb_config_parsing() {
    let toml_str = r#"
    [observer]
    latitude = 35.7903
    longitude = 139.2584
    altitude_m = 200.0

    [adsb]
    enabled = true
    data_url = "http://localhost:8080/data/aircraft.json"
    tar1090_url = "http://localhost:8080"
    poll_interval_secs = 2
    max_distance_km = 8.0
    min_altitude_m = 500.0
    max_altitude_m = 13000.0
    cooldown_minutes = 30
    fetch_routes = true
    fetch_photos = true
    discord_alert = true
    voice_alert = true
    "#;

    let config: Config = toml::from_str(toml_str).expect("パース成功");
    assert!(config.adsb.enabled);
    assert_eq!(config.adsb.max_distance_km, 8.0);
    assert_eq!(config.adsb.poll_interval_secs, 2);
}
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test unit_config`
Expected: FAIL (`struct Config has no field adsb`)

- [ ] **Step 3: `AdsbConfig` と `Default` 実装**

`apps/ground-station/src/config.rs` に `AdsbConfig` を追加し、`Config` 構造体に `#[serde(default)] pub adsb: AdsbConfig` を追加。
`apps/ground-station/config.toml` に `[adsb]` セクションを追記。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_config`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/ground-station/src/config.rs apps/ground-station/config.toml apps/ground-station/tests/unit/config_test.rs
git commit -m "feat: ADS-B近接監視の設定スキーマAdsbConfigを追加"
```

---

### Task 2: ADS-B モデル、Haversine 距離計算、キャッシュ状態管理 (`src/adsb.rs`)

**Files:**
- Create: `apps/ground-station/src/adsb.rs`
- Modify: `apps/ground-station/src/lib.rs`
- Modify: `apps/ground-station/Cargo.toml`
- Create: `apps/ground-station/tests/unit/adsb_test.rs`

**Interfaces:**
- Produces:
  ```rust
  pub fn haversine_distance_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64;
  pub struct AircraftData { pub hex: String, pub flight: Option<String>, pub lat: Option<f64>, pub lon: Option<f64>, pub alt_baro: Option<serde_json::Value>, pub gs: Option<f64>, ... }
  pub struct AdsbCache { ... }
  ```

- [ ] **Step 1: 失敗する単体テストの作成**

`apps/ground-station/tests/unit/adsb_test.rs` を新規作成：
- 青梅市（35.7903, 139.2584）から羽田空港（35.5494, 139.7798）の距離計算テスト（直線距離約 54.2 km ± 1km）
- `aircraft.json` のサンプル JSON デシリアライズテスト（`alt_baro` が数値または `"ground"` の両パターンに対応）
- `AdsbCache` のクールダウン判定テスト

- [ ] **Step 2: テストを実行して失敗を確認**

`Cargo.toml` に `[[test]] name = "unit_adsb" path = "tests/unit/adsb_test.rs"` を追加し：
Run: `cargo test --test unit_adsb`
Expected: FAIL (`module adsb not found`)

- [ ] **Step 3: `src/adsb.rs` のデータモデル・幾何計算・キャッシュ実装**

- `src/lib.rs` に `pub mod adsb;` を追加。
- `haversine_distance_km` 関数を実装。
- `readsb` の `aircraft.json` に対応する serde デシリアライズ構造体を実装。
- `AdsbCache` 構造体を実装（`check_and_update_alert(...)`、写真・ルートキャッシュ `get_photo` / `set_photo` 等）。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_adsb`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/ground-station/src/adsb.rs apps/ground-station/src/lib.rs apps/ground-station/Cargo.toml apps/ground-station/tests/unit/adsb_test.rs
git commit -m "feat: ADS-BデータモデルとHaversine距離計算およびキャッシュを実装"
```

---

### Task 3: 外部 API クライアント（`hexdb.io` & `Planespotters.net`）

**Files:**
- Modify: `apps/ground-station/src/adsb.rs`
- Modify: `apps/ground-station/tests/unit/adsb_test.rs`

**Interfaces:**
- Produces:
  ```rust
  pub struct FlightRoute { pub callsign: String, pub origin_iata: Option<String>, pub origin_name: Option<String>, pub destination_iata: Option<String>, pub destination_name: Option<String> }
  pub struct AircraftPhotoMeta { pub thumbnail_large: String, pub photographer: String, pub aircraft_type: Option<String>, pub airline_name: Option<String> }
  pub async fn fetch_flight_route(client: &reqwest::Client, callsign: &str) -> Option<FlightRoute>;
  pub async fn fetch_aircraft_photo(client: &reqwest::Client, hex: &str) -> Option<AircraftPhotoMeta>;
  ```

- [ ] **Step 1: 外部 API レスポンス解析の単体テスト作成**

`tests/unit/adsb_test.rs` に hexdb.io と Planespotters.net の JSON モックパーステストを追加：
- hexdb の JSON（`origin`, `destination`）パース検証
- planespotters の JSON（`photos[0].thumbnail_large.src`, `photographer`）パース検証
- 存在しない機体（空配列や 404）の `None` フォールバック検証

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test unit_adsb`
Expected: FAIL

- [ ] **Step 3: API 問い合わせ関数の実装**

`src/adsb.rs` 内に `fetch_flight_route` および `fetch_aircraft_photo` を実装。
User-Agent ヘッダを適切に設定し、タイムアウト 5 秒、エラー時はログ出力して `None` を返す Graceful な実装。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_adsb`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/ground-station/src/adsb.rs apps/ground-station/tests/unit/adsb_test.rs
git commit -m "feat: hexdbルート照会とPlanespotters実機写真照会クライアントを実装"
```

---

### Task 4: Discord & VOICEVOX 通知フォーマットの実装

**Files:**
- Modify: `apps/ground-station/src/discord.rs`
- Modify: `apps/ground-station/src/adsb.rs`
- Modify: `apps/ground-station/tests/unit/discord_test.rs`

**Interfaces:**
- Produces:
  ```rust
  pub struct AircraftAlert {
      pub icao_hex: String,
      pub callsign: String,
      pub airline: Option<String>,
      pub aircraft_type: Option<String>,
      pub origin: Option<String>,
      pub destination: Option<String>,
      pub altitude_m: f64,
      pub speed_kmh: f64,
      pub distance_km: f64,
      pub photo_url: Option<String>,
      pub photographer: Option<String>,
      pub tar1090_url: String,
  }
  impl DiscordClient {
      pub async fn send_aircraft_alert(&self, alert: &AircraftAlert) -> Result<()>;
  }
  ```

- [ ] **Step 1: Discord Embed 生成の単体テスト作成**

`tests/unit/discord_test.rs` に `test_aircraft_alert_embed_build` を追加：
- 実機写真がある場合、`image.url` がセットされること
- ルートがある場合、タイトルや description に `HND ➜ FUK` が含まれること
- 写真がない場合でもエラーにならず正常な Embed JSON が構築されること

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test unit_discord`
Expected: FAIL (`send_aircraft_alert` / `AircraftAlert` 未定義)

- [ ] **Step 3: `send_aircraft_alert` と Embed 構築の実装**

`src/discord.rs` に `AircraftAlert` 構造体と `send_aircraft_alert` メソッドを追加。
カラー `0x3498DB`、インラインフィールド、写真 Embed、フッターを構築。
`src/adsb.rs` にずんだもん発話用テキスト生成関数 `build_voice_text(alert: &AircraftAlert) -> String` を実装。

- [ ] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_discord`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/ground-station/src/discord.rs apps/ground-station/src/adsb.rs apps/ground-station/tests/unit/discord_test.rs
git commit -m "feat: 航空機接近通知用のDiscord実機写真Embedとずんだもん発話を実装"
```

---

### Task 5: 常駐監視ループ (`run_adsb_monitor`) と CLI サブコマンド (`test-adsb`) の統合

**Files:**
- Modify: `apps/ground-station/src/adsb.rs`
- Modify: `apps/ground-station/src/scheduler.rs`
- Modify: `apps/ground-station/src/main.rs`

**Interfaces:**
- Produces:
  ```rust
  pub async fn run_adsb_monitor(
      config: Config,
      discord: std::sync::Arc<DiscordClient>,
      voice: std::sync::Arc<VoicevoxClient>,
  ) -> Result<()>;
  pub async fn test_adsb_alert(config: &Config) -> Result<()>;
  ```

- [ ] **Step 1: 監視メインループ `run_adsb_monitor` の実装**

`src/adsb.rs` に `run_adsb_monitor` を実装：
- `tokio::select!` によりシャットダウンシグナルを監視。
- 指定インターバル（`poll_interval_secs`）で `data_url` から JSON 取得。
- 各機体の座標から近接判定、未通知なら API 照会 $\to$ Discord/VOICEVOX 通知 $\to$ キャッシュ更新。

- [ ] **Step 2: `test_adsb_alert` 単体検証関数の実装**

`aircraft.json` を 1 回フェッチし、現在レーダー圏内で最も自宅に近い機体（またはサンプル機体）を抽出して、実際に Discord 送信およびずんだもん発話を 1 回実行するテスト関数を実装。

- [ ] **Step 3: `scheduler.rs` と `main.rs` への配線**

- `src/scheduler.rs`: `run_daemon` 内で `if config.adsb.enabled` の場合に `tokio::spawn(run_adsb_monitor(...))` を起動。
- `src/main.rs`: `Commands::TestAdsb` を追加し、`test_adsb_alert(&config).await?` を呼び出す。

- [ ] **Step 4: ビルドとテストの確認**

Run: `cargo check`
Run: `cargo test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/ground-station/src/adsb.rs apps/ground-station/src/scheduler.rs apps/ground-station/src/main.rs
git commit -m "feat: ADS-B常駐監視ループとtest-adsbサブコマンドを統合"
```

---

### Task 6: 総合検証とドキュメント更新

**Files:**
- Test: `cargo test` 全体実行
- Verify: `cargo run -- test-adsb`（SSH 先の Ultrafeeder と通信テスト）
- Modify: `docs/04_qa.md`
- Modify: `docs/qa/08_adsb_flight_tracking_and_tar1090.md`

- [ ] **Step 1: 全単体テストの実行**

Run: `cargo test`
Expected: 全テスト PASS（`unit_config`, `unit_adsb`, `unit_discord`, `unit_worker`, `unit_scheduler` 等）

- [ ] **Step 2: 動作検証（CLI テスト）**

Run: `cargo run -- test-adsb`（または SSH 先での実機確認用コマンドの案内）
Expected: 現在のフライト情報、Planespotters の写真、Discord Embed の送信成功

- [ ] **Step 3: ドキュメントの更新とコミット**

Q&A ドキュメントに「衛星地上局と ADS-B 近接監視の時分割統合アーキテクチャ」を追記。

```bash
git add docs/04_qa.md docs/qa/08_adsb_flight_tracking_and_tar1090.md
git commit -m "docs: ADS-B近接監視と写真通知の統合アーキテクチャをQ&Aに追記"
```
