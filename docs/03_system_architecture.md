# 🛰️ 自宅KubeEdge電波天文学システム構成図

本ドキュメントでは、**GPD Pocket3（Ubuntu 26.04）** と **分析・運用ワークステーション（Ubuntu PC）** の2台を活用し、CNCF **KubeEdge** を中核に据えた「**エッジ完結・オフライン自律稼働型 電波観測基盤**」のシステム構成を定義します。  
*(※ 不明な用語やハードウェア構成要素は [00_glossary.md](00_glossary.md) を、KubeEdge の内部通信コンポーネントの詳細は [docs/qa/13_kubeedge_architecture_and_edge_cloud_communication.md](qa/13_kubeedge_architecture_and_edge_cloud_communication.md) を参照してください)*

---

## 1. 全体アーキテクチャ図（Ubuntu 2台構成: エッジ常時自律観測 + オンデマンド分析）

```mermaid
flowchart TB
    subgraph EdgeArea["エッジ観測ノード (GPD Pocket3: Ubuntu 26.04)"]
        direction TB
        Antenna["📡 アンテナ (付属 / 21cmホーン / ダイポール)"]
        LNA["⚡ LNA + BPF (1420MHz / 偏波器)"]
        RTLSDR["📻 RTL-SDR Blog V4 (Bias-T ON)"]
        
        subgraph EdgeCoreBox["KubeEdge: EdgeCore (エッジデーモン)"]
            EdgeHub["🔌 EdgeHub<br/>(WebSocket/QUIC クライアント)"]
            EdgeStream["🚇 EdgeStream<br/>(リバーストンネル クライアント)"]
            MetaManager["🗄️ MetaManager<br/>(ローカル SQLite キャッシュ)"]
            Edged["⚙️ Edged<br/>(軽量 Kubelet / CRI containerd)"]
        end

        SDR_Pod["⚡ sdr-collector Pod (Rust / C / Python)<br/>2.4MSPS IQ受信 / リアルタイムFFT<br/>1秒積算スペクトル生成 (8KB/s圧縮)"]
        EdgeDB["💾 Edge Storage (DuckDB / Parquet)<br/>ローカルNVMe SSD常時蓄積 / SQL対応"]

        Antenna --> LNA --> RTLSDR -->|USB| SDR_Pod
        SDR_Pod -->|8KB/s 保存| EdgeDB
        Edged -->|Pod管理・死活監視| SDR_Pod
        MetaManager -->|オフライン時ローカル起動情報| Edged
    end

    subgraph CloudArea["クラウド / 分析・運用ノード (Ubuntu PC)"]
        direction TB
        
        subgraph K8sControl["Kubernetes (k3s) コントロールプレーン"]
            APIServer["☸️ K8s API Server / etcd"]
        end

        subgraph CloudCoreBox["KubeEdge: CloudCore"]
            EdgeController["🔄 EdgeController<br/>(API Server イベント監視 & 反映)"]
            CloudHub["🌐 CloudHub<br/>(WebSocket/QUIC サーバ: :10000/:10002)"]
            CloudStream["🚪 CloudStream<br/>(トンネル受付: :10003 / logs・exec中継)"]
        end

        APIServer <--> EdgeController
        EdgeController <--> CloudHub
        APIServer <--> CloudStream

        subgraph AnalyticsApps["分析・可視化ワークロード (Pod または Native)"]
            JupyterPod["📓 JupyterLab / Analytics<br/>Astropy / GPU銀河回転曲線解析"]
            GrafanaPod["📊 Grafana<br/>ウォーターフォール (Spectrogram)"]
        end

        DevCLI["🖥️ 開発・運用 CLI / UI<br/>kubectl / k9s / VS Code / ブラウザ"]
        DevCLI --> APIServer
        DevCLI --> JupyterPod
        DevCLI --> GrafanaPod
    end

    %% 通信フロー
    EdgeHub ==>|① WebSocket アウトバウンド接続 (ポート 10000/10002)| CloudHub
    EdgeStream ==>|② トンネル確立 (ポート 10003)| CloudStream
    JupyterPod -->|③ 宅内LAN経由 高速SQLクエリ (DuckDB/Parquet)| EdgeDB
    GrafanaPod -->|③ 宅内LAN経由 クエリ| EdgeDB
    DevCLI -.->|LAN SSH メンテナンス| EdgeArea
```

---

## 2. なぜ電波天文学に「KubeEdge」なのか？（SRE的メリット）

1. **Ubuntu 2台体制によるシンプルな運用と完全な自律性**:
   - GPD Pocket3 上の `edgecore` は、クラウド（Ubuntu PC）との通信が途切れても、ローカルキャッシュ（`MetaManager` / SQLite）を使って **Podの稼働を自律的に継続** します。
   - クラウドPCを電源OFFにしていても、ネットワーク障害が発生していても、**観測データは1秒も欠損することなく GPD Pocket3 に記録され続けます**。
