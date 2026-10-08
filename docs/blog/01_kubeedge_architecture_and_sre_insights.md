# 📝 技術ブログ用ネタ帳 & SRE 解説: KubeEdge アーキテクチャ徹底解剖

本ドキュメントは、ベランダ電波天文学プロジェクトにおいて実際に構築・議論された、**「SRE・プラットフォームエンジニア目線での KubeEdge アーキテクチャの真価とハマりどころ」** をブログ記事（Zenn / Qiita / 個人ブログ等）としてそのまま執筆・公開できるように体系化した素材・解説集です。

---

## 🎯 タイトル案
- **SRE目線で解剖する KubeEdge: なぜエッジに Kubernetes を持ち込むのか？クラスタ数・Custom Controller・iptables・Local-First の真実**
- **「ベランダのアンテナ直下」を Kubernetes で管理する：KubeEdge 内部通信の解剖と 1台PoC 構築記**

---

## 📖 導入 (Introduction)

Raspberry Pi や UMPC（GPD Pocket3）などを使ってベランダで電波天文学・SDR（ソフトウェアラジオ）観測基盤を作ろうとすると、従来の IoT 開発では「端末に SSH して Docker Compose や systemd を手動再起動し、ログを tail する」という泥臭い運用になりがちだった。

これを **「いつもの `kubectl` や `k9s`、Prometheus、Grafana、GitOps で一元管理したい」**。
そこで CNCF のエッジコンピューティング基盤 **KubeEdge** を採用した。

しかし、実際に触ってみると「Kubernetes と何が違うのか？」「なぜ k3s が出てくるのか？」「内部でどうパケットが流れているのか？」という疑問が次々と湧き出た。本記事では、SRE の視点からその内部構造を解剖する。

---

## 💡 SRE 的疑問とアーキテクチャの真実 (Q&A)

### Q1. 「KubeEdge って k3s 使うの？ クラスタが2個あるってこと？」
> **A. いいえ、Kubernetes クラスタは「たった1個」です！**

- **誤解**: Cloud 側に1個、Edge 側にもう1個クラスタがあって、クラスタ間連携（マルチクラスタ / フェデレーション）している？
- **真実**:
  - `kubectl get nodes` を叩くと、Cloud ノード（master）と Edge ノード（agent, edge）が**同一クラスタ内のノード一覧**として並ぶ。
  - Cloud 側に Kubernetes のコントロールプレーン（API Server や etcd）が必要であり、その軽量バックエンドとして **k3s** を選定している。
  - **Edge 側には k3s も API Server も etcd も存在しない**。Kubelet を超軽量化した `edgecore` デーモンが1個動いているだけ。

---

### Q2. 「Edge のコンポーネントは Cloud 側の Custom Controller とやり取りしてるの？」
> **A. 完全にその通り！CloudCore の実体は API Server を watch する Custom Controller 群の集約です。**

- **内部構造**:
  - `EdgeController`: Pod / ConfigMap / Secret を watch し、エッジ宛ての変更をフィルタリング。
  - `DeviceController`: IoT 機器（RTL-SDR やアンテナ設備）を抽象化する CRD（`Device`）を watch。
- **なぜ直接 API Server / etcd を叩かせないのか？**:
  - 通常の Kubelet のように何百・何千台のエッジが貧弱な回線越しに etcd や API Server を直接叩くと、**Thundering Herd（接続爆発）** でクラスタが即死する。
  - CloudCore が API Server との通信を一手に集約し、エッジへは **WebSocket 1本で差分イベントだけを圧縮配信** している。

---

### Q3. 「CloudStream と EdgeStream って何？ 通信断のキュー？ ただのトンネル？」
> **A. キューイングは一切しない「ただのリアルタイム直通リバーストンネル」です！**

KubeEdge は通信の性質によって、完全に異なる2系統のパイプラインを使い分けている：

