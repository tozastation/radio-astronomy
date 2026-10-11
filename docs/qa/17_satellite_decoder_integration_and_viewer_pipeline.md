# 🛰️ 衛星デコーダ統合（SatDump・gr-satellites）と自律ビューアパイプライン詳解

本ドキュメントでは、気象衛星 METEOR-M2 の LRPT（QPSK 72k/80kbps）および CubeSat FUNcube-1（AO-73）の BPSK 1200bps に対する真のデコーダ結合（方式B：コンテナイメージへの SatDump & gr-satellites 統合）のアーキテクチャ、物理層と信号処理（DSP）の数理的背景、および自律パイプラインにおける成果物可視化について解説します。

---

## 1. プレースホルダー問題の背景と物理層の課題

### 1.1 「中身が見える」観測工学の原則
本地上局プロジェクトでは、[docs/qa/10_observation_metrics_and_heuristic_evaluation.md](10_observation_metrics_and_heuristic_evaluation.md) で定義されている通り、**「ベースバンド電波から画像やテレメトリ数値が人間・システムに見える形で復元できて初めて受信成功」**と厳密に定めています。
そのため、単にスペクトログラム画像を生成しただけでサマリに「準備中」と表示する状態は、電波天文学・地上局システムとして未完の過渡的状態でした。

### 1.2 プレースホルダーにとどまっていた4つの技術的ボトルネック
1. **物理層・占有帯域幅のミスマッチ（生IQ vs 48kHz WAV音声）**:
   - METEOR-M2 LRPT の QPSK 変調波は、シンボルレート $R_s = 72\,\text{ksps}$（または $80\,\text{ksps}$）で送信され、ロールオフ率 $\alpha \approx 0.5$ のルートレイズドコサイン（RRC）フィルタが適用されています。
   - 占有帯域幅 $B$ は以下の通りです：
     $$B \approx (1 + \alpha) R_s \approx 1.5 \times 72\,\text{kHz} \approx 108\,\text{kHz} \sim 120\,\text{kHz}$$
   - FM 復調後の 48kHz 音声 WAV のナイキスト周波数は $f_N = 24\,\text{kHz}$（帯域幅 $24\,\text{kHz}$）に過ぎず、広帯域な QPSK 位相情報が物理的に遮断されていました。SatDump による復調には最低 $240\,\text{kSPS}$ の直交IQベースバンド信号（`cu8` や `cf32`）が不可欠です。
2. **KEDA 0-scale コンテナのフットプリントと起動時間**:
   - ワーカー（`satellite-analyzer-worker`）は KEDA により「待機タスク発生時に 0 から 1 へ数秒で起動する」オンデマンド構成。
   - 当初ベースイメージは `python:3.11-slim`（約 200MB）であり、巨大な C++ 依存（Volk, FFTW, OpenCL, Qt等）を持つ SatDump のコンテナ化が先送りされていました。
3. **ホスト地上局（Rust）と K8s ワーカー（Python）の二重管理**:
   - ホスト環境には `/usr/bin/satdump` が存在し、Rust 製デーモン（`apps/ground-station/src/decoder.rs`）に呼び出しコードが存在していたものの、K8s 側の自律ワークフローに統合されていませんでした。
4. **FUNcube-1 BPSK 復調の巻き添え**:
   - FUNcube-1（145.935MHz / 1200bps BPSK）は 48kHz 音声からでも十分復調可能であるにもかかわらず、METEOR と一緒にプレースホルダーとして扱われていました。

---

## 2. 方式B（コンテナ統合）による解決アーキテクチャ

### 2.1 公式パッケージを活用した軽量マルチデコーダコンテナ
Ubuntu 26.04 (Resolute / Stonking) の公式 universe リポジトリには、世界最高峰の宇宙 SDR デコーダである **`satdump`**（1.2.2）、アマチュア衛星デコーダ **`gr-satellites`**（5.8.0）、および APRS デコーダ **`direwolf`**（1.8.1）がすべて標準パッケージとして提供されています。

これを利用し、重いソースコンパイルを行うことなく、`ubuntu:devel` をベースとした単一の統合コンテナイメージ（`satellite-analyzer-worker:v1.5.0`）を構築しました：

