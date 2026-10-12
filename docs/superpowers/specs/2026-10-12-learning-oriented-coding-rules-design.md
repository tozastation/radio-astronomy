# 宇宙業界の慣習を学べるコーディングルール（設計案）

- **作成日**: 2026-10-12
- **ステータス**: 提案。ユーザーが採用する項目を選んだあと、`.cursorrules` と `GEMINI.md` へ同時に反映する
- **対象**: `apps/` 配下の Rust（`satellite-tracker-rs`, `ground-station`）と Python（`satellite-analyzer`, `satellite-viewer`, `seestar-plan`）、および AI エージェントの作業手順

この仕様は、モノ作りの速度を落とさずに、宇宙業界で使われる規格・慣習・言語に日常的に触れるための仕掛けを決める。観測パイプラインの構成は変えない。

---

## 1. 目的

1. コードを読むだけで、その処理が依拠する **規格（CCSDS、ITU-R、AX.25 など）** と **物理量の単位・座標系・時刻系** が分かる状態にする。
2. 宇宙機ソフトウェアで実際に求められる **安全側の書き方**（NASA/JPL の Power of 10 など）を、地上局コードで軽量に体験する。
3. エージェントが作ったコードを、ユーザーが **自分で説明・改造できる** ようにする。全部を自分で書くことは求めない。

成功は「PR を読んだあと、その変更が何の規格の何節に基づき、どの単位で何を計算しているかを、ユーザーが自分の言葉で言える」ことである。

---

## 2. 前提: 宇宙業界で実際に使われている言語と規格

学習の地図として、業界の主な言語と、本リポジトリとの対応を置く。

