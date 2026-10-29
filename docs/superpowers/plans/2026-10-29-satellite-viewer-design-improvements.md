# Satellite Viewer デザイン・UI・物理可視化 総合改善計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apps/satellite-viewer` の UI/UX 表示崩れ（モバイル対応）、ダークマター検証・ケプラー回転数値の物理的整合性、API エラーハンドリング、アクセシビリティ（フォントサイズ）、Canvas 高DPI対応、および JavaScript スコープ設計を上から順に体系的に改善する。

**Architecture:** Python/HTTP サーバ上の単一 SPA ビューアにおいて、CSS クラスのモジュール化によるレスポンシブ崩れの解消、IAU/オオルトモデルに基づく天文学計算・表記の厳密化、Canvas の `devicePixelRatio` 鮮明化、およびエラー状態の UI フィードバックを導入する。

**Tech Stack:** Python 3.12, HTML5, CSS3, JavaScript (Canvas API), pytest, Node.js (`node -c`)

**Spec:** 前述のデザイン監査における 3 区分（UI/UX・レスポンシブ、天文・DSP物理厳密性、フロントエンド・アーキテクチャ）の全指摘事項

## Global Constraints

- `GEMINI.md` のコミットルール（日本語、絵文字禁止、Conventional Commits `feat:`, `fix:`, `docs:`, `style:`, `refactor:`）を遵守する。
- 既存の `pytest apps/satellite-viewer`（全48テスト）および `node -c` による JavaScript 構文解析回帰テストを常時パスさせる。
- 数式・理論の解説は [docs/00_glossary.md](../../docs/00_glossary.md) を参照し、記号一覧・日本語の読み解き・展開ステップをセットにする。
- 作業ペースは一方的に進めず、ステップごとに検証と報告を行う。

## Review Focus

- モバイル（画面幅 360px〜412px）で銀河モーダルを開いた際に水平スクロールやコンポーネントのはみ出しが発生しないこと。
- ケプラー回転速度 $V_{\text{kepler}}$ の計算式 $V_{\text{kepler}} = V_0 \sqrt{R_0 / R}$ とペルセウス腕（$R=10.3\text{ kpc}$）の数値整合性が保たれていること。
- S3 / API 取得がネットワークエラーになった際、ローディング表示のまま固まらずエラーメッセージと再試行ボタンが表示されること。
- 生成ファイル一覧が初期状態で折りたたまれ、カード全体の縦幅が過大にならないこと。
- 高解像度ディスプレイ（DPI 2.0+）で Canvas 描画がぼやけず鮮明にレンダリングされること。

---

### Task 1: 銀河モーダルのレスポンシブ CSS クラス化とモバイル横はみ出し解消

**Files:**
- Modify: `apps/satellite-viewer/app.py:1260-1440, 1720-1730`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `.galactic-viz-grid` CSS クラス定義
- Produces: モバイル幅（< 640px）で `grid-template-columns: 1fr` に折り返される 2 カラムグリッド

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_galactic_modal_responsive_grid` を追加し、`HTML_TEMPLATE` 内にインラインの `minmax(420px, 1fr)` が存在せず、`.galactic-viz-grid` クラスおよび `@media (max-width: 640px)` での上書きが存在することを検証する。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_galactic_modal_responsive_grid`
  Expected: FAIL (assertion error: `galactic-viz-grid` not in HTML_TEMPLATE)

