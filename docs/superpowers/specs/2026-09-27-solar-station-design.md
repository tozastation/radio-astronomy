# ☀️ Architecture Design: Solar Radio Observatory (`solar-station`)

- **Author**: tozastation & Antigravity
- **Date**: 2026-09-27
- **Status**: Approved
- **Target Repository**: `radio-astronomy` (`apps/ground-station/src/bin/solar_station.rs`)

---

## 1. 概要と背景 (Executive Summary)

本システムは、**RTL-SDR Blog V4** および付属の**マグネットベース伸縮ホイップアンテナ**（金属板グラウンド吸着）を活用し、太陽フレア発生に伴うコロナプラズマ電波バースト（Type II / Type III バースト）をリアルタイムに自動検知・データ蓄積する**「自律型パーソナル太陽電波観測ステーション（Personal Solar Observatory）」**です。

### 1.1 企画の動機と科学的・工学的背景
1. **太陽フレア電波バーストの桁外れのエネルギー**:
   - 太陽表面の磁気リコネクション（フレア爆発）によって加速された高エネルギー電子線がコロナ中を突き抜ける際、局所プラズマ周波数 $f_p \propto \sqrt{n_e}$ で強力な電磁波（Type III バースト等）を放射します。
   - 地球での受信強度は **$10^5 \sim 10^9 \text{ Jy}$（数十万〜数十億ジャンスキー）** に達し、普段の背景ノイズを **10〜20 dB（10倍〜100倍）吹き飛ばす強烈なシグナル** となります。
2. **付属アンテナの共振特性（70MHz帯）の完全適合**:
   - 付属の伸縮アンテナ（最大伸長 約 1.0 m）は、金属板に吸着させてグラウンドプレーンを形成することで、**75 MHz 付近の 1/4 波長モノポールアンテナとして理想的に共振** します。
   - 70.0 MHz 付近（FMラジオ放送 76MHz 以下の VHF ローバンド）は地上波の混信が少なく、太陽プラズマ波の放射強度が最も強い帯域です。
3. **エッジ自律観測（タイムドメイン天文学）の強み**:
   - 既存の衛星追尾やADS-B等の観測を一時停止し、SDRチューナーとアンテナリソースを100%太陽電波に集中させます。
   - ファンレス・低消費電力な GPD Pocket3 上で 24時間365日自律稼働させ、世界中の宇宙天気速報（GOES衛星・NICT）に先駆けて、**自宅地上局がリアルタイムにフレア発生を捉えて Discord に画像付き速報を配信** します。

---

## 2. 物理・DSPパイプライン設計 (Physical & DSP Pipeline Design)

```mermaid
flowchart LR
    SDR["📻 RTL-SDR Blog V4<br/>70.0 MHz / 2.4 MSPS IQ<br/>(4.8 MB/s 生ストリーム)"]
    --> Window["🪟 ハミング窓掛け<br/>N_fft = 1024 ビン<br/>(Δf ≈ 2.34 kHz)"]
    --> FFT["⚡ rustfft 高速フーリエ変換<br/>毎秒約 2,343 回 FFT"]
    --> Accum["⏱️ 1秒積算・平均化<br/>P[k] = 10 log10(|X[k]|²)<br/>(8 KB/s に圧縮)"]
    --> Detect{"🚨 動的しきい値判定<br/>直近5分間の中央値 + 4σ<br/>急上昇 dP/dt > +3dB/s"}

    Detect -->|Yes (フレア検知)| RingBuf["🖼️ リングバッファから<br/>前後60秒のスペクトル切出<br/>ウォーターフォール PNG 生成"]
    Detect -->|常時| Storage["💾 1秒統計・スペクトルを<br/>JSON Lines / Parquet 永続化"]
```

### 2.1 受信RFパラメータ仕様
- **中心周波数 ($f_c$)**: `70.0 MHz`
- **サンプリングレート ($f_s$)**: `2.4 MSPS` (IQ 2.4 MHz 帯域幅)
- **観測周波数スパン**: **68.80 MHz 〜 71.20 MHz**
- **RFゲイン**: 固定ゲイン `28.0 dB`（微小な物理的ノイズフロア変動を正確に追跡するため AGC は無効化）

