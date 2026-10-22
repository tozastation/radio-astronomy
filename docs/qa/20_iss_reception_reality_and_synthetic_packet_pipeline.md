# ISS 受信データの実態解剖とリアルタイム RTL-SDR 受信パイプライン設計

本ドキュメントは、国際宇宙ステーション（ISS）の自律追尾・復調において発生していた「常に同一の固定パケットが得られ、ダミーデータに見える問題」の電波工学的・システム的根本原因、および RTL-SDR Blog V4 による実電波受信パイプラインへの刷新仕様を体系的にまとめた技術解説です。

---

## 1. 調査の経緯と結論

### 1.1 結論
エッジ観測コンポーネント（`satellite-tracker-rs`）において、ISS 通過時に SDR ハードウェアからの受信・復調を行わず、コンパイル時にバイナリ埋め込みされた固定音声波形（`iss_aprs_packet_48k.wav`）を合成して Garage S3 にアップロードするモック生成処理が本番環境で稼働していました。

その結果、ダウンストリームの `satellite-analyzer`（`atest -B 1200`）および `satellite-viewer` には、通過のたびに全く同一のパケット（`RS0ISS>CQ,ARISS::Hello from ISS via APRS!`）が出力され続けていました。

---

## 2. ダミーデータ生成のシステム的メカニズム

### 2.1 コールツリーとアーキテクチャの断絶

```text
【ダミーデータ生成の旧パイプライン】
satellite-tracker-rs (Edge Pod)
  ├─► orbit.rs: SGP4 による ISS (ZARYA) 北向き視界判定 (AOS 検知)
  ├─► main.rs: chosen_sat.name.contains("ISS") 判定
  │     └─► sdr.rs: generate_mock_audio() を実行 (実機 SDR は未操作)
  │           ├─► include_bytes!("../assets/iss_aprs_packet_48k.wav") を展開
  │           └─► ガウス白色雑音と合成して 48kHz WAV を生成
  ├─► spooler.rs: /tmp/spool/ISS (ZARYA)_<timestamp>.wav に保存
  └─► s3.rs: Garage S3 (raw/ISS (ZARYA)/...) へアップロード
        │
        ▼
satellite-analyzer (Cloud Worker)
  └─► aprs_decoder.py: atest -B 1200 で WAV をオフライン復調
        └─► 固定文字列 "RS0ISS>CQ,ARISS::Hello from ISS via APRS!" を抽出
```

### 2.2 S3 上の実証データ
Garage S3 に蓄積された `results/ISS (ZARYA)/` 配下の解析サマリおよびパケット JSON を検証した結果、全パスで以下の同一ペイロードが記録されていました：

```json
{
  "satellite": "ISS (ZARYA)",
  "satellite_type": "SpaceStation",
  "signal_type": "APRS / AX.25 (1200bps AFSK)",
  "frequency_label": "145.825 MHz",
  "packets_count": 1,
  "packets": [
    {
      "source": "RS0ISS",
      "destination": "CQ",
      "repeater": "ARISS",
      "message": ":Hello from ISS via APRS!",
      "raw_frame": "[0] RS0ISS>CQ,ARISS::Hello from ISS via APRS!"
    }
  ],
  "summary_text": "APRS パケット 1 件検出"
}
```

この固定サンプルは、イベント駆動型解析パイプライン（Temporal + KEDA）の結合テスト用に作成されたゴールデンサンプルであり、本来はテスト時のみ有効化されるべきものでしたが、トラッカー側の実機受信ロジック未実装により本番動作に流用されていました。

---

## 3. ISS APRS（145.825MHz）実電波受信の物理と信号処理

### 3.1 狭帯域 FM（NFM）と Carson の規則

ISS の APRS ダウンリンクは、周波数 $f_c = 145.825\ \text{MHz}$ において、可聴周波数シフトキーイング（AFSK: Bell 202）で周波数変調（FM）された信号です。

FM 信号の伝送に必要な所要帯域幅 $B_{\text{FM}}$ は、**Carson の規則（Carson's Rule）** によって規定されます：

$$B_{\text{FM}} = 2 \left( \Delta f + f_m \right)$$

#### 記号一覧
| 記号 | 物理的・数学的意味 | 単位 / 代表値 |
| :--- | :--- | :--- |
| $B_{\text{FM}}$ | FM 信号の有効伝送帯域幅（Carson 帯域幅） | $\text{Hz}$ （約 $14.4\ \text{kHz}$） |
| $\Delta f$ | 周波数偏移（Frequency Deviation / ピーク周波数偏位） | $\text{Hz}$ （アマチュア無線 NFM では約 $5.0\ \text{kHz}$） |
| $f_m$ | 変調信号の最高周波数成分（Modulating Frequency） | $\text{Hz}$ （Bell 202 スペース周波数 $2200\ \text{Hz}$） |

