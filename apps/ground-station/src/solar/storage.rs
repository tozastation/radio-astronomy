use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use super::dsp::SolarSecondMetrics;
use super::waterfall::render_waterfall_png;

/// 太陽観測データの永続化管理
#[derive(Debug, Clone)]
pub struct SolarStorage {
    data_dir: PathBuf,
}

impl SolarStorage {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            data_dir: data_dir.as_ref().to_path_buf(),
        }
    }

    /// 1秒集約メトリクスを追記型 JSON Lines (`metrics_YYYY-MM-DD.jsonl`) にアトミック保存します。
    pub fn record_metric(&self, metric: &SolarSecondMetrics) -> Result<PathBuf> {
        fs::create_dir_all(&self.data_dir)
            .with_context(|| format!("データディレクトリの作成に失敗しました: {:?}", self.data_dir))?;

        let dt = DateTime::<Utc>::from_timestamp(metric.timestamp, 0)
            .unwrap_or_else(|| Utc::now());
        let date_str = dt.format("%Y-%m-%d").to_string();
        let file_path = self.data_dir.join(format!("metrics_{}.jsonl", date_str));

        let json_line = serde_json::to_string(metric)
            .context("SolarSecondMetrics のシリアライズに失敗しました")?;

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)
            .with_context(|| format!("メトリクスログファイルのオープンに失敗しました: {:?}", file_path))?;

        writeln!(file, "{}", json_line)
            .with_context(|| format!("メトリクスの書き込みに失敗しました: {:?}", file_path))?;

        Ok(file_path)
    }

    /// バースト検知時のウォーターフォール PNG を `data/solar/events/` に保存します。
    pub fn save_event_waterfall(&self, spectra: &[Vec<f32>], timestamp: i64) -> Result<PathBuf> {
        let events_dir = self.data_dir.join("events");
        fs::create_dir_all(&events_dir)
            .with_context(|| format!("イベントディレクトリの作成に失敗しました: {:?}", events_dir))?;

        let dt = DateTime::<Utc>::from_timestamp(timestamp, 0)
            .unwrap_or_else(|| Utc::now());
        let ts_str = dt.format("%Y%m%d_%H%M%S").to_string();
        let file_path = events_dir.join(format!("flare_{}.png", ts_str));

        render_waterfall_png(spectra, 800, 400, &file_path)?;

        Ok(file_path)
    }

    /// 1日の全時間スペクトログラム PNG を `data/solar/daily/` に保存します。
    pub fn save_daily_waterfall(&self, spectra: &[Vec<f32>], date_str: &str) -> Result<PathBuf> {
        let daily_dir = self.data_dir.join("daily");
        fs::create_dir_all(&daily_dir)
            .with_context(|| format!("デイリーディレクトリの作成に失敗しました: {:?}", daily_dir))?;

        let file_path = daily_dir.join(format!("full_day_{}.png", date_str));

        render_waterfall_png(spectra, 1920, 1080, &file_path)?;

        Ok(file_path)
    }
}
