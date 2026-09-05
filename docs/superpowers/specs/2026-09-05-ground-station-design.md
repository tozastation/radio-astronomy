# 🛰️ Architecture Design: Personal Ground Station (`ground-station`)

- **Author**: tozastation & Antigravity
- **Date**: 2026-09-05
- **Status**: Approved (Transitioning from `noaa-station` to `ground-station`)
- **Target Repository**: `radio-astronomy` (`apps/ground-station`)

---

## 1. 概要と背景 (Executive Summary)

本システムは、ベランダの屋外アンテナ（137MHz〜437MHz対応）および **RTL-SDR Blog V4** を用いて、上空を通過する人工衛星（極軌道気象衛星、超小型衛星 CubeSat、国際宇宙ステーション ISS）の電波を24時間自律的に追跡・受信・デコードし、地球写真や宇宙テレメトリを復元・通知する**「自律型パーソナル地上局（Personal Ground Station）」**です。

### 1.1 `noaa-station` から `ground-station` への進化の必然性
プロジェクト初期は米国気象衛星 NOAA のアナログ画像（APT）受信を目的として `noaa-station` と命名されましたが、以下の理由により抜本的な改名とアーキテクチャの汎用化を行います：
1. **NOAA POES (15, 18, 19) の完全退役**: 2025年8月に米国 NOAA POES 全機が退役・停波。
2. **多周波・多種衛星への拡張**:
   - 現役のロシア極軌道気象衛星 **Meteor-M (N2-3, N2-4)**（137.9MHz LRPT デジタルQPSK、1km/pix 高精細カラー地球画像）
   - 世界中の大学・研究機関が運用する **CubeSat（超小型衛星群）**（145MHz / 437MHz 帯の SSDV/SSTV カメラ画像、CWビーコン、テレメトリ）
   - **国際宇宙ステーション (ISS)**（145.800MHz 宇宙飛行士撮影 SSTV 画像・ARISS 音声交信）
3. **時分割＆非同期I/Oパイプラインの実現**:
   1台の SDR チューナーリソースを最大限に活用し、複数衛星のパスが連続しても受信の取りこぼしが発生しない Producer-Consumer パイプラインを構築。

---

## 2. システムアーキテクチャ (System Architecture)

### 2.1 全体コンポーネント構成図

```text
═════════════════════════════════════════════════════════════════════════════════════════
【ground-station 全体アーキテクチャ】
═════════════════════════════════════════════════════════════════════════════════════════

  [ CelesTrak API (CelesTrak.org) ]
        │ (24時間ごとに最新TLEを取得: weather & amateur)
        ▼
  [ 🛰️ SGP4 マルチスケジューラー (src/scheduler.rs & src/orbit.rs) ]
        │
        ├─► [ CLIコマンド: ground-station schedule ] ──► 24時間の統合パス一覧をテーブル表示
        │
        ▼ (常駐モード: AOS 3分前にイベント発火)
  [ 📻 SDR 受信タスク (src/receiver.rs) ]
        │ ・ターゲット衛星の周波数を切り替え (137.9M / 145.9M / 437.5M 等)
        │ ・240kSPS 広帯域生IQ録音 (rtl_sdr -f <freq> -s 240000 -g 45.0)
        │ ・録音完了と同時にSDRデバイスを即座に解放
        ▼
  ( tokio::sync::mpsc::channel<DecodeJob> )
        ▼
  [ ⚙️ 非同期デコードワーカ (src/worker.rs & src/decoder.rs) ]
        │ ※ 次の衛星を受信している最中でも裏で並行処理を実行！
        │
        ├─► [ Meteor-M LRPT ] ──► SatDump CLI ────────► 1km/pix フルカラー地球画像 PNG
        ├─► [ CubeSat SSDV ] ──► gr-satellites / SatDump ► 宇宙カメラ JPEG 画像
        ├─► [ CubeSat BPSK/GFSK ] ► gr-satellites CLI ──► テレメトリ JSON / ログ
        ├─► [ CubeSat CW ] ─────► 内部モールス解析器 ──► コールサイン・電圧テキスト
        └─► [ ISS SSTV ] ───────► SSTV デコーダ ──────► 宇宙飛行士写真 PNG
        │
        ▼ (DecodeResult: 画像パス or テレメトリ要約テキスト)
  [ 📢 通知クライアント (src/discord.rs & src/voicevox.rs) ]
        ├─► 📱 Discord: 復元された写真やテレメトリをWebhookで自動投稿
        └─► 🔊 VOICEVOX: ずんだもん「〇〇衛星のデータを受信したのだ！」
```

