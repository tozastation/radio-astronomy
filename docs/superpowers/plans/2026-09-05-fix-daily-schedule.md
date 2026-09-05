# デイリー衛星受信スケジュール配信機能 修正実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** デイリー衛星受信スケジュールのDiscord自動配信機能を、メインループのブロッキングから独立した非同期定期タスクへ分離し、設定した定時（デフォルト毎朝07:00 JST）に正確にその日の予定を配信できるようにする。

**Architecture:** 
1. `scheduler.rs` の観測制御ループ（SDR Receiver State Machine）からデイリー配信判定を完全分離し、`tokio::spawn` による独立したバックグラウンドスケジューラタスク（`run_daily_scheduler`）を新設。
2. `Config` に定時配信の有効化フラグ、配信時刻（時・分）、起動時即時配信フラグを追加。
3. デイリー配信時は「当日（JST 00:00〜23:59）」のパスを厳密に抽出してタイトルと整合させ、手動CLI実行時は「今後24時間」として日付付きで明確に表示を分ける。
4. Discord Webhook 送信失敗時のリトライ機構と、成功時のみ完了マークする堅牢なステート管理を導入。

**Tech Stack:** Rust 2021, Tokio (async tasks / timer / select), Chrono (DateTime, Local, Utc, NaiveDate), Reqwest, Serde/TOML.

**Spec:** 現行コード分析に基づくレビュー仕様（[scheduler.rs](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/ground-station/src/scheduler.rs), [discord.rs](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/ground-station/src/discord.rs), [config.rs](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/ground-station/src/config.rs)）

## Global Constraints
- 日本語で記述する。
- 絵文字は禁止。
- コミットメッセージは Conventional Commits 形式（`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`）に従い、「〜を追加」「〜を修正」のように簡潔に記述する。
- すべてのコードとテストが `cargo check` および `cargo test --all` をパスすること。

---

### Task 1: 設定構造体（Config）へのデイリースケジュール設定追加

**Files:**
- Modify: `apps/ground-station/src/config.rs`
- Modify: `apps/ground-station/config.toml`
- Test: `apps/ground-station/tests/unit/config_test.rs`

**Interfaces:**
- `SchedulerConfig` に以下のフィールドを追加:
  - `daily_schedule_enabled: bool` (default: true)
  - `daily_schedule_hour_jst: u32` (default: 7)
  - `daily_schedule_minute_jst: u32` (default: 0)
  - `daily_schedule_send_on_startup: bool` (default: false)

- [ ] **Step 1: 失敗するユニットテストを作成**

`apps/ground-station/tests/unit/config_test.rs` に `test_daily_schedule_config_defaults` を追加。

```rust
#[test]
fn test_daily_schedule_config_defaults() {
    let toml = r#"
        [observer]
        latitude = 35.68
        longitude = 139.76
        altitude_m = 50.0

        [scheduler]
        min_elevation_deg = 25.0
        pre_alert_minutes = 15.0
        tle_update_interval_hours = 24

        [voicevox]
        enabled = false
        host = "http://localhost:50021"
        speaker_id = 3

        [storage]
        output_dir = "data/noaa"
    "#;
    let config = Config::from_str(toml).expect("パース成功すること");
    assert!(config.scheduler.daily_schedule_enabled);
    assert_eq!(config.scheduler.daily_schedule_hour_jst, 7);
    assert_eq!(config.scheduler.daily_schedule_minute_jst, 0);
    assert!(!config.scheduler.daily_schedule_send_on_startup);
}
```

- [ ] **Step 2: テストを実行して失敗を確認**
- [x] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test unit_config test_daily_schedule_config_defaults`
Expected: FAIL (フィールドが存在しないためコンパイルエラー)

- [x] **Step 3: 設定構造体とデフォルト値を実装**

`apps/ground-station/src/config.rs`:
```rust
fn default_true() -> bool {
    true
}

fn default_false() -> bool {
    false
}

fn default_schedule_hour_jst() -> u32 {
    7
}

fn default_schedule_minute_jst() -> u32 {
    0
}

#[derive(Debug, Clone, Deserialize)]
pub struct SchedulerConfig {
    pub min_elevation_deg: f64,
    pub pre_alert_minutes: f64,
    pub tle_update_interval_hours: u64,
    #[serde(default = "default_true")]
    pub daily_schedule_enabled: bool,
    #[serde(default = "default_schedule_hour_jst")]
    pub daily_schedule_hour_jst: u32,
    #[serde(default = "default_schedule_minute_jst")]
    pub daily_schedule_minute_jst: u32,
    #[serde(default = "default_false")]
    pub daily_schedule_send_on_startup: bool,
}
```

`apps/ground-station/config.toml` に設定例をコメント付きで追記。

- [x] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_config`
Expected: PASS

