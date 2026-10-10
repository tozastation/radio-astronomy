# satellite-tracker-rs Rust リライト実装計画
## (Ultra-low Power Edge Satellite Tracker in Rust Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** エッジ（GPD Pocket3）の消費電力・メモリフットプリント（約10MB）・イメージサイズ（約30MB）を極小化するため、衛星観測コンポーネントを Rust ネイティブバイナリ `satellite-tracker-rs` でリライトする。

**Architecture:** `apps/satellite-tracker-rs` を新設クレートとして作成。SGP4 軌道力学計算（`sgp4`）、動的省電力 SDR 制御（待機時クローズ・AOS 30秒前オープン）、ゼロコピー DSP（インプレースドップラー補正・FM検波・48kHzデシメーション）、`hound` による WAV スプール録音、Garage S3 アップロード、および `axum` による Prometheus メトリクス HTTP サーバー（`:9100/metrics`）を単一バイナリに凝縮する。

**Tech Stack:** Rust 2021, Tokio, sgp4, rustfft, hound, axum, reqwest/aws-sdk-s3, serde, Docker (distroless)

**Spec:** `docs/superpowers/specs/2026-10-10-satellite-tracker-rs-rewrite-design.md`

## Global Constraints
- メモリフットプリント約 15MB 以下、アイドル時 CPU 使用率 0.05 cores 以下。
- 待機時は SDR ハードウェアを完全にクローズし、給電と発熱を停止する。
- 既存の Prometheus メトリクス仕様（名前、ラベル、ポート `:9100/metrics`）と完全互換。
- コミットメッセージは日本語、絵文字禁止、Conventional Commits 形式（例: `feat: ...`, `fix: ...`）。

## Review Focus
1. ゼロコピー DSP 処理において、バッファ再利用によりループ内メモリアロケーションが発生しないこと。
2. 浮動小数点ドップラー偏移計算の精度が Python 版（SGP4）と数十 Hz 以内で一致すること。
3. 狭帯域 48kHz WAV ファイルが標準 RIFF WAVE 形式として `hound` で正常に出力され破損しないこと。
4. ホスト空き容量が 10% 未満になった際、Circuit Breaker が最古ファイルを正しく削除してパージすること。
5. 非通過時のスタンバイ状態で CPU ループが回らず、`tokio::time::sleep` で完全にスリープすること。

---

### Task 1: クレート雛形作成と Cargo ワークスペース設定

**Files:**
- Create: `apps/satellite-tracker-rs/Cargo.toml`
- Create: `apps/satellite-tracker-rs/src/lib.rs`
- Create: `apps/satellite-tracker-rs/src/main.rs`
- Modify: `Cargo.toml`

**Interfaces:**
- Produces: Cargo ワークスペースメンバー `apps/satellite-tracker-rs`、バイナリターゲット `satellite-tracker-rs`

- [ ] **Step 1: `apps/satellite-tracker-rs/Cargo.toml` を作成**
  必要な依存関係（`tokio`, `sgp4`, `rustfft`, `hound`, `axum`, `serde`, `serde_json`, `chrono`, `anyhow`, `log`, `env_logger`）を定義。

- [ ] **Step 2: ルート `Cargo.toml` の `members` に `apps/satellite-tracker-rs` を追加**

- [ ] **Step 3: `cargo check` を実行してコンパイル疎通を確認**
  Run: `cargo check -p satellite-tracker-rs`
  Expected: PASS

- [ ] **Step 4: コミット**
  ```bash
  git add apps/satellite-tracker-rs/ Cargo.toml Cargo.lock
  git commit -m "feat: satellite-tracker-rs クレートの雛形とワークスペース設定を追加"
  ```

---

### Task 2: SGP4 軌道予測・視界判定・ドップラー偏移計算モジュール (`orbit.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/orbit.rs`
- Test: `apps/satellite-tracker-rs/tests/orbit_test.rs`

**Interfaces:**
- Produces: `OrbitPredictor` 構造体
  - `new(lat: f64, lon: f64, alt_m: f64, balcony_facing: &str) -> Self`
  - `calculate_position(sat_name: &str, tle1: &str, tle2: &str, freq_hz: f64, time: DateTime<Utc>) -> Result<SatPosition>`
  - `is_in_view(elevation_deg: f64, azimuth_deg: f64) -> bool`
  - `get_next_pass(...) -> Option<PassPrediction>`

- [ ] **Step 1: 軌道計算と視界判定のテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/orbit_test.rs` に ISS と CAS-4A の TLE を用いた現在位置・ドップラー偏移および北向きベランダ視界判定のテストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test orbit_test`
  Expected: FAIL (`orbit.rs` 未定義)

