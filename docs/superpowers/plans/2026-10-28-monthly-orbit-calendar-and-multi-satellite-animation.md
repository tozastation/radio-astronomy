# 月間軌道予測カレンダーと複数衛星・電波伝搬アニメーション 実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `satellite-viewer` に対し、向こう 1 ヶ月（当月）の全衛星パスとスカイウィンドウを一覧できる「月間カレンダー」と、宇宙を周回する複数衛星からアンテナへ電波が届く物理現象を体験できる「世界地図＆電波伝搬 Canvas アニメーション」を統合する。

**Architecture:** バックエンド（Python/SGP4）に適応的ステップ探索を用いた月間パス計算エンジンとキャッシュ付き API を新設し、フロントエンド（HTML5 Canvas 2D ＋ バニラ JS）に外部 CDN 不要で自己完結する世界地図・昼夜ターミネーター・波面リップル・距離減衰（FSPL）ビーム・開口窓ロックオン演出・タイムコントローラーを実装する。

**Tech Stack:** Python 3.12, SGP4 (WGS72), HTML5 Canvas 2D, Vanilla JavaScript, CSS, pytest

**Spec:** [docs/superpowers/specs/2026-10-28-monthly-orbit-calendar-and-multi-satellite-animation-design.md](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/docs/superpowers/specs/2026-10-28-monthly-orbit-calendar-and-multi-satellite-animation-design.md)

## Global Constraints

- **外部依存の禁止**: 世界地図データやアニメーションライブラリ（Leaflet, Cesium 等）の外部 CDN / 外部リクエストは一切使用せず、Viewer コンテナ内で自己完結する。
- **コミットルール**: 日本語記述、絵文字禁止、Conventional Commits 形式（`feat: ...`, `fix: ...`, `test: ...`）、簡潔な文体。
- **北向きベランダ幾何学**: 方位角 $250^\circ \sim 110^\circ$、仰角 $25^\circ \sim 60^\circ$ をスカイウィンドウ開口窓とする。
- **パフォーマンス要件**: 月間 30 日分の SGP4 パス計算は 2 秒以内に完了し、インメモリキャッシュヒット時は 10ms 以内に返却する。Canvas 描画は 60fps でスライダーに追従する。

## Review Focus

1. **月末・うるう年の境界処理**: 28日/30日/31日の月および年越し（12月→1月）で日付計算が狂わず、該当年月の正確な日数が算出されること。
2. **タイムゾーンの厳密性**: 内部計算・API 返却は UTC、カレンダー表示およびツールチップは JST（Asia/Tokyo）へ正確に変換され、日付ズレが生じないこと。
3. **電波ビームのFSPL計算異常**: 衛星が地球裏側にあるとき（LOS以下）に誤ってビームが結ばれないこと（電波地平線判定が機能すること）。
4. **開口窓突入判定の境界値**: 方位角が $0^\circ$（北）を跨ぐ区間（$250^\circ \sim 360^\circ \cup 0^\circ \sim 110^\circ$）で判定漏れが生じないこと。
5. **Canvas リサイズ時の崩れ**: ウィンドウリサイズやスマホ回転時にもアスペクト比と観測所・衛星座標の投影位置が正しく維持されること。

---

### Task 1: SGP4 月間パス高速探索エンジン（適応的ステップ探索）の実装

**Files:**
- Modify: `apps/satellite-viewer/orbit_calculator.py`
- Create: `apps/satellite-viewer/test_calendar_passes.py`

**Interfaces:**
- Produces: `OrbitCalculator.find_month_passes(year: int, month: int, targets: Optional[List[Tuple[str, str, str]]] = None, history_stats: Optional[Dict] = None, time_drifts: Optional[Dict] = None) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing test for `find_month_passes`**

```python
# apps/satellite-viewer/test_calendar_passes.py
import unittest
from datetime import datetime, timezone
from orbit_calculator import OrbitCalculator, DEFAULT_TARGET_TLES