- [x] **Step 5: コミット**

```bash
git add apps/ground-station/src/config.rs apps/ground-station/config.toml apps/ground-station/tests/unit/config_test.rs
git commit -m "feat: デイリースケジュール配信の設定項目を追加"
```

---

### Task 2: Discord Embed とスケジュール送信ロジックの改善 (日付・期間の明確化 & エラー伝播)

**Files:**
- Modify: `apps/ground-station/src/discord.rs`
- Test: `apps/ground-station/tests/unit/discord_test.rs`

**Interfaces:**
- `DiscordClient::build_daily_schedule_embed(passes: &[SatellitePass], title: &str, description: &str) -> serde_json::Value`
- `DiscordClient::send_schedule_embed(&self, title: &str, description: &str, greeting: &str, passes: &[SatellitePass]) -> Result<()>`
  - HTTP 非 200 や送信エラー時に `anyhow::bail!` または `Err` を返し、呼び出し元が成否を判定できるようにする。
- 25件超過時の省略表示（`... 他 X 件`）対応。
- 日付を跨ぐパスにおける日付表示（例: `09-06 08:30`）対応。

- [x] **Step 1: 失敗するユニットテストを作成**

`apps/ground-station/tests/unit/discord_test.rs` に、25件超過時のフッター表示および日付フォーマットのテストを追加。

- [x] **Step 2: テストを実行して失敗を確認**

Run: `cargo test --test unit_discord`

- [x] **Step 3: 実装**

`apps/ground-station/src/discord.rs` の `build_daily_schedule_embed` および `send_daily_schedule` をリファクタリング。
- エラーハンドリング: レスポンスが `is_success()` でない場合は `bail!` してエラーを呼び出し元に伝える。
- Embed 内で 25 件を超えた場合、フッターまたは末尾フィールドで超過件数を案内。
- 各パスの日時について、当日の場合は時刻のみ、別日の場合は月日も併記。

- [x] **Step 4: テストを実行して成功を確認**

Run: `cargo test --test unit_discord`
Expected: PASS

- [x] **Step 5: コミット**

```bash
git add apps/ground-station/src/discord.rs apps/ground-station/tests/unit/discord_test.rs
git commit -m "fix: スケジュールEmbedの日時表示改善と送信エラー伝播を追加"
```

---

### Task 3: 当日パス抽出ロジックおよび独立定期スケジューラタスクの実装

**Files:**
- Modify: `apps/ground-station/src/scheduler.rs`
- Test: `apps/ground-station/tests/unit/scheduler_test.rs` (新規または既存拡張)

**Interfaces:**
- `pub fn filter_passes_for_jst_date(passes: &[SatellitePass], date: chrono::NaiveDate) -> Vec<SatellitePass>`
- `pub async fn run_daily_scheduler(config: Config, discord: Arc<DiscordClient>) -> Result<()>`
  - 独立タスクとして起動。
  - 次回の配信時刻（JST）を計算し、`tokio::time::sleep` で待機（Graceful Shutdown シグナル対応）。
  - 配信時刻到達時: CelesTrak から最新 TLE 取得 → 当日 JST 00:00〜23:59:59 のパスを計算 → Discord 送信。
  - 成功時: 送信済み日付を記録し、翌朝の配信待機へ。
  - 失敗時: ログ出力し、5分後にリトライ。
- `run_daemon`: メインループ内の直列同期送信処理を削除し、`tokio::spawn(run_daily_scheduler(...))` を起動。
- `send_schedule_to_discord`: 手動CLI実行時は「今後24時間の衛星通過スケジュール」として送信。

- [x] **Step 1: 失敗するユニットテストを作成**
- [x] **Step 2: テストを実行して失敗を確認**
- [x] **Step 3: 実装**
- [x] **Step 4: 全テストを実行して成功を確認**
- [x] **Step 5: コミット**

---

### Task 4: 統合動作確認とドキュメント更新

**Files:**
- Modify: `docs/superpowers/plans/2026-09-05-fix-daily-schedule.md`
- Modify: `docs/04_qa.md` (または該当ドキュメント)

- [x] **Step 1: 全ユニットテスト・結合テスト実行**
- [x] **Step 2: ヘルプメッセージやCLIの動作確認**
- [x] **Step 3: ドキュメントの記録とコミット**


```bash
git add docs/
git commit -m "docs: デイリースケジューラの非同期分離アーキテクチャを記録"
```
