use crate::config::DiscordConfig;
use anyhow::{Context, Result};
use log::{info, warn};
use reqwest::multipart::{Form, Part};
use reqwest::Client;
use std::path::Path;
use std::time::Duration;

// =============================================================================
// 📲 Discord Webhook 通知クライアント (DiscordClient)
// -----------------------------------------------------------------------------
// 【機能と特徴】
// 1. multipart/form-data による画像添付:
//    Discord Webhook API の仕様に基づき、画像ファイルを "files[0]" として添付し、
//    JSON payload の embed 内部から "attachment://image.png" で参照させることで、
//    Discord タイムライン上にフル解像度の雲画像カードを美しく表示します。
// 2. SRE的 Graceful Degradation:
//    ネットワーク瞬断やレート制限で Discord 送信が失敗しても、SDR観測デーモン本体を
//    巻き添えにせず、警告ログを出力して正常に処理を継続します。
// =============================================================================

#[derive(Clone)]
pub struct DiscordClient {
    config: DiscordConfig,
    http_client: Client,
}

impl DiscordClient {
    pub fn new(config: DiscordConfig) -> Self {
        let http_client = Client::builder()
            .timeout(Duration::from_secs(15))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            config,
            http_client,
        }
    }

    /// 衛星受信・デコード完了の報告を画像付きで Discord に送信
    pub async fn send_satellite_pass_report(
        &self,
        sat_name: &str,
        max_elev: f64,
        direction: &str,
        freq_hz: u64,
        pass_time_str: &str,
        image_path: Option<&Path>,
        next_pass_info: Option<&str>,
    ) -> Result<()> {
        if !self.config.enabled {
            return Ok(());
        }

        let webhook_url = match &self.config.webhook_url {
            Some(url) if !url.trim().is_empty() => url.trim(),
            _ => {
                warn!("Discord通知が有効化されていますが、Webhook URL が未設定です");
                return Ok(());
            }
        };

        info!("Discord に観測レポートを送信中: {} (最大仰角: {:.1}° 方角: {})", sat_name, max_elev, direction);

        let freq_mhz = freq_hz as f64 / 1_000_000.0;
        let content_text = format!("🛰️ **{}** の受信・デコードが完了したのだ！宇宙からの雲画像をお届けするのだ！", sat_name);

        // Discord Embed フィールドの動的構築
        let mut fields = vec![
            serde_json::json!({
                "name": "最大仰角 / 方角",
                "value": format!("{:.1}° ({})", max_elev, direction),
                "inline": true
            }),
            serde_json::json!({
                "name": "受信周波数",
                "value": format!("{:.4} MHz", freq_mhz),
                "inline": true
            }),
            serde_json::json!({
                "name": "通過時間 (JST)",
                "value": pass_time_str,
                "inline": false
            }),
        ];

        if let Some(next_info) = next_pass_info {
            fields.push(serde_json::json!({
                "name": "⏰ 次の通過予定",
                "value": next_info,
                "inline": false
            }));
        }

        // Discord Embed オブジェクトの構築
        let embed = serde_json::json!({
            "title": format!("{} 気象衛星 雲画像デコード完了", sat_name),
            "color": 3447003, // エレガントな宇宙ブルー (#3498DB)
            "fields": fields,
            "image": {
                "url": "attachment://image.png"
            },
            "footer": {
                "text": "Radio Astronomy • GPD Pocket3 自律地上局"
            }
        });

        let payload_json = serde_json::json!({
            "content": content_text,
            "embeds": [embed]
        });

        // multipart/form-data の組み立て
        let mut form = Form::new().text("payload_json", payload_json.to_string());

        // 画像ファイルが存在する場合は添付
        if let Some(path) = image_path {
            if path.exists() {
                match tokio::fs::read(path).await {
                    Ok(bytes) => {
                        let part = Part::bytes(bytes)
                            .file_name("image.png")
                            .mime_str("image/png")
                            .context("MIME設定エラー")?;
                        form = form.part("files[0]", part);
                    }
                    Err(e) => {
                        warn!("Discord送信用の画像読み込みに失敗しました: {}", e);
                    }
                }
            }
        }

        // Webhook POST リクエスト送信
        match self.http_client.post(webhook_url).multipart(form).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                    info!("✨ Discord への画像投稿が完了しました！");
                } else {
                    let status = resp.status();
                    let text = resp.text().await.unwrap_or_default();
                    warn!("Discord 送信失敗 (HTTP {}): {}", status, text);
                }
            }
            Err(e) => {
                warn!("Discord 送信エラー (スキップして処理継続): {}", e);
            }
        }

        Ok(())
    }
}
