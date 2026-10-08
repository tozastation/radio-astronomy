# 🌐 KubeEdge 自律観測インフラ構成 (GPD Pocket3)

本ディレクトリでは、GPD Pocket3（Ubuntu 26.04 LTS）上で動作させる CNCF **KubeEdge** のインフラ構成管理および設定ファイルを配置します。

---

## 📁 ディレクトリ構成

```text
infrastructure/kubeedge/
├── cloud/                          # Kubernetes (k3s) & KubeEdge CloudCore
│   ├── README.md                   # k3s インストール & keadm init 手順
│   └── k3s-config.yaml             # k3s 軽量化設定 (Traefik/ServiceLB無効化)
└── edge/                           # KubeEdge EdgeCore
    ├── README.md                   # containerd 導入 & keadm join 手順
    └── edgecore.yaml               # EdgeCore 設定テンプレート
```

---

## 🚀 クイックスタート手順

1. **CloudCore の初期化**:  
   [cloud/README.md](cloud/README.md) の手順に従い、k3s および KubeEdge CloudCore を起動してエッジ参加トークンを取得します。
2. **EdgeCore の参加**:  
   [edge/README.md](edge/README.md) の手順に従い、containerd をセットアップして EdgeCore をクラスタに参加させます。
3. **監視スタックの導入**:  
   [../monitoring/kube-prometheus-stack/README.md](../monitoring/kube-prometheus-stack/README.md) の手順に従い、スペック最低要件で Prometheus & Grafana をデプロイします。

詳細な全体アーキテクチャおよび通信フローは [docs/03_system_architecture.md](../../docs/03_system_architecture.md) を参照してください。
