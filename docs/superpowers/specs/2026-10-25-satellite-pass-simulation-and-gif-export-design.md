# 衛星通過天球シミュレータ ＆ 観測リプレイ GIF 切り出し機能 設計仕様書

- **作成日**: 2026-10-25
- **ステータス**: 設計承認済み（実装前レビュー段階）
- **対象コンポーネント**: `apps/satellite-viewer`（Python Web サーバー ＆ フロントエンド）

---

## 1. 目的とスコープ

### 1.1 背景と目的
東京都北向きベランダ電波観測所において、`METEOR-M2 3` の $137.900\text{ MHz}$ LRPT 初画像復調に成功した。
実測スペクトログラム解析により、復調成功の背景には「北向きスカイウィンドウ（方位角 $250^\circ \sim 110^\circ$、仰角 $30^\circ \sim 60^\circ$）」を衛星が通過した約 35 秒間の強電界（$\Delta P = +5.05\text{ dB}$, $E_s/N_0 \approx +3.88\text{ dB}$）と、南側マンション躯体による電波遮蔽という明確な幾何学的・物理的因果関係が存在することが実証された。

本機能の目的は、この観測履歴（S3 上の生 IQ スペクトログラム・受信電力・復調画像・CADU パケット）および今後の通過予測軌道に基づき、**「自宅ベランダの空を衛星がどう横切り、どこで電波が受かって画像が復元されたのか」を直感的にシミュレーション再生し、その任意の瞬間をアニメーション GIF として切り出してダウンロードできるインタラクティブ UI** を提供することである。

### 1.2 スコープ
- **対象**: `apps/satellite-viewer`（NodePort 30088）の拡張。
- **含まれる機能**:
  1. **北向きベランダ天球レーダー（2D 極座標スカイドーム）**:
     - 開口窓（スカイウィンドウ）のシアン色ハイライト表示
     - マンション南側壁面・天井庇・手すりの遮蔽マスク表示
     - 衛星通過軌道の電波強度ヒートマップ（青 $\to$ 緑 $\to$ 黄色）描画
  2. **観測リプレイ同期タイムライン**:
     - 衛星の天球上位置とスペクトログラム上の再生カーソルの完全同期
     - 強電界ゾーン突入時の「CADU 同期確立」インジケータと復調画像（雲画像）のポップアップ表示
  3. **クライアントサイド GIF 切り出し（GIF Clipper）**:
     - タイムライン上の [始点] と [終点] の範囲選択
     - ブラウザ内 Canvas キャプチャと軽量 `gifenc` によるアニメーション GIF 生成・ダウンロード
  4. **未来パス予測レーダー**:
     - 今後 24 時間に北向きスカイウィンドウを通過する衛星パスのカウントダウンと予定コースプレビュー
- **含まれないもの**:
  - リアルタイム SDR 録音制御の変更（`satellite-tracker-rs` の受信系には一切手を加えない）。
  - 重量な外部 3D グラフィックスエンジン（Babylon.js / heavy Cesium 等）の導入（モバイルでも 60fps 動作する軽量 HTML5 Canvas + SVG + `gifenc` で構成する）。

---

## 2. 天球レーダーの電磁幾何学と座標変換の数理

### 2.1 正距方位極座標投影（Azimuthal Equidistant Projection）

天球上の衛星位置（方位角 $\text{Az}$、仰角 $\text{El}$）を 2D レーダー画面（ピクセル座標 $x, y$）へ投影する変換式は次式で定義される。

$$r = R_{\max} \cdot \left(1 - \frac{\text{El}}{90^\circ}\right), \qquad x = x_c + r \sin(\text{Az}), \qquad y = y_c - r \cos(\text{Az})$$

#### 記号一覧
| 記号 | 物理的・幾何学的意味 | 単位 | 設定値・範囲 |
| :--- | :--- | :--- | :--- |
| $\text{Az}$ | 衛星の方位角（北を $0^\circ$ とし時計回り：東 $90^\circ$, 南 $180^\circ$, 西 $270^\circ$） | 度（$^\circ$） | $0^\circ \le \text{Az} < 360^\circ$ |
| $\text{El}$ | 衛星の仰角（地平線を $0^\circ$、天頂を $90^\circ$ とする見上げ角） | 度（$^\circ$） | $0^\circ \le \text{El} \le 90^\circ$ |
| $R_{\max}$ | レーダー画面における地平線（$\text{El}=0^\circ$）の外周半径 | ピクセル | キャンバスサイズの約 $45\%$ |
| $(x_c, y_c)$ | レーダー円の中心ピクセル座標（天頂 $\text{El}=90^\circ$） | ピクセル | キャンバス中心 |
| $r$ | 中心（天頂）から衛星描画点までの画面上半径距離 | ピクセル | $0 \le r \le R_{\max}$ |
| $(x, y)$ | 描画ピクセル座標 | ピクセル | スクリーン座標系（$y$ 軸下向き） |

