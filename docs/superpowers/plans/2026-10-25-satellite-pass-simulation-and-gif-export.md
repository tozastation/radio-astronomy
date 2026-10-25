# 衛星通過天球シミュレータ ＆ 観測リプレイ GIF 切り出し機能 実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apps/satellite-viewer` に北向きベランダのスカイウィンドウ（開口窓）と衛星軌道を可視化する 2D 極座標天球レーダー、スペクトログラム連動観測リプレイ、およびブラウザ内 GIF 切り出しダウンロード機能（`gifenc`）を実装する。

**Architecture:** サーバー側（`app.py` ＋ `orbit_calculator.py`）で SGP4 による 1 秒刻みの Az/El/Range 軌道計算および未来パス予測 API を提供し、フロントエンド側（HTML5 Canvas）で極座標スカイドーム（開口窓ハイライト・建物遮蔽マスク・ヒートマップ軌跡）をレンダリング。タイムライン操作でスペクトログラム上のシーク線や復調雲画像ポップアップと同期させ、オフスクリーン Canvas から `gifenc` を用いてアニメーション GIF をブラウザ内で即時生成・ダウンロードする。

**Tech Stack:** Python 3.12, SGP4 (`sgp4>=2.23`), HTML5 Canvas API, Pure JS `gifenc`, Boto3, UnitTest/Pytest

**Spec:** [`docs/superpowers/specs/2026-10-25-satellite-pass-simulation-and-gif-export-design.md`](../specs/2026-10-25-satellite-pass-simulation-and-gif-export-design.md)

## Global Constraints

- **観測所座標**: 東京（広域代表値: 北緯 35.6895, 東経 139.6917, 標高 30.0m）。
- **スカイウィンドウ定義**: 方位角 $\text{Az} \in [250^\circ, 360^\circ] \cup [0^\circ, 110^\circ]$, 仰角 $\text{El} \in [25.0^\circ, 60.0^\circ]$。
- **建物遮蔽定義**: 南側 $\text{Az} \in (110^\circ, 250^\circ)$, 庇天井 $\text{El} > 60.0^\circ$。
- **極座標投影変換**: 天頂中心 $r = R_{\max}(1 - \text{El}/90^\circ)$, $x = x_c + r\sin\text{Az}, y = y_c - r\cos\text{Az}$。
- **GIF エンコード**: サーバー負荷ゼロのクライアントサイド処理（`gifenc` による Canvas キャプチャ、10 fps、推奨サイズ $480 \times 360\text{ px}$）。
- **既存データ互換性**: Garage S3 上の既存 200+ パスデータ（`summary.json`, `packets.json`, `spectrogram.png`, 各種画像）の読み込みと 100% 互換性を維持する。

## Review Focus

1. **不正な pass_id や TLE 欠落時のハンドリング**: TLE カタログに存在しない衛星名や不正フォーマットの pass_id が要求された際、500 Internal Error にならず適切な 404 / 400 JSON エラーを返すこと。
2. **画像が存在しないパスのリプレイ動作**: 復調画像のないパス（信号微弱パス）でも天球レーダーとスペクトログラムの同期アニメーションが破綻なく動作し、画像ポップアップが静かにスキップされること。
3. **GIF 切り出しの始点・終点境界**: ユーザーが逆順（始点 > 終点）やゼロ秒未満、録音長超過の範囲を選択した場合でも、安全に範囲を正規化してエラーを出さないこと。
4. **モバイル画面・タッチ操作対応**: スマホブラウザでレーダー Canvas やタイムラインバーがはみ出さず、レスポンシブに収まること。
5. **長大パス（400秒以上）のメモリ・描画パフォーマンス**: 400 点以上の軌道データや Canvas アニメーションが 60 fps で滑らかに動作し、メモリリークを起こさないこと。

---

## タスク一覧

### Task 1: SGP4 軌道伝搬 ＆ ベランダスカイウィンドウ計算エンジン (`orbit_calculator.py`)

**Files:**
- Create: `apps/satellite-viewer/orbit_calculator.py`
- Test: `apps/satellite-viewer/test_orbit_calculator.py`
- Modify: `apps/satellite-analyzer/requirements.txt` (add `sgp4>=2.23`)