```dockerfile
FROM ubuntu:devel

ENV DEBIAN_FRONTEND=noninteractive

# SatDump (気象衛星QPSKデコーダ), gr-satellites (CubeSat BPSKデコーダ), direwolf (APRS AFSKデコーダ)
RUN apt-get update && apt-get install -y --no-install-recommends \
    satdump \
    gr-satellites \
    direwolf \
    python3 \
    python3-pip \
    python3-numpy \
    python3-scipy \
    python3-matplotlib \
    libsndfile1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*
```

### 2.2 3大衛星変調方式とデコーダの完全統合

| 衛星カテゴリ | 対象衛星の代表例 | 物理変調方式 | 採用デコーダ | 成果物（中身） |
| :--- | :--- | :--- | :--- | :--- |
| **アマチュア宇宙局** | **ISS (ZARYA)** | 145.825MHz FM / 1200bps AFSK (Bell 202) | **`atest` (Dire Wolf)** | AX.25 UI フレーム、コールサイン、メッセージ |
| **CubeSat** | **FUNcube-1 (AO-73)** | 145.935MHz USB / 1200bps BPSK (AO-40) | **`gr-satellites AO-73`** | ソーラーパネル電圧、バッテリ温度等のテレメトリ |
| **極軌道気象衛星** | **METEOR-M2 3 / 4** | 137.900MHz QPSK/OQPSK 72k/80kbps | **`satdump meteor_m2-x_lrpt_80k`** | MSU-MR 可視光・赤外線気象画像（PNG/JPG）、CADU |

---

## 3. デジタル信号処理 (DSP) の数理的メカニズム

### 3.1 BPSK (Binary Phase Shift Keying) と コスタスループの数理
FUNcube-1 の 1200bps BPSK は、搬送波位相 $\theta(t) \in \{0, \pi\}$ でビットを表現します。

$$s(t) = A \cdot m(t) \cdot \cos(2\pi f_c t + \theta_0)$$

1. **記号一覧**:
   - $s(t)$: 受信高周波信号
   - $m(t) \in \{+1, -1\}$: 差分符号化されたベースバンドデータシンボル列（1200 baud）
   - $f_c$: 搬送波周波数（可聴帯域オフセット周波数 $\approx 1500\,\text{Hz}$）
   - $\theta_0$: 搬送波の初期位相・ドップラー位相誤差

2. **工学的読み解き**:
   搬送波成分そのものは抑圧（Suppressed Carrier）されているため、通常の PLL では同期できません。
   `gr-satellites` は直交位相検波器（In-phase: $I(t)$, Quadrature: $Q(t)$）と乗算器による **コスタスループ（Costas Loop）** を用いて位相誤差 $e(t) = I(t) \cdot Q(t) \propto \sin(2\Delta\theta)$ を検出し、搬送波位相同期を確立して $m(t)$ を復調します。

### 3.2 OQPSK (Offset QPSK) と ビタビ軟判定復号
METEOR-M2 LRPT の OQPSK では、直交軸（Q軸）のシンボルタイミングを同相軸（I軸）に対して半シンボル期間（$T_s / 2$）遅延させることで、原点を通る $180^\circ$ の急激な位相反転を防ぎ、非線形増幅器におけるスペクトル再拡散を抑制します。

1. **ビタビ復号器 (Viterbi Decoder)**:
   送信側で畳み込み符号化（拘束長 $K=7$, 符号化率 $R=1/2$）されたシンボルに対し、軟判定メトリック（尤度）を計算しながら最尤経路（トレリス線図）を探索し、ノイズに埋もれたビット列を高精度に復元します。
2. **リード・ソロモン符号化 (Reed-Solomon RS(255, 223))**:
   CCSDS 規格に準拠したバイト単位のブロック誤り訂正を行い、最大 16 バイトのバースト誤りを完全訂正して CADU（Channel Access Data Unit）フレームを出力します。

---

## 4. 自律パイプラインとビューアの拡張

1. **衛星ビューアの画像自動レンダリング**:
   `apps/satellite-viewer/app.py` において、`spectrogram.png` のみならず、SatDump が出力した `msu_mr.png` や気象衛星画像を自動検知し、ダッシュボードカード内に高解像度プレビューおよびモーダル拡大表示を行う機能を実装しました。
2. **生 IQ ファイルの自律スキャン**:
   ビューアのディスパッチャおよび `/api/pending-tasks` を `.wav` のみから `.u8` / `.raw`（生 IQ データ）へ拡張し、METEOR の 240kSPS 直交IQデータが S3 に投入された場合にも KEDA が即座に 0→1 スケールアウトして SatDump を起動するパイプラインを確立しました。
