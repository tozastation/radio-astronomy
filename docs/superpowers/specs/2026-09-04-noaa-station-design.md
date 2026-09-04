# NOAA気象衛星 自律自動受信・デコードパイプライン (`apps/noaa-station`) 設計仕様書

- **作成日**: 2026-09-04
- **ステータス**: 設計承認済み (Draft Spec)
- **対象プラットフォーム**: GPD Pocket3 (WSL2 / Linux) & ゲーミングPC (WSL2 / Linux)
- **言語・主要スタック**: Rust 2021, `tokio`, `sgp4`, `reqwest`, `serde`, `rtl_fm`, `noaa-apt`

---

## 1. 概要と目的

### 1.1 背景
[docs/05_getting_started.md](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/docs/05_getting_started.md) により、RTL-SDR Blog V4 の公式ドライバ導入、アンテナ健全性テスト、およびLAN越し音声ストリーミングの最小開通（Tracer Bullet）が完了した。  
次のフェーズとして、ベランダに設置されたアンテナと常時稼働エッジノード（GPD Pocket3）を活用し、**地球上空を周回するNOAA気象衛星（NOAA-15, 18, 19）のAPT（Automatic Picture Transmission）電波（137MHz帯）を完全自律で自動受信・画像デコードする地上局デーモン** を構築する。

### 1.2 ゴール
1. **完全自律常駐**: 人手を介さず、バックグラウンドデーモンが衛星の軌道（TLE）から通過予定（AOS/LOS/最大仰角）を自動計算し、最適なタイミングでSDR受信を実行する。
2. **ずんだもん事前・事後音声通知**: 通過前（カウントダウン）とデコード完了時に、VOICEVOX連携（またはフォールバック）でずんだもんが音声通知する。
3. **単一バイナリ・超軽量稼働**: SRE目線での高信頼性を追求し、Rustによる超省メモリ・低CPU負荷・panicフリーなエッジデーモンを実現する。
4. **モノレポ構成（`apps/`）の確立**: 本リポジトリで今後追加される各種ツール群（21cm線エッジDSP、流星検知など）を独立して管理・運用するための共通ディレクトリ構造を確立する。

---

## 2. システムアーキテクチャ & ディレクトリ構成

### 2.1 リポジトリ全体アーキテクチャ (`apps/` 構成)
```text
radio-astronomy/
├── apps/
│   ├── noaa-station/           # [Rust] 本システム (NOAA自動地上局デーモン)
│   │   ├── Cargo.toml
│   │   ├── config.toml
│   │   └── src/
│   │       ├── main.rs         # CLIエントリポイント (サブコマンド制御)
│   │       ├── config.rs       # 設定読み込み (serde / toml)
│   │       ├── orbit.rs        # CelesTrak TLE取得 & sgp4軌道・仰角計算
│   │       ├── scheduler.rs    # tokio非同期タイマーループ & ステートマシン
│   │       ├── receiver.rs     # rtl_fm プロセス制御 (WAV録音・優雅な終了)
│   │       ├── decoder.rs      # noaa-apt CLI 呼び出し & PNG画像生成
│   │       └── voicevox.rs     # VOICEVOX HTTP API クライアント & 音声再生
│   ├── sdr-collector/          # [Rust] (今後) 2.4MSPS 高速IQ・FFT積算エッジデーモン
│   └── meteor-detector/        # [Python/Rust] (今後) 流星エコー自動検知
├── notebooks/                  # 天文・DSP分析ノートブック
├── docs/                       # ドキュメント・仕様書
├── k8s/                        # KubeEdge / Kubernetes マニフェスト (cloud / edge)
└── data/                       # 観測データ・生成画像出力 (.gitignore 対象)
    └── noaa/
```

### 2.2 データフロー & 状態遷移

```text
       ┌──────────┐
       │   Idle   │ ◄─────────────────────────────────────┐
       └────┬─────┘                                       │
            │ 次の事前通知 (AOS - 3分) まで sleep 待機      │
            ▼                                             │
       ┌──────────┐                                       │
       │Approaching│ ずんだもん「まもなく通過するのだ！」    │
       └────┬─────┘                                       │
            │ AOS まで sleep 待機                          │
            ▼                                             │
       ┌──────────┐                                       │
       │Receiving │ rtl_fm で 137.x MHz WAV 録音開始      │
       └────┬─────┘                                       │
            │ LOS 到達 (プロセス優雅停止)                  │
            ▼                                             │
       ┌──────────┐                                       │
       │ Decoding │ noaa-apt で PNG 画像生成              │
       └────┬─────┘                                       │
            │ デコード成否判定                             │
            ▼                                             │
       ┌──────────┐                                       │
       │Notifying │ ずんだもん「デコードに成功したのだ！」 │
       └────┬─────┘                                       │
            └─────────────────────────────────────────────┘
```

---

## 3. 各コンポーネント詳細仕様

### 3.1 `config` (設定管理)
`config.toml` から設定をパースする。環境変数によるオーバーライドも可能とする。

