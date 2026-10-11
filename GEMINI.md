# ベランダ電波観測所 - AI Context & Guidelines

本リポジトリは、東京都の北向きベランダから RTL-SDR Blog V4 と GPD Pocket3 で電波を受信し、機影・画像・テレメトリ・復号テキストとして中身を確認するベランダ天文学のプロジェクトである。

21cm 中性水素線と銀河回転曲線の解説は `docs/02_radio_astronomy_guide.md` に背景資料として残している。現行の観測プログラムには入れていない。太陽は南天を通るため、北向きベランダからの太陽電波直視も現行対象外である。`solar-station` のコードは残置している。

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

## システムアーキテクチャ（現行: GPD Pocket3 1台同居）
- **観測地**: 東京都（広域代表値: 北緯 35.6895、東経 139.6917、標高 30 m）。アンテナは北向きベランダ（2026-09-15 移設）。見通しは方位およそ \(270^\circ \sim 90^\circ\)。南〜南西は RC 遮蔽。
- **ホスト**: GPD Pocket3（Ubuntu 26.04 LTS、ネイティブ Linux）。k3s（CloudCore）と EdgeCore を同一端末に同居させる。Windows + WSL2 は初日の受信確認手順（`docs/05_getting_started.md`）であり、現行ホストではない。
- **エッジ**: RTL-SDR v4。案Aでは Ultrafeeder が 1090 MHz を占有する。衛星側は `satellite-tracker-rs` が通過時だけ SDR を開き、48 kHz WAV を Garage へ置く。
- **クラスタ側**: Garage S3、Temporal、KEDA、`satellite-analyzer`（普段は 0 レプリカ）、`satellite-viewer`、kube-prometheus-stack。
- **物理分離**: 分析用 PC へ CloudCore を切り離す構成は拡張先である。現行の稼働形態は 1 台同居。
- **現行フェーズ（案A）**: Meteor / CubeSat / ISS とデイリー衛星スケジュールは `apps/ground-station/config.toml` でオフ。RTL-SDR は ADS-B（Ultrafeeder、ホストポート 18090）に明け渡し、機影が見える成功を先に取る。
- **成功の定義**: 中身が見える（画像・テレメトリ JSON・復号テキスト、ADS-B では機影）。判定困難な対象は `unknown`。詳細は `docs/qa/10_observation_metrics_and_heuristic_evaluation.md`。

## 技術スタック
- **エッジ受信**: Rust（`ground-station`, `satellite-tracker-rs`）。`librtlsdr`, `rustfft`, `sgp4`
- **解析**: Python 3.10+（`satellite-analyzer`, `satellite-viewer`）。NumPy, SciPy, Matplotlib, Temporal, boto3
- **ストレージ**: Garage S3（録音と解析結果）。Temporal の状態は SQLite。地上局メトリクスは JSON Lines
- **運用**: KubeEdge, k3s, Prometheus, Grafana, KEDA, Docker Compose（Ultrafeeder / VOICEVOX）

## 📝 コミットルール
- 日本語で記述する
- 絵文字は禁止
- Conventional Commits の形式に従う（例: `feat: ...`, `fix: ...`, `docs: ...`）
- 「〜を追加」「〜を修正」のように簡潔な文章で記述する
