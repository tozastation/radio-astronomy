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
    #[serde(default)]
    pub sdr: SdrConfig,
    #[serde(default)]
    pub discord: DiscordConfig,
    #[serde(default)]
    pub satellites: SatellitesConfig,
}

fn default_gain() -> f64 {
    45.0 // 宇宙からの微弱信号用に 45.0 dB の高利得をデフォルト化
}

fn default_sample_rate() -> u32 {
    60000 // 60kSPS (APTの信号帯域約34kHzを完全にカバー)
}

fn default_ppm_error() -> i32 {
    0
}

/// RTL-SDR チューナーの受信設定
#[derive(Debug, Clone, Deserialize)]
pub struct SdrConfig {
    #[serde(default = "default_gain")]
    pub gain: f64,               // チューナー利得 (dB単位: 例 45.0)
    #[serde(default = "default_sample_rate")]
    pub sample_rate: u32,        // SDRサンプリングレート (Hz: デフォルト 60000)
    #[serde(default = "default_ppm_error")]
    pub ppm_error: i32,          // 水晶発振器の周波数偏差補正 (PPM: 通常 0)
}

impl Default for SdrConfig {
    fn default() -> Self {
        Self {
            gain: default_gain(),
            sample_rate: default_sample_rate(),
            ppm_error: default_ppm_error(),
        }
    }
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

/// Discord Webhook 通知設定
#[derive(Debug, Clone, Deserialize, Default)]
pub struct DiscordConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub webhook_url: Option<String>,
}

fn default_enable_meteor() -> bool {
    true // ロシア Meteor-M (N2-3, N2-4): 2026年現在も現役でLRPTデジタル気象画像を送信中
}

fn default_enable_noaa() -> bool {
    false // 米国 NOAA (15, 18, 19): 2025年8月に全機退役・停波したためデフォルトOFF
}

/// 追尾・受信対象の気象衛星シリーズ設定
#[derive(Debug, Clone, Deserialize)]
pub struct SatellitesConfig {
    #[serde(default = "default_enable_meteor")]
    pub enable_meteor: bool,
    #[serde(default = "default_enable_noaa")]
    pub enable_noaa: bool,
}

impl Default for SatellitesConfig {
    fn default() -> Self {
        Self {
            enable_meteor: default_enable_meteor(),
            enable_noaa: default_enable_noaa(),
        }
    }
}

// =============================================================================
// メソッド実装ブロック (impl Config)
// =============================================================================
impl Config {
    /// TOML文字列から設定構造体をパース
    pub fn from_str(s: &str) -> Result<Self> {
        toml::from_str(s).context("TOML設定のパースに失敗しました")
    }

    /// ファイルパスから設定を読み込み、環境変数（.env, .local.env）でオーバーライド
    pub fn load_from_file<P: AsRef<Path>>(path: P) -> Result<Self> {
        // .env および .local.env を自動探索して読み込み
        dotenvy::dotenv().ok();
        dotenvy::from_filename(".local.env").ok();
        dotenvy::from_filename("../.local.env").ok();

        let content = fs::read_to_string(path.as_ref())
            .with_context(|| format!("設定ファイルの読み込みに失敗しました: {:?}", path.as_ref()))?;
        let mut config = Self::from_str(&content)?;

        // 環境変数 DISCORD_WEBHOOK_URL が存在する場合は最優先で採用し、自動有効化
        if let Ok(url) = std::env::var("DISCORD_WEBHOOK_URL") {
            if !url.trim().is_empty() {
                config.discord.webhook_url = Some(url.trim().to_string());
                config.discord.enabled = true;
            }
        }

        Ok(config)
    }
}
