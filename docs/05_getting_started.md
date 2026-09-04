# 🚀 Getting Started: エッジ観測ノード構築＆電波受信ファーストステップ

本ドキュメントは、**GPD Pocket3（WSL2）** と **RTL-SDR Blog V4** を用い、ベランダのアンテナで電波を受信してLAN経由でリアルタイム再生・データ化するまでの**完全ハンズオン手順書**です。

---

## 🗺️ 全体アーキテクチャ

```text
[ 屋外・ベランダ ]
  📡 マグネットホイップアンテナ (金属板グラウンド吸着)
      │ (同軸ケーブル: サッシのゴムパッキン通過)
      ▼
[ GPD Pocket3 (エッジ観測ノード) ]
  📻 RTL-SDR Blog V4 (USB)
      │ (usbipd による USB/IP パススルー)
  🐧 WSL2 (Ubuntu)
      │ ・V4公式ドライバ (librtlsdr / R828D対応)
      │ ・rtl_fm (電波復調 DSP)
      │
      ▼ (宅内LAN: SSHストリーミングパイプライン)
[ ゲーミングPC (分析・クライアント) ]
  🔊 ffplay (リアルタイム音声再生) / Python / JupyterLab
```

---

## Step 1: アンテナの設置（金属板グラウンドと引き込み）

### 1. アンテナを「金属板」に固定する
- **マグネットベース付きホイップアンテナ（モノポールアンテナ）** は、アンテナ自身が「片側の極」しか持ちません。
- もう片方の極（仮想グラウンド）として機能させるため、**必ずスチール缶のフタ（お菓子の缶など）、金属製トレー、エアコン室外機の天板、ベランダの金属製手すり** にマグネットでカチッと吸着させてください。
- これを行わないとインピーダンスが整合せず、受信感度が大幅に低下します。

### 2. 室内への同軸ケーブル引き込み
- ベランダの窓やドアを閉める際、**金属サッシ枠同士で力任せに挟んでペチャンコに潰さない** ように注意してください。
- 芯線とシールドが接触（内部ショート）すると電波強度がゼロになります。サッシ側面の**「柔らかいゴムパッキン」のクッション部分**にそっと通してください。

---

## Step 2: GPD Pocket3（WSL2）への外部SSH接続設定

ゲーミングPCから GPD Pocket3 の WSL2 に LAN 経由で直接 SSH できるようにします。

### 1. WSL2 側で SSH サーバーを起動
GPD Pocket3 の WSL2 ターミナルで実行：

```bash
sudo apt update && sudo apt install -y openssh-server
sudo service ssh start

# パスワード未設定の場合は設定
passwd
```

