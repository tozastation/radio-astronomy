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

### Q6. 「1台同居環境（PoC）で全PodがCrashLoopBackOffに！ 何が起きたのか？」
> **A. k3s の kubelet と KubeEdge の edged が同一 containerd ソケット（namespace `k8s.io`）を共有したことによる「Pod Sandbox 殺し合いループ（GC デッドロック）」です！**

- **遭遇した現象**:
  - `edgecore` を起動した直後、`kubectl get nodes` で `CIDRAssignmentFailed` が記録され、ノードが `NotReady`（`NodeStatusUnknown`）に転落。
  - 同時にクラスタ内の全 Pod（`coredns`, `metrics-server`, `cloudcore`）が一斉に `CrashLoopBackOff` や `Completed`（再起動）を数秒おきに繰り返す異常事態に。
- **SRE 的深掘りとメカニズム**:
  1. k3s 側の kubelet は `/run/k3s/containerd/containerd.sock` を見て自ノード（`tozastation-g1621-02`）の Pod を管理している。
  2. KubeEdge 側の edged（kubelet 互換デーモン）も設定ミスで同じ `/run/k3s/containerd/containerd.sock` を見に行っていた。
  3. CRI 仕様上、双方が同じ `k8s.io` namespace のコンテナ一覧を取得する。
  4. すると、k3s 側の kubelet は「自分のノードに割り当てていない謎の Pod/Sandbox」をゴミと判断して削除（Kill）する。
  5. 逆に edged 側も「自分のエッジノードに割り当てていない謎の Pod/Sandbox」をゴミと判断して削除（Kill）する。
  6. **結果、双方が相手の Pod Sandbox を「不要なゴミ」と判定して無限に殺し合う（Pod Sandbox Churn）デッドロックが発生！**
  7. この巻き添えで `cloudcore` が死に、エッジとクラウドの WebSocket が切断され、ノードがハートビート途絶（`NotReady`）に陥った。
- **得られた知見（ベストプラクティス）**:
  - 1台の Linux マシン上で複数の Kubelet（または Kubelet と EdgeCore）を動かす場合、**CRI ランタイム（containerd デーモン・ソケット・ストレージ）は絶対に共用してはならず、完全に分離しなければならない**。
  - エッジ専用の `containerd-edge.service`（`/run/containerd-edge/containerd.sock`）を立てることで、ホストの既存 Docker や k3s に一切干渉しない堅牢な 1 台完結 PoC が実現できる。

### Q7. 「CNI ブリッジ名が競合！ `cni0 already has an IP address different from ...`」
> **A. k3s 既存の `cni0`（10.42.0.1/24）とエッジの PodCIDR（10.42.3.0/24）が同一ブリッジ名を奪い合っていたためです！**

- **遭遇した現象**:
  - `containerd-edge` を起動したものの、Pod が `ContainerCreating` のまま進まず、`kubectl describe node` に `cni plugin not initialized` が記録される。
  - edgecore ログに `plugin type="bridge" failed (add): failed to set bridge addr: "cni0" already has an IP address different from 10.42.3.1/24` が大量出力。
- **SRE 的深掘りとメカニズム**:
  - k3s 側の Flannel CNI がホスト上にすでに `cni0`（IP: `10.42.0.1/24`）を作成していた。
  - エッジ用の CNI 設定（`/etc/cni/net.d/10-containerd-net.conflist`）でも同じブリッジ名 `"bridge": "cni0"` を指定したため、Linux カーネルのネットワークスタックが「同一ブリッジに異なるサブネット（`10.42.3.1/24`）を共存させられない」と拒絶した。
- **解決策**:
  - エッジ用 CNI 設定のブリッジ名を `"bridge": "edge-cni0"` にリネーム。
  - k3s 側のネットワークブリッジとエッジ側のネットワークブリッジを物理的に分離することで、綺麗に IP が払い出されるようになった。

---

### Q8. 「公式推奨の iptables DNAT ルールを入れたら、エッジが `400 Bad Request` で自爆した話」
> **A. 「1台同居環境」では、エッジ自身のローカルヘルスチェックまでトンネルに曲げられてしまう Self-loopback DNAT トラップ！**

- **遭遇した現象**:
  - `edgecore` のログに `edged.go:660] internal error and status code: 400` が 50ms ごとに無限に出力され、ノード初期化が完了しない。
- **SRE 的深掘りとメカニズム**:
  - KubeEdge 公式ドキュメントの推奨ルール：
    ```bash
    sudo iptables -t nat -A OUTPUT -p tcp --dport 10350 -j DNAT --to 127.0.0.1:10003
    ```
    は本来、**「別マシンの Cloud 側からエッジ宛て（10350）の通信を CloudStream トンネル（10003）に中継する」** ためのもの。
  - しかし 1台同居環境でこのルールを無邪気に入れると、`edged` 自身が自分自身の起動確認のために叩く `http://localhost:10350/healthz/syncloop`（HTTP 平文）まで CloudStream の HTTPS（TLS）ポート `10003` に強制転送されてしまう。
  - CloudStream は「HTTPS ポートに HTTP リクエストが来た」ので当然 `400 Bad Request` を返し、`edged` はヘルスチェック失敗とみなして永遠に起動完了しなかった。
