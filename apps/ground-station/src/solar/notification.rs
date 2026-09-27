use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use log::{info, warn};
use reqwest::multipart::{Form, Part};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::sync::Arc;

use crate::discord::DiscordClient;
use super::dsp::SolarSecondMetrics;

/// Discord Embed フィールド
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolarEmbedField {
    pub name: String,
    pub value: String,
    pub inline: bool,
}

/// 太陽電波アラート用 Discord Embed
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolarDiscordEmbed {
    pub title: String,
    pub description: String,
    pub color: u32,
    pub fields: Vec<SolarEmbedField>,
    pub timestamp: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image: Option<serde_json::Value>,
}

/// 太陽電波バーストの Discord 通知管理
pub struct SolarNotifier {
    discord_client: Arc<DiscordClient>,
    cooldown_secs: u64,
    last_notified_timestamp: Option<i64>,
}

impl SolarNotifier {
    pub fn new(discord_client: Arc<DiscordClient>, cooldown_secs: u64) -> Self {
        Self {
            discord_client,
            cooldown_secs,
            last_notified_timestamp: None,
        }
    }

    /// クールダウン期間が経過しており、通知可能かどうか判定します。
    pub fn should_notify(&self, current_timestamp: i64) -> bool {
        match self.last_notified_timestamp {
            Some(last) => (current_timestamp - last).max(0) as u64 >= self.cooldown_secs,
            None => true,
        }
    }

    /// 通知完了時刻を記録します。
    pub fn record_notification(&mut self, timestamp: i64) {
        self.last_notified_timestamp = Some(timestamp);
    }

    /// 太陽フレア検知時のリッチ Embed を構築します。
    pub fn build_flare_alert_embed(
        &self,
        metric: &SolarSecondMetrics,
        center_freq: f64,
        bandwidth: f64,
    ) -> SolarDiscordEmbed {
        let dt = DateTime::<Utc>::from_timestamp(metric.timestamp, 0)
            .unwrap_or_else(|| Utc::now());
        let freq_mhz = center_freq / 1e6;
        let bw_mhz = bandwidth / 1e6;

        let scale_str = if metric.snr_db >= 20.0 {
            "特大 (Extreme Flare Event)"
        } else if metric.snr_db >= 10.0 {
            "強 (Strong Flare Event)"
        } else {
            "中 (Moderate Flare Event)"
        };

        let fields = vec![
            SolarEmbedField {
                name: "⚡ 電波強度上昇".to_string(),
                value: format!("**+{:.1} dB** (背景雑音比 約{:.0}倍)", metric.snr_db, 10.0f32.powf(metric.snr_db / 10.0)),
                inline: true,
            },
            SolarEmbedField {
                name: "🔥 推定規模".to_string(),
                value: scale_str.to_string(),
                inline: true,
            },
            SolarEmbedField {
                name: "📻 観測周波数".to_string(),
                value: format!("{:.1} MHz (帯域 {:.1} MHz)", freq_mhz, bw_mhz),
                inline: true,
            },
            SolarEmbedField {
                name: "☀️ 太陽位置".to_string(),
                value: format!("方位角 {:.1}° / 仰角 {:.1}°", metric.sun_az_deg, metric.sun_el_deg),
                inline: true,
            },
            SolarEmbedField {
                name: "🕒 検知時刻 (JST)".to_string(),
                value: format!("<t:{}:T> (<t:{}:R>)", metric.timestamp, metric.timestamp),
                inline: true,
            },
            SolarEmbedField {
                name: "🌌 宇宙天気確認".to_string(),
                value: "[NICT宇宙天気](https://swc.nict.go.jp/) / [GOES X-Ray](https://www.swpc.noaa.gov/products/goes-x-ray-flux)".to_string(),
                inline: true,
            },
        ];

        SolarDiscordEmbed {
            title: format!("🚨 【太陽電波バースト検知】 {:.1} MHz帯", freq_mhz),
            description: "太陽表面において急激なプラズマ電波放射（太陽フレア）を検出しました。".to_string(),
            color: 0xE67E22, // Orange
            fields,
            timestamp: dt.to_rfc3339(),
            image: Some(serde_json::json!({
                "url": "attachment://solar_waterfall.png"
            })),
        }
    }

    /// Discord へスペクトログラム画像を添付してアラートを送信します。
    pub async fn send_flare_alert(
        &mut self,
        metric: &SolarSecondMetrics,
        center_freq: f64,
        bandwidth: f64,
        image_path: &Path,
    ) -> Result<()> {
        let webhook_url = match self.discord_client.webhook_url() {
            Some(url) if !url.trim().is_empty() => url,
            _ => {
                info!("Discord Webhook が未設定のため、通知をスキップしました");
                return Ok(());
            }
        };

        let embed = self.build_flare_alert_embed(metric, center_freq, bandwidth);
        let payload = serde_json::json!({
            "content": "🚨 **太陽フレア電波バースト（Type III / Type II）をリアルタイム検知したのだ！**",
            "embeds": [embed]
        });

        let mut form = Form::new()
            .text("payload_json", serde_json::to_string(&payload)?);

        if image_path.exists() {
            let img_bytes = fs::read(image_path)
                .with_context(|| format!("画像ファイルの読み込みに失敗しました: {:?}", image_path))?;
            let part = Part::bytes(img_bytes)
                .file_name("solar_waterfall.png")
                .mime_str("image/png")?;
            form = form.part("files[0]", part);
        }

        let resp = self.discord_client.http_client()
            .post(webhook_url)
            .multipart(form)
            .send()
            .await;

        match resp {
            Ok(r) if r.status().is_success() => {
                info!("✨ Discord への太陽フレア速報アラート送信が完了しました！");
                self.record_notification(metric.timestamp);
            }
            Ok(r) => {
                warn!("Discord 送信失敗 (HTTP {}): {}", r.status(), r.text().await.unwrap_or_default());
            }
            Err(e) => {
                warn!("Discord 送信エラー: {}", e);
            }
        }

        Ok(())
    }
}