| 領域 | 主な言語 | 代表的な実装・規格（一次情報） | 本リポジトリでの対応 |
| :--- | :--- | :--- | :--- |
| 宇宙機の搭載ソフトウェア | C, C++（一部 Rust が研究段階） | NASA [core Flight System (cFS)](https://github.com/nasa/cFS)、JPL [F Prime](https://github.com/nasa/fprime) | `satellite-tracker-rs`（Rust）。リアルタイム処理の書き方を搭載ソフトに寄せる |
| 地上局・運用系 | Python, Java, C++ | [Yamcs](https://yamcs.org/)（Java）、[OpenC3 COSMOS](https://github.com/OpenC3/cosmos)（Ruby/Python） | `satellite-analyzer`, `satellite-viewer`（Python） |
| 通信プロトコル | 規格文書 | [CCSDS Blue Books](https://public.ccsds.org/Publications/BlueBooks.aspx)（131.0-B 同期・符号化、132.0-B TM データリンク、133.0-B Space Packet、301.0-B 時刻コード、660.0-B XTCE） | Meteor-M LRPT の復号、テレメトリ JSON |
| 軌道力学 | C++, Python, Fortran（歴史的） | SGP4: [Vallado ほか "Revisiting Spacetrack Report #3"](https://celestrak.org/publications/AIAA/2006-6753/) | `orbit.rs`（`sgp4` クレート） |
| ソフトウェア品質 | 規格文書 | NASA [NPR 7150.2 / SWEHB](https://swehb.nasa.gov/)、ESA [ECSS-E-ST-40C / ECSS-Q-ST-80C](https://ecss.nl/)、[The Power of 10 (Holzmann, 2006)](https://spinroot.com/gerard/pdf/P10.pdf) | 本仕様の第 4 章 |

新しい言語を増やすことは本仕様の範囲外とする。C/C++ には「言語対比」コメント（第 5 章）で触れる。

---

## 3. ルール A: 物理量は「単位・座標系・時刻系」をコードに書く

### 3.1 背景

1999 年の Mars Climate Orbiter は、地上ソフトが推力のインパルスをポンド秒（lbf·s）で出し、軌道決定側がニュートン秒（N·s）として読んだため失われた（[NASA Lessons Learned 0641](https://llis.nasa.gov/lesson/641)）。単位をコードで明示するのは、宇宙業界で最も基本的な慣習である。

### 3.2 ルール

1. **単位の接尾辞**: 物理量の変数・フィールド・関数名には単位を付ける。既存コードの `elevation_deg`, `range_km`, `doppler_shift_hz` の書き方を全体の規則にする。
   - 例: `_hz`, `_db`, `_dbm`, `_deg`, `_rad`, `_m`, `_km`, `_m_s`, `_km_s`, `_s`, `_sps`（サンプル毎秒）
   - 無次元量は `_ratio` や `_count` とし、何の比かをコメントに書く。
2. **座標系の接尾辞**: 位置・速度のベクトルには座標系を付ける。
   - `_teme`（SGP4 の出力。True Equator Mean Equinox）、`_ecef`（地球固定）、`_enu`（観測地の東・北・天頂）、`_azel`（方位角・仰角）
   - 座標変換の関数名は `teme_to_ecef` のように「元_to_先」とする。
3. **時刻系**: 時刻は UTC の `DateTime<Utc>`（Rust）/ timezone 付き `datetime`（Python）だけを使う。ファイル名・JSON・ログは ISO 8601（`2026-10-12T09:32:00Z`）。GPS 時刻や TAI を扱うときは変数名に `_gps` / `_tai` を付け、閏秒の扱いを [CCSDS 301.0-B](https://public.ccsds.org/Publications/BlueBooks.aspx) の該当節とともにコメントに書く。
4. **生値と工学値の区別**: テレメトリは、受信した生の値（raw count）と、校正式で換算した工学値（engineering value）を別のフィールドにする。衛星運用で一般的な分け方で、XTCE（CCSDS 660.0-B）も同じ区別をしている。

次の値と校正式は書式を示すための架空の例である。`mnemonic` は運用で使う大文字の略号で、`サブシステム_項目_単位` の順に並べる慣習に合わせた。

```json
{
  "mnemonic": "EPS_BATT_V",
  "raw": 2817,
  "eng": 7.42,
  "unit": "V",
  "calibration": "eng = 0.002634 * raw",
  "source": "衛星運用者が公開しているテレメトリ定義（実際の衛星では URL を書く）"
}
```

### 3.3 段階導入

Rust の newtype（例: `struct Hertz(f64)`）や単位ライブラリ（Rust の [uom](https://github.com/iliekturtles/uom)、Python の [Pint](https://pint.readthedocs.io/)）は、型で単位の取り違えを防げる。導入は重いので、第 1 段階は命名規則だけにし、ドップラー補正など単位の混同が実害になる箇所から newtype を試す。

---

## 4. ルール B: 宇宙機ソフト流の安全側の書き方（地上局向けに軽量化）

[The Power of 10](https://spinroot.com/gerard/pdf/P10.pdf) は JPL で使われた C 言語向けの 10 規則である。Rust と Python に合う形へ置き換え、適用範囲を「受信中に止まると観測を失うコード」（`satellite-tracker-rs` の録音ループと DSP）に限る。

| Power of 10 の規則（要旨） | 本リポジトリでの置き換え | 確認方法 |
| :--- | :--- | :--- |
| 1. 単純な制御フロー（goto・再帰を使わない） | 録音ループと DSP では再帰を使わない | レビュー |
| 2. ループには上限を置く | 外部入力（IQ サンプル、S3 応答）を待つループにタイムアウトか最大回数を置く | レビュー、テスト |
| 3. 初期化後に動的確保しない | 録音ループ内でバッファを毎回確保しない。開始前に `Vec::with_capacity` で確保する | レビュー |
| 5. 1 関数あたり 2 個以上のアサーション | 物理的にありえない値（仰角が $-90^\circ$ 未満、サンプルレートが 0）を `debug_assert!` で検出する | テスト |
| 7. 戻り値を必ず確認する | 録音系では `unwrap()` / `expect()` を使わず `Result` を返す。Clippy の [`unwrap_used`](https://rust-lang.github.io/rust-clippy/master/index.html#unwrap_used) を `satellite-tracker-rs` で warn にする | `cargo clippy` |
| 10. 警告ゼロでコンパイルする | `cargo clippy -- -D warnings` と Python の `ruff` を CI 相当のローカル確認に入れる | コマンド |

規則 4（関数は 1 画面以内）、6（変数のスコープ最小化）、8（プリプロセッサ制限）、9（ポインタ制限）は Rust の言語仕様でおおむね満たされるため、ルールにしない。各規則を採用した箇所には、コメントで `Power of 10 規則 N` と書き、どの規則を体験しているかを分かるようにする。

---

## 5. ルール C: 学習用コメントの型を決める

既存コードには `【言語対比】` や `【数学・信号処理】` の見出し付きコメントがある。これを次の 4 種類に揃え、書く場所と量を決める。

| 見出し | 書く内容 | 書く場所 |
| :--- | :--- | :--- |
| `【規格】` | 規格名・版・節番号と、その節が何を決めているか。例: `CCSDS 131.0-B（版と節は原文で確認して書く）: 同期マーカー ASM = 0x1ACFFC1D` | 規格の値をそのまま使う定数・関数の直前 |
| `【物理・数学】` | 式、記号の単位、式の意味（`.cursorrules` の 3 要素の短縮版）。長い説明は `docs/qa/` へリンクする | DSP・軌道計算の関数の直前 |
| `【宇宙の慣習】` | 業界でなぜそう書くか。例: 生値と工学値を分ける理由、UTC に統一する理由 | 初めてその慣習が出る箇所 |
| `【言語対比】` | Rust / Python の書き方を、C（cFS）や C++（F Prime）ではどう書くかと対比する | モジュールで初めて出る言語機能の箇所 |

量の上限: 同じ説明は 1 モジュール 1 回だけ書き、2 回目以降は最初の箇所か `docs/00_glossary.md` を参照させる。コードの行数より学習コメントが長くなる場合は、`docs/qa/` に切り出す。

数値の出どころも見出しと同じ形で書く。定数の横に `[規格]`、`[実測 2026-10-11]`、`[仮定]` のいずれかを付け、仮定値は実測後に差し替える対象だと分かるようにする。

### 5.1 例（ドップラー偏移）

```rust
// 【物理・数学】ドップラー偏移
//   Δf_hz = -f0_hz * v_r_m_s / c_m_s
//   f0_hz: 送信周波数 [Hz]、v_r_m_s: 視線速度 [m/s]（遠ざかる向きを正）、c_m_s: 光速 [m/s]
//   衛星が近づく（v_r < 0）と受信周波数は上がる。詳しくは docs/qa/01_... の FM 復調の節。
// 【宇宙の慣習】視線速度の符号は「遠ざかる向きを正」に統一する。距離の時間微分（range rate）と同じ符号になる。
pub fn doppler_shift_hz(f0_hz: f64, radial_velocity_m_s: f64) -> f64 {
    debug_assert!(f0_hz > 0.0); // Power of 10 規則 5
    debug_assert!(radial_velocity_m_s.abs() < 12_000.0); // 地球周回の上限を超える値は計算誤り
    -f0_hz * radial_velocity_m_s / SPEED_OF_LIGHT_M_S
}
```

---

## 6. ルール D: 要求とテストを番号で結ぶ（トレーサビリティ）

宇宙機開発では、要求（requirement）ごとに検証方法を決め、テスト結果まで番号で追えるようにする（[NASA Systems Engineering Handbook](https://www.nasa.gov/reference/systems-engineering-handbook/) の検証の章。検証方法は Test / Analysis / Demonstration / Inspection の 4 種）。

1. `docs/superpowers/specs/` の仕様に、要求を `REQ-<領域>-<番号>` で書く。例: `REQ-TRK-003: 仰角 10° 未満では SDR を開かない`。
2. 各要求に検証方法（T / A / D / I）を 1 つ書く。
3. テスト関数のドキュメントコメントに、検証する要求番号を書く。例: `/// 検証: REQ-TRK-003 (T)`。
4. 要求番号はすべての仕様に後から付けることはしない。新しく書く仕様から始める。

---

## 7. ルール E: エージェントの作業手順に学習の仕掛けを入れる

コードを書くのは基本的にエージェントとし、ユーザーが学ぶための出口を PR と作業の区切りに置く。

1. **PR 本文に「学習ポイント」節を置く**: その変更で触れた規格・慣習・言語機能を最大 3 つ挙げ、一次情報へリンクする。
2. **確認問題を 1 問付ける**: 変更内容から、答えがコードかドキュメントの中にある問題を 1 問出す。例: 「この関数で視線速度の符号を逆にすると、ドップラー補正後の周波数誤差は何 Hz になるか（437 MHz、7 km/s の場合）」。答えは PR 本文の折りたたみ（`<details>`）に書く。
3. **練習モード（任意）**: ユーザーが依頼に「練習モード」と書いた場合だけ、エージェントは失敗するテストと関数の型（シグネチャ）を用意し、本体は `todo!()`（Rust）/ `raise NotImplementedError`（Python）で残す。ユーザーが実装し、エージェントがレビューする。それ以外の依頼では、エージェントが最後まで実装する。
4. **用語集の更新**: 新しく出た用語は `docs/00_glossary.md` に追記する。
5. **質疑の記録**: 既存の方針どおり `docs/04_qa.md` に追記する。

---

## 8. 導入の順序

1 段階ずつ承認を得て進める。

| 段階 | 内容 | 変更するもの |
| :--- | :--- | :--- |
| 1 | 採用するルールを選び、`.cursorrules` と `GEMINI.md` に反映する | 方針ファイル 2 つ |
| 2 | ルール A・C を、新しく書くコードと触ったファイルから適用する | 該当コード |
| 3 | ルール B の Clippy 設定を `satellite-tracker-rs` に入れ、既存の `unwrap()` を整理する | `Cargo.toml` または `lib.rs` の lint 設定、録音ループ |
| 4 | ルール D を次の新しい仕様から始める | 仕様とテスト |

ルール E は段階 1 の直後から始められる。

---

## 9. ユーザーに決めてもらうこと

1. ルール A〜E のうち、採用するもの（全部 / 一部）。
2. 学習コメントの量: 第 5 章の上限（1 モジュール 1 回）でよいか、もっと多く・少なくするか。
3. 練習モードを用意するか。用意する場合、既定を「オフ（依頼時だけ）」にするか「毎回 1 関数だけ残す」にするか。
4. C / C++ に触れる範囲: 対比コメントだけにするか、cFS や F Prime のコードを読む課題を `docs/` に置くか。
5. 確認問題の難易度: 計算問題（数値を出す）中心か、規格を引く問題（どの節に書いてあるか）中心か。