- **解決策**:
  - 1台同居環境ではそもそも不要。もし適用する場合でも、`-d <エッジIP>` に限定し `localhost (127.0.0.1)` を絶対に曲げないように除外する。

---

### Q9. 「なぜエッジノードに `NoSchedule` テイントが必須なのか？（Pod 誤配置事故）」
> **A. テイントがないと、k3s のコントロールプレーン系アドオン（metrics-server 等）がエッジノードに流れてきて自滅する！**

- **遭遇した現象**:
  - `metrics-server` がエッジノード `gpd-pocket3-edge` にスケジュールされ、`CrashLoopBackOff` で死亡。
- **SRE 的深掘りとメカニズム**:
  - KubeEdge のエッジノードは軽量化された別環境（API Server 直結ではない、CNI トポロジーが異なる等）。
  - 一般的なクラスタ管理 Pod や k3s 内蔵アドオンはエッジノード上で動くことを想定していない。
  - エッジノードにテイントが付いていないと、K8s スケジューラは「空いている通常ノード」とみなして重要 Pod をエッジに配置してしまう。
- **解決策**:
  - エッジノードに `node-role.kubernetes.io/edge:NoSchedule` を付与。
  - エッジで動かしたい業務 Pod（`satellite-tracker` 等）にのみ `tolerations` を明記して、明確にワークロードを隔離する。

---

### Q10. 「non-root Pod と emptyDir の権限罠（`metrics-server` の panic）」
> **A. `runAsUser: 1000` なのに `fsGroup: 1000` がなく、`/tmp` への自己署名証明書書き込みが Permission Denied に！**

- **遭遇した現象**:
  - `metrics-server` が `panic: error creating self-signed certificates: open /tmp/apiserver.crt: permission denied` でクラッシュ。
- **SRE 的深掘りとメカニズム**:
  - コンテナは非 root（`runAsUser: 1000`）で動作。
  - `/tmp` には `emptyDir` ボリュームがマウントされていたが、Pod レベルの `securityContext.fsGroup` が未定義だったため、マウントディレクトリの所有権が `root:root (0755)` のままになり、一般ユーザーから書き込めなかった。
- **解決策**:
  - `pod.spec.securityContext.fsGroup: 1000` を付与し、さらにエッジノードの cAdvisor スクレイプ用に `--kubelet-insecure-tls` を追加して完全解決。

---

### Q11. 「巨大な Prometheus Operator CRD で `kubectl apply` が 256KB 制限で爆死する話」
> **A. `kubectl apply` はマニフェスト全文をアノテーションに保存しようとするため、Server-Side Apply (`--server-side`) が必須！**

- **遭遇した現象**:
  - `helm show crds prometheus-community/kube-prometheus-stack | kubectl apply -f -` を実行したところ、以下のエラーで失敗：
    ```text
    CustomResourceDefinition ... "prometheuses.monitoring.coreos.com" is invalid:
    metadata.annotations: Too long: may not be more than 262144 bytes
    ```
- **SRE 的深掘りとメカニズム**:
  - `kubectl apply` (Client-Side Apply) は、前回の適用状態との差分（3-way merge）を計算するために、`kubectl.kubernetes.io/last-applied-configuration` というアノテーションにマニフェスト JSON 全文を書き込む。
  - しかし Kubernetes の etcd/API Server において、アノテーション 1 つの最大容量は **262,144 bytes（256KB）**。
  - 近年の Prometheus Operator の CRD（OpenAPI v3 スキーマ定義）は非常にリッチで巨大なため、この 256KB 制限をあっさり超過して弾かれてしまう。
- **解決策**:
  - **Server-Side Apply (SSA)** を利用する：
    ```bash
    kubectl apply --server-side -f -
    ```
  - SSA はアノテーションではなく API Server 側のフィールド管理機構（`managedFields`）で差分を追跡するため、256KB 制限に引っかかることなく巨大な CRD も一撃で適用できる。

---

### Q12. 「Prometheus Operator の `spec.retentionSize` バリデーション罠（`2Gi` vs `2GiB`）」
> **A. Kubernetes のリソース単位 `2Gi` を書くと、Prometheus の正規表現バリデーションで弾かれる！**

- **遭遇した現象**:
  - Helmfile apply 時に以下のエラーで適用失敗：
    ```text
    spec.retentionSize in body should match '(^0|([0-9]*[.])?[0-9]+((K|M|G|T|E|P)i?)?B)$'
    ```
