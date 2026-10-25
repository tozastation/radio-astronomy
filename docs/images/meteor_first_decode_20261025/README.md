# METEOR-M2 3 初復調 気象衛星画像データセット（2026-10-25 09:53 JST）

本ディレクトリは、東京都北向きベランダ電波観測所において、**当プロジェクト初となる本物の地球気象衛星画像（雲画像）の復調・デコードに成功した歴史的観測データ**（パス ID: `METEOR-M2 3_20261025_005344`）をローカルに永久保存したものです。

---

## 🛰️ 観測諸元

- **衛星名**: METEOR-M2 3 (NORAD 57166)
- **録音時刻**: 2026-10-25 00:53:44 UTC（**2026-10-25 09:53:44 JST**）
- **受信周波数**: $137.900\text{ MHz}$
- **変調方式**: LRPT（OQPSK, シンボルレート $72\text{ kSym/s}$, 占有帯域幅 $\approx 80\text{ kHz}$）
- **生 IQ データ長**: 録音時間 $406.3\text{ 秒}$, ファイルサイズ $195.0\text{ MB}$（$240\text{ kSPS}$ `cu8`）
- **アンテナ姿勢**: 前傾 $60^\circ$（仰角 $30^\circ$ 持ち上げ）、開き角 $120^\circ$「10時10分」対称配置
- **実測ピーク電力比**: $\Delta P = +5.05\text{ dB}$（真の $\text{SNR} = +3.42\text{ dB}$, $E_s/N_0 \approx +3.88\text{ dB}$）
- **復号パケット**: 26,624 バイト（正規 CADU フレーム同期成功）

---

## 📸 保存ファイル一覧

| ファイル名 | チャンネル / 種類 | 解像度 / サイズ | 内容・特徴 |
| :--- | :--- | :--- | :--- |
| [`MSU-MR-1.png`](MSU-MR-1.png) | 可視光（Ch 1: 0.5〜0.7 $\mu\text{m}$） | $1568 \times 32\text{ px}$ (25 KB) | 積雲・巻雲のうねり、筋状の雲パターン、海洋と陸地のコントラストが鮮明に写る白黒画像 |
| [`MSU-MR-2.png`](MSU-MR-2.png) | 近赤外（Ch 2: 0.7〜1.1 $\mu\text{m}$） | $1568 \times 32\text{ px}$ (17 KB) | 植生反射や地表面の反射率の違いを捉えた赤外線画像 |
| [`MSU-MR-4.png`](MSU-MR-4.png) | 短波長赤外（Ch 4: 3.5〜4.1 $\mu\text{m}$） | $1568 \times 32\text{ px}$ (13 KB) | 雲頂高度や海面温度の違いを反映する熱赤外画像 |
| [`msu_mr_rgb_MSU-MR_124_False_Color_corrected.png`](msu_mr_rgb_MSU-MR_124_False_Color_corrected.png) | 疑似カラー合成（MSU-MR 1-2-4） | $1568 \times 32\text{ px}$ (140 KB) | Ch 1(青), Ch 2(緑), Ch 4(赤) の合成。陸地の緑、海の深い青、雲のシアン・白が鮮やかに分離 |
| [`msu_mr_rgb_AVHRR_221_False_Color_corrected.png`](msu_mr_rgb_AVHRR_221_False_Color_corrected.png) | 疑似カラー合成（AVHRR 2-2-1） | $1568 \times 32\text{ px}$ (94 KB) | 気象庁等で標準的に用いられる自然色に近い雲・海洋カラー合成画像 |
| [`msu_mr_3.9_um_Shortwave_IR_(Uncalibrated)_corrected.png`](msu_mr_3.9_um_Shortwave_IR_\(Uncalibrated\)_corrected.png) | 3.9 $\mu\text{m}$ 赤外線幾何補正 | $1568 \times 32\text{ px}$ (21 KB) | 地球曲率の歪みを補正した短波長赤外画像 |
| [`spectrogram.png`](spectrogram.png) | 全パス両側複素 IQ スペクトログラム | $1920 \times 1080\text{ px}$ (819 KB) | $406\text{ 秒}$ 全時間のドップラー軌跡、マルチパス抑制、$+5.05\text{ dB}$ の QPSK 平坦ハンプを可視化 |
| [`packets.json`](packets.json) | CADU テレメトリパケット | JSON (1.1 KB) | 復号された CCSDS パケットヘッダ、APID、シーケンス番号等のメタデータ |
| [`summary.json`](summary.json) | 解析結果サマリ | JSON (375 B) | ステータス `completed`、復調画像リスト、パケット件数の集計結果 |

---

## 🔍 画像が「横に細長い帯（アスペクト比 約 50:1）」である物理的理由

気象衛星の MSU-MR 放射計は、スマホのような「面（CMOS）」ではなく、回転する鏡による「**ラインスキャナ（複写機方式）**」で地球を観測します：
1. **横軸（X軸: $1,568\text{ px}$）**: 反射鏡が東西方向に回転し、**地平線から地平線まで（約 $2,800\text{ km}$）を一気に 1 行スキャン** します。
2. **縦軸（Y軸: $\approx 20 \sim 30\text{ px}$）**: 衛星が秒速約 $7.5\text{ km}$ で宇宙を前進することで行が進みます。
3. **アスペクト比**: 今回は QPSK の搬送波位相ロックが維持された区間（約 10〜15 秒間）に相当する約 $20 \sim 30$ 行分を受信できたため、**「東西 2,800 km × 南北 40 km」という超・横長スライスの地球写真** となっています。

完全な全パス（約 300 秒間）をロックできるようになると、縦が $1,000\text{ 行}$ 以上に伸びて日本列島全体を覆う巨大な衛星写真となります。
詳細な数理と物理メカニズムは [docs/qa/21_meteor_lrpt_reception_diagnosis_and_dsp_remediation.md](../../qa/21_meteor_lrpt_reception_diagnosis_and_dsp_remediation.md) を参照してください。
