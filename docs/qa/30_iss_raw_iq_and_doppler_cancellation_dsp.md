# ISS 生 IQ 録音とクラウド側 DSP ドップラー相殺復調パイプライン詳解

本ドキュメントでは、国際宇宙ステーション（ISS: $145.825\ \text{MHz}$）の観測において発生していた「復調率の見かけ上の過大評価問題」の根本原因とパージ措置、および Meteor / FUNcube とアーキテクチャを統一した「生 IQ 録音 ＋ クラウド側 DSP 完全ドップラー相殺」パイプラインの数理・工学的メカニズムについて解説します。

---

## 1. ISS 復調率が見かけ上高かった理由とパージ措置

### 1.1 経緯と根本原因
観測ビューア（`satellite-viewer`）の通過予測画面において、ISS の成功確率が仰角帯別で以下のように異常に高く表示されていました：
- 高仰角帯: **14/18回成功 (78%)**
- 中仰角帯: **13/15回成功 (87%)**
- 低仰角帯: **13/32回成功 (41%)**

Garage S3 の `results/ISS (ZARYA)/` 配下を精査したところ、全 66 パス中 **40 パス** において、過去のイベント駆動解析パイプライン結合テスト時に生成された固定モックパケット（`RS0ISS>CQ,ARISS::Hello from ISS via APRS!`）を含む成果物がそのまま蓄積されていました。
ビューアの集計関数 `get_satellite_history_stats()` が S3 の全期間データを走査していたため、このダミーパケットが「成功実績」としてカウントされていたことが原因です。

### 1.2 パージとメトリクス正常化
- **退避・パージ**: 固定モックパケットを含む 40 パス分の成果物を `archive/mock_iss_202610/` へ安全に退避し、`results/` から完全除去しました。
- **実力値への正常化**: ビューアを再起動してインメモリキャッシュを同期した結果、直近 25 パスの純粋な実機観測（すべてパケット 0 件）に基づき、**「0/19回成功 (0%)」「0/4回成功 (0%)」** と正常な実力値（成功確率 5%〜25%）へと補正されました。

---

## 2. 実機受信における課題：ドップラー周波数偏移と Carson の規則

### 2.1 低軌道（LEO）周回に伴う周波数偏移

ISS（高度約 400km、速度約 $7.66\ \text{km/s}$）からの電波（$145.825\ \text{MHz}$）は、地上局から見た相対視線速度 $v_r(t)$ の変化に伴い、大きなドップラー偏移を生じます。

#### ① 記号一覧
| 記号 | 物理的・工学的意味 | 単位 / 代表値 |
| :--- | :--- | :--- |
| $f_0$ | ISS APRS 公称送信周波数 | $145.825\ \text{MHz}$ |
| $v_r(t)$ | 地上局から見た衛星の相対視線速度 | $\pm 7.0\ \text{km/s}$（AOS: 接近 / LOS: 離脱） |
| $c$ | 光速（電磁波の伝搬速度） | $2.99792458 \times 10^8\ \text{m/s}$ |
| $\Delta f(t)$ | 瞬時ドップラー周波数偏移 | 最大約 $\mp 3.4\ \text{kHz}$（全幅約 $7.0\ \text{kHz}$） |
| $B_{\text{FM}}$ | Carson の規則による NFM 伝送帯域幅 | 約 $14.4\ \text{kHz}$ |

#### ② 日本語での読み解き
衛星が地上局に接近してくる AOS（Acquisition of Signal）時には電波の波面が圧縮されて周波数が約 $+3.4\ \text{kHz}$ 高くなり、最接近時（TCA: Time of Closest Approach）にズレが $0\ \text{Hz}$ となり、遠ざかる LOS（Loss of Signal）時には約 $-3.4\ \text{kHz}$ 低くなります。

#### ③ 展開ステップと直感イメージ
ドップラー偏移は以下の式で規定されます：
$$\Delta f(t) = -f_0 \frac{v_r(t)}{c}$$
接近時（$v_r = -7000\ \text{m/s}$）では：
$$\Delta f = -145.825 \times 10^6 \times \frac{-7000}{2.99792458 \times 10^8} \approx +3405\ \text{Hz}$$