- **SRE 的深掘りとメカニズム**:
  - Kubernetes の PVC や Pod リソース（CPU/メモリ）では `2Gi`（2 Gibibytes）と書くのが標準。
  - しかし Prometheus 本体の TSDB フラグ（`--storage.tsdb.retention.size`）は末尾に **`B`**（Bytes）を要求する仕様（例: `2GB`, `2GiB`）。
  - Prometheus Operator の CRD OpenAPI スキーマでも厳格に末尾 `B` を強制する正規表現が設定されているため、`2Gi` だとバリデーションに失敗する。
- **解決策**:
  - `retentionSize: "2GiB"` のように、明示的に `B` を付与して記述する。

---

### Q13. 「KubeEdge 1台同居環境における edged の『孤児ボリューム誤判定・削除ループ』」
> **A. エッジノード上の edged が、control-plane 側 Pod のボリュームを『孤児』とみなして2秒おきに物理削除していた！**

- **遭遇した現象**:
  - `node-exporter` が `Exit Code: 137` で `CrashLoopBackOff` を繰り返す。
  - Pod イベントには `Error: open /var/lib/kubelet/pods/<UID>/etc-hosts: no such file or directory` と `Pod sandbox changed, it will be killed and re-created.` が無限に記録される。
- **SRE 的深掘りとメカニズム**:
  - `node-exporter` を control-plane ノード上に配置した際、同じ物理ホスト上で root 権限で稼働している KubeEdge の `edgecore`（内蔵 edged）がローカルの `/var/lib/kubelet/pods` ディレクトリを走査した。
  - `edgecore` は「この Pod UID はエッジノード（`gpd-pocket3-edge`）に割り当てられた Pod ではない（＝孤児ボリューム orphaned pod volumes だ！）」と誤認。
  - `edgecore` ログ：
    ```text
    edgecore: Cleaned up orphaned pod volumes dir podUID="3816f16f..." path="/var/lib/kubelet/pods/3816f.../volumes"
    ```
  - kubelet が Pod を起動するそばから、edged が 2 秒おきにそのボリューム（etc-hosts 等）を消し去るため、kubelet 側でサンドボックス破損とみなされて再作成ループに陥っていた。
- **解決策**:
  - 1台同居 PoC 環境において、ホスト直結の DaemonSet（特に `/var/lib/kubelet` 周辺に依存するもの）を無理に動かすのはアンチパターン。
  - ノードメトリクスは `metrics-server`（k3s/edged の API 経由）で完全に取得できているため、`node-exporter` は無効化（`enabled: false`）し、リソース消費も節約した。

---

## 🏆 結論：超軽量・完全自律型 電波天文学 KubeEdge クラスタの完成

すべての障害を論理的に解明・克服した結果、GPD Pocket3（メモリ 16GB / Ubuntu 26.04）単一端末上で、以下のスタックが完全自律稼働を達成した：

```bash
$ kubectl get pods -A
NAMESPACE            NAME                                                        READY   STATUS    AGE
container-registry   local-registry-9d959d85f-j2656                              1/1     Running   35m
default              satellite-tracker-59994966b5-vlzjp                          1/1     Running   30m
kube-system          coredns-7cfb7bc9c7-2rbxd                                    1/1     Running   122m
kube-system          local-path-provisioner-77b9867795-wbdkx                     1/1     Running   122m
kube-system          metrics-server-6f58cdc499-mzswn                             1/1     Running   26m
kubeedge             cloud-iptables-manager-nxww4                                1/1     Running   116m
kubeedge             cloudcore-7499476549-t29qs                                  1/1     Running   116m
monitoring           kube-prometheus-stack-grafana-79d88bc745-4d5cz              3/3     Running   8m
monitoring           kube-prometheus-stack-kube-state-metrics-687686d88b-64xr8   1/1     Running   10m
monitoring           kube-prometheus-stack-operator-bdbf4977d-jxftd              1/1     Running   10m
monitoring           prometheus-kube-prometheus-stack-prometheus-0               2/2     Running   9m
```

```bash
$ kubectl top pods -n monitoring
NAME                                                        CPU(cores)   MEMORY(bytes)   
kube-prometheus-stack-grafana-79d88bc745-4d5cz              17m          382Mi           
kube-prometheus-stack-kube-state-metrics-687686d88b-64xr8   2m           23Mi            
kube-prometheus-stack-operator-bdbf4977d-jxftd              15m          30Mi            
prometheus-kube-prometheus-stack-prometheus-0               11m          335Mi           
```

監視基盤全体のメモリ消費量は **わずか約 770MB**。
エッジノード上で動く **`satellite-tracker`** が RTL-SDR v4 から吸い上げた観測データ（SGP4 軌道追尾・ドップラー偏移・RSSI）は、Prometheus によってリアルタイムに収集され、Grafana（`http://localhost:30080/d/satellite-radio-tracker/c97e60f`）上に美しく可視化されている。

分散エッジシステムの真骨頂は、「現場のエッジノードが壊れても自律的に観測を続け、クラウドと再接続した瞬間にテレメトリを同期・統合できる」レジリエンスにある。この 1台 PoC 環境の完成により、将来のマルチノード分散観測所展開への盤石な基盤が整った！
