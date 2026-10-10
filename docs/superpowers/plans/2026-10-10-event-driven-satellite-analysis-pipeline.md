# イベント駆動型衛星解析パイプライン 実装計画
## (Event-Driven Satellite Analysis Pipeline Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** エッジ（GPD Pocket3）の消費電力・ディスク枯渇を防ぎつつ、Garage S3 と Temporal、KEDA 0-scale を用いて衛星通信パケット（APRS等）とスペクトログラムを自動解析・永続化するイベント駆動型パイプラインを構築する。

**Architecture:** エッジ側の `satellite-tracker` は通過時のみ SDR を動的起動し、48kHz WAV（狭帯域）をスプールして Garage S3 へアップロード後に即削除。クラウド／コントロールプレーン側で Temporal ワークフローが起動され、KEDA が `satellite-analyzer-worker` を `0 → 1` に起動してパケットデコードと画像生成を行い、完了後に `0` へ自動縮退する。

**Tech Stack:** Python 3.10+, Garage S3 (Rust), Temporal Python SDK, KEDA, direwolf, SciPy/Matplotlib, boto3, pytest, Docker, Kubernetes/k3s

**Spec:** `docs/superpowers/specs/2026-10-10-event-driven-satellite-analysis-pipeline-design.md`

## Global Constraints
- エッジのローカルスプール空き容量ガード（ホスト空き容量 < 10% または スプール > 500MB で最古ファイルを強制削除）。
- 待機時の SDR 給電カット（SDR クローズ状態を維持し、AOS 30 秒前にウォームアップ起動）。
- Garage S3 は単一ノードモード（`singleNode: true`）でメモリ約 30MB 以下の軽量稼働。
- コミットメッセージは日本語、絵文字禁止、Conventional Commits 形式（例: `feat: ...`, `fix: ...`）。

## Review Focus
1. ネットワーク切断により S3 アップロードが失敗した際、ローカルスプールが無限肥大化せず Circuit Breaker で容量ガードされること。
2. SDR デバイスの多重オープンやクローズ漏れによる USB リソースロックが発生しないこと。
3. 狭帯域 48kHz WAV のサンプリングレートおよびヘッダが `direwolf` やオーディオツールで正常に読める標準形式であること。
4. Temporal のタスクキューが空になった際、KEDA が指定クールダウン時間（120秒）後に確実に Worker Pod を `0` にスケールインすること。
5. パケットが 1 件も検出されなかったパス（無変調トーンのみ等）でも、エラーで落ちずに空配列の `packets.json` とスペクトログラム画像を正常保存できること。

---

### Task 1: Garage S3 の Kubernetes マニフェスト作成とデプロイ設定

**Files:**
- Create: `infrastructure/storage/garage/kustomization.yaml`
- Create: `infrastructure/storage/garage/configmap.yaml`
- Create: `infrastructure/storage/garage/secret.yaml`
- Create: `infrastructure/storage/garage/service.yaml`
- Create: `infrastructure/storage/garage/statefulset.yaml`
- Create: `scripts/init_garage.sh`

**Interfaces:**
- Produces: S3 エンドポイント `http://garage-s3.storage.svc.cluster.local:3900`、バケット `satellite-recordings`、S3 アクセスキー＆シークレット。

- [ ] **Step 1: Garage S3 の K8s マニフェスト一式を作成**
  `infrastructure/storage/garage/` 配下に `singleNode: true` 設定の ConfigMap、RPC Secret、StatefulSet（`metadata` 1Gi, `data` 20Gi PVC）、および Service（`:3900` S3 API, `:3901` RPC）を定義。

- [ ] **Step 2: 初期化スクリプト `scripts/init_garage.sh` を作成**
  Garage Pod 起動後にレイアウト適用（10GB 割り当て）、バケット `satellite-recordings` 作成、およびアクセスキー `tracker-key` 発行を自動化するスクリプトを作成。