アマチュア無線 NFM（1200bps AFSK / Bell 202）の Carson 帯域幅は $B_{\text{FM}} = 2(\Delta f_{\text{dev}} + f_m) \approx 2(5.0 + 2.2) = 14.4\ \text{kHz}$ です。
受信機の中心周波数を固定したままだと、通過の前後で信号が検波器の通過帯域端に押し出され、検波後の音声信号に激しい DC オフセットや波形歪みを生じ、Dire Wolf のシンボル同期（Mark 1200Hz / Space 2200Hz）が崩壊します。

---

## 3. Meteor / FUNcube と共通化された「案 B」アーキテクチャ

### 3.1 エッジとクラウドの責務分担

従来、Meteor-M2 や FUNcube-1 は生 IQ を録音してクラウド側でソフトウェア復調していたのに対し、ISS のみエッジ側で `rtl_fm` を起動して FM 復調する非対称な構成になっていました。
本改修により、全衛星のパイプラインが **「案 B（生 IQ 録音 ＋ クラウド側 DSP 復調）」** に統一されました。

```text
【新・統一パイプライン】
アンテナ側エッジ (satellite-tracker-rs)
  └─► rtl_sdr -f 145825000 -s 240000 -g 45.0 (240kSPS uint8 生 IQ を記録)
        │
        ▼ Garage S3 (raw/ISS (ZARYA)/...)
クラウド解析側 (satellite-analyzer)
  ├─► SGP4 軌道伝搬による瞬時視線速度 v_r(t) の算出
  ├─► 複素ミキシングによるドップラー周波数相殺: y[n] = x[n] · exp(-j θ[n])
  ├─► 位相差分 FM 復調: Δφ[n] = arg(y[n] · y*[n-1])
  ├─► FIR フィルタ (fc=4kHz) + 5倍デシメーション (240k -> 48kSPS WAV)
  └─► Dire Wolf (atest -B 1200) による AX.25 パケット復号 & CRC-16 検証
```

### 3.2 複素ミキシングによるドップラー相殺の数学

離散時間サンプル $n$（サンプリング周期 $T_s = 1/240000\ \text{s}$）における累積回転位相 $\theta[n]$ は：
$$\theta[n] = \theta[n-1] + 2\pi \frac{\Delta f(t_n)}{f_s}$$

この逆位相ベクトルを複素受信信号 $x[n] = I[n] + j Q[n]$ に乗算します：
$$y[n] = x[n] \cdot e^{-j \theta[n]} = x[n] \left( \cos\theta[n] - j \sin\theta[n] \right)$$

これにより、時間とともに $7\ \text{kHz}$ ドリフトしていた周波数成分が、ベースバンド上で完全に $0\ \text{Hz}$（DC 中心）に静止します。

### 3.3 位相差分 FM 復調（Polar Discriminator）

FM 変調信号 $y[n] = A[n] e^{j \phi[n]}$ に対し、1 サンプル前の共役積をとります：
$$P[n] = y[n] \cdot y^*[n-1] = A[n] A[n-1] e^{j (\phi[n] - \phi[n-1])}$$

この偏角（Argument）を求めると：
$$\Delta \phi[n] = \arg(P[n]) = \text{atan2}\left( \text{Im}(P[n]),\ \text{Re}(P[n]) \right)$$

振幅 $A$（フェージングや電波強度変動）の影響を一切受けず、瞬時周波数偏位に厳密に比例した音声信号 $\Delta \phi[n]$ が取り出されます。

---

## 4. 一次情報リンク
- [Dire Wolf - Automated Packet Reporting System (APRS) 公式リポジトリ](https://github.com/wb2osz/direwolf)
- [AX.25 Link-Layer Protocol Specification v2.2 (TAPR)](https://www.tapr.org/pdf/AX25_2.2.pdf)
- [SatDump - General Purpose Satellite Data Processing Software](https://github.com/SatDump/SatDump)
- [SGP4 Python Library (Brandon Rhodes)](https://pypi.org/project/sgp4/)
- [Shannon, C. E. (1948) "A Mathematical Theory of Communication"](https://archive.org/details/bstj27-3-379)