#### 日本語での読み解き
FM 変調では搬送波の周波数を音声信号の振幅に応じて揺らします（偏移 $\Delta f$）。変調信号そのものが周波数 $f_m$ を持つため、生成されるスペクトルには無限の側波帯が現れますが、全電力の $98\%$ 以上は「最大周波数偏移 $\Delta f$ と変調最高周波数 $f_m$ の和の 2 倍」の帯域内に収まります。

Bell 202 AFSK においては、$f_m = 2.2\ \text{kHz}$、$\Delta f \approx 5.0\ \text{kHz}$ であるため：
$$B_{\text{FM}} = 2 \times (5.0\ \text{kHz} + 2.2\ \text{kHz}) = 14.4\ \text{kHz}$$
となります。したがって、SDR の中間周波数（IF）フィルタおよびサンプリングレートは $15 \sim 25\ \text{kHz}$ 以上（余裕をみて $48\ \text{kSPS}$）を確保すれば、側波帯の歪みなく完全に復調できます。

#### 展開ステップと直感イメージ
FM 信号の理論式は、変調指数 $\beta = \Delta f / f_m$ を用いてベッセル関数 $J_n(\beta)$ の級数展開で表されます：

$$s(t) = A_c \sum_{n=-\infty}^{\infty} J_n(\beta) \cos\left( 2\pi (f_c + n f_m) t \right)$$

$\beta \approx 5.0 / 2.2 \approx 2.27$ のとき、$|n| > \beta + 1$ となる高次の側波帯振幅 $J_n(\beta)$ は急激にゼロへ減衰します。この「エネルギーが集中する側波帯の限界境界」を近似したものが Carson の規則であり、$|n| \le 3$ までの側波帯（帯域幅約 $14.4\ \text{kHz}$）を捉えれば情報欠落は生じません。

---

## 4. 本物の電波を受信するためのパイプライン設計

### 4.1 `rtl_fm` プロセス制御
Meteor 衛星に対して `rtl_sdr` を外部起動して生 IQ を記録しているアーキテクチャと同様に、ISS に対しては `rtl_fm` を起動して実時間 FM 復調音声（生 PCM）を取得します。

```text
【新・実機受信パイプライン】
RTL-SDR Blog V4 (USB)
  └─► rtl_fm -M fm -f 145825000 -s 48000 -g 45.0 -F 9 - (標準出力に raw 16bit LE PCM)
        │
        ▼ Tokio Async Pipe (ゼロコピー転送)
spooler: 先頭 44 バイトに RIFF/WAVE ヘッダを配置し、PCM を追記
  └─► 通過完了時: ファイル先頭にシークして総データ長をヘッダに書き込み確定
        │
        ▼
Garage S3: raw/ISS (ZARYA)/ISS (ZARYA)_<timestamp>.wav (本物の空中線電波音声)
```

### 4.2 48kHz 16-bit モノラル RIFF/WAVE ヘッダ生成仕様
`rtl_fm` の標準出力はヘッダのない生 PCM バイト列であるため、Rust 側で正確な 44 バイト RIFF/WAVE ヘッダを構築します：

```rust
pub fn create_wav_header(sample_rate: u32, data_size: u32) -> [u8; 44] {
    let mut header = [0u8; 44];
    // "RIFF" チャンク
    header[0..4].copy_from_slice(b"RIFF");
    let file_size = 36u32.saturating_add(data_size);
    header[4..8].copy_from_slice(&file_size.to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");

    // "fmt " サブチャンク (リニア PCM: 1, モノラル: 1, 16bit)
    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes());
    header[20..22].copy_from_slice(&1u16.to_le_bytes());
    header[22..24].copy_from_slice(&1u16.to_le_bytes());
    header[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    let byte_rate = sample_rate * 1 * 2;
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&2u16.to_le_bytes());
    header[34..36].copy_from_slice(&16u16.to_le_bytes());

    // "data" サブチャンク
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_size.to_le_bytes());
    header
}
```

### 4.3 `MOCK_SDR` フラグによる振る舞いの完全分離
- **`MOCK_SDR=false`（本番稼働）**:
  - `rtl_fm` コマンドを起動し、実際のドングルから電波を受信。
  - 受信に失敗した場合はエラーログを記録し、偽のダミーデータは一切生成しない。
- **`MOCK_SDR=true`（CI / ユニットテスト）**:
  - 合成 PCM 音声を生成し、クラスタやテストの自己完結性を担保。

---

## 5. 公式一次情報リンク
- [ARISS (Amateur Radio on the International Space Station) 公式](https://www.ariss.org/)
- [AX.25 Link-Layer Protocol Specification v2.2 (TAPR)](https://www.tapr.org/pdf/AX25_2.2.pdf)
- [Dire Wolf - Automated Packet Reporting System (APRS) 公式リポジトリ](https://github.com/wb2osz/direwolf)
- [rtl-sdr-blog 公式リポジトリ (RTL-SDR Blog V4 ドライバ)](https://github.com/rtlsdrblog/rtl-sdr-blog)
