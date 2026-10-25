# 衛星通過天球シミュレータ＆観測リプレイ GIF 切り出し機能の電磁幾何学・軌道力学・SRE アーキテクチャ詳解

東京都内の北向きベランダ（北緯 $35.6895^\circ$、東経 $139.6917^\circ$、標高 $30\text{ m}$）に設置されたベランダ電波観測所において、上空を通過する人工衛星（気象衛星 METEOR-M2 3/4、国際宇宙ステーション ISS、アマチュア衛星 FUNCUBE-1 等）の軌道幾何学を可視化する「2D 極座標天球レーダーシミュレータ」および「観測リプレイ同期・クライアントサイド GIF 切り出し機能」を設計・実装した。

本ドキュメントでは、正距方位極座標投影（Azimuthal Equidistant Projection）の幾何学、北向きベランダのスカイウィンドウ数理、SGP4 から Topocentric ENU への一連の座標変換理論、および GPD Pocket3（KubeEdge エッジノード）のリソースを一切消費させないゼロ負荷クライアントサイド GIF 生成（`gifenc`）の SRE アーキテクチャを体系的に解説する。

---

## 1. 2D 極座標天球レーダーの電磁幾何学

### 1.1 正距方位極座標投影（Azimuthal Equidistant Projection）

天頂（Zenith: $\text{El} = 90^\circ$）を中心とし、全周囲の地平線（Horizon: $\text{El} = 0^\circ$）を外周円とする極座標レーダー投影を採用している。

$$r(\text{El}) = R_{\max} \cdot \left(1 - \frac{\text{El}}{90^\circ}\right)$$

$$x = x_c + r(\text{El}) \cdot \sin(\theta)$$

$$y = y_c - r(\text{El}) \cdot \cos(\theta)$$

$$\theta = \text{Az} \cdot \frac{\pi}{180^\circ}$$

#### 記号一覧
| 記号 | 物理的・幾何学的意味 | 単位 |
| :--- | :--- | :--- |
| $\text{El}$ | 衛星の仰角（Elevation: 地平線から天頂への角度） | 度（$^\circ$） |
| $\text{Az}$ | 衛星の方位角（Azimuth: 真北を $0^\circ$ とし東回りに測る角度） | 度（$^\circ$） |
| $R_{\max}$ | レーダー画面上の地平線円（$\text{El}=0^\circ$）の半径 | ピクセル（$\text{px}$） |
| $(x_c, y_c)$ | レーダー Canvas の中心座標（天頂 $\text{El}=90^\circ$ に相当） | ピクセル（$\text{px}$） |
| $r(\text{El})$ | 中心 $(x_c, y_c)$ からの描画動径長 | ピクセル（$\text{px}$） |
| $\theta$ | 数学的な方位角ラジアン | $\text{rad}$ |
| $(x, y)$ | Canvas 2D コンテキスト上の直交描画座標 | ピクセル（$\text{px}$） |

#### 日本語での読み解き
- 天頂（真上 $\text{El} = 90^\circ$）に衛星がいるとき動径 $r(90^\circ) = 0$ となり、画面の中心点 $(x_c, y_c)$ にプロットされる。
- 地平線（$\text{El} = 0^\circ$）に衛星がいるとき動径 $r(0^\circ) = R_{\max}$ となり、最外周円上にプロットされる。
- 仰角が線形に減少するにつれて動径 $r$ は線形に増大するため、天球上の仰角スケール（$30^\circ, 60^\circ$ 同心円）が等間隔に配置される。
- 方位角 $\text{Az}$ は真北（$0^\circ$）を上向き（$-Y$ 方向）、東（$90^\circ$）を右向き（$+X$ 方向）、南（$180^\circ$）を下向き（$+Y$ 方向）、西（$270^\circ$）を左向き（$-X$ 方向）に対応させて直交座標系 $(x, y)$ へ写像する。

#### 展開ステップ・直感イメージ
1. **動径 $r$ の線形スケーリング**:
   通常、物理的な天球上の仰角 $\text{El}$ は天頂角 $z = 90^\circ - \text{El}$ で表される。正距方位投影では、動径 $r$ は天頂角 $z$ に正比例する：
   $$r \propto z = 90^\circ - \text{El} \implies r(\text{El}) = R_{\max} \cdot \frac{90^\circ - \text{El}}{90^\circ} = R_{\max} \left(1 - \frac{\text{El}}{90^\circ}\right)$$