**Interfaces:**
- Consumes: 観測地点座標（lat, lon, alt_m）、TLE 文字列（Line 1, Line 2）、パス開始日時（UTC）、継続秒数（seconds）
- Produces: `OrbitCalculator.calculate_trajectory(tle_l1, tle_l2, start_time, duration_seconds) -> List[TrajectoryPoint]`
  - `TrajectoryPoint`: `{ t: int, az: float, el: float, range_km: float, in_window: bool }`
- Produces: `OrbitCalculator.is_in_sky_window(az: float, el: float) -> bool`

- [ ] **Step 1: Write the failing test for trajectory and sky window calculation**
  `apps/satellite-viewer/test_orbit_calculator.py` に、METEOR-M2 3 の既知 TLE と 2026-10-25 00:53:44 UTC のパスに対するテストを作成：
  - `test_is_in_sky_window`: 開口部（北 $35^\circ$、仰角 $45^\circ$）で True、南側（$180^\circ$）や低仰角（$10^\circ$）で False を検証。
  - `test_calculate_trajectory_reaches_expected_tca`: $t=0 \sim 406\text{秒}$ の軌道計算を実行し、$t \approx 45 \sim 50\text{秒}$ 付近で最大仰角 $\text{El} \approx 48.5^\circ \pm 3^\circ$、方位角 $\text{Az} \approx 34^\circ \pm 5^\circ$ に達することを検証。

- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=apps/satellite-viewer pytest apps/satellite-viewer/test_orbit_calculator.py`
  Expected: FAIL with `ModuleNotFoundError: No module named 'orbit_calculator'`

- [ ] **Step 3: Implement `orbit_calculator.py`**
  - `sgp4.api.Satrec` を用いた高速バッチ伝搬
  - WGS84 座標系から局所水平座標系（Topocentric Horizon: Azimuth, Elevation, Range）への変換
  - `is_in_sky_window(az, el)` 判定（$\text{Az} \in [250^\circ, 360^\circ] \cup [0^\circ, 110^\circ]$ かつ $\text{El} \in [25^\circ, 60^\circ]$）の実装

- [ ] **Step 4: Run test to verify it passes**
  Run: `PYTHONPATH=apps/satellite-viewer pytest apps/satellite-viewer/test_orbit_calculator.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run: `git add apps/satellite-viewer/orbit_calculator.py apps/satellite-viewer/test_orbit_calculator.py apps/satellite-analyzer/requirements.txt && git commit -m "feat: SGP4軌道計算とベランダスカイウィンドウ判定エンジンを追加"`

---

### Task 2: バックエンド REST API 実装 (`/api/passes/{id}/trajectory` ＆ `/api/future-passes`)

**Files:**
- Modify: `apps/satellite-viewer/app.py`
- Modify: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `OrbitCalculator` (from Task 1)
- Produces: HTTP API `GET /api/passes/{pass_id}/trajectory`
  - Returns JSON: `{ "pass_id": str, "satellite": str, "total_seconds": int, "points": [...], "images": [...] }`
- Produces: HTTP API `GET /api/future-passes`
  - Returns JSON: `{ "generated_at": str, "passes": [...] }`

- [ ] **Step 1: Write failing tests for the new API endpoints**
  `apps/satellite-viewer/test_viewer.py` にテストケースを追加：
  - `test_api_pass_trajectory_endpoint`: モック S3 とダミー pass_id に対し、正しいステータスコード 200 と JSON スキーマ（points 配列、各要素に t, az, el, in_window、images 配列）を返すか検証。
  - `test_api_future_passes_endpoint`: 未来パス予測リストが返り、各エントリに satellite, aos_time, max_elevation, in_window が含まれるか検証。
  - `test_api_pass_trajectory_not_found`: 存在しない pass_id に対し 404 を返すか検証。

- [ ] **Step 2: Run tests to verify failure**
  Run: `PYTHONPATH=apps/satellite-viewer pytest apps/satellite-viewer/test_viewer.py`
  Expected: FAIL on new test cases.

- [ ] **Step 3: Implement API routing and handlers in `apps/satellite-viewer/app.py`**
  - TLE ローダーの実装（`assets/satdump_tles.txt` および `apps/ground-station/data/default_tles.txt` を探索）
  - `/api/passes/{pass_id}/trajectory` ハンドラ実装:
    - パス ID から衛星名と開始時刻を抽出
    - S3 またはローカルメタデータから画像ファイル（`*.png`）のリストを検出
    - `OrbitCalculator` で 1 秒刻みの軌道計算を行い、画像が存在する時間帯に `has_image: true` を付与
  - `/api/future-passes` ハンドラ実装:
    - 追跡衛星（METEOR-M2 3, METEOR-M2 4, NOAA 15/18/19, ISS 等）の今後 24 時間のパスを計算し、北向きスカイウィンドウ通過条件に合致するものを抽出

