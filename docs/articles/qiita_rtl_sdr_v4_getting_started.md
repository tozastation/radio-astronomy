---
title: 【ベランダ電波観測所 #1】RTL-SDR Blog V4 と WSL2 で電波を受信してみる 〜公式ドライバビルドの罠からSSHストリーミングまで〜
tags:
  - SDR
  - Linux
  - WSL2
  - Ubuntu
  - 電子工作
private: false
updated_at: '2026-10-07T11:08:00+09:00'
id: null
organization_url_name: null
slide: false
ignorePublish: false
---

# 【ベランダ電波観測所 #1】RTL-SDR Blog V4 と WSL2 で電波を受信してみる 〜公式ドライバビルドの罠からSSHストリーミングまで〜

## はじめに

初めまして、戸澤（@tozastation）といいます。  
純粋に宇宙が大好きで、趣味で色々と勉強したり実験したりしています！

以前も [MultimodalUniverse を使って活動銀河核(AGN)の2クラス分類を作ってみる](https://qiita.com/tozastation/items/09e118bf67e129813d00) という記事を書いたりしていました。  
本プロジェクトは、AIアシスタントの Antigravity に電波工学やDSP（デジタル信号処理）の理論を相談し、教わりながら進めています。大変お世話になっております🙇‍♂️

今回作成しているコードやドキュメントは GitHub の [tozastation/radio-astronomy](https://github.com/tozastation/radio-astronomy) に置いていますので、あわせて見ていただけると嬉しいです。

---

## 今日話すこと

ある日ふと思い立ちました。

> **「ベランダから、天の川銀河の水素ガスやダークマターの証拠、太陽フレアの電波バーストを個人で観測できないだろうか？」**

(めちゃくちゃロマンを感じますよね...!)

電波天文学というと、巨大なパラボラアンテナ施設（野辺山やアルマ望遠鏡など）のイメージがありますが、最近は数千円〜数万円で手に入る **SDR（Software Defined Radio: ソフトウェア無線）** とオープンソースソフトウェアのおかげで、個人でも銀河系の回転曲線を導出したりできる時代になっているそうです（わくわく）。

本シリーズでは、Linuxやプログラミングの力を借りながら、自宅のベランダに自分だけの小さな **「ベランダ電波観測所」** を作っていく過程を記録していこうと思います。

今回はその第1歩（Day 1）として、**最新の「RTL-SDR Blog V4」と超小型PC（GPD Pocket3 / WSL2）を使い、ベランダのアンテナで電波を受信して宅内LAN経由でリアルタイム再生するまでの手順** をまとめてみました。

---

## 全体アーキテクチャ

アンテナケーブルを部屋の奥まで長く引っ張ると高周波信号が減衰してしまうため、**「アンテナ直下の窓際にエッジPCを置き、電波データを宅内LAN（SSH）経由で手元のPCにストリーミングする」** という構成にしてみました。

```text
[ 屋外・ベランダ ]
  📡 マグネットベース ホイップアンテナ (エアコン室外機に吸着)
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

## ハードウェアと物理層のセットアップ

### 使用機材
- **SDRドングル**: [RTL-SDR Blog V4](https://www.rtl-sdr.com/V4/) (広帯域SDR受信機 / HF内蔵アップコンバータ / R828Dチューナー)
- **アンテナ**: RTL-SDR Blog 付属の伸縮式ダイポール/ホイップアンテナ（マグネットベース付き）
- **エッジPC**: GPD Pocket3（Windows 11 + WSL2 Ubuntu）
- **クライアントPC**: 宅内の作業PC（Linux / macOS / WSL2）

---

### 【ノウハウ①】モノポールアンテナには「金属板（仮想グラウンド）」が必須

付属のマグネットベース付きロッドアンテナは、アンテナ素子が1本しか伸びていない **モノポールアンテナ** です。

```text
       │  ← 伸びたアンテナ素子 (1/4波長: 約75cm〜1m)
       │
     [===] ← マグネットベース
  ═══════════ ← ★金属板（ベランダの手すり、スチール缶のフタ、室外機など）★
      : : :
       │  ← 鏡像効果 (Image Antenna) により仮想的にもう1本のアンテナが形成される
```

> **電磁気学のポイント（鏡像理論）**:  
> ダイポールアンテナは「＋」と「−」の2本の極で電波をキャッチしますが、モノポールアンテナは片側の極しかありません。金属板の上に置くことで、金属表面に誘導される電荷によって**「鏡像（Image Antenna）」**が地面の下に仮想的に形成され、初めて一人前のアンテナとして共振・機能します。

そのため、アンテナのマグネットベースは **エアコン室外機の天板、スチール缶のフタ、ベランダの金属製手すり** などの金属面に必ずカチッと吸着させてください（これをやらないとインピーダンスが合わず、感度が激減します）。

今回、私の環境ではベランダの **エアコンの室外機** の上にアンテナをピタッと吸着させて設置しました。室外機の広い金属ボディが広大な仮想グラウンドとして機能してくれるので、ベランダ設置にはうってつけです！

![エアコン室外機にマグネット吸着させたアンテナ](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/192927/e73d097f-db7f-4a2b-9ed6-b1e6f0cf64d7.jpeg)
*▲ ベランダのエアコン室外機（金属天板）にマグネット吸着させたアンテナ*

---

### 【ノウハウ②】同軸ケーブルを窓サッシで潰さない

ベランダから室内に同軸ケーブルを引き込む際、**「金属サッシ枠同士で力任せに挟んでペチャンコに潰す」のは絶対にNG** です。

同軸ケーブルの内部は「中心の芯線」と「周囲の網組シールド」が絶縁体を挟んで同心円状に入っています。サッシで強く押し潰すと絶縁体が破れて **芯線とシールドが内部ショートし、受信強度が完全にゼロ** になってしまいます。

窓を閉める際は、サッシ側面にある **「柔らかいゴムパッキン」のクッション部分** にそっと沿わせて引き込んでください（パッキンの弾力性を利用し、ケーブルの被覆を傷つけないように優しく挟み込みます）。

---

### エッジPCとSDRドングルの接続

超小型UMPC（GPD Pocket3）のUSBポートに RTL-SDR Blog V4 を直結します。アンテナ直下（窓際）に小型PCを配置することで、同軸ケーブルの長さを最小限に抑えて高周波の伝送損失を防ぎます。

![GPD Pocket3 に接続した RTL-SDR Blog V4](https://qiita-image-store.s3.ap-northeast-1.amazonaws.com/0/192927/80f5b4d0-f8ba-486e-b166-67ab7cf1006b.jpeg)
*▲ GPD Pocket3 のUSBポートに接続された RTL-SDR Blog V4*

---

## さっそく、環境構築へ

### 1. WSL2 への USB パススルー (`usbipd-win`)

エッジPC（Windows 11）上で動く WSL2 に、USB接続された RTL-SDR ドングルを直接認識させます。  
Windows 側で管理者権限の PowerShell を開き、Microsoft 公式推奨の `usbipd-win` を導入します。  
*(参考: [GitHub: dorssel/usbipd-win](https://github.com/dorssel/usbipd-win) / [Microsoft Learn: USB デバイスの接続](https://learn.microsoft.com/ja-jp/windows/wsl/connect-usb))*

```powershell
# usbipd-win のインストール
winget install dorssel.usbipd-win
```
*(※インストール後、PowerShell を一度開き直してください)*

```powershell
# 1. デバイス一覧から RTL-SDR（ID: 0bda:2838 等）の BUSID を確認
usbipd list

# 2. バインド（初回のみ必要・例: BUSID が 1-6 の場合）
usbipd bind --busid 1-6

# 3. WSL2 へアタッチ（自動再接続オプション付き）
usbipd attach --wsl --auto-attach --busid 1-6
```

### 2. WSL2 側の Mirrored モード設定（外部SSH用）

宅内LANの手元PCから GPD Pocket3 の WSL2 に直接 SSH できるよう、WSL2 のネットワークを Mirrored モードにします。  
*(参考: [Microsoft Learn: WSL の詳細構成設定 - Mirrored mode networking](https://learn.microsoft.com/ja-jp/windows/wsl/wsl-config#mirrored-mode-networking))*

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

## 【最重要】ここからトラブルシュートが続くので...（公式ドライバビルドの罠）

ここが **今回の最大のハマりどころ** でした。

### 結論から言いますと：`apt install rtl-sdr` を実行すると詰みます

Ubuntu 標準パッケージの `rtl-sdr` は、旧型の **RTL-SDR V3（Rafael Micro R820Tチューナー搭載）** 向けにビルドされています。

新型の **V4** ではチューナーチップが **Rafael Micro R828D** に刷新されており、旧ドライバのまま動かそうとすると周波数のロックに失敗して以下のエラーが無限に出力されてしまいます...

```text
[R82XX] PLL not locked!
[R82XX] PLL not locked!
...
```

そのため、公式パッチが当たった最新の `rtl-sdr-blog` ドライバを自前でビルド・導入する必要があります。  
*(参考: [RTL-SDR Blog V4 Users Guide](https://www.rtl-sdr.com/V4/) / [GitHub: rtlsdrblog/rtl-sdr-blog](https://github.com/rtlsdrblog/rtl-sdr-blog))*

### 公式ドライバのビルド＆インストール手順

WSL2（Ubuntu）のターミナルで以下を実行します（30秒ほどで完了します）：

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

実行して、以下のように **`Found Rafael Micro R828D tuner`** と表示されれば無事成功です！

```text
Found 1 device(s):
  0:  RTLSDRBlog, Blog V4, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R828D tuner
Supported gain values (29): 0.0 0.9 1.4 ... 49.6 dB
Sampling at 2048000 S/s
No E4000 tuner found, aborting.
```
*(※末尾の `No E4000 tuner found` はチップ判別の正常終了ログなので安心してください)*

---

## アンテナ健全性のテスト（`rtl_power` による定量評価）

「ラジオが聞こえないな？」となったとき、アンテナが悪いのか、ケーブルが断線しているのか、ソフトの設定ミスなのか切り分けるのが大変です。  
そこで、**定量データ（S/N比: 信号対雑音比）** でアンテナ回路の健全性をテストしてみます。

広帯域パワースペクトル密度（PSD）ロガーである `rtl_power` を使い、FM放送帯（80MHz〜100MHz）を10秒間スキャンしてみます。

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

## ハローワールド：リアルタイムFMストリーミング再生

いよいよ音を鳴らしてみます！  
エッジ側で `rtl_fm`（FM復調処理）を動かし、その生PCM音声ストリームを SSH の標準出力経由でクライアントPCの `ffplay` に流し込みます。

### 実行コマンド（クライアントPC側で実行）

```bash
# J-WAVE (81.3MHz) をリアルタイム受信＆再生
ssh <WSLユーザー名>@<エッジPCのLAN_IP> "rtl_fm -M wfm -f 81.3M -s 200k -r 48k" | ffplay -nodisp -f s16le -ar 48000 -ch_layout mono -
```

コマンドを叩いた瞬間、ベランダのアンテナが拾ったクリアなラジオ放送が手元PCのスピーカーから流れてきました！（感動）  
（※終了するときは `Ctrl + C` を押します）

### オプション解説
- `-M wfm`: ワイドバンドFM復調モード（放送用FM）
- `-f 81.3M`: 受信周波数（J-WAVE: 81.3 MHz）
- `-s 200k`: SDRのサンプリングレート（FM帯域幅をカバーする 200 kHz）
- `-r 48k`: 音声出力のリサンプリングレート（一般的な 48 kHz PCM）
- `ffplay -f s16le -ar 48000`: 16-bit リトルエンディアン、48kHzの生PCMストリームとして再生

---

## おわりに & 次回予告

今回は SDRv4 を初めて利用するところまで、「ベランダ電波観測所」のハローワールドを紹介しました。

SDRを初めて触るときに一番引っかかりやすい「アンテナのグラウンド設置」「サッシ引き込みの注意点」「V4特有のドライバビルドの罠」などを一通りクリアして、綺麗な音で電波を受信できるようになりました。

とはいえ、これはまだ「ベランダ電波観測所」の壮大な宇宙観測プロジェクトの第一歩に過ぎません。

### 次なる目標：電波天文学と宇宙観測へ
このエッジ受信基盤を足がかりに、今後は以下の観測プロジェクトへと進んでいく予定です：

1. **NOAA気象衛星の自動追尾・地球画像デコード**:  
   上空を高速通過する周極軌道衛星（NOAA-15/18/19）の軌道計算（TLE/SGP4）と、雲画像（137MHz APT信号）の自動受信パイプライン。
2. **太陽電波バースト（Solar Radio Burst）自動検知デーモン**:  
   Rust製のエッジ常時観測デーモンによる、太陽フレアに伴うVHF帯（70MHz）電波急増のリアルタイムFFT＆動的検知（すでに本リポジトリで稼働中！）。
3. **電波天文学の金字塔：天の川銀河の 21cm 中性水素線（HI Line）観測**:  
   専用LNA・BPFとパラボラアンテナを導入し、水素原子のスピン反転輝線（1420.405MHz）のドップラー偏移から銀河回転曲線をプロットして暗黒物質（ダークマター）の証拠を検証する。

コードや設計ドキュメントはすべて以下の GitHub リポジトリでオープンソースとして公開していますので、ぜひ覗いてみてください！

👉 **GitHub リポジトリ**: [tozastation/radio-astronomy](https://github.com/tozastation/radio-astronomy)

次回は、**「Rustで書く太陽電波バースト常時監視デーモンのアーキテクチャ」** または **「NOAA衛星画像の自律デコード」** をお届けする予定です。

最後まで読んでいただきありがとうございました！