2. **クラウド復帰時の自動ステータス同期**:
   - クラウド側の Ubuntu PC を起動して `cloudcore` がオンラインになると、エッジ側の `EdgeHub` が自動再接続し、エッジノードのヘルスチェックや設定（ConfigMap/Secret）が即座に同期されます。
3. **エッジでの極小データフットプリント**:
   - 生[IQデータ](00_glossary.md#iq)（4.8 MB/s = 415 GB/日）をエッジ内で即座に[FFT](00_glossary.md#fft) & 1秒[積算](00_glossary.md#integration)し、**8 KB/s（約 20 MB/日）のスペクトルデータに圧縮**。
   - GPD Pocket3 の内蔵SSD（512GB〜1TB）だけで **数年分の観測データをローカル完結で蓄積** できます。
4. **NAT超え・リバースアクセス（CloudStream / EdgeStream）**:
   - エッジからクラウドへアウトバウンドでトンネルを張るため、エッジノード側のルーター設定やポート開放が不要で、クラウド側から `kubectl logs` や `kubectl exec` による遠隔デバッグが標準K8s同様に行えます。

---

## 3. 各マシンの役割分担マトリクス

| マシン | スペック・OS | 担当コンポーネント | 役割と動作ポリシー |
| :--- | :--- | :--- | :--- |
| **GPD Pocket3** | ・超小型 UMPC<br>・Ubuntu 26.04 LTS<br>・ベランダ / 窓際設置 | **KubeEdge: EdgeCore**<br>・`EdgeHub`, `EdgeStream`<br>・`MetaManager` (SQLite)<br>・`Edged` (containerd)<br>・`sdr-collector` Pod<br>・`edge-storage` (DuckDB/Parquet) | **【24h 常時自律観測ノード】**<br>・[SDR](00_glossary.md#sdr)直結（[Bias-T](00_glossary.md#bias-t)で[LNA](00_glossary.md#lna)+[BPF](00_glossary.md#bpf)給電）で電波を受信し、エッジDSP（[積算](00_glossary.md#integration)）を実行。<br>・クラウドPCの電源状態に一切依存せず24時間稼働。 |
| **Ubuntu PC** | ・高火力 CPU/GPU/RAM<br>・Ubuntu Desktop / Server<br>・宅内常設 / オンデマンド | **KubeEdge: CloudCore & 分析基盤**<br>・Kubernetes (`k3s`) コントロールプレーン<br>・`CloudHub`, `CloudStream`, `EdgeController`<br>・`jupyterlab-astro` (GPU活用)<br>・`grafana` (可視化)<br>・VS Code, ブラウザ, `kubectl`, `k9s` | **【分析・開発 & コントロールプレーン】**<br>・観測データの集計・分析を行いたい時に起動（常時起動も可能）。<br>・LAN越しに GPD Pocket3 のデータを高速クエリして分析（[ウォーターフォール](00_glossary.md#waterfall)・[LSRドップラー補正](00_glossary.md#lsr)・[銀河回転曲線](00_glossary.md#rotation-curve)）。 |

---

## 4. データアクセスとクエリパターン

クラウド側の Ubuntu PC から、GPD Pocket3 に蓄積された観測データを分析する手法：

```text
[ GPD Pocket3 (Edge: Ubuntu) ]                   [ Ubuntu PC (Cloud / 分析環境) ]
Parquetファイル / DuckDB ─── (宅内LAN 1GbE / 2.5GbE / Wi-Fi) ───► DuckDB / Polars / Astropy (Jupyter)
                                                                     │
                                                                     ▼
                                                            ・21cm線 ドップラー解析
                                                            ・天の川銀河回転曲線プロット
                                                            ・流星電波反射エコーの集計
```

- **DuckDB over LAN / Parquet直接参照**:
  - GPD Pocket3 で日別に保存された Parquet ファイル（例: `data/spec_2026-08-15.parquet`）を、クラウドPC の JupyterLab / Python スクリプトから直接SQLでクエリし、[21cm線](00_glossary.md#21cm)の[ドップラー効果](00_glossary.md#doppler)や[銀河回転曲線](00_glossary.md#rotation-curve)を解析。
  - クエリ実行時のみネットワーク帯域を消費するため、極めて高速かつ省リソース。

---

## 5. 推奨 KubeEdge マニフェスト構成

```text
k8s/
├── cloud/                          # Ubuntu PC (CloudCore) 側の構成
│   ├── cloudcore/                  # KubeEdge CloudCore デプロイ設定 (Helm / YAML)
│   ├── analytics/
│   │   ├── jupyterlab-deployment.yaml  # 天文解析用 JupyterLab (GPU対応)
│   │   └── grafana-deployment.yaml     # 観測ダッシュボード
│   └── ingress/                    # 各種WebUIへのルーティング
└── edge/                           # GPD Pocket3 (EdgeCore) 側の構成
    ├── sdr-collector-pod.yaml      # RTL-SDR v4 受信 & FFT積算デーモン
    └── edge-storage-pod.yaml       # DuckDB / Parquet ローカルストレージ
```