---

## 3. コンポーネント詳細設計

### 3.1 軌道予測・スケジューラー (`src/orbit.rs`, `src/scheduler.rs`)
- **TLE 自動更新**:
  - `https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle`
  - `https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle`
  - 24時間周期で自動プルし、メモリ上の衛星定義と照合。
- **SGP4 伝搬モデルと座標系変換**:
  - TLE から SGP4 で地心慣性座標（ECI / TEME）を推算。
  - グリニッジ恒星時（GMST）により地球中心固定直交座標系（ECEF）へ回転。
  - 観測地点（青梅市: 35.7903°N, 139.2584°E, 200m）を原点とするローカル水平座標系（Topocentric ENU: East, North, Up）へ変換し、仰角（Elevation）と方位角（Azimuth）を算出。
- **重複調停アルゴリズム (Conflict Resolution)**:
  - 複数衛星の通過時刻が被った場合、**「最大仰角（Max Elevation）が高い方」**を優先採用し、SDR デバイスの競合を回避。

### 3.2 SDR 受信エンジン (`src/receiver.rs`)
- **広帯域生IQキャプチャ**:
  - コマンド: `rtl_sdr -f <freq_hz> -s 240000 -g <gain> -n <samples> <output.raw>`
  - サンプリングレート: `240,000 S/s`（帯域幅 $\pm 120\text{ kHz}$）
  - **435MHz帯ドップラー偏移（$\pm 11\text{ kHz}$）を完全包含**するため、録音中のハードウェア周波数追尾は不要。
- **録音完了後の即時解放**:
  - 録音が終わると直ちに `rtl_sdr` プロセスを終了し、SDR デバイスファイルを解放。
  - これにより、直後に次の衛星が飛来しても即座に受信可能。

### 3.3 非同期デコードワーカ (`src/worker.rs`, `src/decoder.rs`)
- **Tokio 非同期チャネル（MPSC）**:
  - バッファサイズ 32 の `tokio::sync::mpsc::channel<DecodeJob>` を使用。
  - 受信タスク（I/O）とデコードタスク（CPU）を疎結合化。
- **衛星種別（`SignalType`）に応じたデコードストラテジ**:
  1. `SignalType::MeteorLrpt`: `satdump meteor_m2-x_lrpt raw --input_level u8 ...`
  2. `SignalType::CubeSatSsdv`: `gr_satellites <satellite_name> --rawinput ...` または `satdump`
  3. `SignalType::CubeSatTelemetry`: `gr_satellites` によるパケットパース
  4. `SignalType::MorseCw`: 内部オーディオ復調＋モールス短点/長点パース
  5. `SignalType::IssSstv`: SSTV復調
- **Graceful Degradation（SRE耐障害性）**:
  - 外部デコードツール（`satdump` や `gr_satellites`）がシステムに未インストールの場合でもプロセスはパニックせず、生IQファイルを `data/ground-station/raw/` に安全に保持し、「生データ保存完了・デコーダ未検出」として Discord/ログに通知。

### 3.4 通知サブシステム (`src/discord.rs`, `src/voicevox.rs`)
- **Discord Webhook**:
  - 画像が存在する場合は `multipart/form-data` で PNG/JPEG を直接添付投稿。
  - テレメトリ（電圧・温度・パケット情報）やパス諸元（最大仰角、方角、受信時刻）をリッチな Embed 形式で送信。
- **VOICEVOX 連携**:
  - エッジノード上の VOICEVOX Engine REST API（`:50021`）を非同期 HTTP POST で呼び出し。
  - 「〇〇衛星の通過が完了したのだ！綺麗な画像が復元できたのだ！」等の状況に応じた音声合成・再生。

### 3.5 起動時事前ヘルスチェック (Preflight Health Check: `src/health.rs`)
常駐デーモンの起動時および専用 CLI コマンド（`check`）において、SRE的ベストプラクティスである**事前ヘルスチェック（Preflight Checks / Fail-fast）**を実行し、運用トラブルを即座に可視化します：

1. **SDR デバイス疎通チェック**:
   - `rtl_test -t` 相当のプローブを試行し、RTL-SDR v4 が USB 上でオープン可能か、他のプロセスにロックされていないかを検査。
   - **失敗時のトラブルシュート提示**: 未検出時は「`usbipd attach --wsl --auto-attach --busid <BUSID>` を実行してください」など具体的なコマンドを表示。
