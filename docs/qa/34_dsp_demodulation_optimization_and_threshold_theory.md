# BPSK 復調のサンプリングレート過剰比・FM 検波前帯域幅制限・TLE 鮮度管理の数理と物理詳解

本ドキュメントでは、直近の受信結果（気象衛星、CubeSat、宇宙ステーション、21cm 銀河面ドリフトスキャン）の診断を通じて明らかになった 4 つの信号処理・軌道工学的課題（① BPSK Costas ループと Samples Per Symbol、② NFM 検波前 Carson 帯域制限と FM スレッショルド改善、③ LEO 衛星の SGP4 TLE 元期劣化、④ 北向きベランダの受信ジオメトリと RF ゲイン）の本質的な物理・数学的メカニズムと対策について解説する。

---

## 1. BPSK 復調における Samples Per Symbol (SPS) と Costas Loop 引込特性

### 1.1 背景と問題
`satellite-tracker-rs` が $145.935\text{ MHz}$ で録音した `FUNCUBE-1 (AO-73)`（$1200\text{ bps}$ BPSK）の生 IQ データ（$240\text{ kSPS}$）を `gr_satellites AO-73` に入力した際、電波は受かっていたにもかかわらず 67 パス中 1 パケットも復号できない状態（成功率 $0\%$）が発生していた。
一方、SGP4 ドップラー補正を施した上で $1/5$ デシメーション（$240\text{ kSPS} \to 48\text{ kSPS}$）と振幅正規化を適用すると、アーカイブ済みパスから $9 \sim 15$ パケット/パスの実テレメトリが安定して復号できた。

### 1.2 記号一覧
| 記号 | 物理・工学的意味 | 単位 / 典型値 |
| :--- | :--- | :--- |
| $R_s$ | シンボルレート（Baud rate） | $1200\text{ Baud}$（AO-73 BPSK） |
| $f_s$ | サンプリング周波数 | $240\text{ kHz} \to 48\text{ kHz}$ |
| $\text{SPS}$ | 1 シンボルあたりのサンプル数（Samples Per Symbol: $f_s / R_s$） | $200 \to 40$ |
| $B_L$ | Costas ループ / FLL の等価雑音帯域幅（Loop Noise Bandwidth） | $\text{Hz}$（通常 $0.01 R_s \sim 0.05 R_s$） |
| $\Delta f$ | キャリア周波数オフセット（残留ドップラー含む） | $\text{Hz}$（最大 $\pm 3.4\text{ kHz}$） |
| $\Delta \omega_{\text{lock}}$ | 位相ロックループの引込範囲（Pull-in range） | $\text{rad/s}$ |

### 1.3 日本語での読み解き
デジタル復調器（Costas ループや Timing Error Detector）は、シンボルレート $R_s$ に対して適正なサンプルレート比（通常 $\text{SPS} = 2 \sim 8$、あるいは高くても数倍〜数十倍）を想定してループゲインや内部フィルタ係数が設計されている。
$\text{SPS} = 200$（$240\text{ kSPS} / 1200\text{ Baud}$）という過剰なサンプリングレートでは、シンボル間の遷移が時間軸上で非常に緩慢になり、タイミング同期誤差の検出感度が激減する。さらに、ドップラーシフトによる $\Delta f \approx \pm 3.4\text{ kHz}$ のオフセットが存在すると、狭帯域なループフィルタの周波数引込限界（Pull-in range）を大きく超えてしまい、Costas ループがロックできなくなる。
したがって、① SGP4 によるドップラー逆回転で $\Delta f \approx 0\text{ Hz}$ に引き戻し、② $\text{SPS} = 40$（$48\text{ kSPS}$）までデシメーションしてシンボルあたりの位相・時間変化率を適正化することが必須となる。