### 2. Windows 側で Mirrored モードを設定
WSL2 がホスト Windows の LAN IP アドレスを直接共有するようにします（ポートフォワーディング不要）。  
*(公式仕様: [Microsoft Learn: WSL の詳細構成設定 - Mirrored mode networking](https://learn.microsoft.com/ja-jp/windows/wsl/wsl-config#mirrored-mode-networking) / [WSL のネットワーク構成](https://learn.microsoft.com/ja-jp/windows/wsl/networking))*

GPD Pocket3 の Windows 側で `C:\Users\<Windowsユーザー名>\.wslconfig` を作成・編集：

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

設定後、PowerShell で WSL2 を再起動：
```powershell
wsl --shutdown
```

### 3. Windows ファイアウォールを開放
GPD Pocket3 の **PowerShell（管理者として実行）** でポート22の受信を許可：

```powershell
New-NetFirewallRule -Name "WSL-SSH" -DisplayName "WSL-SSH-Inbound" -Direction Inbound -LocalPort 22 -Protocol TCP -Action Allow
```

### 4. クライアント（ゲーミングPC）から公開鍵認証を設定
ストリーミング時にパイプ（`|`）を使用するとパスワード入力ができなくなるため、**公開鍵認証（パスワードなしSSH）** を設定します。

ゲーミングPCのターミナルで実行：

```bash
# 鍵を生成（Enter連打でOK）
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

# GPD Pocket3 へ公開鍵を転送（パスワード入力はこれが最後）
ssh-copy-id <WSLユーザー名>@<GPD Pocket3のLAN_IP>
```

---

## Step 3: RTL-SDR の USB パススルー（`usbipd-win`）

Windows ホストに挿した RTL-SDR ドングルを WSL2 内へパススルーします。  
*(公式リポジトリ: [GitHub: dorssel/usbipd-win](https://github.com/dorssel/usbipd-win) / [Microsoft Learn: USB デバイスの接続](https://learn.microsoft.com/ja-jp/windows/wsl/connect-usb))*

### 1. `usbipd-win` のインストール（Windows 側）
GPD Pocket3 の PowerShell（管理者）で実行：

```powershell
winget install dorssel.usbipd-win
```
*(※インストール後、PowerShell を一度開き直す)*

### 2. デバイスの特定とアタッチ
```powershell
# 1. デバイス一覧から RTL-SDR（ID: 0bda:2838 など）の BUSID を確認
usbipd list

# 2. バインド（初回のみ必要・例: BUSIDが 1-6 の場合）
usbipd bind --busid 1-6

# 3. WSL2 へアタッチ（自動再接続オプション付き）
usbipd attach --wsl --auto-attach --busid 1-6
```

### 3. WSL2 内での認識確認
SSH で GPD Pocket3 の WSL2 に入り確認：

```bash
lsusb
```
👉 `Realtek Semiconductor Corp. RTL2838 DVB-T`（ID `0bda:2838`）が表示されれば成功！

---

## Step 4: RTL-SDR Blog V4 公式ドライバのビルド

> [!WARNING]
> **超重要（既知の罠）**:  
> Ubuntu 標準の `apt install rtl-sdr` で入るドライバは旧型（V3/R820T用）です。  
> 新型 V4 に搭載されたチューナーチップ（Rafael Micro R828D）に対応していないため、指定した周波数にロックできず **`[R82XX] PLL not locked!` が無限に出力される問題** が必ず発生します。  
> 必ず公式パッチ適用済みドライバを自前ビルドして導入してください。  
> *(公式情報: [RTL-SDR Blog V4 Users Guide](https://www.rtl-sdr.com/V4/) / [GitHub: rtlsdrblog/rtl-sdr-blog](https://github.com/rtlsdrblog/rtl-sdr-blog))*

### 一発ビルド＆インストール手順

GPD Pocket3 の WSL2 ターミナルで以下を実行（約30秒で完了）：

```bash
# 1. 古いドライバの完全アンインストール
sudo apt purge -y rtl-sdr librtlsdr0 librtlsdr-dev

# 2. 依存パッケージのインストール
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

# 4. テレビ用標準カーネルモジュールの無効化
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
```

### 動作確認テスト
```bash
rtl_test -t
```
`Found Rafael Micro R828D tuner` および `Sampling at 2048000 S/s` が表示されれば準備完了です！  
*(※末尾の `No E4000 tuner found, aborting.` は正常終了のログです)*

---

## Step 5: 電波スキャンによるアンテナ健全性テスト（`rtl_power`）

アンテナが屋外から電波を正しく吸い上げられているか、FMラジオ帯（80MHz〜100MHz）をスキャンして検証します。

GPD Pocket3 のターミナルで実行：

```bash
# 80MHz〜100MHz を 10秒間スキャン
rtl_power -f 80M:100M:100k -i 5 -e 10s fm_scan.csv
```

### 結果の確認
```bash
head -n 5 fm_scan.csv
```

- **正常な場合のデータ例**:
  - ベースラインノイズフロアが約 `-10 dB` 前後
  - 放送局がある周波数だけ数値が大きく跳ね上がる：
    - **80.0 MHz**: `+12 dB` 前後（TOKYO FM）
    - **81.3 MHz**: `+11 dB` 前後（J-WAVE）
    - **82.5 MHz**: `+10 dB` 前後（NHK-FM）
    - **90.5 MHz**: `+15 dB` 前後（TBSラジオ ワイドFM）
    - **91.6 MHz**: `+15 dB` 前後（文化放送 ワイドFM）
- **判定**: 周囲のノイズに対して **20dB〜25dB 以上（電力比で100倍〜300倍以上）** のピークが立っていれば、アンテナ・ケーブル・チューナーの全回路が完全に健全です！

---

## Step 6: リアルタイム電波ストリーミング再生

エッジ側で復調した音声を、宅内LAN経由でゲーミングPCにパイプし、リアルタイム再生します。

### 実行コマンド（ゲーミングPC側のターミナルで実行）

```bash
# J-WAVE（81.3MHz）をリアルタイム受信＆再生
ssh <WSLユーザー名>@<GPD Pocket3のLAN_IP> "rtl_fm -M wfm -f 81.3M -s 200k -r 48k" | ffplay -nodisp -f s16le -ar 48000 -ch_layout mono -
```

*(※終了するときは `Ctrl + C` を押します)*

### 主要FM放送局の周波数一覧（東京・関東エリア）

| 局名 | 周波数 | コマンド指定例 |
| :--- | :--- | :--- |
| **TOKYO FM** | 80.0 MHz | `-f 80.0M` |
| **J-WAVE** | 81.3 MHz | `-f 81.3M` |
| **NHK-FM (東京)** | 82.5 MHz | `-f 82.5M` |
| **InterFM** | 89.7 MHz | `-f 89.7M` |
| **TBSラジオ (ワイドFM)** | 90.5 MHz | `-f 90.5M` |
| **文化放送 (ワイドFM)** | 91.6 MHz | `-f 91.6M` |
| **ニッポン放送 (ワイドFM)** | 93.0 MHz | `-f 93.0M` |

---

## 🎯 次のステップへ

おめでとうございます！これで「電波を受信し、データ処理し、ネットワーク越しに活用する」ための基盤が完成しました。

ここからは、本プロジェクトの真の目的である**宇宙・天体観測**に進みます：
1. **人工衛星（NOAA気象衛星 / Meteor-M）の軌道計算とリアルタイム地球画像デコード**:
   - 軌道要素（TLE）計算: [Skyfield 公式ドキュメント](https://rhodesmill.org/skyfield/)
   - NOAA APT デコーダ: [noaa-apt 公式サイト・GitHub](https://noaa-apt.mbernardi.com.ar/)
   - 汎用衛星デコーダ: [GitHub: SatDump/SatDump](https://github.com/SatDump/SatDump)
   - 詳細解説: [04_qa.md#3-アプリケーションシステム応用](04_qa.md#3-アプリケーションシステム応用)
2. **流星電波前方散乱観測（53.57MHz 流星エコー検知）**:
   - 福井高専の送信所や国内の電波反射観測網を活用した突入エコーの自動カウント。
   - 詳細解説: [04_qa.md#1-観測対象宇宙の状態](04_qa.md#1-観測対象宇宙の状態)
3. **天の川銀河の21cm中性水素線観測＆銀河回転曲線解析**:
   - 1420MHz水素線スペクトルからドップラー速度を算出し、ダークマターの存在を検証。
   - 詳細解説: [02_radio_astronomy_guide.md](02_radio_astronomy_guide.md)
