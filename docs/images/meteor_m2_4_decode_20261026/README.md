# METEOR-M2 4 初復調 気象衛星画像データセット（2026-10-26 03:17 JST）

本ディレクトリは、東京都北向きベランダ電波観測所において受信・復調された、**姉妹機 METEOR-M2 4 における初画像復調データ**（パス ID: `METEOR-M2 4_20261025_181705`）をブログ・技術記事および観測実績としてローカルに保存したものです。

METEOR-M2 3 に続き、2024年に打ち上げられた最新の METEOR-M2 4 衛星からも LRPT（$137.900\text{ MHz}$, QPSK $72\text{ kSym/s}$）信号を安定受信し、気象雲画像の復調に成功しました。

---

## 🛰️ 観測諸元

- **衛星名**: METEOR-M2 4 (NORAD 59051)
- **録音開始時刻**: 2026-10-25 18:17:05 UTC（**2026-10-26 03:17:05 JST**）
- **受信周波数**: $137.900\text{ MHz}$
- **変調方式**: LRPT（OQPSK, シンボルレート $72\text{ kSym/s}$, 占有帯域幅 $\approx 80\text{ kHz}$）
- **最大仰角 (TCA)**: **$64.8^\circ$（スカイウィンドウ直撃パス）**
- **アンテナ姿勢**: 前傾 $60^\circ$（仰角 $30^\circ$ 見上げ）、開き角 $120^\circ$「10時10分」対称配置
- **復調パイプライン**: SatDump CLI（`meteor_m2-x_lrpt`, `--dc_block true`）
- **出力画像数**: 19 枚

---

## 📸 ブログ・解説用 おすすめ画像一覧

| ファイル名 | 分類 | 解像度 / サイズ | 内容・特徴 |
| :--- | :--- | :--- | :--- |
| [`msu_mr_rgb_MSU-MR_124_False_Color_corrected.png`](msu_mr_rgb_MSU-MR_124_False_Color_corrected.png) | **カラー（標準疑似カラー）** | $2,800 \times 32\text{ px}$ (228 KB) | **【ブログ掲載推奨】** MSU-MR 1-2-4 合成。深夜未明パスのため可視光は暗めですが、マルチスペクトルによるパノラマ幾何補正画像。 |
| [`MSU-MR-1.png`](MSU-MR-1.png) | **モノクロ（可視光）** | $1,568 \times 32\text{ px}$ (15 KB) | **【白黒写真におすすめ】** 可視光チャンネル（0.5〜0.7 $\mu\text{m}$）。 |
| [`msu_mr_rgb_AVHRR_221_False_Color_corrected.png`](msu_mr_rgb_AVHRR_221_False_Color_corrected.png) | カラー（AVHRR 自然色風） | $2,800 \times 32\text{ px}$ (167 KB) | 自然色風パレットによる幾何補正画像。 |
| [`MSU-MR-2.png`](MSU-MR-2.png) | モノクロ（近赤外） | $1,568 \times 32\text{ px}$ (17 KB) | 近赤外チャンネル（0.7〜1.1 $\mu\text{m}$）。 |
| [`MSU-MR-4.png`](MSU-MR-4.png) | モノクロ（短波長赤外） | $1,568 \times 32\text{ px}$ (20 KB) | 3.5〜4.1 $\mu\text{m}$ 短波長赤外。夜間でも雲頂温度を明瞭に検出。 |
| [`spectrogram.png`](spectrogram.png) | RF スペクトログラム | $1,920 \times 1,080\text{ px}$ (896 KB) | METEOR-M2 4 特有のドップラー周波数偏移曲線と QPSK 信号スペクトラム。 |