2. **Canvas 座標系への幾何学的射影**:
   コンピュータグラフィックス（HTML5 Canvas）では原点 $(0, 0)$ が左上にあり、下向きが $+Y$ 軸である。
   真北（$\text{Az} = 0^\circ$）を「真上（$-Y$ 方向）」に合わせるため：
   $$\Delta x = r \sin(\text{Az}), \quad \Delta y = -r \cos(\text{Az})$$
   となり、中心座標 $(x_c, y_c)$ を加算することで、直観的なレーダースクリーン座標が得られる。

---

## 2. 北向きベランダの開口窓（スカイウィンドウ）と建物遮蔽の数理

観測地点は東京都内のマンション北向きベランダであり、見通し空間（スカイウィンドウ）は幾何学的・建築学的に明確に制限されている。

```
                    【上階スラブ庇 (El > 60° 遮蔽)】
         --------------------------------------------------
                             ▲
                             │   開口窓 (スカイウィンドウ)
                             │   Az: 250° 〜 110° (西〜北〜東)
                             │   El: 25° 〜 60°
                             ▼
         ==================================================
                    【ベランダ手すり (El < 25° 遮蔽)】
```

### 2.1 スカイウィンドウ境界条件

衛星の位置 $(\text{Az}, \text{El})$ が以下の論理積を満たすとき、衛星はベランダから電波を直接受信できる開口窓内に存在する：

$$\text{in\_window}(\text{Az}, \text{El}) = \left(25^\circ \le \text{El} \le 60^\circ\right) \land \left(\text{Az} \ge 250^\circ \lor \text{Az} \le 110^\circ\right)$$

#### 幾何学的境界の物理的意味
1. **仰角下限 $25^\circ$（手すり遮蔽）**:
   ベランダのコンクリートおよび金属製手すりにより、仰角 $25^\circ$ 未満の電波は物理的に遮られ、手すり金属面での反射・回折およびマルチパス干渉（Lloyd's Mirror 効果）が生じる。
2. **仰角上限 $60^\circ$（上階スラブ庇遮蔽）**:
   上階ベランダの張り出し庇（ひさし）によって、仰角 $60^\circ$ を超える天頂付近の電波は鉄筋コンクリート（RC）躯体に突入し、約 $30 \sim 50\text{ dB}$ の深刻な透過減衰を受ける。
3. **方位角範囲 $250^\circ \sim 110^\circ$（北向き視界）**:
   東南東（$110^\circ$）から南を経て西南西（$250^\circ$）までの $140^\circ$ の方位帯はマンション本館の居住棟躯体によって完全に塞がれている。

---

## 3. SGP4 軌道伝搬から Topocentric ENU への座標変換

衛星の 2 行軌道要素形式（TLE: Two-Line Elements）から観測者基準の局所座標（$\text{Az}, \text{El}, \text{Range}$）を算出する数学的プロセスは、以下の 4 段階の座標変換からなる。

```
[TLE (NORAD ID)]
       │
       ▼ (SGP4 軌道力学伝搬)
[TEME 慣性座標系: r_teme]
       │
       ▼ (グリニッジ平均恒星時 GMST による自転回転)
[ECEF 地球固定座標系: r_ecef]
       │
       ▼ (観測者測地座標 WGS84 ECEF とのベクトル差分)
[Topocentric ENU 局所座標系: (East, North, Up)]
       │
       ▼ (三角関数変換)
[観測諸元: Azimuth, Elevation, Slant Range]
```

### 3.1 観測地点の WGS84 測地座標から ECEF 直交座標への変換

地球楕円体面上の緯度 $\phi$、経度 $\lambda$、標高 $h$ から、地球中心固定直交座標系（ECEF: Earth-Centered, Earth-Fixed）の座標 $(X_{\text{obs}}, Y_{\text{obs}}, Z_{\text{obs}})$ を算出する。

$$N(\phi) = \frac{a}{\sqrt{1 - e^2 \sin^2 \phi}}$$

$$X_{\text{obs}} = (N(\phi) + h) \cos \phi \cos \lambda$$

$$Y_{\text{obs}} = (N(\phi) + h) \cos \phi \sin \lambda$$

$$Z_{\text{obs}} = \left(N(\phi)(1 - e^2) + h\right) \sin \phi$$

