# 🛠️ KubeEdge 基本コンポーネント デプロイ＆セットアップガイド

本ドキュメントは、GPD Pocket3（Ubuntu 26.04 LTS）上で k3s（CloudCore）と Edged（EdgeCore）を稼働させ、RTL-SDR v4 を用いた電波観測 Pod を実行するための KubeEdge セットアップ手順書です。

将来的に分析専用PCを追加した「物理2台分離構成」へ移行する際も、同一のコマンド体系と手順でそのままスケールアウト可能です。

---

## 1. 前提環境と一次情報リンク (Prerequisites & References)

### 1.1 一次情報・公式リファレンス
- **KubeEdge 公式サイト**: [https://kubeedge.io/](https://kubeedge.io/)
- **KubeEdge GitHub リポジトリ**: [https://github.com/kubeedge/kubeedge](https://github.com/kubeedge/kubeedge)
- **keadm インストールガイド**: [https://kubeedge.io/docs/setup/keadm_command/](https://kubeedge.io/docs/setup/keadm_command/)
- **k3s プライベートレジストリ設定**: [https://docs.k3s.io/installation/private-registry](https://docs.k3s.io/installation/private-registry)
- **RTL-SDR Blog V4 公式ユーザーズガイド**: [https://www.rtl-sdr.com/rtl-sdr-blog-v4-users-guide/](https://www.rtl-sdr.com/rtl-sdr-blog-v4-users-guide/)

### 1.2 ホスト環境
- **OS**: Ubuntu 26.04 LTS (x86_64)
- **コンテナランタイム**: k3s 内蔵 CRI `containerd` (`/run/k3s/containerd/containerd.sock`)
- **SDR ハードウェア**: RTL-SDR Blog V4（USB接続）

---

## 2. 事前準備 (Host Preparation)

### 2.1 RTL-SDR の OS 設定（udev ルール & ドライバ無効化）

Linux カーネル標準の DVB-T（地上デジタルTV）ドライバが RTL-SDR を専有するのを防ぎ、SDR として一般ユーザーやコンテナからアクセスできるように設定します。

```bash
# 1. カーネル標準 DVB-T ドライバのブラックリスト化
sudo tee /etc/modprobe.d/blacklist-rtl.conf << 'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF

# 2. RTL-SDR Blog V4 用 udev ルールの作成（アクセス権付与）
sudo tee /etc/udev/rules.d/rtl-sdr.rules << 'EOF'
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE:="0666"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE:="0666"
EOF

# 3. udev ルールの再読み込み
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 2.2 k3s containerd のローカルレジストリ信頼設定

k3s の containerd がクラスタ内のローカルレジストリ（`localhost:5000` / `127.0.0.1:5000`）と平文 HTTP 通信できるように、**k3s インストール前に** 設定ファイルを事前配置します。

```bash
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml << 'EOF'
mirrors:
  "localhost:5000":
    endpoint:
      - "http://127.0.0.1:5000"
  "127.0.0.1:5000":
    endpoint:
      - "http://127.0.0.1:5000"
EOF
```

### 2.3 k3s Server（軽量 Kubernetes コントロールプレーン）のインストール

CloudCore が接続するバックエンドとして、軽量 Kubernetes である k3s Server をインストールします。
エッジ環境でのリソース（メモリ・CPU）消費を最小限に抑えるため、今回は不要なデフォルトアドオン（Traefik Ingress Controller や ServiceLB）を無効化（`--disable`）して起動します。

- **公式一次情報**: [k3s Quick-Start Guide (k3s.io)](https://docs.k3s.io/quick-start)

```bash
# 1. k3s Server のインストール
# --write-kubeconfig-mode=644 により一般ユーザーでも kubectl が利用可能になります
curl -sfL https://get.k3s.io | sh -s - server \
  --disable=traefik \
  --disable=servicelb \
  --write-kubeconfig-mode=644

# 2. サービス稼働状態の確認
sudo systemctl status k3s

# 3. 一般ユーザー環境への kubeconfig 設定
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config
export KUBECONFIG=~/.kube/config

# 4. ノード状態の確認
kubectl get nodes
```

> **将来の分離時やリセット用（参考）**:
> 将来分析PC（Ubuntu）を追加して GPD Pocket3 から k3s を切り離す際、または最初からやり直す際は、以下の公式スクリプトで完全にアンインストール可能です。
> ```bash
> /usr/local/bin/k3s-uninstall.sh
> ```

### 2.4 keadm CLI ツールのインストール

KubeEdge 公式の管理 CLI である `keadm` をダウンロードして配置します。

```bash
# 最新安定版バージョン（例: v1.18.0 等）を指定
export KUBEEDGE_VERSION="v1.18.0"
curl -sSL "https://github.com/kubeedge/kubeedge/releases/download/${KUBEEDGE_VERSION}/keadm-${KUBEEDGE_VERSION}-linux-amd64.tar.gz" -o keadm.tar.gz
tar -zxvf keadm.tar.gz
sudo cp "keadm-${KUBEEDGE_VERSION}-linux-amd64/keadm/keadm" /usr/local/bin/keadm
rm -rf keadm.tar.gz "keadm-${KUBEEDGE_VERSION}-linux-amd64"

# バージョン確認
keadm version
```

---

## 3. CloudCore の初期化 (Cloud Setup)

k3s Server 側で CloudCore を初期化し、エッジノード参加用の認証トークンを発行します。

```bash
# 1. CloudCore の初期化（k3s クラスタの kubeconfig を指定）
sudo keadm init --advertise-address="127.0.0.1" \
  --kubeconfig="/etc/rancher/k3s/k3s.yaml"

# 2. CloudCore Pod が running になったことを確認
kubectl get pods -n kubeedge -l k8s-app=kubeedge -o wide

# 3. EdgeCore 参加用トークンを取得（後ほど EdgeCore で使用）
sudo keadm gettoken --kubeconfig="/etc/rancher/k3s/k3s.yaml"
# 出力例: 9a7b5... (このトークン文字列をコピーしておく)
```

---

## 4. EdgeCore のセットアップ (Edge Setup)

GPD Pocket3（Edged）側で EdgeCore を起動し、k3s クラスタへエッジノードとして参加させます。

```bash
# トークンを環境変数に設定（先ほど取得した文字列）
export EDGE_TOKEN="<先ほど取得したトークン>"

# EdgeCore の参加
# ※ containerd ソケットとして k3s 内蔵 containerd を指定
sudo keadm join \
  --cloudcore-ipport="127.0.0.1:10000" \
  --token="${EDGE_TOKEN}" \
  --runtimetype="remote" \
  --remote-runtime-endpoint="unix:///run/k3s/containerd/containerd.sock" \
  --edgename="gpd-pocket3-edge"

# EdgeCore サービス（systemd）の稼働確認
sudo systemctl status edgecore
```

### 4.1 Edged 10350 ポートのメトリクス有効化

Prometheus からコンテナ別 CPU/メモリ使用量を取得するため、`/etc/kubeedge/config/edgecore.yaml` を確認・調整します。

```yaml
edged:
  cadvisorInterface: ""
  cgroupDriver: systemd
  cgroupsPerQOS: true
  enableMetricsServer: true  # true に設定されていることを確認
```

設定変更後は EdgeCore を再起動します：
```bash
sudo systemctl restart edgecore
```

---

## 5. 疎通確認と検証 (Verification)

### 5.1 ノード状態の確認

```bash
kubectl get nodes -o wide
```
**期待される出力**:
```
NAME                 STATUS   ROLES    AGE   VERSION
tozastation-G1621-02 Ready    master   ...   v1.30.x+k3s1
gpd-pocket3-edge     Ready    agent    ...   v1.18.0-kubeedge
```
`gpd-pocket3-edge` ノードが `Ready,agent` として認識されていれば、エッジコントロールプレーンの開通成功です。

### 5.2 Edged メトリクスの疎通テスト

エッジノードの cAdvisor / リソースメトリクスエンドポイント（ポート 10350）が応答するか確認します。

```bash
curl -s http://127.0.0.1:10350/metrics/cadvisor | head -n 15
```

### 5.3 CloudStream トンネル経由のログ・コマンド実行テスト

KubeEdge のリバーストンネル（CloudStream）が正常に機能しているか、エッジ上のテスト Pod で確認します。

```bash
# エッジ上に nginx テスト Pod を配置
kubectl run test-edge --image=nginx:alpine --overrides='{"spec": {"nodeSelector": {"node-role.kubernetes.io/edge": ""}}}'

# 起動確認
kubectl get pods -o wide

# CloudCore 側からエッジ Pod のログを取得（CloudStream 経由）
kubectl logs test-edge

# テスト完了後に削除
kubectl delete pod test-edge
```

---

## 6. トラブルシューティング (Troubleshooting)

| 現象 | 主な原因 | 対処方法 |
| :--- | :--- | :--- |
| `keadm join` で接続拒否 | CloudCore の `10000` 番ポートが開いていない | `sudo netstat -tlpn \| grep 10000` で CloudCore がリスンしているか確認。ファイアウォール（ufw）の設定を確認。 |
| ノードが `NotReady` のまま | containerd ソケットパスの不一致 | `unix:///run/k3s/containerd/containerd.sock` が存在するか確認。`journalctl -u edgecore -f` でエラーログを確認。 |
| RTL-SDR へのアクセス拒否 | udev ルール未反映またはカーネルドライバ干渉 | `lsusb` で RTL-SDR が認識されているか確認。`sudo rmmod dvb_usb_rtl28xxu` を実行して DVB-T ドライバを強制アンロード。 |
