use anyhow::{Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::Path;

// =============================================================================
// 設定データ構造体 (Config)
// -----------------------------------------------------------------------------
// 【言語対比】
// - #[derive(...)]: TypeScriptのデコレータや、Goでいう構造体タグ `json:"..."` に対応する
//   コード自動生成マクロです。
//   - Debug: `println!("{:?}", config)` で中身をデバッグダンプできるようにする
//   - Clone: 構造体のディープコピーを許可する
//   - Deserialize: TOMLやJSON文字列からこの構造体へ自動デコードできるようにする (serde)
// - pub struct: Goでいう `type Config struct`（先頭大文字のエクスポート）に相当します。
// =============================================================================

/// NOAA地上局の全体設定
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub observer: ObserverConfig,
    pub scheduler: SchedulerConfig,
    pub voicevox: VoicevoxConfig,
    pub storage: StorageConfig,
}

/// 観測地点（地上アンテナ設置場所）の座標
/// WGS84測地系（GPSと同じ）の緯度・経度・標高を指定します。
#[derive(Debug, Clone, Deserialize)]
pub struct ObserverConfig {
    pub latitude: f64,    // 緯度 (度単位: 北緯がプラス)
    pub longitude: f64,   // 経度 (度単位: 東経がプラス)
    pub altitude_m: f64,  // 海抜標高 (メートル)
}

/// スケジューリングとパス検出の閾値設定
#[derive(Debug, Clone, Deserialize)]
pub struct SchedulerConfig {
    pub min_elevation_deg: f64,         // 受信対象とする最小ピーク仰角 (度)
    pub pre_alert_minutes: f64,         // 通過何分前にずんだもんが事前通知するか (分)
    pub tle_update_interval_hours: u64, // 軌道要素(TLE)を更新する頻度 (時間)
}

fn default_timeout_secs() -> u64 {
    15
}

/// VOICEVOXずんだもん音声合成エンジンの設定
#[derive(Debug, Clone, Deserialize)]
pub struct VoicevoxConfig {
    pub enabled: bool,      // 音声通知を有効にするか (falseの場合はログ出力のみ)
    pub host: String,       // VOICEVOX Engineのエンドポイント (例: "http://localhost:50021")
    pub speaker_id: u32,    // スピーカーID (3 = ずんだもん ノーマル)
    #[serde(default = "default_timeout_secs")]
    pub timeout_secs: u64,  // HTTPリクエストタイムアウト秒数 (CPU推論を考慮しデフォルト15秒)
}

/// 観測データ・生成画像の保存先設定
#[derive(Debug, Clone, Deserialize)]
pub struct StorageConfig {
    pub output_dir: String, // 保存先ディレクトリ (例: "data/noaa")
}

// =============================================================================
// メソッド実装ブロック (impl Config)
// -----------------------------------------------------------------------------
// 【言語対比】
// - impl Config: Goでいう `func (c *Config) Method()` のように構造体にメソッドを生やす構文。
// - Result<Self>: Goでいう `(Config, error)` の多値返却に相当します。
//   成功時は `Ok(config)`、失敗時は `Err(err)` をラップして返します。
// - `?` 演算子: Goでいう `if err != nil { return nil, err }` のボイラープレートを
//   1文字で代行する早期リターン構文です。
// =============================================================================
impl Config {
    /// TOML文字列から設定構造体をパース
    pub fn from_str(s: &str) -> Result<Self> {
        // toml::from_str でデシリアライズし、エラー時は context で文脈メッセージを付加
        toml::from_str(s).context("TOML設定のパースに失敗しました")
    }

    /// ファイルパスから設定を読み込む
    /// 【言語対比】P: AsRef<Path> は Go の io.Reader や TS の string | Path のような
    /// 抽象引数。文字列スライス `&str` でも `PathBuf` でも受け取れるようにするイディオムです。
    pub fn load_from_file<P: AsRef<Path>>(path: P) -> Result<Self> {
        let content = fs::read_to_string(path.as_ref())
            .with_context(|| format!("設定ファイルの読み込みに失敗しました: {:?}", path.as_ref()))?;
        Self::from_str(&content)
    }
}
