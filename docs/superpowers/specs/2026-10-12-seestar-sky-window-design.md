# SeeStar の空の窓と、その夜の撮影計画

- **作成日**: 2026-10-12
- **ステータス**: 計画の可視化を実装する。SeeStar への命令は送らない
- **対象**: SeeStar S50。人が北側バルコニーの決まった位置へ置き、公式アプリで撮影する

この仕様は、いま動いている衛星ダウンリンク（`satellite-tracker-rs`、Garage、Temporal、`satellite-viewer`、Grafana）に、光学の計画を足す。電波の受信系は変えない。

観測地の緯度経度は、リポジトリ内では従来どおり東京都の広域代表値（北緯 35.6895、東経 139.6917）をテストの既定値にする。実開口の緯度経度と、山・向かいの棟の仰角は、コミットしない現地ファイルで上書きする。

---

## 1. 目的

その夜、SeeStar を北の長辺と東の短辺のどちらに置くと、どの対象が何時に窓に入るかを Grafana で見る。撮れた画像は、撮影を始めたあと `satellite-viewer` で見る。

成功は、電波観測と同じく中身が見えることである。ここではスタックした JPEG が見えること。計画の曲線だけでは撮影成功にしない。

---

## 2. 人が置く位置

北側の長辺の中央付近で、ガラス手すりのすぐ内側に置く。鏡筒はバルコニーの内側に残し、対物はガラスの上端より高くする。

この位置を、計画が仮定する唯一の基本姿勢にする。部屋のサッシ側に置くと、上の階の床スラブが頭上を隠す。長辺の東端・西端は、側壁が近づいて別の窓になるので、基本姿勢にはしない。

スラブ先端から鏡筒までの水平距離を \(d\) とすると、見える最大仰角は次である。

\[
\mathrm{Alt}_{\max} = \arctan\left(\frac{H - h_{\mathrm{scope}}}{d}\right)
\]

| 記号 | 意味 | 単位 |
| :--- | :--- | :--- |
| \(d\) | スラブ先端（ガラスの線）から鏡筒までの水平距離 | m |
| \(H\) | バルコニー床からスラブ下面までの高さ | m |
| \(h_{\mathrm{scope}}\) | 床から対物までの高さ | m |
| \(\mathrm{Alt}_{\max}\) | その位置から見える最大仰角 | 度 |

\(d\) が手すりの厚み程度のとき、頭上まで開く。基本姿勢の \(\mathrm{Alt}_{\max}\) は現地ファイルの値とし、未測定のあいだは \(85^\circ\) を仮に使う。サッシ側の姿勢は第一段階では計算しない。

---

## 3. 空の床

方位 \(\mathrm{Az}\)（真北 \(0^\circ\)、真東 \(90^\circ\)）ごとの下限仰角 \(\mathrm{Alt}_{\min}(\mathrm{Az})\) は、開口のすぐ前の手すりではなく、遠方の遮蔽で決まる。三脚をガラスの線に置いたままでは、この床は動かない。

\[
\mathrm{Alt}_{\min}(\mathrm{Az}) =
\begin{cases}
\mathrm{Alt}_{\mathrm{ridge}} & \text{北の開口で、向かいの棟に向かない方位} \\
\mathrm{Alt}_{\mathrm{roof}} & \text{向かいの棟に向かう方位}
\end{cases}
\]

| 記号 | 意味 | 初期値 |
| :--- | :--- | :--- |
| \(\mathrm{Alt}_{\mathrm{ridge}}\) | 北側の斜面を、設置階から見た仰角 | \(10^\circ\)。鏡筒を尾根に向けて一度読み、上書きする |
| \(\mathrm{Alt}_{\mathrm{roof}}\) | 東の向かいの棟の屋上の仰角 | 未測定のあいだは計画に使わない。東の置き場所を出す段階で、屋上へ一度向けて読む |

北の斜面は開口から数百 m にあり、5 階相当の高さから見ると真北付近でおよそ \(10^\circ\) である。この数値はレーザ標高からの見当であり、樹冠と開口の正確な位置で数度動く。現地の一回の読取を正とする。

東の棟は開口より数階高い。屋上の仰角は距離が数十 m のときおよそ \(10^\circ\) から \(20^\circ\) に入る。距離を仕様の定数にはしない。

側壁による方位の制限は、基本姿勢（ガラス際の中央）では半角が広い。第一段階では、方位 \(270^\circ\) から \(90^\circ\)（西から北を経て東）かつ \(\mathrm{Alt}_{\mathrm{ridge}} \le \mathrm{Alt} \le \mathrm{Alt}_{\max}\) を北の窓とする。東の棟に向かう方位の床は、\(\mathrm{Alt}_{\mathrm{roof}}\) が入るまで北の床のままにし、東端の置き場所は第二段階で切り替える。

---

## 4. 動かさずに撮れる天体

緯度 \(\varphi\)、赤緯 \(\delta\) の天体が最も高くなる高度 \(a\) は、南中が天頂の北か南かで分かれる。