### 2.2 デジタル信号処理（DSP）の数理
1. **周波数分解能**:
   $$N_{\text{fft}} = 1024, \quad \Delta f = \frac{f_s}{N_{\text{fft}}} = \frac{2.4 \times 10^6 \text{ Hz}}{1024} \approx \mathbf{2.34 \text{ kHz}}$$
2. **ハミング窓掛け（Hamming Window）**:
   $$w[n] = 0.54 - 0.46 \cos\left( \frac{2\pi n}{N_{\text{fft}} - 1} \right)$$
   近隣の放送波・業務無線のサイドローブ漏れ（Spectral Leakage）を -43 dB 以下に抑圧。
3. **1秒積算（Integration）**:
   毎秒 $N_{\text{blocks}} = \frac{2,400,000}{1024} \approx 2343$ 回のパワースペクトル $|X_m[k]|^2$ を加算平均：
   $$\bar{P}[k] = \frac{1}{N_{\text{blocks}}} \sum_{m=1}^{N_{\text{blocks}}} |X_m[k]|^2$$
   これにより、ガウス白色雑音の分散が $\frac{1}{\sqrt{2343}} \approx \frac{1}{48.4}$（約 17 dB 改善）に圧縮され、微弱なフレアの立ち上がりが明瞭になります。
4. **広帯域総合電力（Total Power）**:
   $$P_{\text{total}} = 10 \log_{10} \left( \sum_{k=0}^{N_{\text{fft}}-1} \bar{P}[k] \right)$$

### 2.3 ロバスト統計による太陽フレア動的検知アルゴリズム
固定しきい値では気温変化や銀河面ノイズの日周変動で誤検知が発生するため、**外れ値に強い中央値絶対偏差（MAD: Median Absolute Deviation）** を採用します。

1. **ベースライン統計量（直近 300秒 = 5分間リングバッファ）**:
   - 中央値: $M = \text{median}(\{P_{\text{total}}[t]\})$
   - ロバスト標準偏差推定値:
     $$\sigma_{\text{robust}} = 1.4826 \times \text{median}(|P_{\text{total}}[t] - M|)$$
2. **フレアトリガー発火条件**:
   以下の **2条件が同時に成立** した場合にフレア検知と判定：
   - **絶対上昇幅**: $P_{\text{total}} > M + (k \times \sigma_{\text{robust}})$ （初期値: $k = 4.0$）
   - **急上昇率（インパルシブフェーズ微分）**: $\frac{dP_{\text{total}}}{dt} \ge +3.0 \text{ dB/s}$
3. **誤検知防止**:
   - 一過性のインパルスノイズ（車の点火プラグ等）による誤判定を防ぐため、**2秒以上継続してしきい値を超過** した場合に確定。
   - 1つのフレアで連投されないよう、検知後は **180秒間（3分間）のクールダウン** を適用。

---

## 3. システムアーキテクチャと並行モデル (System Architecture & Concurrency)

```mermaid
flowchart TB
    subgraph SDR_Process["SDR受信プロセス (OSレベル)"]
        RTLSDR["📻 rtl_sdr (CLI)<br/>-f 70.0M -s 2.4M -g 28.0 -"]
    end

    subgraph Rust_Process["Rust 観測デーモン (apps/ground-station/src/bin/solar_station.rs)"]
        direction TB
        Reader["🧵 Thread 1: Stdout Reader<br/>生IQチャンク読出 (240kSPS / 0.1秒単位)"]
        DSP["🧵 Thread 2: DSP & Detector<br/>rustfft (1024点) / 1秒積算<br/>動的しきい値判定 (MAD)"]
        RingBuf[("🧠 In-Memory Ring Buffer<br/>直近 300秒のスペクトル保持")]
        Notifier["⚡ Tokio Async Task: Event Worker<br/>ウォーターフォール PNG レンダリング<br/>Discord Webhook 非同期送信"]
        Storage["💾 Tokio Async Task: Storage Worker<br/>追記型 JSON Lines 永続化<br/>日没時全日画像生成"]

        Reader -->|crossbeam channel| DSP
        DSP <-->|更新 & 読出| RingBuf
        DSP -->|フレア検知イベント| Notifier
        DSP -->|1秒統計データ| Storage
    end

    RTLSDR -->|Pipe (Stdout)| Reader
    Notifier -->|HTTPS POST| Discord["💬 Discord チャンネル (#solar-watch)"]
    Storage -->|ローカルSSD| LocalDisk[("📁 data/solar/<br/>├── metrics_2026-09-28.jsonl<br/>├── events/<br/>│   └── flare_20260928_114215.png<br/>└── daily/<br/>    └── full_day_20260928.png")]
```

