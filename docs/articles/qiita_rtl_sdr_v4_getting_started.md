---
title: 【SREの電波天文学 #1】RTL-SDR Blog V4 と WSL2 で作るベランダ電波受信エッジ環境 〜公式ドライバビルドの罠からSSHストリーミングまで〜
tags:
  - SDR
  - Linux
  - WSL2
  - Ubuntu
  - 電子工作
private: false
updated_at: '2026-10-07T10:50:00+09:00'
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

# 【SREの電波天文学 #1】RTL-SDR Blog V4 と WSL2 で作るベランダ電波受信エッジ環境 〜公式ドライバビルドの罠からSSHストリーミングまで〜

## 🌌 はじめに：なぜいま「電波」なのか？

普段はクラウドインフラやSRE（Site Reliability Engineering）として分散システムを運用していますが、ある日ふと思い立ちました。

> **「ベランダから、天の川銀河の水素ガスやダークマターの証拠、太陽フレアの電波バーストを個人で観測できないだろうか？」**

電波天文学は、可視光の光学望遠鏡では宇宙塵（ダスト）に遮られて見えない宇宙の姿を電波で捉える学問です。かつては巨大な電波望遠鏡施設だけの特権でしたが、現在では数千円〜数万円で入手できる **SDR（Software Defined Radio: ソフトウェア無線）** とオープンソースソフトウェアの進化により、個人の手で銀河系回転曲線を導出できる時代になっています。

本シリーズでは、ソフトウェアエンジニアのスキル（Linux、インフラ自動化、時系列データパイプライン）をフル活用し、自宅で自律型電波観測ステーションを構築していく過程を記録します。

その第1歩（Day 1: ハローワールド）として、本記事では最新の **RTL-SDR Blog V4** と超小型PC（GPD Pocket3 / WSL2）を使い、**ベランダのアンテナで電波を吸い上げ、宅内LAN経由でリアルタイムストリーミング再生するまでの完全手順** をまとめました。

---

## 🏗️ 全体アーキテクチャ

アンテナケーブルの長距離引き込みによる信号減衰（高周波の同軸損失）を防ぐため、**「アンテナ直下の窓際にエッジPCを置き、電波復調データを宅内LAN（SSH）経由でクライアントPCに飛ばす」** というエッジ分散構成を採用しました。

```text
[ 屋外・ベランダ ]
  📡 マグネットベース ホイップアンテナ (金属板グラウンド吸着)
      │ (同軸ケーブル: サッシのゴムパッキン通過)
      ▼
[ GPD Pocket3 (エッジ観測ノード: Windows 11) ]
  📻 RTL-SDR Blog V4 (USB)
      │ (usbipd-win による USB/IP パススルー)
  🐧 WSL2 (Ubuntu)
      │ ・V4公式パッチ適用ドライバ (librtlsdr / R828D対応)
      │ ・rtl_fm (ベースバンド復調処理)
      │
      ▼ (宅内LAN: SSH ストリーミングパイプライン)
[ メインPC / ゲーミングPC (クライアント) ]
  🔊 ffplay (リアルタイム音声再生) / Python / JupyterLab
```

---

## 🛠️ ハードウェアと物理層のセットアップ