- [ ] **Step 3: マニフェストの適用と起動確認**
  `kubectl apply -k infrastructure/storage/garage/` を実行し、`garage-0` Pod が Running（メモリ消費 ~30MB）になることを確認。`scripts/init_garage.sh` でバケットを初期化。

- [ ] **Step 4: boto3 による接続疎通テストを実行**
  Python スクリプトから Garage S3 にテストオブジェクトを put/get/delete できることを確認。

- [ ] **Step 5: コミット**
  ```bash
  git add infrastructure/storage/garage/ scripts/init_garage.sh
  git commit -m "feat: Garage S3 オブジェクトストレージの Kubernetes マニフェストと初期化スクリプトを追加"
  ```

---

### Task 2: エッジ側 SDR の動的省電力ライフサイクル制御

**Files:**
- Modify: `apps/satellite-tracker/src/sdr_collector.py`
- Modify: `apps/satellite-tracker/src/main.py`
- Test: `apps/satellite-tracker/tests/test_sdr_lifecycle.py`

**Interfaces:**
- Consumes: `OrbitPredictor` の次回 AOS 時刻
- Produces: `SDRCollector.warmup()` / `SDRCollector.standby()` による動的デバイスオープン・クローズ

- [ ] **Step 1: 待機時クローズおよび事前ウォームアップのテストを作成**
  `apps/satellite-tracker/tests/test_sdr_lifecycle.py` に、次回 AOS までの残り時間に応じた `standby()`（close）と `warmup()`（open）の呼び出しを検証するテストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_sdr_lifecycle.py`
  Expected: FAIL (`warmup`/`standby` 未実装)

- [ ] **Step 3: `sdr_collector.py` と `main.py` に省電力制御を実装**
  非通過時は SDR をクローズして待機し、次回 AOS の 30 秒前になったら `open()` して中心周波数をチューニングするロジックを実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_sdr_lifecycle.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker/src/sdr_collector.py apps/satellite-tracker/src/main.py apps/satellite-tracker/tests/test_sdr_lifecycle.py
  git commit -m "feat: 衛星通過予測に連動したSDRの動的省電力ライフサイクル制御を追加"
  ```

---

### Task 3: 狭帯域 48kHz WAV スプール録音 ＆ 安全回路 (Circuit Breaker) の実装

**Files:**
- Create: `apps/satellite-tracker/src/audio_spooler.py`
- Modify: `apps/satellite-tracker/src/main.py`
- Test: `apps/satellite-tracker/tests/test_audio_spooler.py`

**Interfaces:**
- Consumes: 受信 IQ サンプル、サンプリングレート 2.4 MSPS
- Produces: 48kHz モノラル 16bit PCM WAV ファイル（`/tmp/spool/{satellite}_{pass_id}.wav`）

- [ ] **Step 1: デシメーション録音と容量ガードのテストを作成**
  `apps/satellite-tracker/tests/test_audio_spooler.py` に、IQ 信号の 48kHz デシメーション処理、WAV ファイル出力、およびディスク容量超過時の最古ファイル破棄テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_audio_spooler.py`
  Expected: FAIL (`AudioSpooler` 未実装)

- [ ] **Step 3: `audio_spooler.py` を実装**
  SciPy の `resample_poly` または `decimate` による 48kHz リサンプリング、Python 標準 `wave` モジュールでのストリーミング書き込み、および `shutil.disk_usage` による安全回路を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_audio_spooler.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker/src/audio_spooler.py apps/satellite-tracker/tests/test_audio_spooler.py apps/satellite-tracker/src/main.py
  git commit -m "feat: 48kHz狭帯域WAVスプール録音とストレージ容量保護ガードを追加"
  ```

---

### Task 4: Garage S3 への自動アップロード ＆ ローカルクリーンアップ

**Files:**
- Create: `apps/satellite-tracker/src/s3_uploader.py`
- Modify: `apps/satellite-tracker/src/main.py`
- Test: `apps/satellite-tracker/tests/test_s3_uploader.py`