### 1.4 数式展開と直感イメージ
BPSK の Costas ループにおける位相誤差検出器の出力 $e[n]$ は、同相成分 $I[n]$ と直交成分 $Q[n]$ の積として表される：
$$e[n] = I[n] \cdot Q[n] = \frac{1}{2} A^2 \sin(2\theta_e[n]) + \text{雑音項}$$
ここで $\theta_e[n] = 2\pi \Delta f \cdot \frac{n}{f_s} + \phi_0$ である。
ループフィルタを通過した制御信号が NCO（数値制御発振器）の瞬時周波数を補正するが、2次ループの周波数引込範囲 $\Delta \omega_{\text{pull-in}}$ は近似的に次式で与えられる：
$$\Delta \omega_{\text{pull-in}} \approx 2 \sqrt{2} \zeta \omega_n \cdot \sqrt{\frac{K_v}{B_L}}$$
（$\zeta$ はダンピング係数、$\omega_n$ は固有角周波数）。
ループが安定して収束するためには、正規化周波数誤差 $|\Delta f / f_s|$ がループの捕獲帯域内でなければならない。$f_s = 240\text{ kHz}$ の場合、1 サンプルあたりの位相回転角 $\Delta \theta = 2\pi \frac{\Delta f}{f_s}$ は $\pm 3.4\text{ kHz}$ のとき
$$\Delta \theta = 2\pi \cdot \frac{3400}{240000} \approx 0.089\text{ rad} \approx 5.1^\circ$$
と毎サンプル回転する。これに対して事前の SGP4 ドップラー補正を適用すると：
$$x_{\text{comp}}[n] = x[n] \cdot \exp\left(-j \int_0^{t_n} 2\pi \Delta f_{\text{sgp4}}(\tau)\,d\tau\right)$$
残留周波数誤差は $|\Delta f_{\text{res}}| < 100\text{ Hz}$ に抑えられ、Costas ループの引込限界内に余裕で収まる。

---

## 2. FM 復調における検波前チャネル帯域制限と FM スレッショルド改善

### 2.1 背景と問題
`apps/satellite-analyzer/src/iss_dsp.py` では、SGP4 ドップラー補正後の $240\text{ kSPS}$ 複素ベースバンド信号に対して直ちに Polar Discriminator（遅延検波 $p[n] = x[n] x^*[n-1]$ の $\arg(p[n])$）を適用していた。
この方式では、信号成分が存在しない周波数帯（$x[n]$ の $\pm 120\text{ kHz}$ 全域）に広がる熱雑音がそのまま非線形な偏角計算（$\operatorname{atan2}$）に流入し、クリック雑音（位相飛び）が多発して FM スレッショルド限界（Threshold Extension）が大きく悪化していた。

### 2.2 記号一覧
| 記号 | 物理・工学的意味 | 単位 / 典型値 |
| :--- | :--- | :--- |
| $B_{\text{IF}}$ | 検波前（中間周波 / ベースバンド）等価雑音帯域幅 | $240\text{ kHz} \to 16\text{ kHz}$ |
| $B_C$ | Carson の帯域幅規則（Carson's Rule Bandwidth） | 約 $10.4\text{ kHz}$（NFM 1200bps AFSK） |
| $\Delta f_{\text{dev}}$ | FM 最大周波数偏移（Peak Frequency Deviation） | 約 $3.0\text{ kHz}$ |
| $f_m$ | 最高変調周波数（AFSK マーク/スペース最高周波数） | $2.2\text{ kHz}$（Bell 202 Space トーン） |
| $C / N$ | 搬送波対雑音比（Carrier-to-Noise Ratio） | $\text{dB}$ |
| $k_B$ | ボルツマン定数 | $1.3806 \times 10^{-23}\text{ J/K}$ |
| $T_{\text{sys}}$ | 受信システム等価雑音温度 | $\text{K}$（都市部 RTL-SDR 約 $600\text{ K}$） |