2. **外部デコードツール導入状況チェック**:
   - `satdump`, `gr_satellites`, `rtl_sdr` のバイナリが `$PATH` 上に存在するか検査。
   - 未インストール時は警告（WARN）とし、利用不可となる衛星種別とインストール手順を明示。
3. **通知サービス疎通チェック**:
   - VOICEVOX（`http://localhost:50021`）への HTTP GET（`/version`）疎通確認。
   - Discord Webhook URL の環境変数設定状況の確認。
4. **ストレージ書き込み＆空き容量チェック**:
   - `data/ground-station/` の書き込み権限とディスク残容量（10GB以上推奨）の確認。

---

## 4. 設定ファイルスキーマ (`config.toml`)

```toml
[observer]
latitude = 35.7903    # 東京都青梅市
longitude = 139.2584  # 東経 139.2584
altitude_m = 200.0    # 標高 (m)

[scheduler]
min_elevation_deg = 15.0      # 最低仰角 (度)
pre_alert_minutes = 3.0       # 通過前アラート時刻 (分)
tle_update_interval_hours = 24

[storage]
output_dir = "data/ground-station"

[sdr]
gain = 45.0          # チューナー利得 (dB)
sample_rate = 240000  # 240kSPS
ppm_error = 0

# -----------------------------------------------------------------------------
# 🛰️ 追尾対象衛星の個別設定
# -----------------------------------------------------------------------------
[satellites.meteor]
enabled = true       # ロシア現役気象衛星 Meteor-M (N2-3, N2-4 / 137.9MHz LRPT)

[satellites.cubesats]
enabled = true       # 超小型衛星群
targets = [
    { name = "FUNcube-1", norad_id = 39444, freq = 145935000, type = "BpskTelemetry" },
    { name = "UmKA-1",    norad_id = 57172, freq = 437625000, type = "CameraSstv" },
    { name = "SONATE-2",  norad_id = 59112, freq = 437025000, type = "CameraSsdv" },
    { name = "XI-IV",     norad_id = 27848, freq = 436848000, type = "MorseCw" },
    { name = "CUTE-1",    norad_id = 27844, freq = 436837500, type = "MorseCw" },
]

[satellites.iss]
enabled = true       # 国際宇宙ステーション (145.800MHz SSTV / 音声)
norad_id = 25544
freq = 145800000

# -----------------------------------------------------------------------------
# 📢 外部通知
# -----------------------------------------------------------------------------
[discord]
enabled = true       # Webhook URL は環境変数 DISCORD_WEBHOOK_URL または .local.env から読込

[voicevox]
enabled = true
host = "http://localhost:50021"
speaker_id = 3
timeout_secs = 5
```

---

## 5. プロジェクト移行（リネーム）計画

1. **Git ディレクトリ移動**:
   `git mv apps/noaa-station apps/ground-station`
2. **ビルド定義の更新**:
   - ルート `Cargo.toml`: `members = ["apps/ground-station"]`
   - `apps/ground-station/Cargo.toml`: `name = "ground-station"`
   - `docker-compose.yaml` やドキュメント内のパス参照を同期。
3. **CLI コマンド体系**:
   - `ground-station check`: 起動前事前ヘルスチェック（SDRデバイス・デコーダ・通知・ストレージ疎通）の実行
   - `ground-station schedule`: 統合パス予測テーブルの表示
   - `ground-station daemon`: 自律受信常駐監視デーモンの起動（起動時に自動Preflightを実行）
   - `ground-station test-voice`: ずんだもん音声疎通テスト
   - `ground-station test-discord`: Discord Webhook 疎通テスト

---

## 6. テスト・検証戦略

1. **単体テスト (Unit Tests)**:
   - `config_test.rs`: 新スキーマ（`[satellites.cubesats]`, `[satellites.iss]`）の正常系・異常系パース検証。
   - `orbit_test.rs`: 複数衛星（VHF/UHF混在）のSGP4パス予測、ENU座標計算、重複調停の動作検証。
   - `worker_test.rs`: MPSCチャネルを介したジョブキューイングとバックグラウンドワーカの非同期連携テスト。
   - `health_test.rs`: ヘルスチェック項目の合格/不合格/警告判定ロジックの検証。
2. **結合・CLIテスト (Integration & CLI Tests)**:
   - `cargo run --bin ground-station -- check` による事前ヘルスチェック結果の表示確認。
   - `cargo run --bin ground-station -- schedule` によるリアルタイムスケジュール出力の整合性確認。
   - `cargo test --all` による全ユニットテストのパス確認。
