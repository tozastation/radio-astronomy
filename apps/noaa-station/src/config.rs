use anyhow::{Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::Path;

/// NOAA地上局の全体設定
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub observer: ObserverConfig,
    pub scheduler: SchedulerConfig,
    pub voicevox: VoicevoxConfig,
    pub storage: StorageConfig,
}

/// 観測地点（地上アンテナ設置場所）の座標
#[derive(Debug, Clone, Deserialize)]
pub struct ObserverConfig {
    pub latitude: f64,
    pub longitude: f64,
    pub altitude_m: f64,
}

/// スケジューリングとパス検出の閾値設定
#[derive(Debug, Clone, Deserialize)]
pub struct SchedulerConfig {
    pub min_elevation_deg: f64,
    pub pre_alert_minutes: f64,
    pub tle_update_interval_hours: u64,
}

/// VOICEVOXずんだもん音声合成エンジンの設定
#[derive(Debug, Clone, Deserialize)]
pub struct VoicevoxConfig {
    pub enabled: bool,
    pub host: String,
    pub speaker_id: u32,
}

/// 観測データ・生成画像の保存先設定
#[derive(Debug, Clone, Deserialize)]
pub struct StorageConfig {
    pub output_dir: String,
}

impl Config {
    /// TOML文字列から設定をパースする
    pub fn from_str(s: &str) -> Result<Self> {
        toml::from_str(s).context("TOML設定のパースに失敗しました")
    }

    /// ファイルパスから設定を読み込む
    pub fn load_from_file<P: AsRef<Path>>(path: P) -> Result<Self> {
        let content = fs::read_to_string(path.as_ref())
            .with_context(|| format!("設定ファイルの読み込みに失敗しました: {:?}", path.as_ref()))?;
        Self::from_str(&content)
    }
}