#### 記号一覧
| 記号 | 物理的意味 | 定数値・単位 |
| :--- | :--- | :--- |
| $a$ | WGS84 地球長半径（赤道半径） | $6378137.0\text{ m}$ |
| $f$ | WGS84 扁平率（Flattening） | $1 / 298.257223563$ |
| $e^2$ | 離心率の 2 乗（$e^2 = 2f - f^2$） | $\approx 0.00669437999014$ |
| $\phi$ | 観測地点の測地緯度（東京） | $35.6895^\circ\ (\approx 0.62290\text{ rad})$ |
| $\lambda$ | 観測地点の測地経度（東京） | $139.6917^\circ\ (\approx 2.43808\text{ rad})$ |
| $h$ | 観測地点の楕円体高（標高） | $30.0\text{ m}$ |
| $N(\phi)$ | 卯酉線（ぼうゆうせん）曲率半径 | $\text{m}$ |

### 3.2 TEME から ECEF への自転回転変換

SGP4 アルゴリズムが出力する衛星位置ベクトル $\mathbf{r}_{\text{TEME}} = (X_T, Y_T, Z_T)^T$ は真赤道平均春分点座標系（TEME: True Equator, Mean Equinox）にある。地球は自転しているため、グリニッジ平均恒星時（GMST: Greenwich Mean Sidereal Time）$\theta_{\text{GMST}}$ を用いて $Z$ 軸周りに回転させる：

$$\begin{pmatrix} X_{\text{ECEF}} \\ Y_{\text{ECEF}} \\ Z_{\text{ECEF}} \end{pmatrix} = \begin{pmatrix} \cos \theta_{\text{GMST}} & \sin \theta_{\text{GMST}} & 0 \\ -\sin \theta_{\text{GMST}} & \cos \theta_{\text{GMST}} & 0 \\ 0 & 0 & 1 \end{pmatrix} \begin{pmatrix} X_T \\ Y_T \\ Z_T \end{pmatrix}$$

### 3.3 ECEF から局所水平座標系（Topocentric ENU）への変換

観測者から衛星への相対ベクトル $\Delta \mathbf{r} = \mathbf{r}_{\text{ECEF}} - \mathbf{r}_{\text{obs}}$ を、観測者位置を原点とする局所東・北・天頂座標系（ENU: East, North, Up）へ変換する：

$$\begin{pmatrix} E \\ N \\ U \end{pmatrix} = \begin{pmatrix} -\sin \lambda & \cos \lambda & 0 \\ -\sin \phi \cos \lambda & -\sin \phi \sin \lambda & \cos \phi \\ \cos \phi \cos \lambda & \cos \phi \sin \lambda & \sin \phi \end{pmatrix} \begin{pmatrix} \Delta X \\ \Delta Y \\ \Delta Z \end{pmatrix}$$

### 3.4 方位角（$\text{Az}$）、仰角（$\text{El}$）、スラントレンジ（$d$）の導出

$$d = \sqrt{E^2 + N^2 + U^2}$$

$$\text{El} = \arcsin\left(\frac{U}{d}\right)$$

$$\text{Az} = \text{atan2}(E, N) \pmod{360^\circ}$$

#### 日本語での読み解き
- $d$（スラントレンジ）は観測者から衛星までのユークリッド直線距離（km）。
- $\text{El}$（仰角）は水平面から見上げる角度であり、天頂方向の成分 $U$ と直線距離 $d$ の比の逆正弦（$\arcsin$）で求まる。
- $\text{Az}$（方位角）は水平面上での北向き成分 $N$ に対する東向き成分 $E$ の比から逆正接（$\text{atan2}$）で求まり、北を $0^\circ$ とした時計回りの角度が得られる。

---

## 4. クライアントサイド GIF 切り出しと SRE 設計思想

### 4.1 なぜサーバーサイド（FFmpeg / Python）ではなくブラウザ完結なのか？

本観測所の中核ノードである GPD Pocket3 は、超小型 Linux ノード上で K3s（CloudCore）、EdgeCore、Prometheus、Garage S3、Temporal、SatDump デコーダワーカーを同時稼働させている。

もし GIF の動画エンコード（毎秒 10 フレームの Canvas 再生、高画質パレット量子化、LZW 可逆圧縮）をサーバー側（Python `Pillow` や `imageio`、`ffmpeg`）で実行すると、以下の SRE 的リスクが発生する：
1. **CPU バーストと音声録音ドロップ**:
   衛星のリアルタイム受信（`satellite-tracker-rs` による 48kHz WAV 録音）と GIF エンコード処理が衝突した場合、CPU コアの枯渇によりサンプリングドロップやアンダーランが発生する。
