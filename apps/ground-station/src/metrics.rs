use crate::decoder::DecodeResult;
use crate::discord::PassStatus;
use crate::orbit::SatellitePass;
use anyhow::{Context, Result};
use log::{info, warn};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use tokio::io::AsyncWriteExt;

// =============================================================================
// 📊 衛星通過メトリクス収集・管理モジュール (Metrics Collector)
// -----------------------------------------------------------------------------
// 【背景と設計方針】
// 地上局における各パスの観測結果（成否）を追記型 JSON Lines (data/metrics/passes.jsonl)
// で記録・永続化します。
//
// 「受信データの中身が見えるところまで」を成功（Success）の判定基準とし、
// 客観的な自動検知が困難な対象（FMレピータの交信有無やデコーダ未導入保全）は
// 「あきらめる作戦」として Unknown に分類します。
// =============================================================================

/// 観測結果の成否判定（3値分類）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PassOutcome {
    /// 成功: 受信データの中身（画像、テレメトリ値、CW復号テキスト）が確認できた
    Success,
    /// 失敗: 電波微弱によるパケット0件・画像未生成、またはデコーダ／SDRエラー
    Failure,
    /// 判定保留 / 対象外（あきらめる作戦）: FM音声（交信有無判定困難）や未復調保全
    Unknown,
}

impl PassOutcome {
    /// PassStatus から成否判定（データの中身が見えるか）へマッピング
    pub fn from_status(status: PassStatus) -> Self {
        match status {
            PassStatus::ImageDecoded | PassStatus::TelemetryDecoded => PassOutcome::Success,
            PassStatus::WeakSignal | PassStatus::DecodeError => PassOutcome::Failure,
            PassStatus::AudioRecorded | PassStatus::RawPreserved => PassOutcome::Unknown,
        }
    }

    /// データの中身（画像・テレメトリ・テキスト）が客観的に確認・閲覧可能か
    pub fn is_content_viewable(&self) -> bool {
        matches!(self, PassOutcome::Success)
    }

    pub fn label(&self) -> &'static str {
        match self {
            PassOutcome::Success => "成功 (中身確認済)",
            PassOutcome::Failure => "失敗 (微弱/エラー)",
            PassOutcome::Unknown => "保留 (検知あきらめ)",
        }
    }
}

/// 衛星通過メトリクスレコード (JSONL の 1 行)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PassMetricRecord {
    /// 記録日時 (ISO 8601 / JST)
    pub timestamp: String,
    /// 衛星名
    pub satellite: String,
    /// 受信周波数 (Hz)
    pub frequency_hz: u64,
    /// 信号方式名
    pub signal_type: String,
    /// 最大仰角 (度)
    pub max_elevation_deg: f64,
    /// ピーク方位 (度)
    pub peak_azimuth_deg: f64,
    /// 観測ステータス
    pub status: PassStatus,
    /// 成否分類
    pub outcome: PassOutcome,
    /// データの中身が閲覧可能か
    pub content_viewable: bool,
    /// データ内容の要約
    pub content_summary: Option<String>,
    /// セッションディレクトリパス
    pub session_dir: String,
    /// 生成画像が存在するか
    pub has_image: bool,
    /// 録音音声が存在するか
    pub has_audio: bool,
    /// テレメトリまたは復号テキストが存在するか
    pub has_telemetry_or_text: bool,
    /// 通過継続時間 (秒)
    pub duration_secs: Option<u64>,
}