### 3.1 バイナリ構成とコード配置
既存の `apps/ground-station` クレート内に独立したバイナリとして配置します：
- **ソースファイル**: `apps/ground-station/src/bin/solar_station.rs`
- **モジュール追加**: `apps/ground-station/src/solar/`
  - `mod.rs`: 太陽観測マネージャー
  - `dsp.rs`: リアルタイムFFT・積算・MAD動的しきい値判定
  - `waterfall.rs`: イベント用ウォーターフォール PNG 生成（`image` クレート）
  - `sun_pos.rs`: 太陽高度・方位角計算（地平座標 AltAz）
- **共有再利用モジュール**:
  - `crate::config`: TOML 設定管理
  - `crate::discord`: Webhook 送信クライアント

### 3.2 3層分離スレッドモデル
毎秒 2.4MSPS の高レートIQストリームを遅延なく処理し、OS のバッファ溢れ（Dropped Samples）を防止：
1. **Stdout Reader スレッド（入力層）**:
   - `std::process::Command` により起動した `rtl_sdr` の標準出力パイプから、240,000 バイト（約0.1秒分）ずつ生IQデータを読み出し、`crossbeam-channel` 経由で DSP スレッドへ転送。
2. **DSP & Detector スレッド（計算層 - CPUバウンド）**:
   - 1024ビン FFT を実行し、パワースペクトルを加算。
   - 1秒（10チャンク）完了ごとに、平均スペクトルと総合電力を算出。
   - インメモリリングバッファ（最新300秒分）を更新し、動的しきい値判定を実行。
3. **Async Event & Storage ワーカー（I/O層 - Tokio）**:
   - イベント検知時の PNG 生成や Discord 送信、ディスク書き込み等の重い I/O 処理を非同期タスクへ委譲。DSP スレッドのリアルタイムループを決してブロックさせない。

---

## 4. データストレージ・永続化設計 (Storage & Persistence)

生IQデータ（4.8 MB/s = **約 415 GB/日**）はディスクに書き込まず、メモリ内で消費・破棄します。  
永続化するのは **1秒集約スペクトル** と **イベント画像** のみに限定し、GPD Pocket3 のローカルストレージを保護します：

| データ種別 | 保存形式 | 記録タイミング | 1日あたりの容量 | 用途 |
| :--- | :--- | :--- | :--- | :--- |
| **生IQデータ** | メモリ上のみ | 常時（ディスク非保存） | 0 MB | リアルタイムFFT専用（破棄） |
| **1秒集約メトリクス** | 追記型 JSON Lines | 毎秒（1レコード約100B） | **約 8 MB / 日** | DuckDB での時系列ライトカーブ分析 |
| **フレアイベント画像** | PNG (800x400) | バースト検知時のみ | **数百 KB / 回** | Discord 通知およびイベントアーカイブ |
| **日別全体スペクトログラム** | PNG (1920x1080) | 日没（観測終了）時に1枚 | **約 2 MB / 日** | 1日の太陽活動の俯瞰・比較 |

- **合計ストレージフットプリント**: **約 10 MB / 日（1年間常時観測してもわずか 3.6 GB）**

### 4.1 1秒集約レコードスキーマ (`data/solar/metrics_YYYY-MM-DD.jsonl`)
```json
{
  "timestamp": 1790510535,
  "datetime": "2026-09-28T11:42:15.120+09:00",
  "total_power_db": -24.35,
  "baseline_median_db": -38.52,
  "snr_db": 14.17,
  "peak_freq_hz": 70125000,
  "is_burst": true,
  "sun_az_deg": 152.4,
  "sun_el_deg": 52.1
}
```