#### 日本語での読み解き
天頂（真上 $\text{El}=90^\circ$）を中心点 $r=0$ とし、地平線（$\text{El}=0^\circ$）を最外周円 $r=R_{\max}$ とする。衛星の仰角が高くなるほど中心に近づき、仰角が低くなるほど外側に位置する。方位角 $\text{Az}$ は時計の針のように北が $12$ 時方向、東が $3$ 時方向、南が $6$ 時方向、西が $9$ 時方向に対応する。

#### 展開ステップ・直感イメージ
1. 天頂 $\text{El} = 90^\circ$ のとき：
   $$r = R_{\max} \left(1 - \frac{90}{90}\right) = 0 \implies (x, y) = (x_c, y_c)$$
2. 地平線 $\text{El} = 0^\circ$、真北 $\text{Az} = 0^\circ$ のとき：
   $$r = R_{\max}, \quad x = x_c + R_{\max}\sin(0^\circ) = x_c, \quad y = y_c - R_{\max}\cos(0^\circ) = y_c - R_{\max} \quad (\text{真上端})$$
3. 最高仰角通過点（例：今朝の METEOR-M2 3 TCA: $\text{Az}=34.2^\circ, \text{El}=48.5^\circ$）のとき：
   $$r = R_{\max} \left(1 - \frac{48.5}{90}\right) \approx 0.461 R_{\max}$$
   $$x = x_c + 0.461 R_{\max} \sin(34.2^\circ) \approx x_c + 0.259 R_{\max}, \quad y = y_c - 0.461 R_{\max} \cos(34.2^\circ) \approx y_c - 0.381 R_{\max}$$
   （北北東の空・同心円のほぼ中間に正確にマッピングされる）。

---

### 2.2 北向きベランダの開口窓（スカイウィンドウ）判定数理

観測地点の建築構造制約に基づき、任意の時刻における衛星が「ベランダから見通せるか（Line of Sight: LOS）」を判定する論理関数 $\text{IsInWindow}(\text{Az}, \text{El})$ は次式で表される。

$$\text{IsInWindow}(\text{Az}, \text{El}) = \left( \text{Az} \ge 250^\circ \lor \text{Az} \le 110^\circ \right) \land \left( \text{El}_{\min}(\text{Az}) \le \text{El} \le \text{El}_{\max} \right)$$

$$\text{El}_{\min}(\text{Az}) = 25.0^\circ, \qquad \text{El}_{\max} = 60.0^\circ$$

- **開口扇形領域**:
  $\text{Az} \in [250^\circ, 360^\circ] \cup [0^\circ, 110^\circ]$ かつ $\text{El} \in [25^\circ, 60^\circ]$。
  レーダー上では、半透明のシアン色（`rgba(56, 189, 248, 0.2)`）の扇形リングとして美しく光る。
- **建物遮蔽領域**:
  南側 $\text{Az} \in (110^\circ, 250^\circ)$ および 天頂庇 $\text{El} > 60^\circ$。
  ダークグレー（`rgba(15, 23, 42, 0.85)`）の斜線ストライプパターンでマスクされ、電波が届かない死角であることが視覚的に直感できる。

---

## 3. UI/UX ＆ 観測リプレイ機能設計

### 3.1 画面レイアウト構成
既存の `apps/satellite-viewer` にモーダルダイアログとして実装する。