\[
a =
\begin{cases}
90^\circ - (\delta - \varphi) & (\delta \ge \varphi) \\
90^\circ - (\varphi - \delta) & (\delta < \varphi)
\end{cases}
\]

| 記号 | 意味 | 単位 |
| :--- | :--- | :--- |
| \(\varphi\) | 緯度。テスト既定値は \(35.6895^\circ\) | 度 |
| \(\delta\) | 赤緯。北が正 | 度 |
| \(a\) | 南中（または北中）の高度 | 度 |

\(\delta > 90^\circ - \varphi\) の天体は沈まない。\(\delta \ge \varphi\) の天体は天頂の北側を通るので、基本姿勢のまま窓に残りやすい。\(\delta < \varphi\) の天体は真南で最も高くなり、北側の窓には東から入って南へ出るまでのあいだしかかからない。

第一段階のカタログは次に固定する。赤経・赤緯は J2000 の計画用概位（\(0.1^\circ\) 程度）でよい。

| 名前 | 赤経 | 赤緯 | 基本姿勢での扱い |
| :--- | :--- | ---: | :--- |
| 北極星 | 2h 32m | \(+89.3^\circ\) | 一晩とどまる |
| M52 | 23h 24m | \(+61.6^\circ\) | 北側を通る。ガラス際なら床より上 |
| 二重星団 NGC 869 | 2h 19m | \(+57.2^\circ\) | 北側を通る |
| M31 | 0h 43m | \(+41.3^\circ\) | ほぼ頭上。ガラスの線まで出していることが条件 |
| M45 | 3h 47m | \(+24.1^\circ\) | 南中は南。東から入っている時間だけ窓に入る |
| M42 | 5h 35m | \(-5.4^\circ\) | 南中は南。東の空にいる短い時間だけ |