### 1. 使用機材
- **SDRドングル**: [RTL-SDR Blog V4](https://www.rtl-sdr.com/V4/) (広帯域SDR受信機 / HF内蔵アップコンバータ / R828Dチューナー)
- **アンテナ**: RTL-SDR Blog 付属の伸縮式ダイポール/ホイップアンテナ（マグネットベース付き）
- **エッジPC**: GPD Pocket3（Windows 11 + WSL2 Ubuntu）
- **クライアントPC**: 宅内の任意のPC（Linux / macOS / WSL2）

---

### 2. 【ノウハウ①】モノポールアンテナには「金属板（仮想グラウンド）」が必須

付属のマグネットベース付きロッドアンテナは、アンテナ素子が1本しか伸びていない**モノポールアンテナ**です。

```text
       │  ← 伸びたアンテナ素子 (1/4波長: 約75cm〜1m)
       │
     [===] ← マグネットベース
  ═══════════ ← ★金属板（スチール缶のフタ、エアコン室外機、金属トレー）★
      : : :
       │  ← 鏡像効果 (Image Antenna) により仮想的にもう1本のアンテナが形成される
```

> **電磁気学的なポイント（鏡像理論）**:  
> ダイポールアンテナは「＋」と「−」の2本の極で電波をキャッチしますが、モノポールアンテナは「片側の極」しかありません。金属板の上に置くことで、金属表面に誘導される電荷によって**「鏡像（Image Antenna）」**が地面の下に仮想的に形成され、初めて一人前のダイポールアンテナとして共振・機能します。

そのため、アンテナのマグネットベースは **スチール缶のフタ（お菓子の缶など）、金属製トレー、エアコン室外機の天板、またはベランダの金属製手すり** に必ずカチッと吸着させてください。これを怠るとインピーダンスが整合せず、受信感度が激減します。

<!-- 📸 写真①: アンテナのマグネットベースが金属板（お菓子の缶のフタや室外機）に吸着している様子 -->
> *(※写真挿入位置: アンテナの金属板グラウンド吸着風景)*

---

### 3. 【ノウハウ②】同軸ケーブルを窓サッシで潰さない

ベランダから室内に同軸ケーブルを引き込む際、**「金属サッシ枠同士で力任せに挟んでペチャンコに潰す」のは絶対にNG** です。

同軸ケーブルの内部は「中心の芯線」と「周囲の網組シールド」が絶縁体を挟んで同心円状に配置されています。サッシで強く押し潰すと絶縁体が破断し、**芯線とシールドが内部ショートして受信強度が完全にゼロ** になります。

窓を閉める際は、サッシ側面にある **「柔らかいゴムパッキン」のクッション部分** にそっと沿わせて引き込んでください。

<!-- 📸 写真②: 窓サッシ側面のゴムパッキン部分をケーブルが通過している様子 -->
> *(※写真挿入位置: サッシのゴムパッキン通過部分)*

<!-- 📸 写真③: GPD Pocket3 のUSBポートに RTL-SDR Blog V4 が接続されている様子 -->
> *(※写真挿入位置: エッジPCとRTL-SDR Blog V4接続風景)*

---

## 🐧 WSL2 × USB パススルー環境の構築

エッジPC（Windows 11）上で動く WSL2 に、USB接続された RTL-SDR ドングルを直接認識させます。

### 1. `usbipd-win` のインストール
Windows 側で管理者権限の PowerShell を開き、Microsoft 公式推奨の `usbipd-win` を導入します。  
*(一次情報: [GitHub: dorssel/usbipd-win](https://github.com/dorssel/usbipd-win) / [Microsoft Learn: USB デバイスの接続](https://learn.microsoft.com/ja-jp/windows/wsl/connect-usb))*

```powershell
winget install dorssel.usbipd-win
```
*(※インストール後、PowerShell を一度開き直してください)*

### 2. デバイスの特定と WSL2 へのアタッチ
```powershell
# 1. デバイス一覧から RTL-SDR（ID: 0bda:2838 等）の BUSID を確認
usbipd list

# 2. バインド（初回のみ必要・例: BUSID が 1-6 の場合）
usbipd bind --busid 1-6

# 3. WSL2 へアタッチ（自動再接続オプション付き）
usbipd attach --wsl --auto-attach --busid 1-6
```

### 3. WSL2 側の Mirrored モード設定（外部SSH用）
宅内LANの他PCから GPD Pocket3 の WSL2 に直接 SSH できるよう、WSL2 のネットワークを Mirrored モードにします。  
*(一次情報: [Microsoft Learn: WSL の詳細構成設定 - Mirrored mode networking](https://learn.microsoft.com/ja-jp/windows/wsl/wsl-config#mirrored-mode-networking))*

Windows の `C:\Users\<ユーザー名>\.wslconfig` に以下を記述します：

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

PowerShell で WSL2 を再起動し、ポート22の受信を許可します：
```powershell
wsl --shutdown
New-NetFirewallRule -Name "WSL-SSH" -DisplayName "WSL-SSH-Inbound" -Direction Inbound -LocalPort 22 -Protocol TCP -Action Allow
```

WSL2 内で `openssh-server` を立ち上げ、クライアントPCから `ssh-copy-id` で公開鍵を登録しておけば、パスワード入力不要でパイプストリーミングが可能になります。

---

## ⚠️【最重要】RTL-SDR Blog V4 公式ドライバの自前ビルド

ここが **本記事最大のハマりどころ（初見殺しの罠）** です。

### 罠：`apt install rtl-sdr` を実行してはいけない！
Ubuntu 標準パッケージの `rtl-sdr` は、旧型の **RTL-SDR V3（Rafael Micro R820Tチューナー搭載）** 向けにビルドされています。

新型の **V4** ではチューナーチップが **Rafael Micro R828D** に刷新され、HF帯アップコンバータやトリプル入力フィルタが追加されています。旧ドライバのまま動かそうとすると、周波数シンセサイザが正しく制御できず、以下のエラーが無限に出力されて完全に沈黙します：

```text
[R82XX] PLL not locked!
[R82XX] PLL not locked!
...
```

公式パッチが当たった最新の `rtl-sdr-blog` ドライバを自前でビルド・導入する必要があります。  
*(一次情報: [RTL-SDR Blog V4 Users Guide](https://www.rtl-sdr.com/V4/) / [GitHub: rtlsdrblog/rtl-sdr-blog](https://github.com/rtlsdrblog/rtl-sdr-blog))*

### 公式ドライバのビルド＆インストール手順

WSL2（Ubuntu）のターミナルで以下を流し込みます（30秒ほどで完了します）：

```bash
# 1. 旧ドライバの完全アンインストール
sudo apt purge -y rtl-sdr librtlsdr0 librtlsdr-dev

# 2. ビルドに必要な依存ツールの導入
sudo apt update && sudo apt install -y git cmake build-essential libusb-1.0-0-dev pkg-config

# 3. 公式リポジトリのクローンとビルド
cd /tmp && rm -rf rtl-sdr-blog
git clone https://github.com/rtlsdrblog/rtl-sdr-blog.git
cd rtl-sdr-blog && mkdir build && cd build
cmake ../ -DINSTALL_UDEV_RULES=ON
make -j$(nproc)
sudo make install
sudo cp ../rtl-sdr.rules /etc/udev/rules.d/
sudo ldconfig

# 4. OS標準のDVB-Tテレビ視聴用カーネルモジュールをブラックリスト化
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
```

### 動作確認
```bash
rtl_test -t
```

実行して、以下のように **`Found Rafael Micro R828D tuner`** と認識されれば成功です！

```text
Found 1 device(s):
  0:  RTLSDRBlog, Blog V4, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R828D tuner
Supported gain values (29): 0.0 0.9 1.4 ... 49.6 dB
Sampling at 2048000 S/s
No E4000 tuner found, aborting.
```
*(※末尾の `No E4000 tuner found` はチップ判別の正常終了ログです)*

---

## 📊 アンテナ健全性のテスト（`rtl_power` による定量評価）

「ラジオが聞こえない」とき、アンテナが悪いのか、ケーブルが断線しているのか、ソフトの設定ミスなのか切り分けるのは困難です。そこでエンジニアらしく **定量データ（S/N比: 信号対雑音比）** でアンテナ回路の健全性をテストします。

広帯域パワースペクトル密度（PSD）ロガーである `rtl_power` を使い、FM放送帯（80MHz〜100MHz）を10秒間スキャンします。

```bash
rtl_power -f 80M:100M:100k -i 5 -e 10s fm_scan.csv
```

出力された CSV データを確認します：
```bash
head -n 5 fm_scan.csv
```

### 健全性の判定基準
- **ベースライン（何もない周波数のノイズフロア）**: 約 `-10 dB` 前後
- **放送局が存在する周波数のピーク**:
  - `80.0 MHz` (TOKYO FM): `+12 dB` 前後
  - `81.3 MHz` (J-WAVE): `+11 dB` 前後
  - `82.5 MHz` (NHK-FM): `+10 dB` 前後
  - `90.5 MHz` (TBSラジオ ワイドFM): `+15 dB` 前後
  - `91.6 MHz` (文化放送 ワイドFM): `+15 dB` 前後

周囲のノイズに対して **約20dB〜25dB以上（電力比で100倍〜300倍以上）の鋭いピーク** が立っていれば、アンテナ・グラウンド・同軸ケーブル・SDRチューナーの全回路が完全に健全に動作している証拠です！

---

## 📻 ハローワールド：SSHパイプライン経由のリアルタイムFMストリーミング

いよいよ音を鳴らしてみます。  
エッジ側で `rtl_fm`（FM復調処理）を動かし、その生PCM音声ストリームを SSH の標準出力経由でクライアントPCの `ffplay` に流し込みます。

### 実行コマンド（クライアントPC側で実行）

```bash
# J-WAVE (81.3MHz) をリアルタイム受信＆再生
ssh <WSLユーザー名>@<エッジPCのLAN_IP> "rtl_fm -M wfm -f 81.3M -s 200k -r 48k" | ffplay -nodisp -f s16le -ar 48000 -ch_layout mono -
```

コマンドを叩いた瞬間、ベランダのアンテナが拾ったクリアなラジオ放送がメインPCのスピーカーから流れてきます！  
（終了するときは `Ctrl + C` を押します）

### オプション解説
- `-M wfm`: ワイドバンドFM復調モード（放送用FM）
- `-f 81.3M`: 受信周波数（J-WAVE: 81.3 MHz）
- `-s 200k`: SDRのサンプリングレート（FM帯域幅をカバーする 200 kHz）
- `-r 48k`: 音声出力のリサンプリングレート（一般的な 48 kHz PCM）
- `ffplay -f s16le -ar 48000`: 16-bit リトルエンディアン、48kHzの生PCMストリームとして再生

---

## 🚀 おわりに & 次回予告

本記事では、SDRを初めて手にしたエンジニアが最も引っかかりやすい「ハードウェア・ドライバ・アンテナ物理層」のセットアップを、エッジ分散アーキテクチャとして整理しました。

しかし、これはまだ壮大な電波観測プロジェクトの **「ハローワールド」** に過ぎません。

### 次なる目標：電波天文学と宇宙観測へ
このエッジ受信基盤を足がかりに、今後は以下の観測プロジェクトへと拡張していきます：

1. **NOAA気象衛星の自動追尾・地球画像デコード**:  
   上空を高速通過する周極軌道衛星（NOAA-15/18/19）の軌道計算（TLE/SGP4）と、雲画像（137MHz APT信号）の自動受信パイプライン。
2. **太陽電波バースト（Solar Radio Burst）自動検知デーモン**:  
   Rust製のエッジ常時観測デーモンによる、太陽フレアに伴うVHF帯（70MHz）電波急増のリアルタイムFFT＆動的検知（すでに本リポジトリで稼働中！）。
3. **電波天文学の金字塔：天の川銀河の 21cm 中性水素線（HI Line）観測**:  
   専用LNA・BPFとパラボラアンテナを導入し、水素原子のスピン反転輝線（1420.405MHz）のドップラー偏移から銀河回転曲線をプロットして暗黒物質（ダークマター）の証拠を検証する。

コードや設計ドキュメントはすべて以下の GitHub リポジトリでオープンソースとして開発・公開しています。興味のある方はぜひ覗いてみてください！

👉 **GitHub リポジトリ**: [tozastation/radio-astronomy](https://github.com/tozastation/radio-astronomy)

次回は、**「Rustで書く太陽電波バースト常時監視デーモンのアーキテクチャ」** または **「NOAA衛星画像の自律デコード」** をお届けする予定です。お楽しみに！