| パイプライン | 担当コンポーネント | 性質 | 通信断（オフライン時） | 主な用途 |
| :--- | :--- | :--- | :--- | :--- |
| **メッセージ系 (Control Plane)** | **CloudHub $\leftrightarrow$ EdgeHub** | 非同期メッセージバス (Kafka 的) | **◎ 完全対応** (ローカル SQLite に保存し復帰時差分同期) | Pod 作成/削除、ConfigMap、実行状態報告 |
| **ストリーム系 (Data Plane)** | **CloudStream $\leftrightarrow$ EdgeStream** | リバーストンネル (SSH `-R` 的) | **✕ キューなし** (回線断時は即エラー) | `kubectl logs`、`kubectl exec`、Prometheus スクレイプ |

> **直感イメージ**:
> `kubectl exec` でシェルを叩いている最中に回線が切れたら、コマンドがキューに溜まって1時間後に勝手に実行されたら大事故になる（即座にエラーになるべき）。だからストリーム系はキューを持たない。

---

### Q4. 「なぜ iptables DNAT でパケットを曲げているのか？ 設定変更じゃダメなの？」
> **A. Kubernetes API Server のハードコード仕様と、Prometheus 標準 Helm Chart を無改造で使うための Netfilter ハックです。**

```bash
sudo iptables -t nat -A OUTPUT -p tcp --dport 10350 -j DNAT --to 127.0.0.1:10003
```

- **なぜ必要なのか？**:
  - Prometheus や `metrics-server` は、エッジノードの cAdvisor を取りに行く際、ポート `10350` へパケットを投げる。
  - しかし Cloud 側で待っているのは CloudStream のポート `10003`。
- **なぜ設定変更ではなく iptables なのか？**:
  - Kubernetes API Server に「このノード宛ては動的にポート 10003 へプロキシする」という設定口が存在しない。
  - `kube-prometheus-stack` などの標準 Helm Chart を改造するとメンテ不能になる。
  - **「上位ツールには普通の Kubelet と直接通信していると錯覚させ、カーネル（Netfilter）で透過的にトンネルへねじ込む」** のが最も安全。
  - そもそも Kubernetes の `kube-proxy` 自体が ClusterIP のルーティングを iptables でパケットを捻じ曲げて実現しており、その哲学を踏襲している。

---

### Q5. 「現代の Local-First アーキテクチャのトレンドと完全に一致している」

クラウド全盛期の「何でもクラウドに集める」から、エッジの物理制約（2.4MSPSの電波データは日量800GBでクラウドへ送れない、Wi-Fi瞬断）に直面した結果：
- **Cloudflare Tunnel (`cloudflared`) / Tailscale**: インバウンド開放なし、アウトバウンドトンネル1本集約。
- **Turso (libSQL) / PocketBase**: エッジ手元に SQLite を置き、通信断でも自律稼働。
- **KubeEdge**: エッジ内 SQLite（MetaManager）による Local-First 自律 ＋ 中央 Kubernetes による宣言的統括。

> **「重いデータ処理と生存はエッジで完結させ、ガバナンスと監視だけを中央から宣言的に行う」** という現代の分散システムのベストプラクティスがここに凝縮されている。

---

## 🛠️ まとめ & 実機でのノード開通

```bash
$ kubectl get nodes -o wide
NAME                   STATUS     ROLES           AGE   VERSION                     CONTAINER-RUNTIME
tozastation-g1621-02   Ready      control-plane   26m   v1.36.5+k3s1                containerd://2.3.4-k3s1.36
gpd-pocket3-edge       NotReady   agent,edge      16m   v1.31.12-kubeedge-v1.22.0   containerd://2.3.4-k3s1.36
```
エッジ端末が Kubernetes クラスタの 1 ノードとして認識された瞬間、インフラエンジニアとしての感動がある。
次回は、このエッジノード上で RTL-SDR v4 を USB パススルー制御し、UHF 435MHz CubeSat のドップラーS字カーブを Grafana に描画するまでを解説する。