impl PassMetricRecord {
    /// SatellitePass と DecodeResult からメトリクスレコードを自動構築
    pub fn from_pass_and_result(
        pass: &SatellitePass,
        result: &Result<DecodeResult>,
        session_dir: &Path,
    ) -> Self {
        let now_jst = chrono::DateTime::<chrono::Local>::from(chrono::Utc::now());
        let duration_secs = (pass.los - pass.aos).num_seconds().max(0) as u64;

        match result {
            Ok(res) => {
                let status = res
                    .telemetry
                    .as_ref()
                    .map(|t| t.status)
                    .unwrap_or(PassStatus::RawPreserved);
                let outcome = PassOutcome::from_status(status);
                let content_viewable = outcome.is_content_viewable();

                let has_image = res.image_path.as_ref().is_some_and(|p| p.exists());
                let has_audio = res.audio_path.as_ref().is_some_and(|p| p.exists());
                let has_telemetry_or_text = res
                    .telemetry
                    .as_ref()
                    .is_some_and(|t| !t.housekeeping.is_empty() || t.lines_or_packets.is_some());

                PassMetricRecord {
                    timestamp: now_jst.to_rfc3339(),
                    satellite: pass.satellite_name.clone(),
                    frequency_hz: pass.frequency_hz,
                    signal_type: pass.signal_type.name().to_string(),
                    max_elevation_deg: pass.max_elevation_deg,
                    peak_azimuth_deg: pass.peak_azimuth_deg,
                    status,
                    outcome,
                    content_viewable,
                    content_summary: res.telemetry_summary.clone(),
                    session_dir: session_dir.to_string_lossy().to_string(),
                    has_image,
                    has_audio,
                    has_telemetry_or_text,
                    duration_secs: Some(duration_secs),
                }
            }
            Err(e) => PassMetricRecord {
                timestamp: now_jst.to_rfc3339(),
                satellite: pass.satellite_name.clone(),
                frequency_hz: pass.frequency_hz,
                signal_type: pass.signal_type.name().to_string(),
                max_elevation_deg: pass.max_elevation_deg,
                peak_azimuth_deg: pass.peak_azimuth_deg,
                status: PassStatus::DecodeError,
                outcome: PassOutcome::Failure,
                content_viewable: false,
                content_summary: Some(format!("デコード異常終了: {}", e)),
                session_dir: session_dir.to_string_lossy().to_string(),
                has_image: false,
                has_audio: false,
                has_telemetry_or_text: false,
                duration_secs: Some(duration_secs),
            },
        }
    }
}

/// 衛星別観測統計
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SatelliteStats {
    pub total: usize,
    pub success: usize,
    pub failure: usize,
    pub unknown: usize,
}

/// メトリクス集計サマリー
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PassMetricsSummary {
    pub total_passes: usize,
    pub success_count: usize,
    pub failure_count: usize,
    pub unknown_count: usize,
    /// 判定対象 (success + failure) における成功率 (%)
    pub success_rate: f64,
    pub satellite_stats: BTreeMap<String, SatelliteStats>,
}

/// メトリクス収集・集計エンジン
#[derive(Debug, Clone)]
pub struct MetricsCollector {
    pub file_path: PathBuf,
}

impl MetricsCollector {
    pub fn new(file_path: PathBuf) -> Self {
        Self { file_path }
    }

    /// パス観測結果を JSON Lines に追記
    pub async fn record_pass(&self, record: &PassMetricRecord) -> Result<()> {
        if let Some(parent) = self.file_path.parent() {
            tokio::fs::create_dir_all(parent)
                .await
                .with_context(|| format!("メトリクスディレクトリの作成に失敗: {:?}", parent))?;
        }

        let json_line = serde_json::to_string(record)
            .context("メトリクスレコードのJSONシリアライズに失敗")?;

        let mut file = tokio::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.file_path)
            .await
            .with_context(|| format!("メトリクスファイルのオープンに失敗: {:?}", self.file_path))?;

        file.write_all(format!("{}\n", json_line).as_bytes())
            .await
            .with_context(|| format!("メトリクスファイルへの書き込みに失敗: {:?}", self.file_path))?;
        file.flush()
            .await
            .with_context(|| format!("メトリクスファイルのフラッシュに失敗: {:?}", self.file_path))?;

        info!(
            "📊 メトリクス記録完了: 衛星 {}, 判定: {:?} (内容確認: {}), ファイル: {:?}",
            record.satellite, record.outcome, record.content_viewable, self.file_path
        );