```toml
[observer]
# 観測地点（WGS84座標）
latitude = 35.6895
longitude = 139.6917
altitude_m = 40.0

[scheduler]
# 受信対象とする最小ピーク仰角 (度)
min_elevation_deg = 20.0
# 通過何分前に事前通知するか (分)
pre_alert_minutes = 3.0
# TLE の更新間隔 (時間)
tle_update_interval_hours = 24

[voicevox]
enabled = true
# VOICEVOX Engine のエンドポイント
host = "http://localhost:50021"
# スピーカーID (3: ずんだもん ノーマル)
speaker_id = 3

[storage]
# 出力先ディレクトリ
output_dir = "data/noaa"
```

### 3.2 `orbit` (軌道要素フェッチ & 通過予測)
- **一次情報データソース**:
  - CelesTrak 気象衛星 TLE: `https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle`
- **対象衛星**:
  - `NOAA 15` (NORAD ID: 25338, 周波数: 137.620 MHz)
  - `NOAA 18` (NORAD ID: 28654, 周波数: 137.9125 MHz)
  - `NOAA 19` (NORAD ID: 33591, 周波数: 137.100 MHz)
- **計算ロジック**:
  - `sgp4` クレートを用いて衛星の ECI（地心慣性座標系）位置・速度ベクトルを計算。
  - 観測地点の緯度・経度・標高（WGS84）からローカル水平座標系（Topocentric: 方位角 Azimuth, 仰角 Elevation）へ変換。
  - 現在時刻から今後24時間（86400秒）を一定間隔（例: 30秒刻み、境界探索時は1秒刻み）でスキャンし、仰角が `min_elevation_deg` を超えるパスを検出。
  - **パスの重複解決**: もし複数衛星の通過区間が重なる場合は、`max_elevation`（最大仰角）が大きい方のパスを優先選択する。

### 3.3 `receiver` (SDR 受信・録音制御)
- `tokio::process::Command` により `rtl_fm` を子プロセスとして起動。
- コマンドライン仕様:
  ```bash
  rtl_fm -M wfm -f {freq_hz} -s 60k -r 11025 -E wav -F 9 {output_wav_path}
  ```
  *(NOAA-APT は 11025 Hz サンプリングの WAV を標準入力とする)*
- **プロセスライフサイクル**:
  - AOS 到達時に起動。
  - LOS 到達時、または Graceful Shutdown 要求時に `nix::sys::signal::kill(pid, Signal::SIGINT)` を送信して安全に停止し、WAVヘッダが破損しないようフラッシュを待つ。

### 3.4 `decoder` (画像デコード)
- [noaa-apt 公式CLI](https://noaa-apt.mbernardi.com.ar/) を呼び出す。
- コマンドライン仕様:
  ```bash
  noaa-apt {input_wav_path} -o {output_png_path}
  ```
- **出力アーティファクト**:
  - `data/noaa/YYYY-MM-DD_HHMMSS_NOAA19/raw.wav`
  - `data/noaa/YYYY-MM-DD_HHMMSS_NOAA19/image.png`
  - `data/noaa/YYYY-MM-DD_HHMMSS_NOAA19/meta.json` (衛星名、AOS/LOS時刻、最大仰角、周波数を記録)

### 3.5 `voicevox` (ずんだもん音声通知 & フォールバック)
- **VOICEVOX Engine API 連携**:
  1. `POST {host}/audio_query?text={encoded_text}&speaker={speaker_id}` -> クエリ JSON 取得
  2. `POST {host}/synthesis?speaker={speaker_id}` -> WAV 音声バイナリ取得
  3. システム標準プレイヤー（`aplay` または `ffplay -nodisp -autoexit`）で再生。
- **発話スクリプト**:
  - 事前通知: `まもなく {衛星名} が通過するのだ！最大仰角は {max_el}度、受信を試みるのだ！`
  - 成功通知: `{衛星名} の受信とデコードに成功したのだ！新しい画像を確認するのだ！`
  - 失敗通知: `画像のデコードに失敗したのだ…電波が弱かったかもしれないのだ`
- **フォールバック設計**:
  - `host` に接続できない場合（ECONNREFUSED / タイムアウト等）、エラーで異常終了せず、WARNING ログを出力して処理を正常継続する。

---

## 4. CLI インターフェース仕様

ユーザーが単体で動作確認できるよう、使いやすいサブコマンドを提供する。

```bash
# 1. 通過予定一覧のテーブル表示
cargo run -- schedule

# 2. ずんだもん音声発話の疎通テスト
cargo run -- test-voice

# 3. 指定した周波数・時間で手動ワンショット録音 & デコードテスト
cargo run -- capture --sat "NOAA 19" --duration 60

# 4. 本番常駐自動監視デーモン起動
cargo run -- daemon
```

---

## 5. テスト・検証戦略

1. **自動単体テスト (`cargo test`)**:
   - `config_test`: TOML パースおよびバリデーションの検証。
   - `sgp4_orbit_test`: サンプル TLE データから既知の通過時刻・仰角が正しく計算されるか検証。
   - `pass_filter_test`: 仰角閾値フィルタリングおよび重複パス調停の検証。
2. **手動・実機検証**:
   - `test-voice` でずんだもんが喋るか確認。
   - `schedule` で直近のパス一覧が表示されるか確認。
   - `daemon` を起動し、事前通知から録音・デコードまでの一連のステートマシンが駆動することを確認。