- [ ] **Step 3: `apps/satellite-viewer/app.py` の CSS および HTML マークアップを修正**
  - CSS に `.galactic-viz-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; margin-bottom: 16px; }` を追加。
  - `@media (max-width: 640px)` 内に `.galactic-viz-grid { grid-template-columns: 1fr; }` を追加。
  - HTML の L1726 を `<div class="galactic-viz-grid">` に置換。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_galactic_modal_responsive_grid`
  Expected: PASS

- [ ] **Step 5: コミット**
  `fix: 銀河観測モーダルのグリッドをCSSクラス化しモバイル幅での横はみ出しを修正`

---

### Task 2: ダークマター検証・ケプラー回転数値の物理的整合性修正

**Files:**
- Modify: `apps/satellite-viewer/app.py:1770-1785, 1945-1950, 4185-4200`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: $R_0 = 8.2\text{ kpc}$, $V_0 = 220.0\text{ km/s}$, $R = 10.3\text{ kpc}$（ペルセウス腕）
- Produces: $V_{\text{kepler}} = 196.3\text{ km/s}$、超過比 $1.26$ 倍（ペルセウス腕）、および最外縁（$R \approx 20\text{ kpc}$）における超過比 $2.43$ 倍の併記

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_dark_matter_kepler_velocity_consistency` を追加し、HTML および JS 内でペルセウス腕のケプラー速度が $196.3\text{ km/s}$、質量比 $1.26$ 倍として正しく計算・表示されていること、および最外縁 $R=20\text{ kpc}$ の補足が含まれていることを検証する。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_dark_matter_kepler_velocity_consistency`
  Expected: FAIL

- [ ] **Step 3: `apps/satellite-viewer/app.py` の数値と解説文を修正**
  - L1777: `galacticDmKeplerV` の初期値を `196.3 km/s`、L1781 の超過比を `1.26 倍 (外縁部 2.43倍)` に修正。
  - L1785: 解説文でペルセウス腕（$R=10.3\text{ kpc}$）での減速予測（$196.3\text{ km/s}$）と、銀河外縁部（$R \approx 20\text{ kpc}$）における $141.2\text{ km/s}$・超過比 $2.43$ 倍の関係を正確に記述。
  - L1948-1950: Q3 の解説文を更新。
  - L4189-4197: JS の `galacticUpdateInsights` で `V_kepler = 196.3;`、超過比 `ratio = Math.pow(V_measured / V_kepler, 2);`（1.26倍）を算出し、解説文に外縁部（$R \approx 20\text{ kpc}$）での $2.43$ 倍の比較を正しく反映。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_dark_matter_kepler_velocity_consistency`
  Expected: PASS

- [ ] **Step 5: コミット**
  `fix: ダークマター検証のペルセウス腕ケプラー速度と外縁部ハロー比率の数値を修正`

---

### Task 3: API 通信エラー時の UI 状態管理と再試行ボタンの追加

**Files:**
- Modify: `apps/satellite-viewer/app.py:4055-4075`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `/api/galactic-observations` fetch promise rejection / HTTP error
- Produces: ユーザー向けエラーメッセージ表示および再試行ボタン

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_galactic_fetch_error_handling_ui` を追加し、HTML テンプレート内にエラー表示コンテナ（`id="galacticErrorBanner"` 等）と再試行アクションが含まれていることを検証する。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_galactic_fetch_error_handling_ui`
  Expected: FAIL

- [ ] **Step 3: `apps/satellite-viewer/app.py` にエラー表示とハンドリングを実装**
  - 銀河モーダル上部に `galacticErrorBanner`（初期非表示）を配置。
  - `galacticFetchObservations()` の `catch` 節で `galacticErrorBanner` を表示し、エラー内容と「🔄 再試行」ボタンを提示する。
  - 成功時には `galacticErrorBanner` を非表示にする。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_galactic_fetch_error_handling_ui`
  Expected: PASS

- [ ] **Step 5: コミット**
  `feat: 銀河観測モーダルにAPI通信エラー表示と再試行UIを追加`

---

### Task 4: 生成ファイル一覧アコーディオンの初期折りたたみ（スクロール負荷軽減）

**Files:**
- Modify: `apps/satellite-viewer/app.py:5605-5615`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `render_card_html`
- Produces: `<details class="data-accordion files-accordion">` (open 属性なし)

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` の `test_mobile_responsive_optimizations` で `files-accordion` に `open` 属性が含まれないことを検証するテストを追加。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_mobile_responsive_optimizations`
  Expected: FAIL (or specific new test)

- [ ] **Step 3: `apps/satellite-viewer/app.py` を修正**
  `render_card_html` 内の `files_accordion` から `<details ... open>` の `open` 属性を除去。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  `style: 観測カードの生成ファイル一覧アコーディオンを初期折りたたみに変更`

---

### Task 5: 極小フォントサイズの底上げ・24h ドリフトグラフ Y 軸インジケータ・サマリ連動改善

**Files:**
- Modify: `apps/satellite-viewer/app.py:1790-1805, 4200-4215, 4710-4745`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `galacticDriftCanvas` Y 軸描画, サマリカード文字サイズ
- Produces: 視認性の高いフォントサイズ（$\ge 0.75\text{rem}$）、直感的な銀河面/ハロー方向インジケータ、銀経 $l$ に応じたサマリ注記

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_font_size_and_drift_axis_indicator` を追加。フォントサイズが `0.75rem` 以上であること、ドリフトグラフに銀河面/ハローの軸方向テキストが含まれることを検証。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_font_size_and_drift_axis_indicator`
  Expected: FAIL

- [ ] **Step 3: `apps/satellite-viewer/app.py` を修正**
  - L1790-1805: サマリカードの `font-size: 0.65rem;` / `0.68rem;` を `0.75rem;`（補足は `0.72rem;`）に底上げ。
  - `galacticRenderDrift`: 縦軸目盛り横に「天の川銀河面 ↑」「銀河ハロー ↓」の補助ラベルを追加。
  - `galacticUpdateInsights`: 銀経 $l$ に応じたサマリカードのテキスト（はくちょう座領域／反銀河中心方向に応じた表記）を適切に調整。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_font_size_and_drift_axis_indicator`
  Expected: PASS

