# フルバックアップ＆150GBストレージ保持（ディザスタリカバリ）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1.5年分の季節分析に耐える 150GB ストレージ保持設定の適用と、ビューアからのストリーミング・フルバックアップ（`.tar`）ダウンロードおよび障害復旧用リストア機能（Web UI ＆ CLI）を実装する。

**Architecture:** 
- `satellite-analyzer` の S3 FIFO ローテーション枠を 3世代/1GB から 200世代/32.5GB（計 130GB+）に拡張し、気象画像やテレメトリ復調に成功した RAW データを `golden/{satellite}/` へ自動永久退避する。
- `satellite-viewer` にディスク・メモリ消費ゼロの「S3 ストリーミング Tar 生成エンジン」を組み込み、ブラウザから Google Drive 保存用のフルバックアップアーカイブを即時ダウンロード可能にする。
- 復旧用に Web UI のアップロード復元エンドポイントおよびスタンドアロン型 SRE リストア CLI（`scripts/restore_backup.py`）を提供する。

**Tech Stack:** Python 3.12, tarfile / streaming generator, boto3, Garage S3, Kubernetes (k3s), Docker

---

## Global Constraints

- 日本語でコミットメッセージを記述し、絵文字は使用しない（Conventional Commits 形式）。
- すべての数式・仕様は記号一覧、日本語要約、展開ステップの 3 要素を満たす。
- GPD Pocket3 のメモリ（RAM）消費を常に最小限（ストリーミング処理で < 50MB）に抑える。
- 既存のテスト（Rust 22 件、Python 20 件）を一切破壊しない。

---

## 計画タスク一覧

### Task 1: 150GB ストレージ保持枠の拡大と成功 RAW（Golden）自動保全の実装
**Files:**
- Modify: `apps/satellite-analyzer/src/retention.py`
- Modify: `apps/satellite-analyzer/src/activities.py`
- Modify: `infrastructure/apps/satellite-analyzer/deployment.yaml`
- Test: `apps/satellite-analyzer/tests/test_retention.py`

- [ ] `apps/satellite-analyzer/src/retention.py` に `preserve_golden_raw_recording(s3_client, bucket, raw_s3_key, satellite)` 関数を追加し、成功生データを `golden/{satellite}/{filename}` にコピー退避するロジックを実装。
- [ ] `apps/satellite-analyzer/src/activities.py` の `analyze_satellite_pass_activity` で `packets_count > 0`（画像復調・テレメトリ検出成功）時に `preserve_golden_raw_recording` を呼び出し。
- [ ] `apps/satellite-analyzer/tests/test_retention.py` にゴールデン生データ退避のテストケースを追加。
- [ ] `infrastructure/apps/satellite-analyzer/deployment.yaml` の環境変数に `RAW_RETENTION_MAX_VERSIONS: "200"`、`RAW_RETENTION_MAX_BYTES: "34896609280"`（32.5 GiB / 衛星）を設定。
- [ ] pytest を実行してテストがパスすることを確認。

### Task 2: `satellite-viewer` へのストリーミング・フルバックアップ API 実装
**Files:**
- Modify: `apps/satellite-viewer/app.py`
- Test: `apps/satellite-viewer/test_viewer.py`

- [ ] `apps/satellite-viewer/app.py` にディスク一時ファイルを作らず S3 から直接 Tar ストリームを生成するジェネレータ（`generate_s3_backup_stream`）を実装。
- [ ] `GET /api/backup/download` エンドポイントを追加し、`application/x-tar` のチャンク転送（`Content-Disposition: attachment; filename=radio_astronomy_backup_<timestamp>.tar`）で全 `results/`, `archive/`, `golden/`, `raw/` をストリーミング配信。
- [ ] `apps/satellite-viewer/test_viewer.py` にバックアップストリーミングの単体テストを追加。
- [ ] テストを実行して Tar アーカイブのヘッダと整合性を検証。

### Task 3: ビューア UI（モーダル）および災害復旧 CLI スクリプトの実装
**Files:**
- Modify: `apps/satellite-viewer/app.py`
- Create: `scripts/restore_backup.py`
- Test: `apps/satellite-viewer/test_viewer.py`

- [ ] `apps/satellite-viewer/app.py` に `POST /api/backup/restore` エンドポイントを実装（ブラウザからの Tar ファイル受信と S3 へのオブジェクト展開・`PassDataCache` 再同期）。
- [ ] ビューア UI のヘッダーに「📦 バックアップ & リストア」ボタンおよび説明モーダル（ダウンロード ＆ ドラッグ＆ドロップ復元フォーム）を追加。
- [ ] スタンドアロン復元 CLI `scripts/restore_backup.py` を作成（Google Drive から取得した Tar ファイルを直接 Garage S3 へ流し込む SRE 向けツール）。
- [ ] バックアップ Tar の作成から `restore_backup.py` での投入までのラウンドトリップテストを実施。

### Task 4: コンテナビルド、クラスタ適用および実機検証
**Files:**
- Modify: `infrastructure/apps/satellite-analyzer/deployment.yaml`
- Modify: `infrastructure/apps/satellite-viewer/deployment.yaml` (if needed)

- [ ] `satellite-analyzer-worker:v1.5.5` をビルド・プッシュ。
- [ ] `satellite-viewer:v1.5.1` をビルド・プッシュ。
- [ ] `kubectl apply` でクラスタへ反映し、全 Pod の `1/1 Running` を確認。
- [ ] `http://localhost:30088/api/backup/download` を呼び出して Tar ストリームが正常にダウンロードできることを検証。

### Task 5: 技術ドキュメント整備とメインブランチ統合
**Files:**
- Create: `docs/qa/25_storage_retention_and_disaster_recovery.md`
- Modify: `docs/04_qa.md`

- [ ] `docs/qa/25_storage_retention_and_disaster_recovery.md` を作成し、1.5年保持の数理・ストリーミング Tar の SRE 設計・Google Drive 連携の復旧手順を体系的に記録。
- [ ] `docs/04_qa.md` に Q&A 項目およびトピック索引を追記。
- [ ] 全テスト確認後、コミット・マージ・リモートプッシュを実施。
