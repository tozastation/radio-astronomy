# METEOR-M2 3 最新復調 気象衛星画像データセット（2026-10-27 09:06 JST）

本ディレクトリは、東京都北向きベランダ電波観測所において、**2026年10月27日 09:06 JST に到来した天頂通過パス（最大仰角 $84.9^\circ$）から自動録音・自動復調された最新の地球気象衛星画像（雲画像）データ**（パス ID: `METEOR-M2 3_20261027_000649`）を保存したものです。

垂直 360 行（$2,800 \times 360\text{ px}$）にわたり OQPSK 搬送波ロックが安定維持され、合計 31 枚の高解像度気象画像が自動生成されました。

---

## 🛰️ 観測諸元

- **衛星名**: METEOR-M2 3 (NORAD 57166)
- **録音開始時刻**: 2026-10-27 00:06:49 UTC（**2026-10-27 09:06:49 JST**）
- **受信周波数**: $137.900\text{ MHz}$
- **変調方式**: LRPT（OQPSK, シンボルレート $72\text{ kSym/s}$, 占有帯域幅 $\approx 80\text{ kHz}$）
- **最大仰角 (TCA)**: **$84.9^\circ$（天頂直撃・北向きスカイウィンドウ通過）**
- **生 IQ データ長**: 152.8 MB（$240\text{ kSPS}$ `cu8`）
- **アンテナ姿勢**: 前傾 $60^\circ$（仰角 $30^\circ$ 見上げ）、開き角 $120^\circ$「10時10分」対称配置
- **復調パイプライン**: SatDump CLI（`meteor_m2-x_lrpt`, `--dc_block true`）
- **出力画像数**: 31 枚

---

## 📸 代表画像一覧

| ファイル名 | 分類 | 解像度 / サイズ | 内容・特徴 |
| :--- | :--- | :--- | :--- |
| [`msu_mr_rgb_MSU-MR_124_False_Color_corrected.png`](msu_mr_rgb_MSU-MR_124_False_Color_corrected.png) | **カラー（標準疑似カラー）** | $2,800 \times 360\text{ px}$ (781 KB) | MSU-MR 1-2-4 合成パノラマ幾何補正画像。 |
| [`MSU-MR-1.png`](MSU-MR-1.png) | **モノクロ（可視光）** | $1,568 \times 360\text{ px}$ (124 KB) | 第1チャンネル（0.5〜0.7 $\mu\text{m}$ 可視光）。 |
| [`msu_mr_rgb_AVHRR_221_False_Color_corrected.png`](msu_mr_rgb_AVHRR_221_False_Color_corrected.png) | カラー（AVHRR 自然色風） | $2,800 \times 360\text{ px}$ (571 KB) | 自然色風パレットによる幾何補正画像。 |
| [`MSU-MR-2.png`](MSU-MR-2.png) | モノクロ（近赤外） | $1,568 \times 360\text{ px}$ (130 KB) | 第2チャンネル（0.7〜1.1 $\mu\text{m}$ 近赤外）。 |
| [`MSU-MR-4.png`](MSU-MR-4.png) | モノクロ（熱赤外） | $1,568 \times 360\text{ px}$ (92 KB) | 第4チャンネル（3.5〜4.1 $\mu\text{m}$ 赤外線）。 |
| [`spectrogram.png`](spectrogram.png) | RF スペクトログラム | $1,920 \times 1,080\text{ px}$ (864 KB) | 受信電波の S 字ドップラー曲線と QPSK 変調波の受信証跡。 |