1. **上段（可視化セクション）**:
   - **左側: 天球レーダー（Canvas）**:
     - 幅 $360\text{ px} \times 360\text{ px}$（モバイル時は画面幅に自動追従）。
     - 同心円（$30^\circ, 60^\circ$）、東西南北の軸線、北向きスカイウィンドウの扇形。
     - 過去の通過全軌道を破線でプレビュー（受信強度に応じたカラー）。
     - 現在時刻の衛星アイコン（🛰️）、進行方向矢印、直近 5 秒間の残光トレイル。
   - **右側: テレメトリ ＆ 復調データ情報パネル**:
     - 衛星名・パス ID
     - 再生時刻（JST）および相対経過時間 $t$（秒）
     - リアルタイム仰角 $\text{El}$、方位角 $\text{Az}$、斜距離 $d$（km）
     - 視界ステータスバッジ: `[🟢 スカイウィンドウ内 (見通し良好)]` / `[🏢 建物遮蔽 (南側壁面)]`
     - 受信強度インジケータ: $\Delta P\text{ [dB]}$（プログレスバー表示）
     - **復調画像ポップアップ**: 信号しきい値突破（$+3.5\text{ dB}$）ゾーンに到達すると、`MSU-MR-1.png` や疑似カラー画像がサムネイル表示され、タップで原寸大プレビュー可能。

2. **中段（スペクトログラム連動タイムライン）**:
   - S3 から取得した `spectrogram.png` を幅 $100\%$ で配置。
   - 再生時刻 $t$ に合わせて、**赤い垂直シークバー（タイムカーソル）がスペクトログラム上をリアルタイムに横スクロール**。
   - スペクトログラムの「黄色の盛り上がり」と、天球レーダーの「北の窓の最高仰角」が完全に連動している様子を目の当たりにできる。

3. **下段（再生 ＆ GIF 切り出しコントロールバー）**:
   - **再生操作**: [▶ 再生] / [⏸ 一時停止]、再生速度変更（$1\times, 2\times, 5\times, 10\times$）、手動シークスライダー。
   - **GIF 切り出し操作（Clipper）**:
     - タイムライン上に範囲指定スライダー（始点ピン $t_{\text{start}}$ と 終点ピン $t_{\text{end}}$）。
     - ワンクリック設定ボタン: `[📍 現在位置を始点に]` / `[📍 現在位置を終点に]`。
     - 推奨区間プリセットボタン: `[✨ 最接近ピーク区間（30s〜80s）を自動選択]`。
     - **`[🎬 この区間をアニメーション GIF でダウンロード]`** ボタン。

---

## 4. クライアントサイド GIF 生成エンジンの技術仕様

### 4.1 技術選定：`gifenc`
ブラウザ内で Canvas の描画内容を高速にアニメーション GIF にエンコードするライブラリとして、最新の **`gifenc`**（npm: `gifenc`, 約 8 KB gzip）を採用する。

- **理由**:
  1. WebAssembly 不要の Pure ES6 / TypedArray 実装で、全ブラウザ・スマートフォン（iOS Safari, Android Chrome）で動作。
  2. 高速 NeuQuant / MedianCut パレット量子化と LZW 圧縮アルゴリズムを内蔵。
  3. 依存ライブラリゼロ。CDN（`esm.sh` またはインライン埋め込み）から読み込み可能。

### 4.2 GIF 生成パイプライン
ユーザーが `[🎬 GIF でダウンロード]` を押した際の流れ：

```
[ユーザーが始点 t1, 終点 t2 を指定]
             ⬇
[オフスクリーン Canvas (幅 480px, 高さ 360px) を生成]
             ⬇
[t = t1 から t2 まで、指定フレームレート (例: 10 fps) でループ]
  1. オフスクリーン Canvas に天球レーダーを描画
  2. 衛星アイコンを時刻 t の位置 (x, y) に描画
  3. 画面上部にタイムスタンプ、Az/El、信号強度 ΔP をテキストオーバーレイ
  4. t が画像復調区間の場合は右下に MSU-MR 雲画像のサムネイルを合成
  5. Canvas の ImageData (RGBA) を取得
  6. gifenc.applyPalette() で 256 色パレットへ量子化
  7. gifenc.writeFrame() でフレーム追加
             ⬇
[エンコード完了 (約 1 秒)]
             ⬇
[Blob 生成: new Blob([gif.stream.getBuffer()], { type: 'image/gif' })]
             ⬇
[自動ダウンロード: a.download = 'pass_meteor_m2_3_replay.gif'; a.click()]
```

- **出力 GIF 仕様**:
  - 解像度: $480 \times 360\text{ px}$（モバイル閲覧・SNS 投稿に最適なサイズ）
  - フレームレート: $10\text{ fps}$（$50\text{ 秒}$ の実時間を $5\text{ 秒}$ のタイムラプス動画に圧縮）
  - 容量目安: 約 $1.2 \sim 1.8\text{ MB}$（Discord や X のファイルサイズ上限を余裕でクリア）