- [ ] **Step 5: コミット**
  `style: サマリカードのフォントサイズ底上げとドリフトグラフY軸インジケータを追加`

---

### Task 6: Canvas の Retina / 高DPIディスプレイ対応（`devicePixelRatio`）

**Files:**
- Modify: `apps/satellite-viewer/app.py:4213-4240, 4466-4490, 4680-4700`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: `window.devicePixelRatio`
- Produces: 鮮明な高解像度 Canvas レンダリング

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_canvas_high_dpi_helper` を追加し、Canvas 描画関数内で高DPI対応ヘルパー（例: `galacticSetupCanvasDpi`）が呼び出されていることを検証する。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_canvas_high_dpi_helper`
  Expected: FAIL

- [ ] **Step 3: `apps/satellite-viewer/app.py` に高DPIヘルパーを実装・適用**
  `galacticSetupCanvasDpi(canvas, defaultW, defaultH)` を定義し、`rect = canvas.getBoundingClientRect()` と `dpr = window.devicePixelRatio || 1` を用いて内部バッファ解像度をスケーリングする。
  `galacticRenderTopDownMap`, `galacticRenderSpectrum`, `galacticRenderDrift` に適用。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py`
  Expected: PASS

- [ ] **Step 5: コミット**
  `feat: 銀河観測モニターの各CanvasにRetina高DPIディスプレイ対応を導入`

---

### Task 7: JavaScript スコープのカプセル化と名前空間の健全化

**Files:**
- Modify: `apps/satellite-viewer/app.py:2300-2330, 2900-2950`
- Test: `apps/satellite-viewer/test_viewer.py`

**Interfaces:**
- Consumes: カレンダーおよび世界地図アニメーション用状態変数
- Produces: `orbit*` プレフィックスによるスコープ衝突の完全防止

- [ ] **Step 1: 失敗するテストを書く**
  `test_viewer.py` に `test_js_variable_namespace_prefixed` を追加し、アニメーションやカレンダーのグローバル変数が適切なプレフィックス（`orbit*` 等）を持っていることを検証する。

- [ ] **Step 2: テストを実行して失敗することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py -k test_js_variable_namespace_prefixed`
  Expected: FAIL

- [ ] **Step 3: `apps/satellite-viewer/app.py` の変数名をリファクタリング**
  `currentCalYear` -> `orbitCalYear`、`currentCalMonth` -> `orbitCalMonth`、`calendarMonthData` -> `orbitCalendarMonthData` 等に整理し、重複定義を排除。

- [ ] **Step 4: テストを実行して成功することを確認**
  Run: `pytest apps/satellite-viewer/test_viewer.py`
  Expected: PASS (`node -c` 構文テストも通過することを確認)

- [ ] **Step 5: コミット**
  `refactor: カレンダーおよび軌道アニメーション変数のプレフィックスを統一`

---

### Task 8: 全体結合検証・ドキュメント更新（`docs/04_qa.md`）

**Files:**
- Modify: `docs/04_qa.md`
- Test: `pytest apps/satellite-viewer`

**Interfaces:**
- Consumes: 今回実施した全改善項目の知見
- Produces: Q&A ドキュメントへの体系的追記

- [ ] **Step 1: 全 pytest スイートを実行**
  Run: `pytest apps/satellite-viewer -v`
  Expected: 全テスト PASS

- [ ] **Step 2: `docs/04_qa.md` に今回のデザイン改善・物理量是正の解説を追記**
  ケプラー速度と暗黒物質超過比の幾何学的計算モデル、高DPI Canvas 描画のフロントエンド設計、およびモバイルレスポンシブ最適化のポイントを整理して追記。

- [ ] **Step 3: コミット**
  `docs: 04_qaにビューアのデザイン改善とケプラー速度計算モデルの解説を追記`
