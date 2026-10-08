# ⚡ KubeEdge EdgeCore セットアップ手順 (GPD Pocket3)

本ディレクトリでは、GPD Pocket3（Ubuntu 26.04 LTS）上で KubeEdge **EdgeCore** を起動し、ローカルまたはクラスタの CloudCore にエッジノードとして登録する手順を定義します。

---

## 📚 公式一次情報・リファレンスリンク

- **KubeEdge EdgeCore 公式ドキュメント**: [https://kubeedge.io/docs/architecture/edge/edgehub/](https://kubeedge.io/docs/architecture/edge/edgehub/)
- **KubeEdge keadm join コマンド仕様**: [https://kubeedge.io/docs/setup/install-with-keadm/#join-nodes](https://kubeedge.io/docs/setup/install-with-keadm/#join-nodes)
- **MetaManager オフライン自律仕様**: [https://kubeedge.io/docs/architecture/edge/metamanager/](https://kubeedge.io/docs/architecture/edge/metamanager/)

---

## 1. コンテナランタイム (containerd) の準備

KubeEdge EdgeCore は Pod の起動に CRI（Container Runtime Interface）を使用します。

```bash
# containerd のインストール
sudo apt update
sudo apt install -y containerd

# cgroups v2 / systemd cgroup ドライバーの有効化
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null

# SystemdCgroup = true を設定
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/g' /etc/containerd/config.toml

# containerd の再起動と自動起動設定
sudo systemctl restart containerd
sudo systemctl enable containerd
```

---

## 2. EdgeCore の参加 (`keadm join`)

CloudCore 側で取得したトークン（`keadm gettoken`）を使用して、エッジノードをクラスタに参加させます。

```bash
# CloudCore のアドレス指定 (単一ホスト PoC の場合はホストIPまたは 127.0.0.1)
HOST_IP=$(hostname -I | awk '{print $1}')
TOKEN="<keadm gettoken で取得したトークン文字列>"

sudo keadm join \
  --cloudcore-ipport="${HOST_IP}:10000" \
  --token="${TOKEN}" \
  --cgroupdriver=systemd \
  --edgenode-name="gpd-pocket3-edge"

# EdgeCore サービスのステータス確認
sudo systemctl status edgecore
```

---

## 3. クラスタ側でのノード認識確認

Kubernetes 側からエッジノードが `Ready` として認識されているか確認します：

```bash
kubectl get nodes -o wide
```

期待される出力例：
```text
NAME                 STATUS   ROLES        AGE   VERSION
gpd-pocket3-cloud    Ready    control-plane 5m    v1.31.x+k3s1
gpd-pocket3-edge     Ready    agent,edge   1m    v1.21.0-kubeedge
```

これで、GPD Pocket3 上で CloudCore と EdgeCore の同居 PoC 環境が整いました。
エッジノード上で動作する Pod は、ローカルの SQLite（`/var/lib/kubeedge/edgecore.db`）にキャッシュされるため、k3s が停止しても自律稼働を継続します。