class TestCalendarPasses(unittest.TestCase):
    def setUp(self):
        self.calc = OrbitCalculator()

    def test_find_month_passes_october_2026(self):
        targets = [("METEOR-M2 3", DEFAULT_TARGET_TLES["METEOR-M2 3"][0], DEFAULT_TARGET_TLES["METEOR-M2 3"][1])]
        start_t = datetime.now()
        data = self.calc.find_month_passes(year=2026, month=10, targets=targets)
        elapsed = (datetime.now() - start_t).total_seconds()

        self.assertLess(elapsed, 2.0, "Month pass generation must complete within 2.0 seconds")
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["month"], 10)
        self.assertIn("days", data)
        self.assertIn("2026-10-28", data["days"])
        self.assertGreater(len(data["days"]["2026-10-28"]), 0)
        p = data["days"]["2026-10-28"][0]
        self.assertIn("aos_time", p)
        self.assertIn("max_elevation", p)
        self.assertIn("quality_score", p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py -v`
Expected: FAIL with `AttributeError: 'OrbitCalculator' object has no attribute 'find_month_passes'`

- [ ] **Step 3: Implement `find_month_passes` in `apps/satellite-viewer/orbit_calculator.py`**

適応的ステップ探索（非通過時は 90 秒刻み、仰角 $\ge 5^\circ$ で 15 秒刻み）を用いて月間（初日 00:00 UTC から末日 23:59:59 UTC）のパスを高速抽出し、日付キー（`YYYY-MM-DD`）の辞書として集約する。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/orbit_calculator.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: SGP4適応的ステップ探索による月間パス計算エンジンを追加"
```

---

### Task 2: バックエンド API（`/api/calendar/passes`, `/api/satellites/tle`）とインメモリキャッシュの実装

**Files:**
- Modify: `apps/satellite-viewer/app.py`
- Modify: `apps/satellite-viewer/test_calendar_passes.py`

**Interfaces:**
- Produces:
  - `GET /api/calendar/passes?year=YYYY&month=MM`
  - `GET /api/satellites/tle`
  - `get_month_passes_cached(year: int, month: int, cache=None) -> Dict[str, Any]`

- [ ] **Step 1: Write the failing tests for API handlers and cache**

```python
# In apps/satellite-viewer/test_calendar_passes.py
from app import get_month_passes_cached, get_satellites_tle_data

class TestCalendarAPI(unittest.TestCase):
    def test_get_satellites_tle_data(self):
        data = get_satellites_tle_data()
        self.assertIn("satellites", data)
        self.assertGreaterEqual(len(data["satellites"]), 4)
        sat_names = [s["name"] for s in data["satellites"]]
        self.assertIn("METEOR-M2 3", sat_names)
        self.assertIn("tle1", data["satellites"][0])
        self.assertIn("tle2", data["satellites"][0])
        self.assertIn("color", data["satellites"][0])

    def test_get_month_passes_cached(self):
        data1 = get_month_passes_cached(2026, 10)
        self.assertIn("days", data1)
        # 2回目はキャッシュから瞬時に取得
        start_t = datetime.now()
        data2 = get_month_passes_cached(2026, 10)
        self.assertLess((datetime.now() - start_t).total_seconds(), 0.02)
        self.assertEqual(data1["days"], data2["days"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestCalendarAPI -v`
Expected: FAIL with `ImportError: cannot import name 'get_month_passes_cached'`

- [ ] **Step 3: Implement `get_month_passes_cached`, `get_satellites_tle_data`, and HTTP routing in `app.py`**

インメモリ辞書キャッシュ `_calendar_passes_cache: Dict[Tuple[int, int], Tuple[float, dict]]`（有効期限 3600 秒）を定義し、`app.py` の `do_GET` ハンドラに `/api/calendar/passes` と `/api/satellites/tle` のルーティングを追加。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: 月間パスおよび衛星TLE取得APIエンドポイントとキャッシュを追加"
```

---

### Task 3: 月間カレンダー UI コンポーネントの実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (HTML_TEMPLATE 内の CSS / HTML / JS)

**Interfaces:**
- Consumes: `GET /api/calendar/passes`
- Produces:
  - ヘッダーボタン: `🌍 軌道・電波アニメ`
  - モーダル: `#orbitCalendarModal`
  - カレンダーグリッド: `#calendarGrid`
  - 日別詳細カードリスト: `#calendarDayDetails`
  - JS 関数: `renderMonthlyCalendar(year, month)`, `selectCalendarDay(dateStr)`

- [ ] **Step 1: Write test to verify calendar DOM elements exist in `HTML_TEMPLATE`**

```python
# In apps/satellite-viewer/test_calendar_passes.py
from app import HTML_TEMPLATE

class TestCalendarUI(unittest.TestCase):
    def test_template_contains_calendar_elements(self):
        self.assertIn('id="orbitCalendarModal"', HTML_TEMPLATE)
        self.assertIn('id="calendarGrid"', HTML_TEMPLATE)
        self.assertIn('id="calendarDayDetails"', HTML_TEMPLATE)
        self.assertIn('renderMonthlyCalendar', HTML_TEMPLATE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestCalendarUI -v`
Expected: FAIL

- [ ] **Step 3: Implement Calendar HTML markup, CSS styling, and JavaScript logic in `app.py`**

1. ヘッダーに「🌍 軌道・電波アニメ」ボタンを追加。
2. モーダル `#orbitCalendarModal` 内に年月ナビゲーション、日〜土の7列グリッド、日別パス詳細リストを配置。
3. 衛星カラーピル、ゴールデンパス日（🌟 EXCELLENT）のエメラルド枠ハイライト、日付クリックによるパス詳細表示を実装。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestCalendarUI -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: 月間パス予測カレンダーのUIコンポーネントを追加"
```

---

### Task 4: 世界地図 ＆ 昼夜ターミネーター Canvas 2D 描画エンジンの実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (HTML_TEMPLATE 内の JavaScript / CSS)

**Interfaces:**
- Produces:
  - Canvas 要素: `#orbitWorldCanvas`
  - JS クラス/オブジェクト: `WorldMapRenderer`
  - 描画メソッド: `drawMap()`, `drawTerminator(utcDate)`, `drawTokyoStation()`

- [ ] **Step 1: Write test to verify WorldMapRenderer and Canvas elements in `HTML_TEMPLATE`**

```python
# In apps/satellite-viewer/test_calendar_passes.py
class TestWorldMapCanvas(unittest.TestCase):
    def test_template_contains_world_canvas(self):
        self.assertIn('id="orbitWorldCanvas"', HTML_TEMPLATE)
        self.assertIn('class WorldMapRenderer', HTML_TEMPLATE)
        self.assertIn('drawTerminator', HTML_TEMPLATE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestWorldMapCanvas -v`
Expected: FAIL

- [ ] **Step 3: Implement WorldMapRenderer with Equirectangular projection and Terminator in `app.py`**

1. 軽量ベクター大陸・海岸線ポリラインデータを埋め込み、Canvas 2D で描画。
2. 太陽直下点から計算した昼夜境界（大円）を計算し、夜側を半透明の暗青色グラデーションで塗りつぶす `drawTerminator(date)` を実装。
3. 東京観測地ピンおよび北向きベランダ見通し扇（方位 $250^\circ \sim 110^\circ$）を薄緑で投影。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestWorldMapCanvas -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: 世界地図と昼夜ターミネーターのCanvas描画エンジンを追加"
```

---

### Task 5: 複数衛星の軌道伝搬 ＆ 電波伝搬物理エフェクトの実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (HTML_TEMPLATE 内の JavaScript)

**Interfaces:**
- Consumes: `GET /api/satellites/tle`
- Produces:
  - JS クラス: `MultiSatellitePropagator`
  - 物理エフェクト: `drawWavefrontRipples()`, `drawSlantRangeBeam()`, `drawLockedCapturePulse()`

- [ ] **Step 1: Write test to verify propagator and radio physics in `HTML_TEMPLATE`**

```python
# In apps/satellite-viewer/test_calendar_passes.py
class TestRadioPhysicsAnimation(unittest.TestCase):
    def test_template_contains_radio_physics_logic(self):
        self.assertIn('class MultiSatellitePropagator', HTML_TEMPLATE)
        self.assertIn('drawWavefrontRipples', HTML_TEMPLATE)
        self.assertIn('drawSlantRangeBeam', HTML_TEMPLATE)
        self.assertIn('drawLockedCapturePulse', HTML_TEMPLATE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestRadioPhysicsAnimation -v`
Expected: FAIL

- [ ] **Step 3: Implement MultiSatellitePropagator and Radio Physics Effects in `app.py`**

1. クライアント側で SGP4/Kepler 軌道伝搬を行い、各衛星の緯度・経度・高度・直下点トラックを算出。
2. 衛星位置から同心円状の電波波面リングが広がるリップル演出 `drawWavefrontRipples` を実装。
3. 東京とのスラントレンジ $d$ を計算し、自由空間損失（FSPL）に連動して赤（遠方・弱電界）〜シアン（近接・強電界）に色が変化するリンクビーム `drawSlantRangeBeam` を実装。
4. 北向きベランダ開口窓（$250^\circ \sim 110^\circ$, 仰角 $25^\circ \sim 60^\circ$）内に入った瞬間、ビームがエメラルド色に発光し、アンテナへ向かってパルスが吸い込まれる `drawLockedCapturePulse` を実装。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestRadioPhysicsAnimation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: 複数衛星軌道伝搬と波面リップル・開口窓ロックオン電波演出を追加"
```

---

### Task 6: タイムコントローラーとカレンダー連動の実装

**Files:**
- Modify: `apps/satellite-viewer/app.py` (HTML_TEMPLATE 内の JavaScript / HTML / CSS)

**Interfaces:**
- Produces:
  - タイムスライダー: `#orbitTimeSlider`
  - 速度セレクター: `#orbitSpeedSelect`
  - 制御ボタン: `#btnPlayPause`, `#btnNextPass`, `#btnPrevPass`, `#btnLiveTime`
  - カレンダー連携: パスカードの「🎬 このパスを観測」から指定時刻へジャンプ

- [ ] **Step 1: Write test to verify time controller elements in `HTML_TEMPLATE`**

```python
# In apps/satellite-viewer/test_calendar_passes.py
class TestTimeController(unittest.TestCase):
    def test_template_contains_time_controller(self):
        self.assertIn('id="orbitTimeSlider"', HTML_TEMPLATE)
        self.assertIn('id="orbitSpeedSelect"', HTML_TEMPLATE)
        self.assertIn('id="btnPlayPause"', HTML_TEMPLATE)
        self.assertIn('id="btnNextPass"', HTML_TEMPLATE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestTimeController -v`
Expected: FAIL

- [ ] **Step 3: Implement TimeController and animation playback loop in `app.py`**

1. スライダーのドラッグイベントでシームレスに `currentSimTime` を更新し、Canvas を即時再描画（60fps）。
2. 再生（Play/Pause）、速度切り替え（1x, 10x, 60x, 300x, 1800x）による `requestAnimationFrame` 更新ループ。
3. 次のパス／前のパスへのワンクリック時刻ジャンプ。
4. カレンダーの詳細タイムラインから「🎬 このパスを観測」を押した際、AOS 2分前にセットしてアニメーションタブへ自動遷移する連携ロジック。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/satellite-viewer/test_calendar_passes.py::TestTimeController -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/satellite-viewer/app.py apps/satellite-viewer/test_calendar_passes.py
git commit -m "feat: タイムコントローラーおよびカレンダー連動ジャンプ機能を実装"
```

---

### Task 7: 統合動作確認と回帰テスト

**Files:**
- Test: `apps/satellite-viewer/test_calendar_passes.py`
- Test: `apps/satellite-viewer/test_viewer.py`
- Test: `apps/satellite-viewer/test_orbit_calculator.py`
- Test: `apps/satellite-viewer/test_telemetry_metrics.py`

- [ ] **Step 1: Run all existing and new test suites**

Run: `pytest apps/satellite-viewer/ -v`
Expected: All tests PASS with zero failures

- [ ] **Step 2: Verify API endpoint responses via mock client**

APIエンドポイント `/api/calendar/passes` および `/api/satellites/tle` が HTTP 200 と妥当な JSON を返すことを確認する結合テストを実行。

- [ ] **Step 3: Commit**

```bash
git add apps/satellite-viewer/test_calendar_passes.py
git commit -m "test: 月間カレンダーおよび電波伝搬アニメーションの統合テストを追加"
```