- [ ] **Step 3: `orbit.rs` を実装 (GREEN)**
  `sgp4` クレートを用いた衛星位置計算、地平座標系（Az/El）変換、視線速度からのドップラー計算、および北向きフィルタ（$270^\circ \sim 90^\circ$, $\text{El} \ge 10^\circ$）を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test orbit_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/orbit.rs apps/satellite-tracker-rs/tests/orbit_test.rs
  git commit -m "feat: SGP4軌道計算・ドップラー偏移・北向きベランダ視界判定モジュールを追加"
  ```

---

### Task 3: ゼロコピー DSP 処理モジュール (`dsp.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/dsp.rs`
- Test: `apps/satellite-tracker-rs/tests/dsp_test.rs`

**Interfaces:**
- Produces: `DspProcessor` 構造体
  - `new(input_sample_rate: f64, target_sample_rate: u32) -> Self`
  - `process_samples(iq_samples: &[(f32, f32)], doppler_hz: f64, out_pcm: &mut Vec<i16>)`

- [ ] **Step 1: ドップラー補正と FM 復調・デシメーションのテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/dsp_test.rs` に、合成 IQ 信号に対するドップラー逆ミキシング、クワドラチャ検波、および 48kHz PCM 出力のテストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test dsp_test`
  Expected: FAIL (`dsp.rs` 未定義)

- [ ] **Step 3: `dsp.rs` を実装 (GREEN)**
  バッファのインプレース処理により、周波数補正 $\to$ 位相差分角周波数抽出 $\to$ 50倍デシメーション $\to$ 16bit PCM スケーリングを実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test dsp_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/dsp.rs apps/satellite-tracker-rs/tests/dsp_test.rs
  git commit -m "feat: ゼロコピードップラー補正・FM復調・デシメーションDSPモジュールを追加"
  ```

---

### Task 4: 狭帯域 48kHz WAV スプール録音 ＆ Circuit Breaker (`spooler.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/spooler.rs`
- Test: `apps/satellite-tracker-rs/tests/spooler_test.rs`

**Interfaces:**
- Produces: `AudioSpooler` 構造体
  - `new(spool_dir: PathBuf, sample_rate: u32, max_spool_bytes: u64, min_free_percent: f64) -> Self`
  - `start_pass(satellite: &str, pass_id: &str) -> Result<PathBuf>`
  - `write_frames(pcm_samples: &[i16]) -> Result<()>`
  - `finish_pass() -> Result<Option<PathBuf>>`
  - `enforce_circuit_breaker() -> Result<()>`

- [ ] **Step 1: WAV 出力とサーキットブレーカーのテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/spooler_test.rs` に、`hound` による WAV ファイル生成と容量超過時の最古ファイル削除テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test spooler_test`
  Expected: FAIL (`spooler.rs` 未定義)

- [ ] **Step 3: `spooler.rs` を実装 (GREEN)**
  `hound::WavWriter` によるストリーミング書き込みと、ファイルサイズ・ディスク空き容量チェックによる自動削除ロジックを実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test spooler_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/spooler.rs apps/satellite-tracker-rs/tests/spooler_test.rs
  git commit -m "feat: houndによる48kHzWAVスプール録音と容量保護CircuitBreakerを追加"
  ```

---

### Task 5: SDR 抽象化 ＆ 動的省電力ライフサイクル (`sdr.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/sdr.rs`
- Test: `apps/satellite-tracker-rs/tests/sdr_test.rs`

**Interfaces:**
- Produces: `SdrCollector` 構造体
  - `new(mock_sdr: bool, sample_rate: f64, gain: f64) -> Self`
  - `standby() -> Result<()>`
  - `warmup(center_freq_hz: f64) -> Result<()>`
  - `read_samples(count: usize) -> Result<Vec<(f32, f32)>>`
  - `is_standby(&self) -> bool`

- [ ] **Step 1: 省電力スタンバイとウォームアップのテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/sdr_test.rs` に、モックモードでのスタンバイ（停止）とウォームアップ（再開）の動作検証テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test sdr_test`
  Expected: FAIL (`sdr.rs` 未定義)

- [ ] **Step 3: `sdr.rs` を実装 (GREEN)**
  動的ライフサイクル管理、実機 `librtlsdr` 呼び出しおよびモック信号生成を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test sdr_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/sdr.rs apps/satellite-tracker-rs/tests/sdr_test.rs
  git commit -m "feat: SDRハードウェアの動的省電力スタンバイ・ウォームアップ制御を追加"
  ```