### 2.3 日本語での読み解き
FM 復調は「入力信号の位相の時間微分 $\frac{d\phi}{dt}$」を復元する処理である。
搬送波電力 $C$ が検波前雑音電力 $N = k_B T_{\text{sys}} B_{\text{IF}}$ に対して十分に大きい（$C/N > 10\text{ dB}$）場合は線形検波領域（FM 改善度による SNR 向上が得られる領域）で動作する。しかし、$C/N$ が $10\text{ dB}$ を下回ると、ガウス雑音のベクトル合成によって瞬時位相が原点まわりを $2\pi$ 跨ぐ現象（クリック雑音、ポアソン・インパルス雑音）が急増し、検波後の音声 SNR が崖のように急落する（これを **FM スレッショルド効果** と呼ぶ）。
検波前の帯域幅 $B_{\text{IF}}$ を Carson 帯域幅（約 $10.4\text{ kHz}$）に合わせて $240\text{ kHz} \to 16\text{ kHz}$（デシメーション比 5）に制限することで、非線形な位相検波器に入る雑音電力を約 $11.8\text{ dB}$ 削減できるため、FM スレッショルドの発生境界を約 $12\text{ dB}$ 押し下げ、微弱な衛星信号でもクリック雑音に埋もれずに音声パケットを復元できるようになる。

### 2.4 数式展開と直感イメージ
Carson の帯域幅規則は次式で与えられる：
$$B_C = 2 (\Delta f_{\text{dev}} + f_m)$$
ISS の 1200bps AFSK（Bell 202）では、$\Delta f_{\text{dev}} \approx 3.0\text{ kHz}$、$f_m = 2.2\text{ kHz}$ であるから、
$$B_C = 2 (3.0\text{ kHz} + 2.2\text{ kHz}) = 10.4\text{ kHz}$$
検波前の雑音電力 $N_{\text{IF}}$ は帯域幅 $B_{\text{IF}}$ に比例する：
$$N_{\text{IF}} = k_B T_{\text{sys}} B_{\text{IF}}$$
フィルタ帯域を $B_1 = 240\text{ kHz}$ から $B_2 = 16\text{ kHz}$（複素通過帯域 $\pm 8\text{ kHz}$）へ絞ることで得られる雑音電力の改善比 $\Delta \text{SNR}_{\text{pre}}$ は：
$$\Delta \text{SNR}_{\text{pre}} = 10 \log_{10}\left(\frac{B_1}{B_2}\right) = 10 \log_{10}\left(\frac{240\text{ kHz}}{16\text{ kHz}}\right) = 10 \log_{10}(15) \approx 11.76\text{ dB}$$
この $11.76\text{ dB}$ の入力 SNR 向上により、Polar Discriminator $p[n] = x[n] x^*[n-1]$ における位相角
$$\Delta \phi[n] = \operatorname{atan2}(\operatorname{Im}(p[n]), \operatorname{Re}(p[n]))$$
の誤検出率（$\pm \pi$ を跨ぐクリック発生確率 $N_c = \frac{B_{\text{IF}}}{\sqrt{3}} \operatorname{erfc}\left(\sqrt{\frac{C}{N}}\right)$）が指数関数的に減少し、AFSK トーンのシンボル判定誤り率が大幅に改善される。

---

## 3. LEO 衛星における TLE 元期（Epoch）の経時劣化と SGP4 伝搬誤差

### 3.1 背景と問題
`satellite-viewer` および `satellite-analyzer` において、`/root/.config/satdump/satdump_tles.txt`（`assets/satdump_tles.txt`）の優先読み込みにより、`ISS (ZARYA)` と `FUNCUBE-1 (AO-73)` に **Epoch 26247（2026年9月上旬、約60日前）** の TLE が使われていた。
その結果、10月下旬のパスにおいて実際には最大仰角 $39.6^\circ$ で頭上を通過していたパスが「最大仰角 $-14.3^\circ$（地平線下）」と判定され、可視窓が $0\text{ 秒}$ と誤判定される障害が発生していた。