- [ ] **Step 4: Run tests to verify they pass**
  Run: `PYTHONPATH=apps/satellite-viewer pytest apps/satellite-viewer/test_viewer.py`
  Expected: All tests PASS.

- [ ] **Step 5: Commit**
  Run: `git add apps/satellite-viewer/app.py apps/satellite-viewer/test_viewer.py && git commit -m "feat: 軌道シミュレーション用trajectoryおよびfuture-passes APIを追加"`

---

### Task 3: 2D 極座標天球レーダー ＆ 観測リプレイ同期フロントエンド実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (HTML_TEMPLATE, CSS, JavaScript)

**Interfaces:**
- Consumes: `/api/passes/{pass_id}/trajectory` (from Task 2)
- Produces: 
  - `openReplayModal(passId)` JS 関数
  - `renderPolarRadar(ctx, points, currentTimeIndex, config)` Canvas 描画エンジン
  - タイムライン同期アニメーションループ（再生/一時停止/シーク）
  - スペクトログラム連動シークバーカーソル
  - 雲画像ポップアップアニメーション

- [ ] **Step 1: Write HTML / Canvas scaffolding and CSS for Replay Modal**
  - モーダル構造の追加（`#replayModal`）:
    - ヘッダー: 衛星名、パス ID、閉じるボタン
    - 上部: 天球レーダー Canvas（幅 360px × 高さ 360px）＋ テレメトリ情報（時刻、Az、El、距離、電波強度インジケータ）
    - 画像プレビュー枠（`#replayImagePreview`）
    - 下部: スペクトログラム同期表示枠 ＋ タイムラインスライダー ＋ 再生制御ボタン群（再生・一時停止・速度切替）
  - 各観測カードに `[▶️ 観測リプレイ＆シミュレータ]` ボタンを追加

- [ ] **Step 2: Implement Polar Radar Canvas rendering logic in JavaScript**
  - 正距方位極座標変換:
    $$r = R_{\max} (1 - \text{El}/90), \quad x = x_c + r\sin(\text{Az}), \quad y = y_c - r\cos(\text{Az})$$
  - 背景グリッド:
    - 同心円（$30^\circ, 60^\circ$）および東西南北軸線の描画
    - スカイウィンドウ（$\text{Az} \in [250^\circ, 110^\circ], \text{El} \in [25^\circ, 60^\circ]$）を半透明シアン（`rgba(56, 189, 248, 0.2)`）で扇形描画
    - 南側遮蔽領域（$\text{Az} \in [110^\circ, 250^\circ]$）を斜線ストライプパターンでマスク描画
  - 軌道ラインのヒートマップ描画:
    - 電波強度に応じて青（フロア） $\to$ 緑（中電界） $\to$ 黄色（$+3.5\text{ dB}$ 以上）でグラデーション描画
  - 現在位置の衛星アイコン（🛰️）、進行方向ベクトル、過去 5 秒の残光トレイルを描画

- [ ] **Step 3: Implement Timeline & Spectrogram sync controller**
  - タイムラインスライダー操作時に Canvas 再生位置を同期更新
  - スペクトログラム画像（`spectrogram.png`）上に赤い垂直バーを重ねて配置し、再生時間 $t$ に合わせて横位置（`left: (t / total_seconds) * 100%`）を更新
  - `point.has_image` が True の区間に達した瞬間に、復調画像サムネイルをスライドイン表示

- [ ] **Step 4: Manual & automated verification of frontend rendering**
  - ローカルで `satellite-viewer` を起動し、ブラウザでモーダルが開き、レーダーとスペクトログラムカーソルが滑らかに連動することを確認

- [ ] **Step 5: Commit**
  Run: `git add apps/satellite-viewer/app.py && git commit -m "feat: 2D極座標天球レーダーとスペクトログラム連動リプレイUIを実装"`

---

### Task 4: クライアントサイド GIF 切り出し ＆ ダウンロード機能（`gifenc`）