---

### Task 6: Garage S3 非同期アップロードモジュール (`s3.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/s3.rs`
- Test: `apps/satellite-tracker-rs/tests/s3_test.rs`

**Interfaces:**
- Produces: `S3Uploader` 構造体
  - `new(endpoint: String, bucket: String, access_key: String, secret_key: String) -> Self`
  - `upload_and_cleanup(local_path: &Path, s3_key: &str) -> Result<()>`

- [ ] **Step 1: S3 アップロードと完了時削除のテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/s3_test.rs` にモックアップロードおよびファイル削除検証テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test s3_test`
  Expected: FAIL (`s3.rs` 未定義)

- [ ] **Step 3: `s3.rs` を実装 (GREEN)**
  非同期 HTTP PUT による S3 アップロードと成功時の `std::fs::remove_file` を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test s3_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/s3.rs apps/satellite-tracker-rs/tests/s3_test.rs
  git commit -m "feat: Garage S3への非同期アップロードとローカルクリーンアップモジュールを追加"
  ```

---

### Task 7: Prometheus メトリクス HTTP サーバー (`metrics.rs`)

**Files:**
- Create: `apps/satellite-tracker-rs/src/metrics.rs`
- Test: `apps/satellite-tracker-rs/tests/metrics_test.rs`

**Interfaces:**
- Produces: `MetricsExporter` 構造体
  - `new() -> Self`
  - `start_server(port: u16) -> JoinHandle<()>`
  - `update_orbit_metrics(...)` / `record_pass_completed(...)`
  - `render_prometheus_text() -> String`

- [ ] **Step 1: Prometheus メトリクス出力フォーマットのテストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/metrics_test.rs` に、Python 版と完全に同一のキー・ラベルでテキスト出力されるかの検証テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `cargo test --test metrics_test`
  Expected: FAIL (`metrics.rs` 未定義)

- [ ] **Step 3: `metrics.rs` を実装 (GREEN)**
  アトミックな値更新と `axum` による `:9100/metrics` レスポンス処理を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `cargo test --test metrics_test`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/metrics.rs apps/satellite-tracker-rs/tests/metrics_test.rs
  git commit -m "feat: axumによる超軽量PrometheusメトリクスHTTPサーバーを追加"
  ```

---

### Task 8: メイン非同期オーケストレーション結合と E2E 検証 (`main.rs`)

**Files:**
- Modify: `apps/satellite-tracker-rs/src/main.rs`
- Test: `apps/satellite-tracker-rs/tests/integration_test.rs`

**Interfaces:**
- Produces: 実行可能バイナリ `target/release/satellite-tracker-rs`

- [ ] **Step 1: 統合 E2E テストを作成 (RED)**
  `apps/satellite-tracker-rs/tests/integration_test.rs` に、擬似パス突入 $\to$ スプール録音 $\to$ LOS 完了 $\to$ S3 アップロード $\to$ スタンバイ移行の一連の統合テストを記述。

- [ ] **Step 2: `main.rs` に各モジュールを結合 (GREEN)**
  Tokio イベントループ、シグナルハンドラ、AOS/LOS 判定、動的省電力スタンバイ制御を実装。

- [ ] **Step 3: 統合テストを実行して成功することを確認**
  Run: `cargo test --test integration_test`
  Expected: PASS

- [ ] **Step 4: コミット**
  ```bash
  git add apps/satellite-tracker-rs/src/main.rs apps/satellite-tracker-rs/tests/integration_test.rs
  git commit -m "feat: satellite-tracker-rsのメイン非同期ループ結合と統合テストを追加"
  ```

---

### Task 9: マルチステージ Dockerfile 作成とビルド検証

**Files:**
- Create: `apps/satellite-tracker-rs/Dockerfile`

**Interfaces:**
- Produces: 極小コンテナイメージ `localhost:5000/satellite-tracker-rs:dev` (約 30MB)

- [ ] **Step 1: `apps/satellite-tracker-rs/Dockerfile` を作成**
  マルチステージビルド（`rust:1.80-bookworm` $\to$ `debian:bookworm-slim` または distroless）を定義。

- [ ] **Step 2: ローカルビルドテストを実行**
  Run: `cargo build --release -p satellite-tracker-rs`
  Expected: バイナリサイズが約 15MB 程度で正常生成されることを確認。

- [ ] **Step 3: コミット**
  ```bash
  git add apps/satellite-tracker-rs/Dockerfile
  git commit -m "feat: satellite-tracker-rsの極小マルチステージDockerfileを追加"
  ```