        Ok(())
    }

    /// 保存された JSON Lines ファイルから集計サマリーを計算
    pub async fn get_summary(&self) -> Result<PassMetricsSummary> {
        let records = self.read_all_records().await?;
        let mut summary = PassMetricsSummary::default();

        for rec in records {
            summary.total_passes += 1;
            match rec.outcome {
                PassOutcome::Success => summary.success_count += 1,
                PassOutcome::Failure => summary.failure_count += 1,
                PassOutcome::Unknown => summary.unknown_count += 1,
            }

            let entry = summary.satellite_stats.entry(rec.satellite.clone()).or_default();
            entry.total += 1;
            match rec.outcome {
                PassOutcome::Success => entry.success += 1,
                PassOutcome::Failure => entry.failure += 1,
                PassOutcome::Unknown => entry.unknown += 1,
            }
        }

        let determined = summary.success_count + summary.failure_count;
        if determined > 0 {
            summary.success_rate = (summary.success_count as f64 / determined as f64) * 100.0;
        }

        Ok(summary)
    }

    /// 全レコードを読み込み
    pub async fn read_all_records(&self) -> Result<Vec<PassMetricRecord>> {
        if !self.file_path.exists() {
            return Ok(Vec::new());
        }

        let content = tokio::fs::read_to_string(&self.file_path)
            .await
            .with_context(|| format!("メトリクスファイル読み込み失敗: {:?}", self.file_path))?;

        let mut records = Vec::new();
        for (i, line) in content.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<PassMetricRecord>(line) {
                Ok(rec) => records.push(rec),
                Err(e) => {
                    warn!("メトリクス行 {} のパースに失敗 (スキップ): {}", i + 1, e);
                }
            }
        }

        Ok(records)
    }

    /// 直近 N 件のレコードを取得
    pub async fn read_recent_records(&self, limit: usize) -> Result<Vec<PassMetricRecord>> {
        let all = self.read_all_records().await?;
        let len = all.len();
        if len <= limit {
            Ok(all)
        } else {
            Ok(all[len - limit..].to_vec())
        }
    }

    /// メトリクス集計結果と直近履歴を整形テーブルでコンソール表示
    pub fn print_metrics_report(summary: &PassMetricsSummary, recent: &[PassMetricRecord]) {
        println!("\n=====================================================================================================================");
        println!("📊 地上局 衛星通過成否メトリクス 集計レポート");
        println!("=====================================================================================================================");
        println!(
            "総観測パス数: {:<4} | 成功 (中身確認済): {:<4} | 失敗 (微弱/エラー): {:<4} | 保留 (検知あきらめ): {:<4} | 成功率: {:.1}%",
            summary.total_passes,
            summary.success_count,
            summary.failure_count,
            summary.unknown_count,
            summary.success_rate
        );
        println!("---------------------------------------------------------------------------------------------------------------------");
        println!("🛰️  衛星別観測実績:");
        println!("{:<18} | {:<8} | {:<8} | {:<8} | {:<8} | {:<8}", "衛星名", "総観測", "成功", "失敗", "保留", "成功率");
        println!("---------------------------------------------------------------------------------------------------------------------");
        if summary.satellite_stats.is_empty() {
            println!("(記録された観測データはまだありません)");
        } else {
            for (sat, stats) in &summary.satellite_stats {
                let det = stats.success + stats.failure;
                let rate_str = if det > 0 {
                    format!("{:.1}%", (stats.success as f64 / det as f64) * 100.0)
                } else {
                    "-".to_string()
                };
                println!(
                    "{:<18} | {:>8} | {:>8} | {:>8} | {:>8} | {:>8}",
                    sat, stats.total, stats.success, stats.failure, stats.unknown, rate_str
                );
            }
        }
        println!("---------------------------------------------------------------------------------------------------------------------");
        println!("🕒 直近の観測履歴 (最大 {} 件):", recent.len());
        println!("{:<20} | {:<15} | {:<16} | {:<8} | {:<16} | {:<30}", "観測日時 (JST)", "衛星名", "信号方式", "最大仰角", "成否判定", "データ内容要約");
        println!("---------------------------------------------------------------------------------------------------------------------");
        if recent.is_empty() {
            println!("(履歴データはありません)");
        } else {
            for rec in recent.iter().rev() {
                let dt_str = if let Ok(parsed) = chrono::DateTime::parse_from_rfc3339(&rec.timestamp) {
                    parsed.format("%Y-%m-%d %H:%M:%S").to_string()
                } else {
                    rec.timestamp.clone()
                };
                let summary_snippet = rec.content_summary.as_deref().unwrap_or("-");
                println!(
                    "{:<20} | {:<15} | {:<16} | {:>6.1}° | {:<16} | {:<30}",
                    dt_str,
                    rec.satellite,
                    rec.signal_type,
                    rec.max_elevation_deg,
                    rec.outcome.label(),
                    summary_snippet
                );
            }
        }
        println!("=====================================================================================================================\n");
    }
}
