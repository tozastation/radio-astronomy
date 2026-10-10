---
title: 【ベランダ電波観測所 #3】KubeEdgeを活かした衛星電波解析パイプラインの構築
tags:
  - Kubernetes
  - KubeEdge
  - Rust
  - SDR
  - Python
  - SRE
private: false
updated_at: '2026-10-10T14:00:00+09:00'
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

# 【ベランダ電波観測所 #3】KubeEdgeを活かした衛星電波解析パイプラインの構築

## はじめに

こんにちは、戸澤（@tozastation）です！  
宇宙や無線が大好きで、自宅のベランダから個人で電波天文学や人工衛星の観測を行う **「ベランダ電波観測所」** プロジェクトを進めています。

本プロジェクトは、AIアシスタントの Antigravity に無線工学、DSP（デジタル信号処理）、分散システムや SRE の設計思想を相談しながら二人三脚で開発しています。いつも大変お世話になっております🙇‍♂️

ソースコード、Kubernetes / Helmfile マニフェスト、詳細な数式・工学ドキュメントはすべて GitHub にオープンソースとして公開しています：  
👉 [tozastation/radio-astronomy (GitHub)](https://github.com/tozastation/radio-astronomy)

---

## 前回の振り返りと今回の課題

これまでの歩み：
- **第1弾**: [【ベランダ電波観測所 #1】RTL-SDR Blog V4 と WSL2 で電波を受信してみる 〜公式ドライバビルドの罠からSSHストリーミングまで〜](https://qiita.com/tozastation/items/b7e411ba034ddf46be22)
- **第2弾**: [【ベランダ電波観測所 #2】KubeEdge×SDRで始める衛星電波観測と単一ノード運用の記録](https://github.com/tozastation/radio-astronomy/blob/main/docs/articles/qiita_kubeedge_satellite_tracker.md)

第2弾では、手のひらサイズの UMPC（GPD Pocket3: Ubuntu 26.04 LTS）上で **k3s（CloudCore）と KubeEdge（EdgeCore）を 1台に同居** させ、頭上を通過する人工衛星（CubeSat / ISS 等）の電波を SGP4 軌道予測で自動追尾し、Prometheus と Grafana で可視化するエッジ観測の足場を固めました。

しかし、実際にベランダで連続稼働させていく中で、**SRE 的に無視できない「リソースの壁」** に直面しました。

### 直面した 3 つの課題
1. **Python 製エッジ観測コンテナのフットプリント**:
   - SGP4 軌道計算、RTL-SDR からの 2.4MSPS 受信、FFT ドップラー追尾、Prometheus メトリクス配信を Python で行うと、メモリを約 100MiB 消費していました。1台で運用する UMPC の限られたリソースでは、GC（ガベージコレクション）によるレイテンシスパイクやバッファ詰まりのリスクが常に付きまといます。
2. **生音声ファイル（WAV）によるディスク逼迫**:
   - 衛星通過（パス）ごとに帯域をまるごと生録音すると、数分間で数百 MB のファイルが溜まります。ディスク容量の限られたエッジ端末では、放っておくと数日でストレージが枯渇してしまいます。
3. **エッジで「観測」と「重い信号解析」を同居させる限界**:
   - 受信した音声から APRS パケットをデコード（direwolf）したり、Matplotlib / SciPy で高解像度スペクトログラムを生成する処理は、CPU もメモリ（150〜200MiB）も瞬間的に激しく消費します。
   - これをエッジで常駐させると、肝心の衛星電波受信処理の邪魔をしてしまい、最悪の場合ドロップやプロセス停止を招きます。

> **「エッジは観測だけに専念させ、重い解析処理は疎結合にクラスタ側に逃がせないか？」**  
> **「しかも衛星が飛んできた時だけ起動して、終わったらメモリ消費ゼロに戻せないか？」**

そこで今回、KubeEdge の特性を最大限に活かした **「超省リソース・イベント駆動型衛星電波解析パイプライン」** へ全面刷新しました！

---

## システム構成の進化（#2 からの差分）

第2弾（#2）では「エッジで動かす単一観測コンテナからメトリクスを吸い上げて監視する」という基盤の立ち上げを行いました。  
今回はそこに **「イベント駆動型の自律解析パイプライン」** を追加し、エッジ観測 Pod も **Rust 化によって極限まで軽量化** しました。

まずは、#2（Before）と #3（After）で何がどう進化したのかを比較します。

### 1. #2（Before）と #3（After）のアーキテクチャ比較

#### 【Before: #2 の状態】単一観測コンテナ ＆ メトリクス監視のみ
#2 の時点では、エッジで Python 製の観測コンテナが常駐し、生 WAV 音声はローカルディスクに溜め込むだけでした。解析処理は存在せず、クラウド側は Prometheus / Grafana によるメトリクス監視のみでした。

```mermaid
flowchart TB
    subgraph Host["GPD Pocket3 (Ubuntu 26.04 LTS / 192.168.68.66)"]
        direction TB

        subgraph EdgeSide["🛰️ エッジ観測ノード (gpd-pocket3-edge / KubeEdge)"]
            direction TB
            RTLSDR["📻 RTL-SDR Blog V4<br/>(USB 直結)"]
            TrackerOld["⚠️ satellite-tracker (Python)<br/>・メモリ約 100MiB 消費<br/>・生 WAV をローカルディスクに蓄積 (容量逼迫)"]
            RTLSDR --> TrackerOld
        end

        subgraph CloudSide["⚙️ コントロールプレーン (tozastation-g1621-02 / k3s)"]
            direction TB
            K3S["☸️ k3s / KubeEdge CloudCore"]
            Prometheus["📈 Prometheus & Grafana (:30080)<br/>(メトリクス監視のみ)"]
            TrackerOld -.->|"メトリクス収集 (:9100)"| Prometheus
        end
    end
```

#### 【After: #3 の状態】エッジ極小化 ＆ イベント駆動型 0-scale 解析パイプライン
#3 では、エッジを Rust で徹底的に絞り込み、クラスタ側に極小 S3 ストレージ（Garage）、ワークフロー（Temporal）、0-scale オートスケーラー（KEDA）を配備して、**観測から解析・可視化・クリーンアップまでが完全自律で完走するパイプライン** を構築しました。

```mermaid
flowchart TB
    subgraph Host["GPD Pocket3 (Ubuntu 26.04 LTS / 192.168.68.66)"]
        direction TB

        subgraph EdgeSide["🛰️ エッジ観測ノード (gpd-pocket3-edge / KubeEdge)"]
            direction TB
            RTLSDR["📻 RTL-SDR Blog V4<br/>(USB 直結)"]
            TrackerNew["⚡ satellite-tracker-rs [RENEWED: Rust]<br/>・メモリ 3MiB / CPU 0.8m (97%削減)<br/>・ゼロコピー 48kHz WAV スプール<br/>・動的省電力 (AOS時のみSDR駆動)"]
            Proxy["🔄 metrics-proxy [NEW]<br/>(KubeEdge cAdvisor :10350 プロキシ)"]

            RTLSDR -->|"IQ サンプル"| TrackerNew
        end

        subgraph CloudSide["⚙️ クラスタ・解析基盤 (tozastation-g1621-02 / k3s)"]
            direction TB
            Garage["📦 Garage S3 (:3900) [NEW]<br/>(Rust製 極小分散ストレージ / メモリ 3MiB)"]
            Temporal["⏳ Temporal Server (:7233) [NEW]<br/>(SQLite内包 / 耐久ワークフロー管理)"]
            KEDA["⚖️ KEDA Operator v2.20.0 [NEW]<br/>(0-scale オートスケーラー)"]
            Worker["🔬 satellite-analyzer-worker [NEW]<br/>(direwolf APRS / FFT スペクトログラム)<br/>普段: 0 replicas (リソース 0)<br/>解析時: 1 replica (オンデマンド起動)"]
            Viewer["📱 satellite-viewer (:30088) [NEW]<br/>(スマホ向け Web ビューア / メモリ 40MiB)"]

            Prometheus["📈 Prometheus & Grafana (:30080) [ENHANCED]<br/>(エッジ全体 & Pod別 リアルタイム監視)"]

            TrackerNew -->|"① 48kHz WAV 保存"| Garage
            Garage -.->|"② 新規 WAV 検知"| Viewer
            Viewer -->|"③ ワークフロー投入"| Temporal
            Temporal -->|"④ キュー監視"| KEDA
            KEDA -->|"⑤ 0 → 1 スケールアウト"| Worker
            Worker -->|"⑥ WAV 取得 & 解析実行"| Garage
            Worker -->|"⑦ 成果物格納 & 生WAV削除"| Garage
            Worker -.->|"⑧ 完了後 1 → 0 縮退"| KEDA

            Proxy -.->|"cAdvisor メトリクス"| Prometheus
            Garage -.->|"成果物プレビュー"| Viewer
        end
    end

    UserPhone["📱 スマホ / ブラウザ [NEW]"]
    UserPhone -->|"観測結果プレビュー (:30088)"| Viewer
    UserPhone -->|"統合リソース監視 (:30080)"| Prometheus
```

---

### 2. コンポーネント別・スペック別の進化一覧表

| 比較項目 | #2 (前回) | #3 (今回) | 進化と SRE 的メリット |
| :--- | :--- | :--- | :--- |
| **エッジ観測実装** | Python 3.11 (`satellite-tracker`) | **Rust (`satellite-tracker-rs`)** | **メモリ 100MiB $\to$ 3MiB（97%削減）**。GC 停止がなくなりバッファドロップを撲滅 |
| **SDR ハード駆動** | 常時チューナー受信稼働 | **動的省電力 (Dynamic Power Mgmt)** | 衛星が地平線上に現れる AOS 直前のみ起動。発熱・消費電力を最小化 |
| **WAV 音声保存** | 2.4MSPS 広帯域を直接録音 (数百MB) | **48kHz ゼロコピー狭帯域スプール** | 音声通信に必要な帯域に絞り、**ファイルサイズを数十分の一（数MB）に圧縮** |
| **ストレージ** | エッジのローカル SSD に生蓄積 | **Garage S3 (:3900) (分散ストレージ)** | **実測メモリ 3MiB** の超軽量 S3。成果物保存後に生 WAV を自動削除し容量維持 |
| **ワークフロー** | なし (録音しっぱなし) | **Temporal Server (:7233)** | 取得 $\to$ 解析 $\to$ 保存 $\to$ 削除 をコードとして**耐久実行 (Durable Execution)** |
| **信号解析処理** | なし (手動または未実装) | **`satellite-analyzer-worker`** | direwolf による APRS デコード ＆ 高解像度スペクトログラム画像を自動生成 |
| **オートスケール** | なし (静的 Pod 配置) | **KEDA v2.20.0 による「0-scale」** | **待機時は 0 レプリカ（リソース 0）**。データ到着時のみ 1 に起動し、完了後 0 に自動縮退 |
| **結果の確認方法** | ターミナルでログ確認 | **スマホ向け Web ビューア (:30088)** | 同一 Wi-Fi のスマホからブラウザを開くだけで、画像拡大や JSON プレビューが可能 |
| **リソース監視** | Python プロセス内部値のみ | **KubeEdge cAdvisor (:10350)連携** | **ホスト全体 (6.8GB / 0.4コア) と Pod毎 (Rust: 3.8MB)** の二層監視を実現 |

---

## アーキテクチャの設計意図（なぜこの構成にしたのか）

本システムの各コンポーネントを選定・設計した意図を、SRE の視点から解説します。

### 1. エッジ観測を Rust で「メモリ 3.8MiB」に極限まで絞り込んだ理由
エッジ観測コンテナ（`satellite-tracker-rs`）は、**「アンテナ直下で絶対に落ちず、最小のフットプリントで動き続けること」** だけを唯一の責務としました。
*(※ SGP4 軌道予測やドップラー追尾の基礎理論、KubeEdge の基本アーキテクチャは [第2弾の記事](https://github.com/tozastation/radio-astronomy/blob/main/docs/articles/qiita_kubeedge_satellite_tracker.md) で詳しく解説していますので、本記事では #3 の進化差分に集中します)*

- **ゼロコピー DSP ＆ 狭帯域 48kHz スプール**:
  - RTL-SDR から 2.4MSPS（毎秒 240万サンプル）で流れてくる大容量 IQ 信号をエッジ内部で高速デシメーションし、音声通信に必要な 48kHz 帯域だけに絞り込んで WAV 保存。これによりファイルサイズを従来の数十分の一（数MB程度）に激減させました。
- **動的省電力（Dynamic Power Management）**:
  - 衛星が地平線下にいる待機時間は RTL-SDR チューナーを完全にスリープさせ、SGP4 予測で衛星が視野内に入る直前（AOS: Acquisition of Signal）にのみハードウェアを起動します。
- **実測リソース**:
  - **メモリ: 約 3.8 MiB / CPU: 0.8m（約 0.08%）** を達成！Python 版（約 100MiB）と比較してメモリフットプリントを **96% 以上削減** しました。Rust のゼロコスト抽象化により、GC 停止によるバッファドロップも完全に撲滅しています。

### 2. なぜ MinIO ではなく「Garage S3」なのか？
Kubernetes 環境のオブジェクトストレージといえば MinIO が有名ですが、MinIO は初期化だけで 150〜250MiB 以上のメモリを消費し、UMPC 環境にはオーバースペックです。
- フランスの研究機関発祥の [Garage S3](https://garagehq.opera.software/)（[dxflrs/garage GitHub](https://github.com/dxflrs/garage)）は、エッジや地理的分散を前提に設計された Rust 製の超軽量オブジェクトストレージです。
- メタデータ管理に SQLite を内包し、**実測メモリ消費量はわずか 3 MiB**。
- それでいて完全な S3 互換 API を提供するため、標準の AWS CLI や boto3、Go SDK から何一つ変更なく透過的に利用できます。

### 3. なぜ KEDA による「0-scale」なのか？
人工衛星が地上局の上空を通過する時間は、**1回あたりわずか 5〜10分、1日に数回** だけです。つまり、**1日のうち 95% 以上の時間は待機時間** です。
- 解析ワーカー（Python + direwolf + SciPy + Matplotlib）を 24時間常駐させるのは、UMPC の貴重なリソースの完全な無駄遣いです。
- [KEDA (Kubernetes Event-driven Autoscaling)](https://keda.sh/)（[GitHub](https://github.com/kedacore/keda)）を導入し、普段は **`replicas: 0`（メモリ 0 MiB）** で待機させます。
- 観測完了後に S3 へ音声がアップロードされると、KEDA の **`metrics-api` scaler** が常駐ビューア（`satellite-viewer`）の `/api/pending-tasks` を直接ポーリング監視し、未処理の生録音（`pending_count > 0`）を検知して **`0 → 1` に完全自動スケールアウト**。解析（パケットデコード ＆ 画像生成）が終わり、生 WAV を自動削除したら、再び **`1 → 0` に自動縮退** します。

### 4. なぜ単なるスクリプト実行ではなく「Temporal」なのか？
「S3 アップロードを検知してコンテナを動かすだけなら、シェルスクリプトや Cron、Webhook で十分では？」と思うかもしれません。しかし SRE 的には以下の問題があります：
- 解析中に UMPC が再起動したり、コンテナが OOMKilled された場合、タスクが途中で消滅して未解析の音声が放置される。
- 音声ダウンロード $\to$ パケット解析 $\to$ スペクトログラム生成 $\to$ 結果保存 $\to$ 元音声削除、という一連のステップのどこで失敗したのかを追跡・自動リトライしたい。
- [Temporal](https://temporal.io/)（[GitHub](https://github.com/temporalio/temporal)）を用いることで、ワークフローの各ステップがコードとして耐久実行（Durable Execution）され、障害耐性と履歴追跡が完璧に担保されます。

### 5. エッジとクラウドを直接繋がない「S3 境界の疎結合イベント駆動」
エッジコンテナに直接 Temporal の gRPC クライアントを持たせることも技術的には可能ですが、あえて **Garage S3 へのアップロード完了をイベントの境界線** としました。
- **エッジの自律性と極限の軽量性**:
  - Temporal クライアント（gRPC、Protobuf、TLS、接続管理スレッド）を Rust に含めると、バイナリサイズやランタイムメモリが増加します。S3 の単純な HTTP PUT だけで完結させることで、エッジのメモリ 3.8MiB を死守しています。
- **ネットワーク断への耐性**:
  - 万が一クラスタ基盤がメンテナンス中や再起動中であっても、エッジは淡々とローカルまたは S3 に音声を保存し続けます。クラスタ側は復帰した瞬間に未処理の WAV を検知して順次 Temporal ワークフローへ投入できるため、疎結合な信頼性を獲得しています。

---

## 実機クラスタでの E2E 結合検証

構築したパイプラインが本当に完全自律で動作するか、実機クラスタ上でエンドツーエンド（E2E）自動結合テストスクリプトを実行して検証しました。

### 検証シナリオ
1. **擬似 48kHz WAV 生成**: ISS（国際宇宙ステーション）の APRS ビーコン音声を模したテスト WAV を生成。
2. **Garage S3 へのアップロード**: `satellite-recordings` バケットへオブジェクト投入。
3. **Temporal ワークフロー開始**: `AnalyzeSatellitePassWorkflow` をトリガー。
4. **KEDA による自動起動**: `satellite-analyzer-worker` が `0 → 1` へスケールアウト。
5. **解析処理の実行**:
   - direwolf による AX.25 APRS パケットのデコード。
   - FFT による時間-周波数スペクトログラム画像（PNG）のプロット。
6. **成果物の格納 ＆ 生音声削除**:
   - `results/ISS/.../spectrogram.png`（約 680KB）
   - `results/ISS/.../packets.json`
   - `results/ISS/.../summary.json` を S3 に保存。
   - 容量節約のため、元の生 WAV を S3 から自動削除。
7. **KEDA による完全縮退**: 解析ワーカーが自動で `1 → 0` レプリカ（リソース消費ゼロ）に復帰。

```bash
$ python3 scripts/trigger_cluster_e2e.py
🚀 [E2E] S3 音声アップロード完了: s3://satellite-recordings/raw/ISS/test_pass.wav
⏳ [E2E] Temporal ワークフロー開始: AnalyzeSatellitePassWorkflow
⚖️ [E2E] KEDA ワーカー起動検知: replicas = 1
🔬 [E2E] 解析実行中 (direwolf APRSデコード & スペクトログラム生成)...
📦 [E2E] 成果物格納完了:
   - spectrogram.png (679,867 bytes)
   - summary.json (status: completed)
🧹 [E2E] 元生 WAV の自動クリーンアップ完了
⚖️ [E2E] KEDA ワーカー縮退確認: replicas = 0 (リソース消費ゼロ復帰)
✅ [E2E] すべてのパイプラインが完全自律で完走しました！
```

---

---

## 実機による本物の衛星電波観測 ＆ 自律解析の完走

E2E テストの成功に続き、**実際にベランダ上空を通過する本物の人工衛星（ISS: 国際宇宙ステーション）** の電波を受信し、人間が一切介入せずに全自動で Garage S3 $\to$ Temporal $\to$ KEDA $\to$ スペクトログラム生成まで完走することを実証しました！

### 🛰️ 本物パスの自動観測ログ & パイプライン推移

- **観測衛星**: **ISS (ZARYA)** (145.825 MHz APRS)
- **通過時間**: 2026-10-10 14:26:10 〜 14:29:03 JST
- **軌道諸元**: 最大仰角 **43.4°**、方位角 73.2° $\to$ 271.2°
- **RF実測値**: RSSI **-62.6 dBm**, SNR **2.71 dB**, 理論ドップラー偏移 **+1,763 Hz**

```text
# 1. AOS 突入（14:26:10 JST）: SGP4予測に基づきSDRが動的スタンバイから自動起動
2026-10-10 14:26:10 [INFO] AOS entered: ISS (ZARYA) (El: 10.2°, Az: 73.5°, Freq: 145.825 MHz)
2026-10-10 14:26:10 [INFO] SDR warmup completed. 48kHz WAV streaming spool started.

# 2. LOS 完了（14:29:03 JST）: 録音クローズと S3 への自動アップロード
2026-10-10 14:29:03 [INFO] LOS completed: ISS (ZARYA) (Max El: 43.4°)
2026-10-10 14:29:04 [INFO] S3Uploader: Uploading 152,576 bytes to raw/ISS (ZARYA)/ISS (ZARYA)_20261010_052606.wav...
2026-10-10 14:29:05 [INFO] S3Uploader: Successfully uploaded: raw/ISS (ZARYA)/ISS (ZARYA)_20261010_052606.wav

# 3. 自動ディスパッチ & Temporal ワークフロー発火
🚀 [Dispatcher] Found new raw recording: raw/ISS (ZARYA)/ISS (ZARYA)_20261010_052606.wav
⏳ [Dispatcher] Starting Temporal workflow for ISS (ZARYA)_20261010_052606...
🎉 [Dispatcher] Workflow started successfully for ISS (ZARYA)_20261010_052606!

# 4. KEDA ワーカー自動起動 & 解析実行
2026-10-10 14:29:12 [INFO] satellite-analyzer-worker: Temporal Worker started. Listening on task queue: 'satellite-analysis'...
2026-10-10 14:29:13 [INFO] Downloading raw WAV from S3...
2026-10-10 14:29:14 [INFO] Decoding APRS packets (direwolf) & Generating Spectrogram (Matplotlib)...
2026-10-10 14:29:16 [INFO] Uploading artifact spectrogram.png (931,933 bytes) to S3...
2026-10-10 14:29:17 [INFO] Uploading artifact summary.json to S3...
2026-10-10 14:29:18 [INFO] Cleaning up raw WAV from S3: raw/ISS (ZARYA)/ISS (ZARYA)_20261010_052606.wav...
2026-10-10 14:29:19 [INFO] Successfully completed AnalyzeSatellitePassWorkflow!

# 5. KEDA による完全自動縮退（1 → 0）
satellite-analyzer-worker-7f7c765f7f-fbcvt   1/1     Terminating   0   42s
```

### 📊 実測スペクトログラム成果物

以下は、ベランダのモービルホイップアンテナと RTL-SDR v4 が実際に受信し、パイプラインが全自動で生成した ISS 通過時の時間-周波数パワースペクトログラムです。

![ISS Spectrogram Real](./images/iss_spectrogram_real.png)

```json
/* results/ISS (ZARYA)/ISS (ZARYA)_20261010_052606/summary.json */
{
  "satellite": "ISS (ZARYA)",
  "pass_id": "ISS (ZARYA)_20261010_052606",
  "packets_count": 0,
  "status": "completed"
}
```

生録音データ（150KB）は解析完了と同時に S3 およびローカルから自動消去され、成果物（スペクトログラム PNG: 約 932KB、サマリ JSON）のみが Garage S3 に永続化されました。

---

## スマホからの観測結果プレビュー & 統合監視

### 1. スマホ向け Web ビューア（NodePort 30088）
「観測したスペクトログラムやパケットを、PCを開かずにベッドやリビングのスマホからパッと見たい！」という思いから、スマホ最適化の軽量 Web ダッシュボード（`satellite-viewer`）を自作しました。

- 同一 Wi-Fi のスマホブラウザから `http://192.168.68.66:30088` を開くだけでアクセス可能。
- **スペクトログラム画像（PNG）のインライン表示 ＆ タップ拡大**。
- **`summary.json` や `packets.json` の中身をダウンロードせずにその場でアコーディオン展開・プレビュー＆コピー**。
- Python 標準ライブラリ主体の超軽量設計で、メモリ消費はわずか **40 MiB**。

### 2. Grafana によるエッジ全体 ＆ Pod別リソース監視（NodePort 30080）
KubeEdge 環境におけるメトリクス監視の落とし穴を解消し、Grafana 上で以下の 4 パネルをリアルタイム監視できるようにしました：
- **エッジノード全体 CPU消費**: 約 0.43 cores（約 10%）
- **エッジノード全体 メモリ消費**: 約 6.8 GiB / 16GB
- **Pod別 CPU消費量**: `satellite-tracker`: **0.8m（約 0.08%）**
- **Pod別 メモリ使用量**: `satellite-tracker`: **3.8 MiB**

---

## SRE 視点での泥臭いトラブルシューティング集

開発中に遭遇した、現場ならではのリアルなトラブルとその解決策を共有します。

### 1. IPv6 未導通ネットワークにおける Happy Eyeballs 遅延
- **事象**: `ghcr.io` からのコンテナイメージ Pull や上流通信が数分間フリーズする。
- **原因**: 宅内ネットワークで外部 IPv6 ルーティングが通っていないにもかかわらず、デュアルスタックホストが IPv6 接続を試行 $\to$ 数分間タイムアウト待ち $\to$ IPv4 フォールバックが発生していた。
- **解決策**: `/etc/gai.conf` で `precedence ::ffff:0:0/96 100` を有効化し、OS レベルで IPv4 優先接続を強制することで即座に解決。

### 2. KubeEdge cAdvisor（:10350）と CloudCore のポート競合
- **事象**: Prometheus から KubeEdge エッジノードの cAdvisor メトリクスを取得しようとすると `Connection Refused` や `Client sent an HTTP request to an HTTPS server` が発生する。
- **原因**: KubeEdge の `edged` はローカルループバック（`127.0.0.1:10350`）のみでリッスンしており、ホスト外の Pod ネットワークからは見えなかった。さらに隣接ポートの `10351` は CloudCore が HTTPS で握っていた。
- **解決策**: `hostNetwork: true` を持った極小 Python プロキシ DaemonSet（`kubeedge-metrics-proxy`）を空きポート（`19095`）で稼働させ、エッジノードの cAdvisor を平文 HTTP でクラスタ内に露出して Prometheus Operator の `ScrapeConfig` で接続。

### 3. Docker Hub レート制限回避とローカルレジストリ徹底
- **事象**: コンポーネントのローリングアップデート時に Docker Hub のレート制限（Too Many Requests）に引っかかる。
- **解決策**: クラスタ内にローカルレジストリ（`localhost:5000` / NodePort `30500`）を構築し、全コンポーネントで `latest` タグを禁止。固定バージョン（`v1.0.1`, `1.9.1`, `2.20.0`）をミラーリングして完全宣言的に管理。

### 4. Temporal SDK Core（Rust）のメモリ特性と OOMKilled（Exit Code 137）の壁
- **事象**: Web ビューア（`satellite-viewer`）内に自動ディスパッチャーを組み込んだ直後、プロセスが `Exit Code: 137`（OOMKilled）で不定期にクラッシュ・再起動を繰り返す。
- **原因**: 
  - 当初、Python 単体の Web サーバーとして `limits.memory: 64Mi` を割り当てていました。
  - しかし Python 版の [Temporal SDK](https://github.com/temporalio/sdk-python) は内部で高性能な Rust 製コア（`temporal-sdk-core`）を内包しており、gRPC コネクションや内部スレッドプールの初期化時に瞬間的に数十 MiB のヒープを消費します。
  - その結果、平常時の 40MiB に加えて SDK 初期化のバーストが重なった瞬間に 64MiB の cgroup 上限を突破して OOMKill されていました。
- **解決策**:
  - メモリプロファイリングに基づき、マニフェストのリミットを `64Mi` $\to$ **`160Mi`**（Requests: `48Mi`）へ緩和。
  - さらにディスパッチャースレッド内部に自動リトライ・再接続ループを組み込むことで、リソース消費を最小限（平常時 59MiB）に抑えつつ堅牢な自律稼働を確立しました。

### 5. KubeEdge エッジノードにおける内部 DNS 未解決と NodePort ルーティング
- **事象**: エッジノード上の観測 Pod（`satellite-tracker`）からクラスタ内 S3 エンドポイント（`garage-s3.storage.svc.cluster.local:3900`）への HTTP PUT が `error sending request for url` で失敗する。
- **原因**: 
  - KubeEdge の `edged` 下で動作するエッジコンテナは、クラスタの CoreDNS（`10.43.0.10`）ではなく、ホスト OS の `/etc/resolv.conf`（プロバイダのパブリック DNS）をそのまま継承します。
  - そのため、クラスタ内部専用の `*.svc.cluster.local` FQDN は名前解決できません。
- **解決策**:
  - エッジ Pod に渡す S3 エンドポイントを、ホスト物理 IP の NodePort（`http://192.168.68.66:30900`）に明示指定することで直接ルーティングを確立しました。

### 6. コンテナ Ephemeral ストレージの教訓と hostPath 永続スプールによるデータ保全
- **事象**: コンテナ内の `/tmp` に直接録音していた初期検証時、Pod を force delete / 再作成した際にコンテナ固有の overlayfs RW 層が破棄され、未アップロードの生録音が消失するリスクに直面。
- **原因**: コンテナのライフサイクル（短命）と電波観測データ（一回限りの天文イベント）のライフサイクルが密結合していた。
- **解決策**:
  - ホスト物理ディレクトリ `/tmp/satellite-recordings` を `hostPath` バインドマウントし、コンテナの生死に関わらずデータが 100% ホストに残る完全分離構造を確立。
  - さらに起動時・LOS 時に未送信ファイルを自動スキャンして再送する「自己治癒スプール同期（`sync_pending_spool`）」を実装。今回の DNS 障害時にも録音データが削除されず無傷で守られました。

### 7. Temporal Dev モードのメトリクス欠落と KEDA `metrics-api` scaler による真のゼロ介入自動化
- **事象**: S3 へ生録音が届き Temporal ワークフローが発火しても、KEDA の解析ワーカー（`satellite-analyzer-worker`）が自動で立ち上がらず `0` レプリカのままスタックする。
- **原因**: 
  - KEDA ScaledObject が Prometheus 経由で Temporal のタスクキュー長メトリクス（`temporal_activity_schedule_to_start_latency_seconds_count`）を監視していました。
  - しかし軽量化のために導入した Temporal 開発用サーバー（Dev モード）が Prometheus エンドポイントを出力しておらず、Prometheus 側のクエリ結果が常に `0` に固定化されていました。
- **解決策**:
  - 常時稼働している軽量 Web ビューア（`satellite-viewer`）に、未処理の生録音ファイル数を返す極小エンドポイント `/api/pending-tasks`（JSON: `{"pending_count": N}`）を追加。
  - KEDA の公式機能である **`metrics-api` scaler** を採用し、KEDA がこの API を直接監視して未処理ファイルが存在するときに `0 → 1` 起動、処理完了で `1 → 0` 縮退するように更新。
  - Prometheus や Temporal の内部仕様に一切依存せず、S3 へのファイル到着をトリガーとする 100% 確実な自律スケーリングを確立しました。

---

## まとめと今後の展望

今回、KubeEdge の特性を活かして **「エッジ極小観測（Rust 3MiB）」** と **「イベント駆動型ゼロスケール解析（Garage + Temporal + KEDA）」** を組み合わせることで、手のひらサイズの UMPC 1台でもリソースを枯渇させずに、24時間365日自律稼働する衛星電波観測パイプラインを構築することができました。

### 次の挑戦（予告）
- **物理2台分離**: 観測エッジPC（ベランダ GPD Pocket3）と、室内の GPU 分析専用 PC（Ubuntu）を宅内 LAN で物理分離し、KubeEdge の真骨頂である分散オーケストレーションを実証する。
- **太陽電波バースト観測 ＆ Meteor Scatter（流星電波観測）**: 人工衛星だけでなく、21cm 中性水素線や太陽フレア電波の自動イベント検知パイプラインへの拡張。

最後までお読みいただきありがとうございました！  
質問やフィードバックなどありましたら、コメントや GitHub の Issue / PR などでお気軽にいただけると嬉しいです！
