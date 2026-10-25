# METEOR-M2 LRPT 画像復調未達の多層原因解剖と DSP・軌道幾何学対策詳解

本ドキュメントでは、北向きベランダ環境において `METEOR-M2 3`（NORAD 57166）および `METEOR-M2 4`（NORAD 59051）の LRPT（Low Rate Picture Transmission）気象画像がなかなか復号できない問題について、Garage S3 上の実録音 IQ データ（`.raw`）のスペクトル解析・軌道計算・復調パイプライン監査から特定された **4 層の根本原因と物理・数学的メカニズム**、および実装した対策を体系的に解説する。

関連用語は [docs/00_glossary.md](../00_glossary.md) を参照。

---

## 1. 物理層：広帯域 QPSK 信号の PSD 盛り上がり $\Delta P$ と真の $E_s/N_0$ の数理

### 1.1 実測された生 IQ データの統計とスペクトル特徴
Garage S3 の `archive/` に保存されていた直近 6 パス分の `.raw` ファイル（サンプリングレート $f_s = 240\text{ kSPS}$, 8-bit unsigned complex I/Q `cu8`）を [FFT](../00_glossary.md#fft) 解析した結果、以下の特性が観測された。

- **ADC 振幅分布**: I/Q 各チャネルとも平均 `127.4`、標準偏差 $\sigma_{\text{ADC}} \approx 1.5\text{ LSB}$、クリッピング率 `0.000%`。8-bit ADC（`0..255`）の下位 1〜2 ビットのみが遷移している状態。
- **LRPT 信号ハンプの出現**: `METEOR-M2 3_20261023_233652.raw`（$t=60\sim 90\text{ s}$）および `METEOR-M2 4_20261023_050548.raw`（$t=60\sim 270\text{ s}$）において、中心周波数（$137.900\text{ MHz}$）周辺の $-40\text{ kHz} \sim +30\text{ kHz}$（帯域幅約 $72\text{ kHz}$）に、Root-Raised-Cosine（RRC）フィルタ整形された QPSK 特有の平坦な台形ハンプが観測された。
- **帯域内外の電力差 $\Delta P$**: 帯域外雑音フロア（$\pm 65 \sim \pm 105\text{ kHz}$）に対する帯域内（$\pm 40\text{ kHz}$）の平均電力密度の上昇量は、最接近時でも **$\Delta P = +1.58 \sim +1.92\text{ dB}$** であった。

### 1.2 帯域内電力比 $(S+N)/N$ から真の $\text{SNR}$・$E_s/N_0$ を求める公式

スペクトル上で観測される帯域内電力密度は「信号電力 $S$ と雑音電力 $N$ の和」であるため、帯域外雑音電力 $N$ とのデシベル差 $\Delta P\text{ [dB]}$ から真の [SNR](../00_glossary.md#snr) および 1 シンボルあたりのエネルギー対雑音電力密度比 $E_s/N_0$ は次式で与えられる。

$$\text{SNR} = \frac{S}{N} = 10^{\frac{\Delta P}{10}} - 1, \qquad \left(\frac{E_s}{N_0}\right)_{\text{dB}} = 10 \log_{10}\left(10^{\frac{\Delta P}{10}} - 1\right) + 10 \log_{10}\left(\frac{B}{R_s}\right)$$

#### 記号一覧
| 記号 | 物理的意味 | 単位 | 実測値・代表値 |
| :--- | :--- | :--- | :--- |
| $S$ | 受信された衛星信号の搬送波電力 | $\text{W}$ | 微弱 |
| $N = k_B T_{\text{sys}} B$ | 評価帯域幅 $B$ 内の熱雑音＋背景雑音電力 | $\text{W}$ | 基準雑音フロア |
| $\Delta P$ | 帯域内電力 $(S+N)$ と帯域外雑音電力 $N$ のデシベル差 | $\text{dB}$ | 実測最大 $+1.92\text{ dB}$ |
| $B$ | 信号電力を積分した評価帯域幅 | $\text{Hz}$ | $80,000\text{ Hz}$（$\pm 40\text{ kHz}$） |
| $R_s$ | OQPSK 変調のシンボルレート（ボーレート） | $\text{Sym/s}$ | $72,000\text{ Sym/s}$（または $80,000\text{ Sym/s}$） |
| $E_s / N_0$ | 1 シンボルあたりのエネルギーと雑音電力密度の比 | $\text{dB}$ | 同期確立に $+3.5 \sim +5.0\text{ dB}$ 必要 |

#### 日本語での読み解き
スペクトル波形がノイズの床から $+1.92\text{ dB}$ 盛り上がっているとき、その山の中には元のノイズ電力 $N$ も含まれている。真の信号成分 $S$ だけを取り出すと、実はノイズ $N$ の約 $0.56$ 倍（$-2.55\text{ dB}$）しかなく、信号がノイズに埋もれている。特に METEOR-M2 の LRPT は $72\text{ kSym/s}$ の広帯域信号（占有帯域幅 $B \approx 80\text{ kHz}$）であるため、狭帯域通信（ISS APRS の $12\text{ kHz}$ や CW の $500\text{ Hz}$）に比べて受信機が取り込む雑音電力 $N = k_B T_{\text{sys}} B$ が約 $7 \sim 160$ 倍大きく、QPSK の搬送波位相ロック（Costas ループ）を維持するための $E_s/N_0$ が約 $6 \sim 7\text{ dB}$ 不足している。

#### 展開ステップ・直感イメージ
1. デシベルの定義より、帯域内電力 $S+N$ と帯域外雑音電力 $N$ の比は次式となる：
   $$\frac{S + N}{N} = 1 + \frac{S}{N} = 10^{\frac{\Delta P}{10}}$$
2. 実測最大値 $\Delta P = 1.92\text{ dB}$ を代入して $S/N$ について解く：
   $$1 + \frac{S}{N} = 10^{0.192} \approx 1.556 \implies \frac{S}{N} = 1.556 - 1 = 0.556$$
3. デシベル表記に変換すると：
   $$\text{SNR}_{\text{dB}} = 10 \log_{10}(0.556) \approx -2.55\text{ dB}$$
4. シンボルレート $R_s = 72\text{ kSym/s}$、評価帯域幅 $B = 80\text{ kHz}$ のとき、帯域幅補正項は $10\log_{10}(80/72) \approx +0.46\text{ dB}$ であるため：
   $$\left(\frac{E_s}{N_0}\right)_{\text{dB}} = -2.55 + 0.46 = -2.09\text{ dB}$$
5. [CCSDS 131.0-B](https://public.ccsds.org/Pubs/131x0b5.pdf) 規格の連接符号（拘束長 $K=7$, 符号化率 $r=1/2$ 畳み込み符号 ＋ Reed-Solomon $\text{RS}(255, 223)$）および SatDump の OQPSK Costas ループがサイクルスリップを起こさずに CADU 同期語（`0x1ACFFC1D`）をロックするには **$E_s/N_0 \gtrsim +3.5 \sim +5.0\text{ dB}$** が必要である。これを逆算すると、スペクトル上のハンプの盛り上がり $\Delta P$ としては：
   $$\Delta P_{\text{req}} = 10 \log_{10}\left(1 + 10^{\frac{3.5 - 0.46}{10}}\right) \approx +4.8 \sim +6.0\text{ dB}$$
   が必要となる。

---

## 2. 復調層：SatDump 72k / 80k パイプライン仕様と Gardner クロック再生ループの引き込み限界

### 2.1 SatDump における METEOR-M2-x パイプラインの二面性
[SatDump 公式リポジトリの `pipelines/Meteor-M.json`](https://github.com/SatDump/SatDump/blob/master/pipelines/Meteor-M.json) には、`METEOR-M2 3` / `METEOR-M2 4` 向けに以下の 2 つのパイプラインが定義されている：

1. **`meteor_m2-x_lrpt`（METEOR M2-x LRPT 72k）**:
   - `symbolrate: 72000`, `constellation: oqpsk`, `rrc_alpha: 0.5`, `pll_bw: 0.002`
   - `ccsds_conv_concat_decoder`（非インターリーブ RS(255,223), Interleaving Depth $I=4$）
   - **M2-3 / M2-4 の標準運用モード**。
2. **`meteor_m2-x_lrpt_80k`（METEOR M2-x LRPT 80k）**:
   - `symbolrate: 80000`, `constellation: oqpsk`, `interleaved: true`

旧実装の `apps/satellite-analyzer/src/meteor_decoder.py` は `meteor_m2-x_lrpt_80k` のみをハードコードして呼び出しており、標準運用の `meteor_m2-x_lrpt`（72k）を試行していなかった。

### 2.2 シンボルレート不一致とクロック再生ループの引き込み条件

デジタル復調器のシンボルタイミング再生（Gardner ループ）がロック可能なシンボルレートの相対誤差条件は次式で表される。

$$\delta_R = \left|\frac{R_{\text{actual}} - R_{\text{nom}}}{R_{\text{nom}}}\right| \le \delta_{\omega,\max}$$

#### 記号一覧
| 記号 | 物理的意味 | 単位 | 値 |
| :--- | :--- | :--- | :--- |
| $R_{\text{actual}}$ | 衛星が実際に送信しているシンボルレート | $\text{Sym/s}$ | $72,000\text{ Sym/s}$ |
| $R_{\text{nom}}$ | 復調器に設定された公称シンボルレート | $\text{Sym/s}$ | `80k` 固定時：$80,000\text{ Sym/s}$ |
| $\delta_R$ | シンボルレートの相対偏差 | 無次元 | $0.10$（$10.0\%$） |
| $\delta_{\omega,\max}$ | クロック再生ループの最大相対引き込み範囲（`clock_omega_relative_limit`） | 無次元 | SatDump 標準：$0.005$（$0.5\%$） |

#### 日本語での読み解き
Gardner タイミング再生ループは、公称シンボルレート $R_{\text{nom}}$ からの微小なクロック誤差（$\pm 0.5\%$ 以内）のみを追従できるようループフィルタ帯域が狭く設計されている。そのため、$72\text{ kSym/s}$ の信号に対して $80\text{ kSym/s}$ のパイプラインのみを実行すると、相対誤差が $10.0\%$（引き込み限界の 20 倍）となり、SNR が無限大であってもシンボル同期は数学的に不可能となる。

#### 展開ステップ・直感イメージ
1. $R_{\text{actual}} = 72,000\text{ Sym/s}$ に対して $R_{\text{nom}} = 80,000\text{ Sym/s}$ を与えたときの相対偏差 $\delta_R$ を求める：
   $$\delta_R = \frac{|72,000 - 80,000|}{80,000} = 0.100 \quad (10.0\%)$$
2. 10 シンボル（約 $125\ \mu\text{s}$）ごとに 1 シンボル分のサンプリング位相スリップが発生するため、8,192 bit（4,096 シンボル）長の CADU フレーム全体では約 400 シンボルものスリップが累積し、フレーム同期は 100% 失敗する。
3. **対策**: [`apps/satellite-analyzer/src/meteor_decoder.py`](../../apps/satellite-analyzer/src/meteor_decoder.py) において、`DEFAULT_PIPELINES = ("meteor_m2-x_lrpt", "meteor_m2-x_lrpt_80k")` を順に試行し、72k モードを優先しつつ 80k モードへ自動フォールバックするマルチパイプライン構成へ改修した。

---

## 3. 軌道・ベランダ幾何学層：北向きベランダの方位角境界と極軌道パスの関係

太陽同期極軌道（軌道傾斜角 $i \approx 98.6^\circ$）を周回する `METEOR-M2 3` / `METEOR-M2 4` のパスを北向きベランダから観測する際、旧実装の `is_in_view`（$\text{Az} \in [270^\circ, 360^\circ] \cup [0^\circ, 90^\circ]$）では以下の問題が生じていた：

- **北東から東へ抜ける降交点パス（例：`2026-10-23T23:36:52Z`）**:
  北北東（$\text{Az} = 27^\circ, \text{El} = 10^\circ$）から昇り、真東をわずかに越えた **$\text{Az} \approx 95^\circ \sim 97^\circ$ で最高仰角（TCA: $\text{El} = 44.4^\circ$, 最短距離 $1,111\text{ km}$）** に達する。しかし旧実装では $\text{Az} = 90.0^\circ$ を越えた瞬間に `LOS` と判定して `rtl_sdr` を強制終了していたため、**最も電波が強くなる最接近ピークの瞬間を取り逃がしていた**。
- **対策**: [`apps/satellite-tracker-rs/src/orbit.rs`](../../apps/satellite-tracker-rs/src/orbit.rs) の `is_in_view` に東西側面の見通し・TCA 保護マージン（$\pm 20^\circ$）を導入し、北向きベランダの有効方位角を **$\text{Az} \in [250^\circ, 360^\circ] \cup [0^\circ, 110^\circ]$**（建物躯体で完全に遮蔽される南側 $110^\circ < \text{Az} < 250^\circ$ のみを除外）に拡張した。あわせて [`apps/satellite-tracker-rs/src/main.rs`](../../apps/satellite-tracker-rs/src/main.rs) の初期 TLE を 2026年10月時点の最新エポックへ更新し、ローカル TLE カタログファイルからの動的ロード機能（`parse_tle_catalog`）を追加した。

---

## 4. 可観測性層：複素 IQ ベースバンドの両側パワースペクトル密度（PSD）可視化

### 4.1 旧実装の課題
旧 [`apps/satellite-analyzer/src/spectrogram_generator.py`](../../apps/satellite-analyzer/src/spectrogram_generator.py) は、`.raw` ファイルを読み込む際に先頭 `48000 * 20` バイト（$240\text{ kSPS}$ 複素 IQ でわずか $2\text{ 秒分}$）だけを切り出し、かつ I と Q が交互に並んだバイト列を「$48\text{ kHz}$ の実数 1 チャネル音声」として STFT していた。

### 4.2 複素ベースバンド信号 $z[n] = I[n] + j Q[n]$ の両側離散フーリエ変換

直交復調（Quadrature Demodulation）された複素ベースバンド信号 $z[n]$ の離散時間フーリエ変換（DTFT / DFT）は、正の周波数オフセットと負の周波数オフセットを独立に表現する：

$$Z[k] = \sum_{n=0}^{N-1} w[n] \left(\tilde{I}[n] + j \tilde{Q}[n]\right) e^{-j \frac{2\pi k n}{N}}, \qquad S_{zz}[k] = \frac{|Z[k]|^2}{f_s \sum_{n=0}^{N-1} w^2[n]}$$

#### 記号一覧
| 記号 | 物理的意味 | 単位 |
| :--- | :--- | :--- |
| $\tilde{I}[n], \tilde{Q}[n]$ | DC オフセット（平均値）を除去した同相・直交成分 | 無次元（$[-1, 1]$ 正規化） |
| $w[n]$ | 窓関数（Hann 窓） | 無次元 |
| $N$ | 1 フレームあたりの FFT ポイント数（`nperseg`） | サンプル数（$1024$） |
| $f_s$ | 複素 IQ サンプリングレート | $\text{Hz}$（$240,000\text{ Hz}$） |
| $k$ | `fftshift` 後の周波数ビン番号（$-N/2 \le k < N/2$） | ビン |
| $S_{zz}[k]$ | 両側パワースペクトル密度（Two-sided PSD） | $\text{W/Hz}$（対数表示で $\text{dB/Hz}$） |

#### 日本語での読み解き
実数信号のフーリエ変換は正負の周波数が対称（$|X[-k]| = |X[k]|$）になるため片側（$0 \sim f_s/2$）しか情報を持たないが、I と Q の位相差（$90^\circ$）を持つ複素 IQ 信号 $z[n] = I[n] + jQ[n]$ では、中心周波数 $f_0$ より高い周波数（$+f$）と低い周波数（$-f$）が互いに打ち消し合わずに分離される。これにより、$-f_s/2 \sim +f_s/2$（$-120\text{ kHz} \sim +120\text{ kHz}$）の全帯域にわたる LRPT 台形スペクトルやドップラーシフトを正確に画像化できる。

#### 展開ステップ・直感イメージ
1. 単一複素正弦波 $z(t) = e^{+j 2\pi f_{\Delta} t} = \cos(2\pi f_{\Delta} t) + j \sin(2\pi f_{\Delta} t)$ を考える。
2. これをオイラーの公式 $e^{-j 2\pi f t}$ と乗算して積分すると、$f = +f_{\Delta}$ では位相が揃って $e^0 = 1$ となり強いピークを生じる一方、鏡像周波数の $f = -f_{\Delta}$ では $e^{+j 4\pi f_{\Delta} t}$ が高速回転して積分値が $0$ となる（イメージ抑圧）。
3. もし I と Q を交互に並べた 1 次元実数列 $x[2n] = I[n], x[2n+1] = Q[n]$ として実数 FFT してしまうと、直交位相情報が失われて正負の周波数が折り返し混信（エイリアシング）を起こす。
4. **対策**: [`SpectrogramGenerator.compute_complex_iq_spectrogram`](../../apps/satellite-analyzer/src/spectrogram_generator.py) を実装し、`np.memmap` を用いて 150 MB 超の `.raw` ファイル全体（約 300 秒間）を走査し、DC スパイク補間とパーセンタイル動的コントラスト調整（5%〜99.5%点）を施した両側複素ベースバンドスペクトログラムを描画するよう改修した。

---

## 5. KubeEdge 本番デプロイ後の実観測検証とマルチパス・アンテナ姿勢（時計盤モデル）の電磁気学

`satellite-tracker:v1.4.2` および `satellite-analyzer-worker:v1.5.2` を KubeEdge / k3s クラスタへロールアウトした当日（2026-10-24）の 3 パス（`13:49 JST`, `15:28 JST`, `19:59 JST`）の実測比較により、以下の軌道幾何学的・電磁気学的メカニズムが実証された。

### 5.1 2026-10-24 当日 3 パスの定量比較

| パス ID | 録音時刻 (JST) | AOS 仰角・方位角 | 斜距離 $d$ | 帯域内電力比 $\Delta P$ | 観測された物理現象 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `METEOR-M2 4_20261024_044352` | `13:43:52〜13:49:28` (`334.8s`) | $\text{El}=55.6^\circ$, $\text{Az}=89.1^\circ$ | $\approx 950\text{ km}$ | **$+2.69\text{ dB}$** ($E_s/N_0 \approx -0.21\text{ dB}$) | $t=120\sim 290\text{ s}$ に鮮明な QPSK 帯と約 15〜20 秒周期のマルチパス縦縞を観測 |
| `METEOR-M2 4_20261024_062309` | `15:23:09〜15:27:37` (`266.5s`) | $\text{El}=14.1^\circ$, $\text{Az}=250.1^\circ$ | $\approx 2,200\text{ km}$ | $+0.33\text{ dB}$（雑音床） | 1周回（101分）後の西側超低仰角パス。距離減衰 $+7.3\text{ dB}$ と手すり遮蔽により不達 |
| `METEOR-M2 3_20261024_105851` | `19:58:51〜20:05:01` (`368.6s`) | $\text{El}=46.4^\circ$, $\text{Az}=109.1^\circ$ | $\approx 1,080\text{ km}$ | **$+0.83\text{ dB}$**（ピーク $+2.5\text{ dB}$） | 東側マージン拡張（$\text{Az}\le 110^\circ$）により進入捕捉。$t=120\sim 280\text{ s}$ でドップラー偏移 $-4.0 \to -5.3\text{ kHz}$ と 20 秒周期の縦縞を観測 |

### 5.2 約 20 秒周期の縦縞（Lloyd's Mirror 2波マルチパス干渉）とブリュースター角の数理

`044352` パスおよび `105851` パスの全パス複素 IQ スペクトログラムには、中心 $\pm 40\text{ kHz}$ の QPSK 信号帯に **約 15〜20 秒周期で信号強度が浮き沈みする規則的な縦縞（フェージング・ノッチ）** が観測された。

これは、衛星からアンテナへ直接届く **直達波** と、ベランダの床・手すり・壁面で反射して届く **反射波** の経路差 $\Delta L(\theta)$ が、衛星仰角 $\theta(t)$ の変化に伴って波長 $\lambda = 2.175\text{ m}$ の整数倍・半整数倍を周期的に通過する **2波干渉（Lloyd's Mirror 効果）** である。また、コンクリート面（比誘電率 $\varepsilon_r \approx 5$）における垂直電界成分（TM波）のフレネル反射係数 $\Gamma_{\text{TM}}(\psi)$ は次式で与えられる。

$$\Delta L(\theta) = 2 h_{\text{eff}} \sin\theta(t), \qquad \Gamma_{\text{TM}}(\psi) = \frac{\varepsilon_r \sin\psi - \sqrt{\varepsilon_r - \cos^2\psi}}{\varepsilon_r \sin\psi + \sqrt{\varepsilon_r - \cos^2\psi}}$$

#### 記号一覧
| 記号 | 物理的意味 | 単位 | 代表値 |
| :--- | :--- | :--- | :--- |
| $h_{\text{eff}}$ | 反射面（ベランダ床・手すり・壁面）からアンテナ給電点までの有効距離 | $\text{m}$ | $0.5 \sim 1.5\text{ m}$ |
| $\theta(t), \psi$ | 時刻 $t$ における衛星の仰角（反射面に対するかすめ角） | $\text{rad}$ または度（$^\circ$） | $20^\circ \sim 55^\circ$ |
| $\Delta L(\theta)$ | 直達波と反射波の幾何学的経路長差 | $\text{m}$ | $\lambda/2 \approx 1.09\text{ m}$ 変化ごとに 1 周期 |
| $\varepsilon_r$ | ベランダのコンクリートスラブの比誘電率 | 無次元 | $\approx 5.0$ |
| $\Gamma_{\text{TM}}(\psi)$ | 垂直電界成分（TM波）の振幅反射係数 | 無次元 | $\psi_B \approx 24.1^\circ$ で $0$ |

#### 日本語での読み解き
衛星の仰角 $\theta(t)$ が時間とともに変化すると、直達波と反射波の経路差 $\Delta L(\theta)$ が変化し、約 15〜20 秒おきに逆位相（打ち消し合い）の谷（ヌル）が訪れて [Costas ループ](../00_glossary.md#costas-loop)の同期を切る。水平偏波（TE波）はコンクリート面で $70\sim 80\%$ 強く反射するが、アンテナを傾けて垂直電界成分（TM波）を持たせると、コンクリートのブリュースター角（$\psi_B \approx 24.1^\circ$）付近で反射波 $\Gamma_{\text{TM}}$ がゼロに近づき、マルチパス干渉の谷を大幅に抑制できる。

#### 展開ステップ・直感イメージ
1. $\Gamma_{\text{TM}}(\psi_B) = 0$ となるブリュースター仰角 $\psi_B$ を求めるため、分子を $0$ と置く：
   $$\varepsilon_r \sin\psi_B = \sqrt{\varepsilon_r - \cos^2\psi_B}$$
2. 両辺を 2 乗して $\cos^2\psi_B = 1 - \sin^2\psi_B$ を代入する：
   $$\varepsilon_r^2 \sin^2\psi_B = \varepsilon_r - 1 + \sin^2\psi_B \implies (\varepsilon_r^2 - 1)\sin^2\psi_B = \varepsilon_r - 1$$
3. $\varepsilon_r > 1$ より $(\varepsilon_r - 1)$ で両辺を割ると：
   $$(\varepsilon_r + 1)\sin^2\psi_B = 1 \implies \psi_B = \arcsin\left(\frac{1}{\sqrt{\varepsilon_r + 1}}\right)$$
4. コンクリートの $\varepsilon_r = 5$ を代入すると $\psi_B = \arcsin(1/\sqrt{6}) \approx 24.1^\circ$ となり、北向きベランダの開口窓下半分（仰角 $20^\circ \sim 45^\circ$）における床・天井反射を効果的に消去できることが導かれる。

### 5.3 Vダイポールアンテナの物理セッティング改善（「壁掛け時計の 10時15分」から「空を見上げる 10時10分」へ）

実機確認により、従来の V ダイポールは以下の姿勢（「壁掛け時計の 10時15分」）で設置されていたことが判明した：
1. **変更前（縦置き・非対称「10時15分」）**:
   - 支柱に対して中心ブロックが直角（縦置き＝壁掛け時計の文字盤のように垂直）に立っており、正面から見て左エレメントが **10時方向（斜め左上 $+30^\circ$）**、右エレメントが **3時＝15分方向（真横 $0^\circ$）** を向いていた。
   - **問題点**:
     1. 右エレメント（3時）がベランダの金属手すりバーと完全に平行となり、強い電磁結合と反射干渉（20秒周期の縦縞）を引き起こしていた。
     2. 文字盤が縦（垂直面）に立っているため、10時方向のエレメントの先端（ダイポールの軸方向＝**利得ゼロの死角 / End-fire null**）が上空の衛星飛来方向を向いてしまっていた。
2. **変更後（北へ $60^\circ$ 寝かせた左右対称「10時10分」）**:
   - **ステップ 1（針の対称化）**: 左右のエレメントを **「10時10分」（開き角 $120^\circ$、左右対称、先端が北西と北東を向く V 字）** に揃え、金属手すりとの平行配置を解消した。
   - **ステップ 2（文字盤の前傾・水平化）**: 縦に立っていた文字盤（V字平面）を三脚の関節で **北側（外側）へ約 $60^\circ$ 倒し（水平から $30^\circ$ だけ起きた前傾角度）**、V字の「面（腹＝最大利得方向）」が北の空の開口窓（仰角 $45^\circ \sim 60^\circ$）を正面から見上げる姿勢に刷新した。
   - これにより、円偏波（RHCP）の METEOR 衛星に対する上空の死角（軸方向ヌル）と手すり平行結合が同時に解消され、直達波利得とマルチパス耐性が最大化される。

---

## 6. アンテナ姿勢刷新後の実証と初画像復調の突破（2026-10-25 09:53 JST 実観測）

2026年10月25日 09:53 JST、物理セッティングを「北へ $60^\circ$ 寝かせた左右対称の 10時10分」に刷新した後の最初の昼間高仰角パス（`METEOR-M2 3_20261025_005344`）において、**当観測所初となる本物の METEOR-M2 LRPT 地球気象衛星画像（雲画像）の復調に成功した**。

### 6.1 実測スペクトルの飛躍的改善：$\Delta P = +3.89\text{ dB}$ と真の $E_s/N_0 \approx +3.88\text{ dB}$

録音時間 $406.3\text{ 秒}$、ファイルサイズ $195.0\text{ MB}$ にわたる複素 IQ データの両側 PSD を解析した結果、アンテナ姿勢の刷新によって電波環境が劇的に好転していることが実証された。

| 観測パラメータ | 姿勢刷新前（`20261024_044352`） | 姿勢刷新後（`20261025_005344` 09:53 JST） | 物理的改善効果 |
| :--- | :--- | :--- | :--- |
| **通過時間 / データサイズ** | $334.8\text{ s}$ / $160.7\text{ MB}$ | **$406.3\text{ s}$ / $195.0\text{ MB}$** | 北向きベランダの開口窓を端から端までフル追跡 |
| **平均帯域内電力比 $\Delta P$** | $+2.69\text{ dB}$ | **$+3.89\text{ dB}$**（平坦部ピーク **$+5.05\text{ dB}$**） | 手すり結合遮断と上空ビーム整合により $+2.36\text{ dB}$ 向上 |
| **真の帯域内 $\text{SNR}$** | $-0.67\text{ dB}$ | **$+1.61\text{ dB}$**（平坦部ピーク **$+3.42\text{ dB}$**） | 信号電力が雑音電力を上回る（$S > N$）正の領域へ突入 |
| **1シンボルあたり $E_s/N_0$** | $-0.21\text{ dB}$ | **$+2.07\text{ dB}$**（平坦部ピーク **$+3.88\text{ dB}$**） | **CADU 同期維持閾値（$+3.5\text{ dB}$）を突破** |
| **マルチパス縦縞（Lloyd's Mirror）** | 約 15〜20 秒周期で深いヌル | **縦縞が大幅に平滑化** | 90 秒以上にわたり安定した位相連続性を維持 |

ピーク時の諸元は、第 1.2 節の公式 $\text{SNR} = 10^{\frac{\Delta P}{10}} - 1$ および $(E_s/N_0)_{\text{dB}} = \text{SNR}_{\text{dB}} + 10\log_{10}(B/R_s)$ より導かれる：
1. $\Delta P_{\text{peak}} = +5.05\text{ dB} \implies \text{SNR} = 10^{0.505} - 1 \approx 2.199 \implies \text{SNR}_{\text{dB}} \approx +3.42\text{ dB}$
2. 帯域幅補正項（$B = 80\text{ kHz}, R_s = 72\text{ kSym/s}$）：$10\log_{10}(80/72) \approx +0.46\text{ dB}$
3. $(E_s/N_0)_{\text{peak}} = +3.42 + 0.46 = \mathbf{+3.88\text{ dB}}$（同期要求閾値 $+3.5\text{ dB}$ をクリア）

### 6.2 RTL-SDR DC オフセット（0Hz スパイク）と OQPSK Costas ループの数理、および `--dc_block` の導入

#### 1. RTL-SDR 残留 DC オフセットによる Costas 位相誤差擾乱の数理
RTL-SDR（R828D + RTL2832U）の ADC は 8-bit unsigned（`cu8`：$0 \sim 255$）を出力する。公称中心値は $127.5$ であるが、アナログ直交ミキサの LO（局部発振器）自己漏洩や ADC 内部のオフセットにより、実測サンプルのアンサンブル平均値は $127.40\text{ LSB}$（中心から $-0.10\text{ LSB}$ の偏り）となっていた。
SatDump が `cu8` を float 複素信号へ変換する際、一律に $127.5$ を減算するため、ベースバンド周波数 $0\text{ Hz}$（中心周波数 $137.900\text{ MHz}$ の真上）に直流ベクトル成分 $z_{\text{DC}} = I_{\text{DC}} + j Q_{\text{DC}}$ が定常的に残留する。

この微小な DC オフセットが、OQPSK の搬送波位相ロック（Costas ループ）を狂わせるメカニズムは次式で表される。

$$e_{\text{Costas}}[n] = \text{Im}\left\{ (z[n] + z_{\text{DC}})^4 \right\} \approx \text{Im}\left\{ z^4[n] \right\} + 4 |z[n]|^2 \text{Im}\left\{ z[n] z_{\text{DC}}^* \right\}$$

##### 記号一覧
| 記号 | 物理的意味 | 単位 | 実測値・代表値 |
| :--- | :--- | :--- | :--- |
| $z[n] = I[n] + j Q[n]$ | 衛星から受信された真の OQPSK 変調複素ベースバンド信号 | 無次元（正規化振幅） | $\sigma \approx 1.81\text{ LSB}$ |
| $z_{\text{DC}} = I_{\text{DC}} + j Q_{\text{DC}}$ | RTL-SDR の ADC 不平衡による残留直流（0Hz）オフセット | 無次元 | 約 $-0.10\text{ LSB}$ |
| $e_{\text{Costas}}[n]$ | 4相位相検出器（Costas ループ位相誤差出力信号） | 無次元 | 追従誤差 |
| $B_{\text{pll}}$ | Costas ループのループフィルタ等価雑音帯域幅 | $\text{Hz}$ | $B_{\text{pll}} = 0.002 \times R_s \approx 144\text{ Hz}$ |
| $P_{\text{DC}} / N_{\text{loop}}$ | PLL ループ帯域内における DC スパイク対雑音電力比 | $\text{dB}$ | **$+10\text{ dB}$ 以上**（強力な固定キャリアとして作用） |

##### 日本語での読み解き
広帯域全体（$240\text{ kHz}$）で見ると $-0.10\text{ LSB}$ の DC 電力は全電力の数パーセントに過ぎない。しかし、復調器の Costas ループフィルタは熱雑音を排除するために等価帯域幅がわずか $144\text{ Hz}$ と極めて狭く設計されている。この狭小な $144\text{ Hz}$ 帯域の中に、周波数幅が理論上ゼロ（デルタ関数 $\delta(f)$）である DC オフセットのエネルギーが全て集中するため、PLL から見ると「真の衛星搬送波の真横に、動かない強力な偽の搬送波（未変調キャリア）が居座っている」状態になる。これにより Costas ループは真の QPSK コンスタレーションの回転を追従できず、偽の位相にトラップされてサイクルスリップ（位相同期の脱落）を頻発していた。

##### 展開ステップ・直感イメージ
1. 4相変調（QPSK/OQPSK）の搬送波再生では、4乗演算を行うことでシンボルデータ（$\pm \pi/4, \pm 3\pi/4$）の変調成分（$\pi/2$ の倍数）を縮退消去し、キャリア周波数・位相誤差のみを抽出する：
   $$z(t) = A e^{j(\theta(t) + m \frac{\pi}{2})} \implies z^4(t) = A^4 e^{j 4\theta(t)} \implies \text{Im}\{z^4(t)\} = A^4 \sin(4\theta(t))$$
2. ここに定常直流オフセット $z_{\text{DC}}$ が加わると、2項展開によって干渉クロス項が発生する：
   $$(z[n] + z_{\text{DC}})^4 = z^4[n] + 4 z^3[n] z_{\text{DC}} + 6 z^2[n] z_{\text{DC}}^2 + 4 z[n] z_{\text{DC}}^3 + z_{\text{DC}}^4$$
3. 変調成分 $z[n]$ のアンサンブル期待値 $\mathbb{E}[z^3] = 0$ であっても、フェージング等で振幅 $A$ が時間変動すると、第 2 項 $4 z^3[n] z_{\text{DC}}$ や高次項がループフィルタへ強力な偏り（バイアストルク）を与え続ける。
4. この結果、ループフィルタの積分器（NCO: 数値制御発振器）が DC スパイク周波数（$0\text{ Hz}$）へ引きずり込まれ、衛星のドップラー周波数追従から脱調する。

---

#### 2. 1次 IIR DC ブロックフィルタ（`--dc_block true`）の伝達関数と周波数応答
この問題を根絶するため、SatDump のベースバンド復調パラメータに `--dc_block true` を追加した。これは復調直前の複素ベースバンド時系列 $x[n]$ に対し、以下の差分方程式で定義される 1 次 IIR 高域通過フィルタ（DC ノッチフィルタ）を適用するものである。

$$y[n] = x[n] - x[n-1] + \alpha y[n-1], \qquad H(z) = \frac{1 - z^{-1}}{1 - \alpha z^{-1}}$$

##### 記号一覧
| 記号 | 物理的意味 | 単位 | 設定値・代表値 |
| :--- | :--- | :--- | :--- |
| $x[n]$ | フィルタ入力複素ベースバンドサンプル（DC オフセット重畳） | 無次元 | サンプリング周期 $T_s = 1/f_s$ |
| $y[n]$ | フィルタ出力複素ベースバンドサンプル（DC 除去済み） | 無次元 | 追従用クリーン信号 |
| $\alpha$ | フィルタの極（Pole）位置を決定する減衰係数（$0 < \alpha < 1$） | 無次元 | $\alpha \approx 0.9995$ |
| $f_c$ | 高域通過フィルタの $-3\text{ dB}$ カットオフ周波数 | $\text{Hz}$ | 約 $20\text{ Hz}$ |
| $f_s$ | 複素 IQ サンプリング周波数 | $\text{Hz}$ | $240,000\text{ Hz}$ |
| $z$ | Z 変換の複素変数（$z = e^{j 2\pi f / f_s}$） | 無次元 | 単位円上の周波数 |

##### 日本語での読み解き
このフィルタは、分子 $(1 - z^{-1})$ によって周波数 $0\text{ Hz}$（$z = 1$）に厳密な「零点（Zero）」を配置し、DC スパイクの振幅を完全にゼロ（$-\infty\text{ dB}$）に削ぎ落とす。同時に、分母 $(1 - \alpha z^{-1})$ によって $z = \alpha$（$1$ に極めて近い位置）に「極（Pole）」を置くことで、DC からわずか $20\text{ Hz}$ 離れた周波数以上では伝達関数 $|H(z)|$ を平坦な $1$（$0\text{ dB}$）へと急峻に復帰させる。これにより、$72\text{ kHz}$ の広帯域 QPSK 変調成分の位相や波形を一切歪ませることなく、Costas ループを狂わせる $0\text{ Hz}$ スパイクのみを瞬時に消滅させる。

##### 展開ステップ・直感イメージ
1. 差分方程式 $y[n] - \alpha y[n-1] = x[n] - x[n-1]$ の両辺を Z 変換する：
   $$Y(z)(1 - \alpha z^{-1}) = X(z)(1 - z^{-1}) \implies H(z) = \frac{Y(z)}{X(z)} = \frac{1 - z^{-1}}{1 - \alpha z^{-1}}$$
2. 直流（$f = 0\text{ Hz} \implies z = e^{j 0} = 1$）での利得を評価する：
   $$H(1) = \frac{1 - 1}{1 - \alpha} = \frac{0}{1 - \alpha} = 0 \quad (-\infty\text{ dB})$$
3. カットオフ周波数 $f_c$ と係数 $\alpha$ の関係を 1 次テイラー展開で近似導出する：
   $$\alpha = 1 - \frac{2\pi f_c}{f_s} = 1 - \frac{2\pi \times 20}{240,000} \approx 1 - 0.000523 = 0.999477$$
4. 通過域（例：衛星信号帯域 $f \ge 1\text{ kHz}$）における振幅特性を評価すると、$|H(e^{j 2\pi f / f_s})| \approx 1.0000$（損失 $0.00\text{ dB}$、位相回転 $0^\circ$）となり、主信号に副作用を与えない。
5. **実装**: [`apps/satellite-analyzer/src/meteor_decoder.py`](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/satellite-analyzer/src/meteor_decoder.py) の SatDump 実行コマンド引数に `"--dc_block", "true"` を恒久適用した。

---

### 6.3 コンテナ化・分散運用環境における 2 つのインフラ障害と解決策

#### 1. SatDump の CelesTrak オンライン TLE 更新による 120 秒タイムアウト
- **障害現象**: コンテナ内で SatDump を実行すると、CPU 使用率が 0% のまま 120 秒間無応答となり、`subprocess.TimeoutExpired` で強制終了されていた。
- **原因解剖**: SatDump のデフォルト設定ファイル `/usr/share/satdump/satdump_cfg.json` には `"tle_update_interval": {"value": "1 day"}` と CelesTrak の URL 群が定義されていた。外部インターネットへのアウトバウンドが制限された Pod 環境や CelesTrak が応答遅延を起こす環境において、SatDump 起動時に同期 HTTP GET が 150 秒間ブロッキングしていた。
- **改修**: [`apps/satellite-analyzer/Dockerfile`](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/satellite-analyzer/Dockerfile) に以下を追加：
  ```dockerfile
  RUN sed -i 's/"value": "1 day"/"value": "Never"/g' /usr/share/satdump/satdump_cfg.json
  ```
  外部通信を完全抑止した結果、SatDump の起動・復調時間は **150 秒超からわずか 10 秒へと激減** した。

#### 2. 長大パス（>380秒・>190MB）における Tracker Pod の OOMKill
- **障害現象**: 録音時間が 400 秒を超えたパスにおいて、`satellite-tracker` Pod が録音終了直後に突然 CrashLoopBackOff（OOMKilled）し、Garage S3 へのアップロードが中断される事象が発生した。
- **原因解剖**: $240\text{ kSPS}$ の生 IQ データは 400 秒で約 $195\text{ MB}$ に達する。`satellite-tracker-rs` がファイルをメモリバッファへロードし、SHA256 チェックサムの算出および HTTP リクエストのストリーミング組み立てを行う過程で、瞬間最大メモリ消費量が約 $390\text{ MB}$ に達していた。従来のコンテナメモリ制限（`limits.memory: 384Mi`）をわずかに超過して Linux OOM Killer が発動していた。
- **改修**: [`infrastructure/apps/satellite-tracker/deployment.yaml`](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/infrastructure/apps/satellite-tracker/deployment.yaml) のメモリ制限を `384Mi` から **`768Mi`** へ倍増。長大な高仰角パスでも安定して S3 へ転送可能となった。

---

### 6.4 初復調の成果と画像フィルタリング

以上の多層改善（アンテナ姿勢刷新、DC ブロック、TLE 更新抑止、メモリ倍増）が結実し、`METEOR-M2 3_20261025_005344` において以下を達成した：

1. **CADU フレーム同期**:
   - SatDump が CADU 同期マーカー（`0x1ACFFC1D`）を捉え、計 **26,624 バイトの正規 CADU パケット** の復号に成功。
2. **気象画像（MSU-MR）の生成**:
   - 可視光チャンネル（`MSU-MR-1.png`: 25 KB、日本上空・オホーツク海周辺の雲の渦・筋状の雲パターンが鮮明に写る本物の地球気象画像）
   - 近赤外・赤外線チャンネル（`MSU-MR-2.png`, `MSU-MR-4.png`）
   - 疑似カラー合成画像（`msu_mr_rgb_MSU-MR_124_False_Color_corrected.png`: 140 KB, `msu_mr_rgb_AVHRR_221_False_Color_corrected.png`: 94 KB）
   - 計 11 枚の有効画像を出力。
3. **空プレースホルダ画像の自動除外**:
   - パイプライン内でデータ不足時に生成される 262 バイト等の空画像が Web ビューアや統計を汚染しないよう、[`apps/satellite-analyzer/src/meteor_decoder.py`](file:///home/tozastation/ghq/github.com/tozastation/radio-astronomy/apps/satellite-analyzer/src/meteor_decoder.py) に `os.path.getsize(img) > 1024`（1KB 超）の検証フィルタを導入。
4. **S3 ストレージと Web ビューアへの統合**:
   - Garage S3 の `results/METEOR-M2 3/METEOR-M2 3_20261025_005344/` に 11 枚の気象画像と `packets.json`、`summary.json` を公開。Web ビューア（NodePort 30088）にて即座に地球画像の閲覧が可能となった。
