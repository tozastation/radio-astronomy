# 🛰️ Radio Astronomy with RTL-SDR v4 - AI Context & Guidelines

本リポジトリは、SDR V4（RTL-SDR Blog V4）を活用した電波天文学（21cm中性水素線観測、銀河回転曲線、太陽・流星電波観測）および分散自律観測基盤の開発プロジェクトです。

## 👤 ユーザープロファイル & コミュニケーション方針
- **バックグラウンド**: ソフトウェアエンジニア（SRE）。インフラ、分散システム、Linux、Python、コンテナ/Kubernetesに精通。
- **補完領域**: 高校〜大学初年級の数学、デジタル信号処理（DSP）、天文学の専門理論。
- **コミュニケーション方針**:
  - 数式を単に提示するのではなく、「IT/分散システムの概念に例える」「Pythonコードで可視化する」など、エンジニア目線で直感的に理解できるよう解説する。
  - 専門用語を用いる際は、適宜 [docs/00_glossary.md](docs/00_glossary.md) へのリンクや補足を添える。
  - **公式情報・一次情報の明記**: ドキュメント作成や手順解説において、外部ツール、OS設定、公式ドライバ、ハードウェア仕様等を参照する際は、必ず公式サイトやGitHubリポジトリ、Microsoft Learn等の一次情報リンクを添える。
  - **知識・質疑応答の記録**: ユーザーとの間で交わされた電波天文学、DSP、宇宙物理、システム設計等の知識に関する質疑応答は、逐次 [docs/04_qa.md](docs/04_qa.md) に体系的に追記・蓄積する。
  - **作業ペース**: 一方的に進めず、必ずユーザーの承認や確認を挟みながら1ステップずつ進める。

## 🏗️ システムアーキテクチャ (2台体制)
- **エッジ観測ノード (GPD Pocket3)**:
  - アンテナ直下に設置。RTL-SDR v4 から2.4MSPSのIQ信号を受信。
  - エッジDSP（高速FFT & 1秒積算）によりデータを 8 KB/s に圧縮。
  - CNCF **KubeEdge (EdgeCore)** により、ゲーミングPCの電源状態に依存せず24時間完全自律稼働。
  - ローカルストレージ（DuckDB / SQLite / Parquet）に数ヶ月〜数年分を常時蓄積。
- **分析・開発ワークステーション (ゲーミングデスクトップ: Windows 11 + WSL2)**:
  - WSL2 上で Kubernetes (`k3s`) + KubeEdge `cloudcore` をホスト。
  - ゲームをしていない時にオンデマンドで起動し、LAN越しにGPD Pocket3のデータを直接SQL/Jupyterで高速分析。
  - ホスト側（Windows）のブラウザ（Grafana / JupyterLab）や VS Code から開発・運用。

## 🛠️ 技術スタック
- **データ分析 & 天文学**: Python 3.10+, Astropy, NumPy, SciPy, Polars, Matplotlib
- **エッジDSP / コレクター**: Rust / C / Python (`librtlsdr`, `sdr`, `rustfft`)
- **ストレージ & DB**: DuckDB, Parquet, SQLite, TimescaleDB, MinIO
- **可視化 & 運用**: Grafana, Prometheus, JupyterLab, KubeEdge, k3s, kubectl, k9s

## 📝 コミットルール
- 日本語で記述する
- 絵文字は禁止
- Conventional Commits の形式に従う（例: `feat: ...`, `fix: ...`, `docs: ...`）
- 「〜を追加」「〜を修正」のように簡潔な文章で記述する