### 3.2 記号一覧
| 記号 | 物理・工学的意味 | 単位 / 典型値 |
| :--- | :--- | :--- |
| $t - t_0$ | TLE 元期（Epoch $t_0$）からの経過時間 | 日（day） |
| $B^*$ | SGP4 擬似弾道係数（大気抵抗パラメータ: $\frac{1}{2}\rho_0 \frac{C_D A}{m}$） | $1/\text{earth radii}$（ISS では $10^{-4} \sim 10^{-3}$） |
| $\Delta M(t)$ | 平均近点角の伝搬誤差（軌道進行方向のズレ） | $\text{rad}$ または $\text{km}$ |
| $n_0$ | 平均運動（Mean Motion: 1日の周回数） | $\text{revs/day}$（LEO: 約 $15.5\text{ rev/day}$） |
| $\dot{n}$ | 平均運動の時間1階微分（半減率項: $\frac{1}{2}\frac{dn}{dt}$） | $\text{revs/day}^2$ |

### 3.3 日本語での読み解き
低軌道（高度 $400 \sim 800\text{ km}$）を周回する衛星は大気抵抗（熱圏の微薄な大気分子との衝突減速）を受ける。特に ISS のような巨大構造物（サッカー場大の太陽電池パドルを持つ）は大気抵抗が極めて大きく、高度が日々数十〜数百メートル低下し、定期的にエンジン噴射（リブースト）を行っている。
TLE の 1 階微分項 $\dot{n}$ や大気抵抗係数 $B^*$ は短期間の平均値にすぎないため、元期から時間が経過すると、時間 $t$ の 2 乗（$\frac{1}{2}\dot{n} t^2$）および 3 乗に比例して進行方向の位置誤差が二次関数的に累積する。
LEO 衛星において 60 日前の TLE を用いると、軌道上の進行方向誤差は数千キロメートル（時間にして数十分のズレ）に達し、ベランダ上空を通過する時刻や方位角・仰角の計算が物理的に破綻する。
したがって、システム内に存在する複数の TLE ソースから **Line 1 の Epoch（元期）を動的に比較し、最も新しい元期を持つ TLE を自動選択するアルゴリズム** を導入した。

### 3.4 数式展開と直感イメージ
TLE Line 1 の 19〜32 カラムに記録される元期文字列（例: `26294.04791053`）の構造は次の通りである：
- `26`: 西暦下 2 桁（2026年）
- `294.04791053`: 1 月 1 日 0:00 UTC を 1.0 とする年間通日（Day of Year: 2026年10月21日 01:09 UTC）

比較可能な元期数値 $E$ への変換式：
$$E = (2000 + YY) \times 1000 + DDD.dddddddd$$
（$YY < 57$ は 2000 年代、$YY \ge 57$ は 1900 年代）。
SGP4 モデルにおける平均近点角 $M(t)$ の時間展開はテイラー級数として近似される：
$$M(t) = M_0 + n_0 (t - t_0) + \frac{1}{2} \dot{n} (t - t_0)^2 + \frac{1}{6} \ddot{n} (t - t_0)^3 + \mathcal{O}((t - t_0)^4)$$
大気密度の変動（太陽活動や地磁気嵐による大気膨張）により、実際の軌道減速とモデル値との差 $\Delta \dot{n}$ が生じると、位置誤差 $\Delta s(t)$ は
$$\Delta s(t) \approx R_{\text{orbit}} \cdot \frac{1}{2} \Delta \dot{n} \cdot (t - t_0)^2$$
で拡大する。60 日（$t - t_0 \approx 60\text{ days}$）経過した場合、$(60)^2 = 3600$ 倍の二乗誤差がかかるため、地上局から見た衛星の位置予測は完全に失効する。最新の元期を選択することで、常に $|t - t_0| \le \text{数日}$ の範囲に収め、TCA 誤差を $\pm 2.5\text{ 秒}$ 以内に保証できる。