---

## 5. 観測スケジューリングとDiscord通知設計 (Scheduling & Discord Alerts)

### 5.1 太陽高度計算と自動スケジューリング
- 東京（緯度 35.68°, 経度 139.76°）における太陽の地平座標（方位角 $Az$, 高度 $El$）をリアルタイム計算。
- **日照連動モード (`mode = "daylight_only"`)**:
  - 太陽高度 $El \ge 5.0^\circ$ の時間帯に自動で `rtl_sdr` プロセスを起動・観測開始。
  - $El < 5.0^\circ$（日没）になると観測を停止し、日別サマリー画像を生成して待機。
- **24時間連続モード (`mode = "continuous_24h"`)**:
  - 夜間も継続して観測（銀河面ノイズの日周変動や夜間背景雑音のロギング）。

### 5.2 Discord 緊急速報フォーマット
太陽フレアバーストを検知した瞬間、1〜2秒以内に以下のリッチ Embed が送信されます：

```text
🚨 【太陽電波バースト検知】 70.0 MHz帯
────────────────────────────────────────
太陽表面において急激なプラズマ電波放射（フレア）を検出しました。

• 検知時刻:     2026-09-28 11:42:15 JST
• 電波強度上昇: +14.8 dB （平時比 約30倍の電力跳ね上がり）
• 推定規模:     強 (Strong Flare Event)
• 継続時間:     約 45 秒間
• 観測周波数:   70.0 MHz （帯域幅 2.4 MHz）
• 太陽位置:     方位角 152.4° (南南東) / 仰角 52.1°
• 宇宙天気連動: GOES 衛星 X線フラックス / NICT 太陽活動を要確認

[ 添付画像: 前後60秒間のウォーターフォール画像 (flare_20260928_114215.png) ]
（横軸: 経過時間(秒)、縦軸: 68.8MHz〜71.2MHz、色: 信号強度dB）
```

---

## 6. 設定ファイル仕様 (`config.toml` の `[solar]` セクション)

```toml
[solar]
enabled = true
center_freq = 70.0e6      # 付属アンテナ共振周波数 (70MHz)
sample_rate = 2.4e6      # 2.4 MSPS
gain = 28.0              # 固定ゲイン (dB)
fft_size = 1024          # FFTビン数
integration_secs = 1     # 積算時間 (秒)

# バースト検知パラメータ
threshold_sigma = 4.0    # 背景雑音中央値に対するしきい値倍率 (4.0σ)
min_jump_db = 3.0        # 1秒あたりの最小跳ね上がり率 (dB/s)
cooldown_secs = 180      # イベント後の通知クールダウン (秒)

# スケジューリング
mode = "daylight_only"   # "daylight_only" または "continuous_24h"
min_elevation = 5.0      # 観測を開始する太陽高度 (度)

# ディレクトリパス
data_dir = "data/solar"
```

---

## 7. テスト・検証計画 (Testing & Verification Strategy)

1. **単体テスト (`tests/unit/solar_test.rs`)**:
   - **ロバスト統計テスト**: ガウス雑音＋外れ値データに対する中央値および MAD 計算の正確性を検証。
   - **動的しきい値テスト**: 急激なステップ入力（フレア模擬）および緩やかなドリフト入力（気温変化模擬）を与過し、急上昇時のみ正しくトリガーが発火することを検証。
   - **太陽位置計算テスト**: 既知の日時における太陽高度・方位角の算出精度を天文学的参照値（誤差 $< 0.1^\circ$）と照合。
2. **統合テスト・ドライラン**:
   - 合成IQ信号（ガウスノイズに 70MHz バーストを注入したテストデータ）を `solar-station` にパイプで流し込み、ウォーターフォール PNG の生成と Discord Webhook 送信モックが正常に完了することを確認。
3. **実機検証（GPD Pocket3 + ベランダ）**:
   - `cargo run --bin solar-station` を実行し、CPU負荷（$< 5\%$）、メモリ使用量（$< 50\text{ MB}$）、および 1秒集約レコードの正常出力を確認。