**Interfaces:**
- Consumes: スプール完了した WAV ファイルパス、S3 エンドポイント / 認証情報
- Produces: Garage S3 の `raw/{satellite}/{pass_id}.wav`、アップロード完了後のローカルファイル削除

- [ ] **Step 1: S3 アップロードと完了時削除のテストを作成**
  `apps/satellite-tracker/tests/test_s3_uploader.py` に、モック S3 クライアントを用いたアップロードおよびローカルファイルのアンリンク検証テストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_s3_uploader.py`
  Expected: FAIL (`S3Uploader` 未実装)

- [ ] **Step 3: `s3_uploader.py` を実装し `main.py` の LOS 処理に接続**
  boto3 を用いたマルチパートアップロード、アップロード成功時の `os.remove`、切断時のリトライ・スプール保持ロジックを実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-tracker/tests/test_s3_uploader.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-tracker/src/s3_uploader.py apps/satellite-tracker/tests/test_s3_uploader.py apps/satellite-tracker/src/main.py
  git commit -m "feat: Garage S3への録音データ自動アップロードとローカルクリーンアップ機能を追加"
  ```

---

### Task 5: APRS パケットデコード ＆ スペクトログラム生成ロジックの実装

**Files:**
- Create: `apps/satellite-analyzer/src/aprs_decoder.py`
- Create: `apps/satellite-analyzer/src/spectrogram_generator.py`
- Create: `apps/satellite-analyzer/src/analyzer.py`
- Test: `apps/satellite-analyzer/tests/test_analyzer.py`

**Interfaces:**
- Consumes: 48kHz WAV ファイル
- Produces: `packets.json`（デコードパケット一覧）、`spectrogram.png`（ドップラーS字画像）、`summary.json`

- [ ] **Step 1: デコードおよび画像生成のテストを作成**
  `apps/satellite-analyzer/tests/test_analyzer.py` に、サンプル WAV 音声から APRS パケット（コールサイン・メッセージ）のパースと、スペクトログラム PNG ファイルの生成を検証するテストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-analyzer/tests/test_analyzer.py`
  Expected: FAIL (モジュール未実装)

- [ ] **Step 3: `aprs_decoder.py` と `spectrogram_generator.py` を実装**
  `direwolf` サブプロセス呼び出しまたは AFSK1200 復調によるパケット抽出、および Matplotlib によるスペクトログラム PNG 生成処理を実装。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-analyzer/tests/test_analyzer.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-analyzer/
  git commit -m "feat: APRSパケットデコーダとスペクトログラム生成処理を追加"
  ```

---

### Task 6: `satellite-analyzer-worker` コンテナイメージの作成とビルド

**Files:**
- Create: `apps/satellite-analyzer/Dockerfile`
- Create: `apps/satellite-analyzer/requirements.txt`
- Create: `apps/satellite-analyzer/src/worker.py`

**Interfaces:**
- Produces: コンテナイメージ `localhost:5000/satellite-analyzer-worker:dev`（`direwolf`, Python 3.11, SciPy, Matplotlib 内包）

- [ ] **Step 1: Dockerfile と worker 起動スクリプトを作成**
  `direwolf` パッケージと Python ランタイムをインストールし、S3 から WAV をダウンロードして解析を実行するスタンドアロンワーカーを作成。

- [ ] **Step 2: ローカルレジストリ向けにコンテナイメージをビルド**
  `docker build -t localhost:5000/satellite-analyzer-worker:dev apps/satellite-analyzer/` を実行。

- [ ] **Step 3: コンテナの単体動作確認**
  テスト用 WAV を渡してコンテナ内で `packets.json` と `spectrogram.png` が出力されることを確認。