惑星（金星、火星、木星、土星、天王星、海王星）は、JPL の近似軌道要素（[Approximate Positions of the Planets](https://ssd.jpl.nasa.gov/planets/approx_pos.html) の Table 1、1800〜2050 年）から地心の赤経・赤緯を求める。PyEphem との差は 2026〜2030 年で \(0.1^\circ\) 未満である。惑星は黄道の近くにいるので、多くは南の空を通り、北の窓には東から昇る時間か西へ沈む時間だけかかる。一晩で動く角度は \(1^\circ\) ほどなので、窓の区間は夜の中ほどの位置で計算する。水星は太陽から \(28^\circ\) 以内にしか離れず、天文薄明の夜にはほぼ地平線の下にいるので入れない。メトリクスの `kind` ラベルは、恒星・星雲・星団が `deep_sky`、惑星が `planet` である。

置き場所は、北の長辺（ガラス際・中央）と東の短辺の二つを並べて出す。どちらに置くかは、ボードの表を見て人が決める。東の短辺の窓は、方位 \(45^\circ\) から \(135^\circ\)、床 \(20^\circ\) を仮に置く。建物の本体はベランダの南にあるので、真南の手前で切る。向かいの棟の屋上と、短辺の手すりから見える方位を現地で読むまでの値である。

---

## 5. 可視化

Prometheus は取りに来た時刻の値を集める。これから先の時刻の点は入れられない。そこで計画器 `apps/seestar-plan` を exporter にし、Prometheus が値を取りに来るたびに、その時刻の値と、今夜の窓に入る時刻・出る時刻を値として返す。描画は Grafana（NodePort 30080）の標準パネルに任せる。

| メトリクス | ラベル | 中身 |
|---|---|---|
| `seestar_target_altitude_degrees` / `seestar_target_azimuth_degrees` | `target`, `name` | 今の高度・方位 |
| `seestar_target_moon_separation_degrees` | `target`, `name` | 星と月の離角 |
| `seestar_target_in_window` | `target`, `name`, `window`, `window_name` | いま窓の中なら 1 |
| `seestar_target_window_start_timestamp_seconds` / `_end_` / `seestar_target_window_duration_seconds` | 同上 | 今夜の暗い時間のうち、いま入っている窓か次の窓 |
| `seestar_window_floor_degrees` / `_ceiling_` / `_azimuth_from_` / `_azimuth_to_` | `window`, `window_name` | 置き場所の設定 |
| `seestar_sun_altitude_degrees`, `seestar_sky_dark` | なし | 太陽の高度、天文薄明が終わっていれば 1 |
| `seestar_night_start_timestamp_seconds` / `_end_` | なし | 今夜の暗い時間 |
| `seestar_moon_altitude_degrees` / `_azimuth_` / `_illumination_ratio` / `_phase_ratio` / `_age_days` | なし | 月の位置と満ち欠け |
| `seestar_moon_in_window` | `window`, `window_name` | 月が窓の中なら 1 |
| `seestar_moon_next_rise_` / `_set_` / `_new_` / `_full_timestamp_seconds` | なし | 次の月の出・月の入り・新月・満月 |
| `seestar_weather_cloud_cover_percent` | `layer`（`total` / `low` / `mid` / `high`） | いまの時間帯の雲量 |
| `seestar_weather_precipitation_probability_percent` / `_relative_humidity_percent` / `_temperature_celsius` / `_dew_point_celsius` / `_wind_speed_meters_per_second` / `_visibility_meters` | なし | いまの時間帯の降水確率・湿度・気温・露点・風速・視程 |
| `seestar_weather_tonight_cloud_cover_percent` / `_precipitation_probability_percent` | `slot`, `hour` | 今夜の暗い時間にかかる 1 時間ごとの予報 |
| `seestar_weather_tonight_clear_seconds` | なし | 今夜の暗い時間のうち、晴れの予報の長さ |
| `seestar_target_window_clear_seconds` | `target`, `name`, `window`, `window_name` | 窓のこれから先のうち、晴れの予報の長さ |
| `seestar_weather_forecast_up` / `_fetch_timestamp_seconds` | なし | 予報の取得に成功したか、手元の予報を取った時刻 |

「暗い時間で、しかも窓の中」は exporter では出さず、ボード側の PromQL `seestar_target_in_window * on() group_left() max(seestar_sky_dark)` で組み合わせる。月は天体暦の低精度式で求め、PyEphem との差は高度 0.1 度以下、新月・満月の時刻で 15 分以内である。

天気は [Open-Meteo の予報 API](https://open-meteo.com/en/docs)（キー不要、非商用は無料、出典の表示が必要）から 1 時間ごとの値を取る。Prometheus が値を取りに来るたびに外へ問い合わせることはしない。裏のスレッドが 30 分ごとに取り直し、失敗したときは前回の予報を返したまま 5 分後に取り直す。晴れは、雲量 30% 未満かつ降水確率 30% 未満の時間帯とする。1 時間ごとの予報は、ラベル `slot`（今夜の何番目の時間帯か）と `hour`（何時か）で持ち、未来の時刻の点としては入れない。

ボード「SeeStar 今夜の窓」は次の標準パネルで組む。

- Stat: 今夜の暗い時間、いまの空、太陽の高度、置き場所ごとのいま撮れる星の数と今夜窓に入る星の数
- Stat、Gauge、Bar gauge、Bar chart: 星空の天気予報。今夜の晴れの長さ、いまの雲量と雲の高さ、降水確率、気温と露点の差（2 度より小さいと結露しやすい）、湿度、風速、今夜 1 時間ごとの雲量と降水確率
- Stat と Gauge: 月相、輝面比、月齢、月の高度、次の月の出・月の入り、次の新月・満月
- Table: 北の長辺と東の短辺の表を並べる。星ごとに、窓に入る時刻・出る時刻・長さ、晴れの長さ、いま撮れるか、高度、方位、月との離角を出す
- Time series: 高度（置き場所ごとの床と天井の破線、月を重ねる）、方位、月との離角
- State timeline: 置き場所ごとに、暗い時間に窓の中にいたあいだと、月が窓にいたあいだ

撮影が始まる第二段階では、SeeStar の現在の高度・方位、バッテリー、スタック枚数だけを Prometheus のゲージにする。画像の JPEG は Garage の `optical/` に置き、`satellite-viewer`（NodePort 30088）が既存の画像カードと同じやり方で表示する。Grafana は鏡筒が窓のどこにいるかを見せ、画像の中身は viewer が見せる。

---

## 6. 撮影は公式アプリ

ファームウェア 7.18 以降は、TCP 4700 番で `get_verify_str` のあとに `verify_client` を通した相手にだけ命令へ返事をする。このプログラムはその署名を持たない。導入、ライブスタック、画像の取り込みは SeeStar 公式アプリで行う。

計画器 `apps/seestar-plan` は望遠鏡に接続しない。ポート 9101 の `/metrics` で値を返し、ServiceMonitor を通して Prometheus が 30 秒ごとに取りに来る。

場所の ConfigMap `seestar-plan-site` は、リポジトリの YAML には入れない。git の管理外にある `apps/seestar-plan/site.local.json`（実座標を 0.01 度に丸めた値と、窓の床・天井）から作る。ひな形は `apps/seestar-plan/config.example.json` である。測り直したら、次で差し替えて Pod を作り直す。計画器は、Prometheus からの取得のたびと、予報を取り直すたびに、このファイルを読み直す。

```bash
kubectl create configmap seestar-plan-site \
  --from-file=site.json=apps/seestar-plan/site.local.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/seestar-plan
```

---

## 7. 第一段階の完了

次が揃えば第一段階は完了である。SeeStar 本体が無くてもテストできる。

1. 代表緯度で、カタログ 6 天体の南中が北か南かを式どおりに判定するテストがある。
2. 高度が \(\mathrm{Alt}_{\mathrm{ridge}}\) 未満、または \(\mathrm{Alt}_{\max}\) を超える時刻は窓の外になるテストがある。
3. 計画器が、指定した時刻の窓・星・月の値を Prometheus の形式で返す。
4. Grafana のボード定義がリポジトリにあり、標準パネルだけで置き場所ごとの窓と月を表示する。

第二段階に入る条件は、第一段階のボードを見て、ガラス際に置いた夜の窓の時刻が使えると判断したときである。
