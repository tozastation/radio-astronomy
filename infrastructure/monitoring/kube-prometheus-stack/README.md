# 📊 kube-prometheus-stack 超軽量セットアップ手順 (GPD Pocket3)

本ディレクトリでは、GPD Pocket3（Ubuntu 26.04 LTS）上の k3s / KubeEdge 環境において、**スペック最低要件（合計メモリ 500MB〜800MB 程度）** で `kube-prometheus-stack` を導入する手順を定義します。

---

## 📚 公式一次情報・リファレンスリンク

- **Prometheus Community Helm Charts**: [https://github.com/prometheus-community/helm-charts](https://github.com/prometheus-community/helm-charts)
- **kube-prometheus-stack 公式リファレンス**: [https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- **Prometheus メモリ最適化ガイド**: [https://prometheus.io/docs/prometheus/latest/configuration/configuration/](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)

---

## 1. 前提条件 (Helm 3 の導入)

Helm が未導入の場合はインストールします：

```bash
# Helm 公式インストールスクリプト
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# バージョン確認
helm version
```

---

## 2. Helm リポジトリの追加

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

---

## 3. 超軽量構成でのデプロイ (`values-minimal.yaml`)

[values-minimal.yaml](values-minimal.yaml) を適用してインストールします：

```bash
# monitoring ネームスペースに超軽量設定でデプロイ
helm install prometheus prometheus-community/kube-prometheus-stack \
  -f values-minimal.yaml \
  --namespace monitoring \
  --create-namespace

# Pod 起動確認
kubectl get pods -n monitoring -o wide
```

---

## 4. Grafana ダッシュボードへのアクセス

`values-minimal.yaml` では、Grafana サービスを `NodePort: 30080` として公開しています。  
GPD Pocket3 上のブラウザから直接アクセス可能です：

- **URL**: `http://localhost:30080` (宅内LANの別PCから見る場合は `http://<GPD-IP>:30080`)
- **ユーザー名**: `admin`
- **初期パスワード**: `admin`

標準で「Kubernetes / Compute Resources / Cluster」や「Node Exporter / Use Method / Node」ダッシュボードが組み込まれており、CPU、メモリ、ディスク使用率、ネットワークI/Oを即座に監視できます。

---

## 5. メモリ消費量の確認 (SRE 検証)

起動後、各 Pod のメモリ消費量が計画値（Requests/Limits）内に収まっているか確認します：

```bash
kubectl top pods -n monitoring
```

期待される消費量の目安：
- `prometheus-operator`: 約 50〜80 MiB
- `prometheus-prometheus-kube-prometheus-prometheus-0`: 約 250〜350 MiB
- `prometheus-grafana`: 約 60〜100 MiB
- `prometheus-kube-state-metrics`: 約 30〜50 MiB
- `prometheus-prometheus-node-exporter`: 約 15〜25 MiB
- **合計**: **約 450〜600 MiB**（デフォルト設定の 2GB+ と比較して約 75% 削減）