---

## 5. バックエンド API 設計（`apps/satellite-viewer/app.py`）

サーバー側には、軌道計算とデータ供給のための 2 つの軽量 REST API を追加する。

### 5.1 `GET /api/passes/{pass_id}/trajectory`
指定された観測パスの $1\text{ 秒}$ ごとの軌道時系列データを返す。

- **処理内容**:
  1. `pass_id` から衛星名（`METEOR-M2 3` 等）と録音開始時刻（UTC）をパース。
  2. S3 またはローカル TLE カタログ（`assets/satdump_tles.txt`）から当該衛星の TLE を取得。
  3. 録音長（例：$406\text{ 秒}$）にわたり、$1\text{ 秒}$ 刻みで観測者位置（東京・北緯 35.6895, 東経 139.6917, 標高 30m）からの $\text{Az}, \text{El}, d$ を SGP4 計算。
  4. 当該パスに復調画像が存在する場合、画像生成に対応する時間帯（$t=40 \sim 75\text{s}$）に `has_image: true` フラグを付与。
- **レスポンス JSON 形式**:
  ```json
  {
    "pass_id": "METEOR-M2 3_20261025_005344",
    "satellite": "METEOR-M2 3",
    "total_seconds": 406,
    "points": [
      { "t": 0, "az": 95.1, "el": 18.0, "range_km": 1540.2, "in_window": false, "has_image": false },
      { "t": 46, "az": 34.2, "el": 48.5, "range_km": 1052.1, "in_window": true, "has_image": true },
      { "t": 220, "az": 235.1, "el": 11.2, "range_km": 2150.8, "in_window": false, "has_image": false }
    ],
    "images": [
      { "name": "MSU-MR-1.png", "url": "/view?key=results/METEOR-M2%203/METEOR-M2%203_20261025_005344/MSU-MR-1.png" },
      { "name": "msu_mr_rgb_MSU-MR_124_False_Color_corrected.png", "url": "/view?key=results/METEOR-M2%203/METEOR-M2%203_20261025_005344/msu_mr_rgb_MSU-MR_124_False_Color_corrected.png" }
    ]
  }
  ```

### 5.2 `GET /api/future-passes`
今後 24 時間に北向きベランダのスカイウィンドウを通過する「次の観測チャンス」を返す。

- **レスポンス JSON 形式**:
  ```json
  {
    "generated_at": "2026-10-25T13:30:00Z",
    "passes": [
      {
        "satellite": "METEOR-M2 4",
        "aos_time": "2026-10-25T21:42:10Z",
        "tca_time": "2026-10-25T21:47:30Z",
        "max_elevation": 54.2,
        "max_elevation_azimuth": 38.0,
        "window_duration_seconds": 95,
        "quality_score": "EXCELLENT (スカイウィンドウ直撃)"
      }
    ]
  }
  ```

---

## 6. テスト ＆ 検証計画

### 6.1 ユニットテスト（Python）
`apps/satellite-viewer/test_viewer.py` にテストケースを追加：
1. `test_trajectory_calculation_for_known_pass`:
   `METEOR-M2 3_20261025_005344` に対し、TCA 付近の仰角が実測値（$\approx 48.5^\circ$、方位角 $\approx 34^\circ$）と一致するか検証。
2. `test_trajectory_api_response_format`:
   `/api/passes/{id}/trajectory` が正しい JSON スキーマと画像 URL を返すか検証。
3. `test_future_passes_filtering`:
   南側のみを通過する非可視パスが除外され、北向きスカイウィンドウ通過パスのみが正しく抽出されるか検証。

### 6.2 フロントエンド ＆ E2E 動作検証
1. **ブラウザ動作確認**:
   - Chrome / Safari / スマホブラウザにおいて、天球レーダーが歪みなく Canvas 描画されるか。
   - タイムライン再生時にスペクトログラムカーソルと衛星アイコンがラグなく同期するか。
2. **GIF 書き出し検証**:
   - $t=30 \sim 80\text{秒}$ を選択して `[🎬 GIF でダウンロード]` を実行。
   - 生成された `.gif` ファイルを画像ビューアで開き、アニメーションが滑らかに再生され、雲画像ポップアップとテキスト情報が正しく記録されているか検証。
   - ファイルサイズが $2\text{ MB}$ 以下に収まっているか確認。