2. **メモリ肥大化（OOM Killer）**:
   数百フレームの高解像度フレームバッファを Python プロセスで確保すると数百 MB のメモリを急激に消費し、K3s の Pod が Eviction / OOMKilled されるリスクがある。
3. **ブラウザ側への負荷オフロード（Edge-to-Client Architecture）**:
   GIF 生成は「閲覧中のユーザーの PC / スマホのブラウザ（Canvas API + JavaScript）」で実行すれば、サーバー側（GPD Pocket3）の CPU / メモリ消費は **完全に 0（ゼロ）** となる。

### 4.2 `gifenc` による超軽量・自己完結型 GIF エンコーダ

本システムでは、外部 CDN や重厚な Wasm を必要とせず、わずか 7KB の純粋な JavaScript で構成される現代的な GIF エンコーダ [`gifenc`](https://github.com/mattdesl/gifenc) を `apps/satellite-viewer/static/gifenc.js` として内包している。

#### GIF エンコードパイプライン
1. **フレームサンプリング**:
   指定されたトリミング区間 $[t_{\text{start}}, t_{\text{end}}]$ にわたり、10 fps（または $1\text{ 秒}$ 刻み）で 440×440 ピクセルの Offscreen Canvas に天球レーダー、衛星位置、現在時刻（JST）、Az/El テキスト、復調気象画像 PiP サムネイルを合成描画。
2. **パレット量子化（Color Quantization）**:
   各フレームの RGBA 32bit ピクセルデータ（$440 \times 440 \times 4 \approx 774\text{ KB}$）から、RGB565 色空間に基づく高速パレット量子化器 `quantize(data, 256)` を用いて 256 色の最適パレットを抽出。
3. **パレットインデックス化（`applyPalette`）**:
   各ピクセルを 256 色パレットのインデックス値（8bit）へ写像。
4. **LZW 圧縮ストリーム書き込み（`gif.writeFrame`）**:
   フレーム間ディレイ（$100\text{ ms}$）を指定して GIF89a 規格の LZW 圧縮ストリームへ追記。
5. **クライアント即時ダウンロード**:
   `gif.finish()` で得られたバイナリバイト列から `Blob` を生成し、`URL.createObjectURL(blob)` を介してブラウザのダウンロードリンクを即時トリガー。

---

## 5. 実機環境での動作検証結果

### 5.1 通過予測 API（`/api/future-passes`）
2026-10-25 14:43 JST 時点における向こう 24 時間の予測レスポンス例：

```json
{
  "generated_at": "2026-10-25T05:43:43Z",
  "passes": [
    {
      "satellite": "FUNCUBE-1 (AO-73)",
      "aos_time": "2026-10-25T06:03:43Z",
      "tca_time": "2026-10-25T06:08:13Z",
      "max_elevation": 76.0,
      "max_elevation_azimuth": 68.8,
      "window_duration_seconds": 90,
      "quality_score": "EXCELLENT (スカイウィンドウ直撃)"
    },
    {
      "satellite": "METEOR-M2 3",
      "aos_time": "2026-10-25T10:31:13Z",
      "tca_time": "2026-10-25T10:36:43Z",
      "max_elevation": 33.2,
      "max_elevation_azimuth": 70.0,
      "window_duration_seconds": 270,
      "quality_score": "GOOD (スカイウィンドウ通過)"
    }
  ]
}
```

### 5.2 観測リプレイ同期と復調画像ポップアップ
- 2026-10-25 00:53:44 UTC の METEOR-M2 3 パス（初画像復調成功パス）において：
  - $t = 0\text{s}$：方位角 $352.9^\circ$、仰角 $9.9^\circ$（手すり下限未満のため遮蔽判定）
  - $t = 135\text{s}$：仰角 $25^\circ$ を突破しスカイウィンドウへ突入（シアン枠線・緑バッジ発光）
  - $t = 280\text{s}$：最大仰角 $33.0^\circ$（TCA、方位 $290.9^\circ$ 西北西）
  - スペクトログラムシーク位置と連動し、CADU ロック区間で復調気象画像（MSU-MR RGB）のサムネイルが画面上にポップアップ。
  - 「🎯 開口窓区間を自動設定」ボタンにより、直撃区間（$t=135 \sim 400\text{s}$）がワンクリックでトリミング選択され、約 1.2MB の美麗なアニメーション GIF としてブラウザ保存が可能となった。
