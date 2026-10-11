# 🛰️ Radio Astronomy with RTL-SDR v4 - AI Context & Guidelines

本リポジトリは、SDR V4（RTL-SDR Blog V4）を活用した電波天文学（21cm中性水素線観測、銀河回転曲線、太陽・流星電波観測）および分散自律観測基盤の開発プロジェクトです。

## 👤 ユーザープロファイル & コミュニケーション方針
- **バックグラウンド**: ソフトウェアエンジニア（SRE）。インフラ、分散システム、Linux、Python、コンテナ/Kubernetesに精通。
- **補完領域**: 高校〜大学初年級の数学、デジタル信号処理（DSP）、天文学の専門理論。
- **コミュニケーション方針**:
  - 数式や理論を過剰にIT/インフラの概念に例えるのは避け、無線工学、電磁気学、デジタル信号処理（DSP）、情報理論の本質的な物理・数学的メカニズムに基づき、情報量を落とさずに詳しく解説する。
  - 専門用語を用いる際は、適宜 [docs/00_glossary.md](docs/00_glossary.md) へのリンクや補足を添える。
  - **公式情報・一次情報の明記**: ドキュメント作成や手順解説において、外部ツール、OS設定、公式ドライバ、ハードウェア仕様等を参照する際は、必ず公式サイトやGitHubリポジトリ、Microsoft Learn等の一次情報リンクを添える。
  - **作業内容・意図の丁寧な解説**: コマンド実行やコード変更を行う際は、「今何をしているのか」「なぜその処理が必要なのか」を技術的背景とともに分かりやすく解説しながら進める。
  - **知識・質疑応答の記録**: ユーザーとの間で交わされた電波天文学、DSP、宇宙物理、システム設計等の知識に関する質疑応答は、逐次 [docs/04_qa.md](docs/04_qa.md) に体系的に追記・蓄積する。
  - **ドキュメントの適切な分割**: [docs/04_qa.md](docs/04_qa.md) や各技術ドキュメントにおいて情報量が多くなる場合や特定トピックを詳細に解説する場合は、1ファイルに詰め込みすぎず、適宜個別ファイルへ切り出して親ドキュメントやインデックスからリンクを貼る。
  - **数式・公式のドキュメント記述ルール**: 数式や公式をドキュメントに記載・解説する際は、数式を単体で提示するだけにせず、必ず以下の3要素をセットで解説する：
    1. **記号一覧（テーブル等）**: 式を構成する各変数・定数・演算子の物理的意味や単位の明記。
    2. **日本語での読み解き**: 式が物理的・工学的に何を主張しているのかを平易な日本語で要約。
    3. **展開ステップ・直感イメージ**: 高校数学〜大学初年級の数学（三角関数、複素数、ベクトル、微積分等）や基礎物理で追える途中式の展開や幾何学的・物理的直感イメージの補足。
  - **作業ペース**: 一方的に進めず、必ずユーザーの承認や確認を挟みながら1ステップずつ進める。

## 🏗️ システムアーキテクチャ（現行は KubeEdge 上の衛星観測パイプラインのみ）
- **ホスト**: GPD Pocket3（Ubuntu 26.04 LTS）1台。k3s（CloudCore）と EdgeCore が論理ノードとして同居する。Kubernetes クラスタは1つ。
- **エッジ (`gpd-pocket3-edge`)**: `satellite-tracker-rs`。RTL-SDR v4 を衛星の可視時間だけ開き、48 kHz WAV を Garage S3 の `raw/` へアップロードしてローカルファイルを削除する。MetaManager の SQLite により、k3s 停止中も Pod は継続する。
- **クラウド側ワークロード**: Garage S3、Temporal、KEDA、`satellite-analyzer-worker`（普段 0 レプリカ）、`satellite-viewer`（NodePort 30088）、kube-prometheus-stack / Grafana（NodePort 30080）。
- **データフロー**: viewer が未解析 WAV を検知して Temporal ワークフローを開始し、KEDA が解析ワーカーを 0 から起動する。成果物は `results/` の JSON とスペクトログラム PNG。
- **現行構成に含めないもの**: ADS-B / tar1090（Ultrafeeder）、旧来の 8 KB/s DuckDB コレクタ、Jupyter を常駐させた分析ノード。
- **将来**: 分析用 PC へ CloudCore を移し、EdgeCore の接続先変更だけで物理分離できる。
- **詳細**: [docs/03_system_architecture.md](docs/03_system_architecture.md)。

## 🛠️ 技術スタック
- **データ分析 & 天文学**: Python 3.10+, Astropy, NumPy, SciPy, Polars, Matplotlib
- **エッジDSP / コレクター**: Rust / C / Python (`librtlsdr`, `sdr`, `rustfft`)
- **ストレージ & DB**: Garage S3, SQLite（MetaManager / Temporal）, Parquet
- **可視化 & 運用**: Grafana, Prometheus, satellite-viewer, Temporal, KEDA, KubeEdge, k3s, kubectl, k9s

## 📝 コミットルール
- 日本語で記述する
- 絵文字は禁止
- Conventional Commits の形式に従う（例: `feat: ...`, `fix: ...`, `docs: ...`）
- 「〜を追加」「〜を修正」のように簡潔な文章で記述する