**Files:**
- Modify: `apps/satellite-viewer/app.py` (Include `gifenc` engine, export controls, progress bar)
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: Canvas rendering engine & trajectory data (from Task 3)
- Produces: `exportReplayGif(startTime, endTime, fps)` JS 関数
  - Generates `.gif` file and triggers automatic browser download

- [ ] **Step 1: Integrate lightweight `gifenc` module into viewer**
  - `gifenc`（GIFEncoder, quantize, applyPalette）を HTML_TEMPLATE 内の `<script>` にバンドル（または信頼できる CDN から ES module / IIFE インポート）
  - オフスクリーン Canvas（$480 \times 360\text{ px}$）の生成処理を実装

- [ ] **Step 2: Implement Range Selection UI (Clipper Controls)**
  - リプレイモーダル内に GIF 区間選択スライダーを追加：
    - 始点インジケータ（デフォルト: TCA - 25秒）
    - 終点インジケータ（デフォルト: TCA + 25秒）
    - `[📍 現在位置を始点に]` / `[📍 現在位置を終点に]` ボタン
    - `[✨ ピーク区間（30s〜80s）自動選択]` ボタン
  - `[🎬 この区間をアニメーション GIF でダウンロード]` ボタンと、生成中プログレスバー（`0% ... 100%`）の配置

- [ ] **Step 3: Implement frame-by-frame GIF generation and download pipeline**
  - 指定された $[t_{\text{start}}, t_{\text{end}}]$ に対し、ステップ幅 $\Delta t = 1.0 / \text{fps}$（既定 10 fps）で時間を進めるループ：
    1. オフスクリーン Canvas に極座標レーダーを描画
    2. 上部に「JST時刻・Az/El・受信強度 $\Delta P$・スカイウィンドウ状態」をテキスト描画
    3. $t$ が画像復調区間の場合は右下に気象画像サムネイルを合成
    4. Canvas のピクセルバッファを `gifenc` で量子化・フレーム書き込み
  - ループ完了後、Blob を作成し、`<a download="pass_replay_<pass_id>.gif">` で即座にダウンロード発火

- [ ] **Step 4: End-to-End verification of GIF export**
  - `METEOR-M2 3_20261025_005344` の $t=30 \sim 80\text{秒}$ 区間を GIF エクスポートし、生成された GIF のフレームレート、テキスト情報、雲画像の出現が正しく記録されているか検証

- [ ] **Step 5: Commit**
  Run: `git add apps/satellite-viewer/app.py && git commit -m "feat: ブラウザ内高速アニメーションGIF切り出しダウンロード機能を実装"`

---

### Task 5: Docker イメージ更新・デプロイ ＆ 全体統合検証

**Files:**
- Modify: `apps/satellite-analyzer/Dockerfile`
- Modify: `infrastructure/viewer/viewer.yaml` (image tag or rollout)
- Docs: `docs/04_qa.md` (add Q&A regarding the sky radar simulation and GIF export)

**Interfaces:**
- Consumes: All completed components from Tasks 1-4
- Produces: Running Kubernetes Deployment with port 30088 accessible and verified

- [ ] **Step 1: Run full test suite across the repository**
  Run: `PYTHONPATH=apps/satellite-viewer pytest apps/satellite-viewer/ apps/satellite-analyzer/tests`
  Run: `cargo test --manifest-path apps/satellite-tracker-rs/Cargo.toml`
  Expected: All tests PASS.

- [ ] **Step 2: Build and deploy updated satellite-viewer container image**
  - Build `localhost:5000/satellite-analyzer-worker:v1.5.4` and `satellite-viewer` image
  - Apply to cluster and rollout restart `deployment/satellite-viewer`
  - Verify Pod status is `1/1 Running`

- [ ] **Step 3: End-to-End functional check on live viewer**
  - `curl -s http://localhost:30088/api/passes/.../trajectory` で軌道データが取得できることを確認
  - ブラウザでアクセスし、極座標天球レーダーの表示、観測リプレイ、GIF ダウンロードが正常に完了することを確認

- [ ] **Step 4: Update Documentation**
  - `docs/04_qa.md` に天球レーダーシミュレータと GIF 切り出し機能のアーキテクチャ・使い方を追記

- [ ] **Step 5: Commit and push**
  Run: `git commit` and `git push`