- [ ] **Step 4: レジストリへ Push**
  `docker push localhost:5000/satellite-analyzer-worker:dev` を実行。

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-analyzer/Dockerfile apps/satellite-analyzer/requirements.txt apps/satellite-analyzer/src/worker.py
  git commit -m "feat: satellite-analyzer-workerのDockerfileとワーカー実装を追加"
  ```

---

### Task 7: Temporal ワークフロー (`AnalyzeSatellitePassWorkflow`) の実装

**Files:**
- Create: `apps/satellite-analyzer/src/workflows.py`
- Create: `apps/satellite-analyzer/src/activities.py`
- Modify: `apps/satellite-tracker/src/main.py`
- Test: `apps/satellite-analyzer/tests/test_workflows.py`

**Interfaces:**
- Consumes: Temporal Server (`:7233`)
- Produces: ワークフロー `AnalyzeSatellitePassWorkflow`（WAV ダウンロード → デコード → 画像生成 → S3 保存 → 元 WAV 削除）

- [ ] **Step 1: ワークフローおよび Activity のテストを作成**
  Temporal テスト環境（`WorkflowEnvironment`）を用いて、Activity の連動と正常終了・リトライ動作を検証するテストを記述。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-analyzer/tests/test_workflows.py`
  Expected: FAIL (ワークフロー未実装)

- [ ] **Step 3: `workflows.py` と `activities.py` を実装**
  Temporal Python SDK を用いて 5 つの Activity とメインワークフローを実装。`main.py` から LOS 時に Temporal クライアントでワークフローをキックする処理を追加。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-analyzer/tests/test_workflows.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  ```bash
  git add apps/satellite-analyzer/src/workflows.py apps/satellite-analyzer/src/activities.py apps/satellite-analyzer/tests/test_workflows.py apps/satellite-tracker/src/main.py
  git commit -m "feat: Temporalによる衛星解析ワークフローとActivity定義を追加"
  ```

---

### Task 8: Temporal / KEDA 0-scale マニフェスト作成と E2E 結合検証

**Files:**
- Create: `infrastructure/apps/temporal/temporal-server.yaml`
- Create: `infrastructure/apps/satellite-analyzer/deployment.yaml`
- Create: `infrastructure/apps/satellite-analyzer/keda-scaledobject.yaml`
- Create: `scripts/test_pipeline_e2e.py`

**Interfaces:**
- Produces: 完全自律型の 0-scale パイプライン（待機時レプリカ 0 → タスク投入時 1 → 完了後 0）

- [ ] **Step 1: Temporal Server と KEDA ScaledObject マニフェストを作成**
  コントロールプレーン向けの軽量 Temporal Server（SQLite）と、Task Queue を監視して Worker Pod を 0 $\leftrightarrow$ 1 制御する ScaledObject を定義。

- [ ] **Step 2: クラスタへデプロイ**
  `kubectl apply -f infrastructure/apps/temporal/` および `kubectl apply -f infrastructure/apps/satellite-analyzer/` を実行。初期状態で Worker Pod が `0` であることを確認。

- [ ] **Step 3: E2E パイプライン結合テストスクリプトを作成・実行**
  `scripts/test_pipeline_e2e.py` を実行し、擬似パスをトリガー → S3 アップロード → Temporal キック → KEDA による Worker 起動（`0 → 1`） → 解析完了・結果 S3 格納 → Worker 縮退（`1 → 0`）の一連の流れを自動検証。

- [ ] **Step 4: Temporal Web UI (:8233) および Garage S3 (:3900) で成果物確認**
  ブラウザで Temporal UI の完了 DAG と、Garage S3 内の `packets.json` および `spectrogram.png` を確認。

- [ ] **Step 5: コミット**
  ```bash
  git add infrastructure/apps/temporal/ infrastructure/apps/satellite-analyzer/ scripts/test_pipeline_e2e.py
  git commit -m "feat: TemporalおよびKEDA 0-scaleオートスケーリングのマニフェストとE2Eテストを追加"
  ```
