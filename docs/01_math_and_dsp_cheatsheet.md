# 📐 数学 & デジタル信号処理 (DSP) 基礎チートシート

ソフトウェアエンジニアが電波天文学とSDR（ソフトウェア無線）を学ぶ上で、必須となる数学および信号処理の概念を「直感的理解」と「Python実装」を交えて解説します。  
*(※ 不明な用語やハードウェア・天文学の前提概念は [00_glossary.md](00_glossary.md) を参照してください)*

---

## 1. 複素数とオイラーの公式

### なぜ無線信号で複素数（虚数）を扱うのか？
現実の空中を飛んでいる電波は実数の電磁波（電圧変化）ですが、[SDR](00_glossary.md#sdr)でデジタルデータとして扱う際は **複素数（$I + jQ$）** に変換（[直交検波 / IQサンプリング](00_glossary.md#iq)）されます。

#### 直感的理由:
実数信号 $s(t) = A \cos(2\pi f_c t + \phi)$ だけでは、**「位相 $\phi$」** と **「周波数の正負（$+f$ か $-f$ か）」** を区別できません。
$\cos(\theta) = \cos(-\theta)$ であるため、実数のみだと中心周波数より高い信号と低い信号が重なってしまいます（エイリアシング）。

#### オイラーの公式:
$$e^{j\theta} = \cos\theta + j \sin\theta$$

複素平面上で、信号は原点を中心に回転するベクトル（フェーザ）として表現されます。
- **正の周波数 $+f$**: 反時計回りに回転 ($e^{+j 2\pi f t}$)
- **負の周波数 $-f$**: 時計回りに回転 ($e^{-j 2\pi f t}$)

### [IQ信号](00_glossary.md#iq)の定義
- **I成分 (In-phase / 同相成分)**: 搬送波 $\cos(2\pi f_c t)$ と掛け合わせた実部
- **Q成分 (Quadrature / 直交成分)**: 90度位相をずらした搬送波 $-\sin(2\pi f_c t)$ と掛け合わせた虚部

$$x[n] = I[n] + j Q[n]$$

- **振幅（Magnitude / 瞬時電力）**: $|x[n]| = \sqrt{I[n]^2 + Q[n]^2}$
- **位相（Phase）**: $\theta[n] = \text{atan2}(Q[n], I[n])$
- **電力（Power）**: $P[n] = I[n]^2 + Q[n]^2 = |x[n]|^2$

```python
import numpy as np

# 例: サンプリングレート fs = 2.4 MSPS, 100 kHz のトーン信号 (IQ)
fs = 2.4e6
t = np.arange(1024) / fs
f_tone = 100e3

# 複素ベースバンド信号 (正の周波数)
iq_signal = np.exp(1j * 2 * np.pi * f_tone * t)

I = iq_signal.real
Q = iq_signal.imag
power = np.abs(iq_signal)**2
```

---

## 2. サンプリング定理と帯域幅

### ナイキスト・シャノンのサンプリング定理
- **実数サンプリングの場合**: 信号の最高周波数を $f_{\max}$ とするとき、サンプリングレート $f_s \ge 2 f_{\max}$ が必要。
- **複素数サンプリング（[IQサンプリング](00_glossary.md#iq)）の場合**: 複素数データは正負の周波数を区別できるため、**サンプリングレート $f_s$ = 受信可能な瞬時帯域幅 $B$** となります。

> 💡 **例**: RTL-SDRで中心周波数 $f_c = 1420 \text{ MHz}$, サンプリングレート $f_s = 2.4 \text{ MSPS}$（[MSPSの解説](00_glossary.md#msps)）に設定した場合、
> 受信帯域は $[1420 - 1.2 \text{ MHz}, 1420 + 1.2 \text{ MHz}] = [1418.8 \text{ MHz}, 1421.2 \text{ MHz}]$ となります。

---

## 3. 離散フーリエ変換 (DFT / [FFT](00_glossary.md#fft))

時間領域のサンプリングデータ $x[n]$ から、どの周波数成分がどれくらい含まれているかを計算するのが離散フーリエ変換です。

### DFTの定義式:
$$X[k] = \sum_{n=0}^{N-1} x[n] e^{-j \frac{2\pi}{N} k n}, \quad k = 0, 1, \dots, N-1$$

- $N$: FFT点数（例: 1024, 2048）
- $k$: 周波数ビン番号（周波数 $f_k = f_c - \frac{f_s}{2} + k \cdot \frac{f_s}{N}$）
- 計算量: DFTは $O(N^2)$ だが、**高速フーリエ変換 ([FFT](00_glossary.md#fft) / Cooley-Tukey法)** により $O(N \log N)$ で超高速計算可能。

### [窓関数 (Window Function)](00_glossary.md#window) とスペクトル漏れ
有限の長さ $N$ で信号を切り取ると、切り出し端点（不連続点）によって本来存在しない周波数成分にエネルギーが漏れ出す**スペクトル漏れ（Spectral Leakage）**が発生します。

これを抑えるために、端点を滑らかにゼロに近づける「窓関数」を乗算してからFFTを行います。

- **矩形窓 (Rectangular)**: 何もしない。周波数分解能は高いが漏れが大きい。
- **Hann窓 (ハン窓)**: 最も汎用的。$w[n] = 0.5 - 0.5 \cos\left(\frac{2\pi n}{N-1}\right)$
- **Blackman窓**: 漏れを強力に抑制。微弱な信号の検出に適する。

```python
import numpy as np

N = 2048
window = np.blackman(N)
windowed_signal = iq_signal[:N] * window

# FFT実行と周波数シフト (ゼロ周波数を中央へ)
spectrum = np.fft.fftshift(np.fft.fft(windowed_signal))
psd_db = 10 * np.log10(np.abs(spectrum)**2 / N)
```

---

## 4. [放射計方程式（Radiometer Equation）](00_glossary.md#radiometer)

電波天文学において「なぜ微弱な宇宙の電波が見えるのか」を数式で証明するのが放射計方程式です。

### 理論導出
受信機で観測される全電力は、天体からの電波 $T_{\text{ant}}$ と、受信機自体の熱雑音 $T_{\text{sys}}$ の和です。
通常、$T_{\text{ant}} \ll T_{\text{sys}}$（宇宙のシグナルはノイズの1/1000以下）です。

白色ガウス雑音（AWGN）を帯域幅 $B [\text{Hz}]$、[積算時間 $\tau [\text{s}]$](00_glossary.md#integration) で平均化（積算）すると、独立なサンプル数 $N_{\text{samples}} = B \cdot \tau$ が得られます。
中心極限定理により、ノイズの標準偏差（ばらつき） $\Delta T$ はサンプル数の平方根で減少します：

$$\Delta T = \frac{T_{\text{sys}}}{\sqrt{B \cdot \tau}}$$

### 実践的意味:
- 帯域幅 $B = 2.4 \text{ MHz}$, 積算時間 $\tau = 60 \text{ 秒}$ の場合:
  $$\sqrt{B \cdot \tau} = \sqrt{2.4 \times 10^6 \times 60} = \sqrt{1.44 \times 10^8} = 12,000$$
- システム雑音 $T_{\text{sys}} = 300 \text{ K}$ であっても、わずか1分の[積算](00_glossary.md#integration)でノイズの揺らぎは:
  $$\Delta T = \frac{300}{12,000} = 0.025 \text{ K}$$
  まで劇的に圧縮されます。これにより、数ケルビン程度の微弱な天体輝線が明瞭に浮かび上がります。

---

## 5. デシベル (dB) と対数スケール

電波信号は $10^{-15} \text{ W}$ から $1 \text{ W}$ まで桁数が極端に広いため、対数表現（dB）を用います。

- **電力比 $[dB]$**: $P_{\text{dB}} = 10 \log_{10}\left(\frac{P_1}{P_2}\right)$
  - $+3 \text{ dB} \approx 2\text{倍}$
  - $+10 \text{ dB} = 10\text{倍}$
  - $+20 \text{ dB} = 100\text{倍}$
  - $-30 \text{ dB} = 1/1000$
- **絶対電力 $[dBm]$**: $1 \text{ mW}$ を基準 ($0 \text{ dBm} = 1 \text{ mW}$)
  $$P_{\text{dBm}} = 10 \log_{10}\left(\frac{P}{1 \text{ mW}}\right)$$
