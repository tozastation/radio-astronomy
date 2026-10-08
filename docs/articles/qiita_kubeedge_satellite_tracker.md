---
title: 【ベランダ電波観測所 #2】KubeEdge×SDRで始める衛星電波観測と単一ノード運用の記録
tags:
  - Kubernetes
  - KubeEdge
  - k3s
  - Prometheus
  - SDR
  - Ubuntu
private: false
updated_at: '2026-10-08T19:30:00+09:00'
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

# 【ベランダ電波観測所 #2】KubeEdge×SDRで始める衛星電波観測と単一ノード運用の記録

## はじめに

こんにちは、戸澤（@tozastation）です！  
純粋に宇宙が大好きで、自宅のベランダから個人で電波天文学や衛星観測を行う **「ベランダ電波観測所」** プロジェクトを進めています。

本プロジェクトは、AIアシスタントの Antigravity に電波工学やDSP（デジタル信号処理）、分散システムの設計を相談し、教わりながら進めています。いつも大変お世話になっております🙇‍♂️

コードや設計ドキュメントはすべて GitHub の [tozastation/radio-astronomy](https://github.com/tozastation/radio-astronomy) に公開していますので、ぜひ覗いてみてください！

---

## 前回の振り返りと今回の動機

前回の記事はこちら：  
👉 [【ベランダ電波観測所 #1】RTL-SDR Blog V4 と WSL2 で電波を受信してみる 〜公式ドライバビルドの罠からSSHストリーミングまで〜](https://qiita.com/tozastation/items/b7e411ba034ddf46be22)

前回（Day 1）では、Windows 11 + WSL2 の環境で RTL-SDR Blog V4 を動かし、ベランダのエアコン室外機の上にアンテナを置いて電波を受信するところまで行きました。  
しかし、記事の最後のオチでこう書きました：

> **「やっぱりエッジ観測機はネイティブ Linux が最強でした！！（GPD Pocket3 をネイティブ Ubuntu 26.04 LTS にクリーンインストール）」**

常時稼働できるネイティブ Linux のエッジマシン（UMPC）が手に入ったのなら、次にやることは決まっています。

> **「エッジ観測機なら、KubeEdge でコンテナオーケストレーションして完全自律稼働させるしかないのでは……！？」**

電波天文学や人工衛星観測の現場では、山奥や屋外アンテナ直下にエッジPCを設置し、観測パイプラインを遠隔管理・監視することが求められます。  
そこで今回は、**GPD Pocket3（メモリ 16GB / Ubuntu 26.04 LTS）単一端末上で、k3s（CloudCore）と KubeEdge（EdgeCore）を同居** させ、頭上を通過する人工衛星（CubeSat / ISS）の電波を SGP4 軌道予測と SDR FFT ドップラー解析で自動追尾し、Prometheus と Grafana でリアルタイム可視化するエッジ観測環境を構築しました。

ただし、本来は複数マシンに分離して構成する KubeEdge を 1台に同居させたことで、コンテナランタイム（CRI）、ネットワーク（CNI）、ボリュームなどのリソース競合に伴うトラブルがいくつか発生しました。  
本記事では、この構成の概要と、単一端末上で運用する際に遭遇したトラブルの解決手順をまとめます。

---

## システムアーキテクチャ（1台完結PoC）

GPD Pocket3 の単一マシン内で、Kubernetes コントロールプレーンと KubeEdge エッジノードを共存させています。

```mermaid
flowchart TB
    subgraph Host["GPD Pocket3 (Ubuntu 26.04 LTS / 192.168.68.66)"]
        subgraph CloudSide["Control-Plane Node (tozastation-g1621-02)"]
            K3S["k3s server (v1.36.5)"]
            CC["KubeEdge CloudCore"]
            REG["Local Container Registry (:5000)"]
            PROM["Prometheus (v3.15.0)"]
            GRAF["Grafana (NodePort :30080)"]
            KSM["kube-state-metrics"]
            MS["metrics-server"]
        end

        subgraph EdgeSide["Edge Node (gpd-pocket3-edge)"]
            EC["KubeEdge EdgeCore (v1.22.0)"]
            CRI_EDGE["containerd-edge (/run/containerd-edge/containerd.sock)"]
            TRACKER["satellite-tracker Pod (:9100/metrics)"]
            SDR_HW["RTL-SDR v4 (USB Dongle)"]
        end
    end

    CloudSide <-->|"CloudStream / CloudHub (WebSocket/TLS)"| EdgeSide
    TRACKER -->|"USB Direct (/dev/bus/usb)"| SDR_HW
    PROM -->|"Scrape (:9100)"| TRACKER
    GRAF -->|"Query"| PROM
```

### KubeEdge の主要コンポーネント

KubeEdge はクラウド側（CloudCore）とエッジ端末側（EdgeCore）で役割を分担して動作します。

| コンポーネント | 配置 | 主な役割 |
| :--- | :--- | :--- |
| **CloudHub** | CloudCore | エッジノードとの通信エンドポイント（WebSocket / QUIC）。メタデータの送受信を担う |
| **EdgeController** | CloudCore | Kubernetes API Server を監視し、エッジノード向けのリソース情報を同期 |
| **CloudStream** | CloudCore | `kubectl logs` や `kubectl exec` のストリーミング通信をエッジ側へトンネリング中継 |
| **EdgeHub** | EdgeCore | CloudHub と接続し、同期メッセージを送受信する通信クライアント |
| **MetaManager** | EdgeCore | ローカル SQLite キャッシュ。ネットワーク切断時でも Pod が自律稼働できるようメタデータを保持 |
| **edged** | EdgeCore | エッジ向けに軽量化された kubelet。CRI（containerd）経由で Pod のライフサイクルを管理 |
| **EdgeStream** | EdgeCore | CloudStream からのリクエストを受け、ローカル containerd のストリーミングエンドポイントへ転送 |

---

## 通常の Kubernetes と KubeEdge の違い

この構成図を見ると、「エッジ側にも k3s などの軽量クラスタを立てて、マルチクラスタ管理ツールで連携するのでは？」と思われるかもしれません。  
しかし、KubeEdge の設計思想は根本から異なります。[KubeEdge 公式アーキテクチャドキュメント](https://kubeedge.io/docs/architecture/)や [GitHub リポジトリ（kubeedge/kubeedge）](https://github.com/kubeedge/kubeedge) の実装を紐解くと、エッジ特化の仕組みが明確に見えてきます。

### 1. エッジ側には Kubernetes クラスタを作らない（EdgeCore のみ）
- **一次情報**: [KubeEdge Architecture - Edge Side](https://kubeedge.io/docs/architecture/edge/edgecore) / [GitHub: `edge/cmd/edgecore`](https://github.com/kubeedge/kubeedge/tree/master/edge/cmd/edgecore)

KubeEdge では、エッジ端末側に API Server や etcd、kube-scheduler などのコントロールプレーンを**一切配置しません**。  
エッジ側で動かす常駐デーモンは、単一バイナリである **`EdgeCore`** と、コンテナランタイム（`containerd`）のみです。

クラウド側の Kubernetes クラスタから見ると、エッジ端末は単に「`node-role.kubernetes.io/edge` ラベルが付いた 1 つの Worker Node」として認識されます。

```bash
$ kubectl get nodes
NAME                     STATUS   ROLES                  AGE    VERSION
tozastation-g1621-02     Ready    control-plane,master   2d     v1.36.5+k3s1
gpd-pocket3-edge         Ready    agent,edge             2d     v1.22.0-kubeedge
```

コントロールプレーンのリソース消費（etcd や API Server 等）がエッジ側に一切発生せず、kubelet 相当の処理も軽量化された [`edged`](https://github.com/kubeedge/kubeedge/tree/master/edge/pkg/edged) が担うため、UMPC や Raspberry Pi などのリソース制約端末でも最小限のオーバーヘッドでコンテナを動かせます。

### 2. 単一 WebSocket トンネル（NAT・モバイル回線越え）
- **一次情報**: [CloudCore - CloudHub](https://kubeedge.io/docs/architecture/cloud/cloudhub) / [EdgeCore - EdgeHub](https://kubeedge.io/docs/architecture/edge/edgehub) / [CloudStream Tunnel](https://kubeedge.io/docs/advanced/cloudcore_stream_tunnel/)
- **実装コード**: [`cloud/pkg/cloudhub`](https://github.com/kubeedge/kubeedge/tree/master/cloud/pkg/cloudhub) / [`edge/pkg/edgehub`](https://github.com/kubeedge/kubeedge/tree/master/edge/pkg/edgehub)

通常の Kubernetes Worker Node（kubelet）は、コントロールプレーンの各ポートへ直接通信できるフラットなネットワークを前提とします。  
しかし、屋外やベランダ、山奥のエッジ端末はプライベート IP（NAT 背後）にあり、LTE/Wi-Fi 回線など不安定な環境も珍しくありません。

KubeEdge では、エッジ側の `EdgeHub` からクラウド側の `CloudHub` に対して**単一の WebSocket（または QUIC）トンネル**を外向きに確立します。  
ノードのメタデータ同期だけでなく、`kubectl logs` や `kubectl exec` のストリーミング通信（[`CloudStream`](https://github.com/kubeedge/kubeedge/tree/master/cloud/pkg/cloudstream) $\leftrightarrow$ [`EdgeStream`](https://github.com/kubeedge/kubeedge/tree/master/edge/pkg/edgestream)）もこの 1 本の暗号化トンネル内で多重化されるため、エッジ側のポート開放や複雑な VPN 構成が不要です。

### 3. オフライン自律性（MetaManager と SQLite）
- **一次情報**: [EdgeCore - MetaManager](https://kubeedge.io/docs/architecture/edge/metamanager)
- **実装コード**: [`edge/pkg/metamanager`](https://github.com/kubeedge/kubeedge/tree/master/edge/pkg/metamanager) / [SQLite DAO: `dao/meta.go`](https://github.com/kubeedge/kubeedge/blob/master/edge/pkg/metamanager/dao/meta.go)

通常の kubelet は API Server との接続が途切れると、一定時間後にノードが `NotReady` となり、最悪の場合は Pod が Evict されて停止します。  
KubeEdge では、エッジ端末側の **`MetaManager`** がローカルの SQLite データベース（`/var/lib/kubeedge/edgecore.db`）に Pod 定義や ConfigMap などのメタデータを永続キャッシュしています。

実装コード（`dao/meta.go`）を見ると、メタデータが Key-Value 形式で SQLite の `meta` テーブルに逐次保存されていることが分かります。  
そのため、ネットワークが一時的に切断（オフライン）されても、エッジ上のコンテナはそのまま稼働し続け、端末を再起動してもローカルキャッシュから自律的に Pod を復元できます。通信が復旧した段階で、クラウド側の状態と自動的に差分同期（Reconcile）されます。

---

## できたもの：ノード・Pod 配置と Grafana 可視化
実際に単一端末（GPD Pocket3）内で構成したクラスタの Pod 配置です。`-o wide` で確認すると、どの Pod がどちらのノードで稼働しているかが分かります。

```bash
$ kubectl get pods -A -o wide
NAMESPACE            NAME                                                        READY   STATUS    IP              NODE
container-registry   local-registry-9d959d85f-j2656                              1/1     Running   10.42.0.144     tozastation-g1621-02
kube-system          coredns-7cfb7bc9c7-2rbxd                                    1/1     Running   10.42.0.142     tozastation-g1621-02
kube-system          local-path-provisioner-77b9867795-wbdkx                     1/1     Running   10.42.0.143     tozastation-g1621-02
kube-system          metrics-server-6f58cdc499-mzswn                             1/1     Running   10.42.0.146     tozastation-g1621-02
kubeedge             cloud-iptables-manager-nxww4                                1/1     Running   192.168.68.66   tozastation-g1621-02
kubeedge             cloudcore-7499476549-t29qs                                  1/1     Running   192.168.68.66   tozastation-g1621-02
monitoring           kube-prometheus-stack-grafana-79d88bc745-4d5cz              3/3     Running   10.42.0.155     tozastation-g1621-02
monitoring           kube-prometheus-stack-kube-state-metrics-687686d88b-64xr8   1/1     Running   10.42.0.148     tozastation-g1621-02
monitoring           kube-prometheus-stack-operator-bdbf4977d-jxftd              1/1     Running   10.42.0.150     tozastation-g1621-02
monitoring           prometheus-kube-prometheus-stack-prometheus-0               2/2     Running   10.42.0.152     tozastation-g1621-02
default              satellite-tracker-59994966b5-vlzjp                          1/1     Running   10.42.3.42      gpd-pocket3-edge
```

- **コントロールプレーン側 (`tozastation-g1621-02`)**:
  - k3s サーバー、CloudCore、ローカルコンテナレジストリ
  - 監視スタック（Prometheus, Grafana, kube-state-metrics, Prometheus Operator, metrics-server）
- **エッジノード側 (`gpd-pocket3-edge`)**:
  - `satellite-tracker`（USB バス経由で RTL-SDR v4 実機ドングルを占有し、FFT スペクトル解析を行う観測 Pod）

コントロールプレーンと監視スタック、そして観測用エッジ Pod が意図通り綺麗にノード分離されて稼働しています。

### リソース消費量（約 770MB）
監視基盤（Prometheus / Grafana）も含め、UMPC の限られたメモリを圧迫しないよう最小構成で運用しています。

```bash
$ kubectl top pods -n monitoring
NAME                                                        CPU(cores)   MEMORY(bytes)   
kube-prometheus-stack-grafana-79d88bc745-4d5cz              17m          382Mi           
kube-prometheus-stack-kube-state-metrics-687686d88b-64xr8   2m           23Mi            
kube-prometheus-stack-operator-bdbf4977d-jxftd              15m          30Mi            
prometheus-kube-prometheus-stack-prometheus-0               11m          335Mi           
```

### Grafana ダッシュボードでのリアルタイム可視化

実際に GPD Pocket3 上で稼働しているリアルタイムダッシュボードのキャプチャです：

![Grafana 衛星追尾ダッシュボード全体](https://raw.githubusercontent.com/tozastation/radio-astronomy/main/docs/images/grafana_satellite_tracker_full.png)

*(※ローカルリポジトリの `docs/images/grafana_satellite_tracker_full.png` および `grafana_satellite_tracker_overview.png` に高解像度画像を格納しています。Qiita 投稿時は Qiita の画像アップローダーにドラッグ＆ドロップして差し替えてください)*

#### 各パネルの解説
1. **ドップラーS字カーブ（中央パネル）**:
   - 緑線（SGP4 軌道力学による理論予測）と黄線（RTL-SDR v4 の FFT パワースペクトルピーク実測値）を表示しています。
   - 衛星接近時の **+10,000 Hz** から最接近（TCA）の **0 Hz ゼロクロス** を経て、離脱時の **-10,000 Hz** へと推移する逆S字カーブを描いています。理論値と実測値の誤差は約 52.7 Hz（相対誤差 0.5%）で推移しています。
2. **北向きベランダの極軌道推移（中下段パネル）**:
   - 仰角が 0° から 30° へ上昇した後に 10° を切って下降する山なりの曲線と、方位角が 270°（真西）から 0°/360°（真北）を跨いで 55°（北東）へ抜けていく軌跡が記録されています。
3. **エッジリソース消費（最下段パネル）**:
   - エッジノード上の `satellite-tracker` Pod の CPU 使用率は **0.28〜0.34 Cores**、物理メモリ消費（RSS）は **約 50 MB** となっており、常時観測を行っても負荷は低く抑えられています。

---

## 衛星追尾の仕組みとDSP処理

衛星追尾パイプラインで処理している数理モデルと、アンテナ設置環境に合わせた視界判定の仕組みです。  
なお、軌道力学やデジタル信号処理（DSP）の数式導出や実装の詳細については、筆者の専門領域外のため AI（Antigravity）とペアプログラミングを行いながら設計・検証を進めました。詳細な理論的背景はリポジトリ内のドキュメントや関連リファレンスを参照してください。

### 1. 第一宇宙速度とドップラー偏移（Doppler S-Curve）
地上約 400〜600 km の地球低軌道（LEO）を周回する人工衛星は、秒速約 7.6 km（時速 27,000 km）の超高速で移動しています。  
電波の送信周波数を $f_0$、光速を $c$、観測者から衛星への相対位置ベクトルを $\vec{r}$、相対速度ベクトルを $\vec{v}$ とすると、受信周波数のドップラー偏移 $\Delta f$ は以下で表されます：

$$\Delta f = - f_0 \frac{v_r}{c} = - f_0 \frac{\vec{v} \cdot \vec{r}}{c \|\vec{r}\|}$$

| 記号 | 物理的意味 | 単位 |
| :--- | :--- | :--- |
| $f_0$ | 送信中心周波数（UHF帯: 例 435.000 MHz） | $\text{Hz}$ |
| $c$ | 真空中の光速（約 $3.0 \times 10^8$） | $\text{m/s}$ |
| $v_r = \frac{\vec{v} \cdot \vec{r}}{\|\vec{r}\|}$ | 観測者から見た衛星の視線速度（Line-of-Sight Velocity） | $\text{m/s}$ |
| $\Delta f$ | 受信周波数の偏移量（Doppler Shift） | $\text{Hz}$ |

- **AOS（信号捕捉 / 水平線から出現）直後**: 衛星が接近してくるため、$v_r < 0$ となり周波数が **約 +8〜10 kHz** 高く受信されます。
- **TCA（最接近時刻）**: 視線速度ベクトルがゼロ（進行方向が直角）になる瞬間、**ドップラー偏移が 0 Hz をクロス**（ゼロクロス点）します。
- **LOS（信号消失 / 水平線へ没入）直前**: 衛星が遠ざかるため、$v_r > 0$ となり周波数が **約 -8〜10 kHz** 低くなります。

この逆S字カーブについて、SGP4 軌道力学による理論予測と、RTL-SDR v4 の FFT パワースペクトル解析による実測ピーク周波数を Grafana でリアルタイムに可視化します。

```text
周波数偏移 Δf
  +10 kHz │  ╭───────────── (AOS: 接近中・高周波)
          │   \
          │    \
    0 Hz  ┼─────●────────── (TCA: 最接近・ゼロクロス)
          │      \
          │       \
  -10 kHz │        ╰─────── (LOS: 離脱中・低周波)
          └──────────────── 時刻 t
```

> **📚 関連リファレンス**:
> - プロジェクト内技術文書: [docs/qa/15_uhf_cubesat_doppler_tracking_and_containerd_pipeline.md](https://github.com/tozastation/radio-astronomy/blob/main/docs/qa/15_uhf_cubesat_doppler_tracking_and_containerd_pipeline.md)
> - SGP4 軌道計算: [CelesTrak: FAQs - Two-Line Element (TLE) Sets](https://celestrak.org/)
> - ドップラー効果の物理原理: [Wikipedia - ドップラー効果](https://ja.wikipedia.org/wiki/%E3%83%89%E3%83%83%E3%83%97%E3%83%A9%E3%83%BC%E5%8A%B9%E6%9E%9C)

### 2. アンテナ設置環境に合わせた「ベランダ視界フィルタ」
筆者の自宅ベランダは北側に面しているため、建物に遮られる南側の空を通過するパスでは電波を受信できません。  
そこで SGP4 予測器に北天方向の視界フィルタ（方位角 $270^\circ \to 360^\circ \to 90^\circ$、仰角 $\ge 10^\circ$）を組み込み、アンテナから見通せるパスのみを自動判定して SDR 受信機を起動するようにしています。

---

## 単一ノード同居環境でのトラブルシューティング

上記のように無事動作するまでに、1台のマシン上で k3s と KubeEdge を同居させたことで直面した 5 つの課題と、その調査・解決策をまとめます。

---

### 1. CRIソケット共有による Pod の再作成ループ
- **現象**: Pod を起動すると、数秒後に `Unknown` や `Terminating` になり、再作成と削除が繰り返される。
- **調査と原因**:
  - k3s の kubelet と KubeEdge の edged が、同一の `/run/k3s/containerd/containerd.sock` を参照していた。
  - k3s 側の kubelet は「自分の管理外のコンテナが containerd にいる（edged の Pod）」と判断してコンテナを GC 削除。
  - edged 側も「自分の管理外のコンテナがいる（k3s の Pod）」と判断して GC 削除。
  - 両者が互いの Pod を管理外コンテナと判断し、交互に GC 削除するループに陥っていた。
- **解決策**:
  - エッジ専用の `containerd-edge.service` を立ち上げ、ソケットを `/run/containerd-edge/containerd.sock` に分離。
  - `edgecore.yaml` の `runtimeType: "remote"`, `remoteRuntimeEndpoint: "unix:///run/containerd-edge/containerd.sock"` を指定してソケット競合を解消。

---

### 2. CNI ブリッジ名の重複衝突 (`cni0`)
- **現象**: エッジ側でコンテナを起動しようとすると、`failed to setup network: bridge cni0 already exists with different IP` でネットワーク設定が失敗。
- **調査と原因**:
  - k3s 側の Flannel CNI がすでに `cni0`（`10.42.0.1/24`）というブリッジを作成していた。
  - エッジ側の containerd がデフォルトの `/etc/cni/net.d/10-containerd-net.conflist` を読んだところ、そこにも `"bridge": "cni0"`（`10.42.3.1/24`）と定義されていたため、同一名で異なる CIDR を持つブリッジを作ろうとしてカーネルで衝突。
- **解決策**:
  - エッジ用の CNI 定義を `"bridge": "edge-cni0"` にリネームして分離。

---

### 3. iptables DNAT ルールによるローカル通信の転送
- **現象**: `edgecore` のログに `edged.go:660] internal error and status code: 400` が 50ms ごとに出力され、ノード初期化が終わらない。
- **調査と原因**:
  - KubeEdge 公式ドキュメントにある推奨ルール：
    ```bash
    sudo iptables -t nat -A OUTPUT -p tcp --dport 10350 -j DNAT --to 127.0.0.1:10003
    ```
    は本来、「別マシンの Cloud 側からエッジ宛て（10350）の通信を CloudStream トンネル（10003）に中継する」ためのもの。
  - 1台同居環境でこのルールを適用すると、`edged` 自身が自分自身の起動確認のために叩く `http://localhost:10350/healthz/syncloop`（HTTP 平文）まで CloudStream の HTTPS（TLS）ポート `10003` に転送されてしまう。
  - CloudStream は HTTPS ポートに平文 HTTP リクエストが到達したため `400 Bad Request` を返し、`edged` 側のヘルスチェックが失敗していた。
- **解決策**:
  - 1台同居環境ではこのルールは不要なため、ルールを削除して解決。

---

### 4. テイント欠落によるアドオン Pod のエッジ誤配置
- **現象**: `metrics-server` などのクラスタ管理 Pod が `gpd-pocket3-edge`（エッジ側）にスケジュールされ、正常に起動しない。
- **調査と原因**:
  - KubeEdge のエッジノードは軽量化されており、通常のワーカーノードのように API Server への直接アクセスや完全なクラスタ内部通信機能を持たない。
  - エッジノードにテイントが付いていないと、K8s スケジューラは通常のワーカーノードとみなして重要なコントロールプレーン系アドオンをエッジに配置してしまう。
- **解決策**:
  - エッジノードに `node-role.kubernetes.io/edge:NoSchedule` を付与。
  - エッジで動かしたい観測 Pod（`satellite-tracker` 等）にのみ明示的に `tolerations` を設定してワークロードを隔離。

---

### 5. edged による管轄外 Pod のボリューム誤削除ループ
- **現象**: `node-exporter` が `Exit Code: 137` で `CrashLoopBackOff` を繰り返す。Pod イベントには `open /var/lib/kubelet/pods/<UID>/etc-hosts: no such file or directory` と `Pod sandbox changed` が記録される。
- **調査と原因**:
  - `node-exporter` を control-plane ノード上に配置した際、同じマシンで root 権限で稼働している KubeEdge の `edgecore`（内蔵 edged）がローカルの `/var/lib/kubelet/pods` ディレクトリを巡回走査した。
  - `edgecore` は「この Pod は自分のエッジノード管轄外の不要な残骸（Kubernetes の自動削除対象である orphaned pod volumes）だ」と誤認。
  - `edgecore` のログ：
    ```text
    edgecore: Cleaned up orphaned pod volumes dir podUID="3816f16f..." path="/var/lib/kubelet/pods/3816f.../volumes"
    ```
  - k3s の kubelet が Pod を起動するそばから、edged が約2秒おきにそのマウントファイル（`etc-hosts` 等）を不要ファイルとして削除していた。
  - kubelet は足元のファイルが削除されたため「サンドボックス破損」と判定してプロセスを停止し、再作成ループに陥っていた。
- **解決策**:
  - 1台同居環境では、ホストファイルシステムを直接参照する DaemonSet が競合の原因になりやすい。
  - ノードメトリクスは `metrics-server`（k3s/edged の API 経由）で取得できているため、`node-exporter` は無効化（`enabled: false`）して解決。

---

## おわりに

1台の GPD Pocket3 上で k3s と KubeEdge を同居させて運用することで、システム内部の挙動について多くの知見が得られました。

- **CRI や CNI、ディレクトリパスの分離**: コンテナランタイムやファイルシステムの分離境界を理解する良い機会となりました。
- **エッジ端末のレジリエンス**: ネットワークが一時的に切断されてもエッジ端末での常時観測は継続でき、再接続時にテレメトリが同期されるというエッジ運用の利点を確認できました。

### 今後の予定

ベランダ電波観測所をさらに発展させるため、今後は以下のようなハードウェア・ソフトウェア両面の拡張に取り組んでいく予定です：

1. **アンテナ設備の強化と観測周波数帯の拡大**:  
   現在は UHF 帯（435MHz）を中心とした簡易アンテナですが、より広い周波数帯を捉えられる広帯域アンテナ（ディスコーン等）や、1.4GHz 帯（21cm 水素線）向けの専用アンテナの導入を検討し、観測対象を広げていきたいと考えています。
2. **エッジストレージの増設**:  
   長時間の連続観測データを余裕を持って蓄積できるよう、外部 SSD / NVMe などのストレージ増設やデータ保持ポリシーの設計を進めます。
3. **エッジでのノイズ除去とクラウド保存連携**:  
   エッジ側（EdgeCore）で生データから都市部特有の環境ノイズ（RFI）を DSP 処理で取り除き、軽量化・クレンジングしたスペクトルデータのみをクラウド側へ転送・長期保存するパイプラインを構築します。
4. **KubeEdge による分散観測体制への拡張**:  
   今回は 1 台同居での PoC でしたが、将来的にはベランダ直下のエッジ端末（EdgeCore）と宅内の分析マシン（CloudCore）を物理的に分離し、KubeEdge の特性を活かしたマルチノード分散観測基盤へと育てていきたいです。

最後まで読んでいただきありがとうございました。
