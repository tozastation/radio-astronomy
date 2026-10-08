# ☁️ KubeEdge CloudCore セットアップ手順 (GPD Pocket3)

本ディレクトリでは、GPD Pocket3（Ubuntu 26.04 LTS）上で Kubernetes（k3s）コントロールプレーンおよび KubeEdge **CloudCore** を初期化する手順を定義します。

---

## 📚 公式一次情報・リファレンスリンク

- **k3s 公式ドキュメント**: [https://docs.k3s.io/](https://docs.k3s.io/)
- **KubeEdge keadm クイックスタート**: [https://kubeedge.io/docs/setup/install-with-keadm/](https://kubeedge.io/docs/setup/install-with-keadm/)
- **KubeEdge CloudCore 設定仕様**: [https://kubeedge.io/docs/architecture/cloud/cloudhub/](https://kubeedge.io/docs/architecture/cloud/cloudhub/)

---

## 1. k3s (軽量 Kubernetes) のインストール

GPD Pocket3 のメモリを節約するため、Traefik や ServiceLB を無効化した最小構成で k3s をインストールします。

```bash
# 1. 設定ディレクトリ作成と設定ファイルの配置
sudo mkdir -p /etc/rancher/k3s
sudo cp k3s-config.yaml /etc/rancher/k3s/config.yaml

# 2. k3s インストールスクリプト実行 (軽量インストール)
curl -sfL https://get.k3s.io | sh -

# 3. ノードおよび Pod の健全性確認
sudo kubectl get nodes -o wide
sudo kubectl get pods -A
```

`~/.kube/config` で一般ユーザーから操作できるように設定します：

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config
```

---

## 2. keadm (KubeEdge 管理 CLI) のインストール

KubeEdge 公式インストーラ `keadm` をダウンロードします：

```bash
# KubeEdge バージョンの指定 (最新安定版 v1.21.0 等)
export KUBEEDGE_VERSION="v1.21.0"
export ARCH="amd64"

curl -sSL "https://github.com/kubeedge/kubeedge/releases/download/${KUBEEDGE_VERSION}/keadm-${KUBEEDGE_VERSION}-linux-${ARCH}.tar.gz" -o keadm.tar.gz
tar -zxvf keadm.tar.gz
sudo mv "keadm-${KUBEEDGE_VERSION}-linux-${ARCH}/keadm/keadm" /usr/local/bin/
rm -rf keadm.tar.gz "keadm-${KUBEEDGE_VERSION}-linux-${ARCH}"

# バージョン確認
keadm version
```

---

## 3. CloudCore の初期化 (`keadm init`)

`keadm init` を実行して CloudCore コンポーネントを Kubernetes 上にデプロイします。

```bash
# 単一ホスト PoC の場合、CloudCore の広告アドレスにホスト IP または 127.0.0.1 を指定
# 宅内LAN IP を取得 (例: 192.168.1.xxx)
HOST_IP=$(hostname -I | awk '{print $1}')

sudo keadm init --advertise-address="${HOST_IP}" --profile version=${KUBEEDGE_VERSION} --kube-config=$HOME/.kube/config

# CloudCore Pod の起動確認
kubectl get pods -n kubeedge -l k8s-app=kubeedge -o wide
```

---

## 4. Edge ノード参加トークンの取得

EdgeCore が CloudCore に接続・登録するために必要な暗号化トークンを取得します：

```bash
# トークンの出力 (EdgeCore 参加時に使用)
keadm gettoken --kube-config=$HOME/.kube/config
```

出力されたトークン文字列を控え、[../edge/README.md](../edge/README.md) の手順に進みます。
