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
//    JSON payload の embed 内部から "attachment://satellite_image.png" で参照させることで、
//    Discord タイムライン上にフル解像度の雲画像カードを美しく表示します。
// 2. ステータス別リッチEmbed表示:
//    画像復元、テレメトリ取得、電波微弱、エラー等のステータスに応じたカラーコードを付与し、
//    周波数、最大仰角・方角、SNR、走査線数、衛星の電圧/温度等のテレメトリを2列のインライン
//    フィールドで美しくレイアウトします。
// 3. SRE的 Graceful Degradation:
//    ネットワーク瞬断やレート制限で Discord 送信が失敗しても、SDR観測デーモン本体を
//    巻き添えにせず、警告ログを出力して正常に処理を継続します。
// =============================================================================

/// 観測・デコード結果のステータス
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum PassStatus {
    /// 鮮明な画像復元成功 (0x2ECC71: エメラルドグリーン)
    ImageDecoded,
    /// テレメトリ/パケット復調成功 (0x3498DB: 宇宙ブルー)
    TelemetryDecoded,
    /// 電波微弱・生データ保全 (0xF39C12: アンバーオレンジ)
    WeakSignal,
    /// デコード異常 (0xE74C3C: コーラルレッド)
    DecodeError,
}

impl PassStatus {
    pub fn color_code(&self) -> u32 {
        match self {
            PassStatus::ImageDecoded => 0x2ECC71,
            PassStatus::TelemetryDecoded => 0x3498DB,
            PassStatus::WeakSignal => 0xF39C12,
            PassStatus::DecodeError => 0xE74C3C,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            PassStatus::ImageDecoded => "画像デコード成功",
            PassStatus::TelemetryDecoded => "テレメトリ取得完了",
            PassStatus::WeakSignal => "電波微弱 (生データ保存)",
            PassStatus::DecodeError => "デコード異常",
        }
    }
}

/// 衛星から取得されたテレメトリおよび受信品質
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SatelliteTelemetry {
    pub snr_db: Option<f64>,
    pub lines_or_packets: Option<String>,
    pub housekeeping: Vec<(String, String)>,
    pub status: PassStatus,
}

/// Discord Webhook に添付可能な最大音声バイト数 (8MB 安全マージン)
pub const MAX_DISCORD_AUDIO_BYTES: usize = 8 * 1024 * 1024;

/// 衛星通過観測レポート
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PassReport {
    pub satellite_name: String,
    pub signal_type_name: String,
    pub max_elevation_deg: f64,
    pub direction: String,
    pub frequency_hz: u64,
    pub pass_time_str: String,
    pub telemetry: Option<SatelliteTelemetry>,
    pub has_image: bool,
    #[serde(default)]
    pub has_audio: bool,
    pub next_pass_info: Option<String>,
}

#[derive(Clone)]
pub struct DiscordClient {
    config: DiscordConfig,
    http_client: Client,
}

/// 最小構成の有効な 1x1 PNG バイナリ (テスト・モック送信用)
const SAMPLE_PNG_BYTES: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, // PNG Signature
    0x00, 0x00, 0x00, 0x0d, // IHDR length (13)
    0x49, 0x48, 0x44, 0x52, // "IHDR"
    0x00, 0x00, 0x00, 0x01, // width: 1
    0x00, 0x00, 0x00, 0x01, // height: 1
    0x08, 0x02, 0x00, 0x00, 0x00, // 8-bit RGB
    0x90, 0x77, 0x53, 0xde, // CRC
    0x00, 0x00, 0x00, 0x0c, // IDAT length (12)
    0x49, 0x48, 0x44, 0x41, 0x54, // "IDAT"
    0x78, 0x9c, 0x63, 0xf8, 0xff, 0xff, 0x3f, 0x00, 0x05, 0xfe, 0x02, 0xfe, // deflate data
    0xa7, 0x35, 0x81, 0x84, // CRC
    0x00, 0x00, 0x00, 0x00, // IEND length (0)
    0x49, 0x45, 0x4e, 0x44, // "IEND"
    0xae, 0x42, 0x60, 0x82, // CRC
];

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

    /// テスト用のサンプル衛星画像バイナリを生成
    pub fn create_test_sample_image() -> Vec<u8> {
        SAMPLE_PNG_BYTES.to_vec()
    }

    /// テスト用のサンプル衛星音声バイナリ (2400Hz APT風正弦波 WAV) を生成
    pub fn create_test_sample_wav() -> Vec<u8> {
        let sample_rate = 11025u32;
        let duration_secs = 0.5f32;
        let num_samples = (sample_rate as f32 * duration_secs) as usize;
        let data_size = (num_samples * 2) as u32;

        let header = crate::receiver::create_wav_header(data_size);
        let mut wav_bytes = Vec::with_capacity(44 + data_size as usize);
        wav_bytes.extend_from_slice(&header);

        // 2400Hz の APT 風ピープ音
        let freq = 2400.0f32;
        for i in 0..num_samples {
            let t = i as f32 / sample_rate as f32;
            let sample = (t * freq * 2.0 * std::f32::consts::PI).sin();
            let sample_i16 = (sample * 8000.0) as i16;
            wav_bytes.extend_from_slice(&sample_i16.to_le_bytes());
        }

        wav_bytes
    }

    /// Discord Embed JSON オブジェクトを構築
    pub fn build_embed(report: &PassReport) -> serde_json::Value {
        let status = report
            .telemetry
            .as_ref()
            .map(|t| t.status)
            .unwrap_or(if report.has_image {
                PassStatus::ImageDecoded
            } else {
                PassStatus::TelemetryDecoded
            });

        let freq_mhz = report.frequency_hz as f64 / 1_000_000.0;
        let title = format!(
            "🛰️ {} [{}] 受信・デコード完了",
            report.satellite_name, report.signal_type_name
        );

        let mut fields = vec![
            serde_json::json!({
                "name": "🛰️ 衛星・方式",
                "value": format!("{} [{}]", report.satellite_name, report.signal_type_name),
                "inline": true
            }),
            serde_json::json!({
                "name": "📡 受信周波数",
                "value": format!("{:.4} MHz", freq_mhz),
                "inline": true
            }),
            serde_json::json!({
                "name": "📐 最大仰角 / 方角",
                "value": format!("{:.1}° ({})", report.max_elevation_deg, report.direction),
                "inline": true
            }),
            serde_json::json!({
                "name": "⏱️ 通過時間 (JST)",
                "value": report.pass_time_str,
                "inline": true
            }),
        ];

        if let Some(ref tel) = report.telemetry {
            if let Some(snr) = tel.snr_db {
                let quality_desc = if snr >= 15.0 {
                    "極めて明瞭"
                } else if snr >= 8.0 {
                    "良好"
                } else {
                    "微弱"
                };
                fields.push(serde_json::json!({
                    "name": "📶 信号品質 (SNR)",
                    "value": format!("{:.1} dB ({})", snr, quality_desc),
                    "inline": true
                }));
            }

            if let Some(ref lines_packets) = tel.lines_or_packets {
                fields.push(serde_json::json!({
                    "name": "📊 復調実績",
                    "value": lines_packets,
                    "inline": true
                }));
            }

            if !tel.housekeeping.is_empty() {
                let hk_str = tel
                    .housekeeping
                    .iter()
                    .map(|(k, v)| format!("{}: {}", k, v))
                    .collect::<Vec<_>>()
                    .join(" | ");
                fields.push(serde_json::json!({
                    "name": "⚡ 衛星ヘルス・テレメトリ",
                    "value": hk_str,
                    "inline": false
                }));
            }
        }

        if let Some(ref next_info) = report.next_pass_info {
            fields.push(serde_json::json!({
                "name": "⏰ 次の通過予定",
                "value": next_info,
                "inline": false
            }));
        }

        if report.has_audio {
            fields.push(serde_json::json!({
                "name": "🎵 受信音声 (WAV)",
                "value": "添付プレーヤーでインライン再生可能",
                "inline": true
            }));
        }

        let mut embed = serde_json::json!({
            "title": title,
            "color": status.color_code(),
            "fields": fields,
            "footer": {
                "text": "Radio Astronomy • GPD Pocket3 自律地上局"
            }
        });

        if report.has_image {
            embed["image"] = serde_json::json!({
                "url": "attachment://satellite_image.png"
            });
        }

        embed
    }

    /// リッチな観測レポート（Embed＋画像＋受信音声WAV）を送信
    pub async fn send_pass_report(
        &self,
        report: &PassReport,
        image_bytes: Option<Vec<u8>>,
        audio_bytes: Option<Vec<u8>>,
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

        info!(
            "Discord に観測レポートを送信中: {} [{}] (最大仰角: {:.1}° 方角: {})",
            report.satellite_name, report.signal_type_name, report.max_elevation_deg, report.direction
        );

        let content_text = format!(
            "🛰️ **{}** の受信・デコードが完了したのだ！宇宙からの最新観測データをお届けするのだ！",
            report.satellite_name
        );

        let embed = Self::build_embed(report);
        let payload_json = serde_json::json!({
            "content": content_text,
            "embeds": [embed]
        });

        let mut form = Form::new().text("payload_json", payload_json.to_string());
        let mut file_index = 0;

        // 画像バイナリが存在する場合は添付
        if let Some(bytes) = image_bytes {
            if !bytes.is_empty() {
                let part = Part::bytes(bytes)
                    .file_name("satellite_image.png")
                    .mime_str("image/png")
                    .context("画像MIME設定エラー")?;
                form = form.part(format!("files[{}]", file_index), part);
                file_index += 1;
            }
        }

        // 音声バイナリ (WAV) が存在し、かつ8MB以内なら添付
        if let Some(bytes) = audio_bytes {
            if !bytes.is_empty() {
                if bytes.len() <= MAX_DISCORD_AUDIO_BYTES {
                    let audio_len = bytes.len();
                    let part = Part::bytes(bytes)
                        .file_name("satellite_audio.wav")
                        .mime_str("audio/wav")
                        .context("音声MIME設定エラー")?;
                    form = form.part(format!("files[{}]", file_index), part);
                    info!("🎵 Discord に受信音声WAVを添付しました (files[{}], {} bytes)", file_index, audio_len);
                } else {
                    warn!(
                        "受信音声WAVのサイズが Discord 制限（8MB）を超過しているため添付をスキップしました ({} bytes)",
                        bytes.len()
                    );
                }
            }
        }

        match self.http_client.post(webhook_url).multipart(form).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                    info!("✨ Discord への観測レポート投稿が完了しました！");
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

    /// 衛星受信・デコード完了の報告を画像付きで Discord に送信 (互換用)
    #[allow(clippy::too_many_arguments)]
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
        let (has_image, image_bytes) = if let Some(path) = image_path {
            if path.exists() {
                match tokio::fs::read(path).await {
                    Ok(bytes) => (true, Some(bytes)),
                    Err(e) => {
                        warn!("Discord送信用の画像読み込みに失敗しました: {}", e);
                        (false, None)
                    }
                }
            } else {
                (false, None)
            }
        } else {
            (false, None)
        };

        let report = PassReport {
            satellite_name: sat_name.to_string(),
            signal_type_name: "APT / 衛星画像".to_string(),
            max_elevation_deg: max_elev,
            direction: direction.to_string(),
            frequency_hz: freq_hz,
            pass_time_str: pass_time_str.to_string(),
            telemetry: Some(SatelliteTelemetry {
                snr_db: Some(15.0),
                lines_or_packets: if has_image {
                    Some("画像復元完了".to_string())
                } else {
                    None
                },
                housekeeping: Vec::new(),
                status: if has_image {
                    PassStatus::ImageDecoded
                } else {
                    PassStatus::WeakSignal
                },
            }),
            has_image,
            has_audio: false,
            next_pass_info: next_pass_info.map(|s| s.to_string()),
        };

        self.send_pass_report(&report, image_bytes, None).await
    }

    /// テキストメッセージを Discord に送信
    pub async fn send_text(&self, text: &str) -> Result<()> {
        if !self.config.enabled {
            return Ok(());
        }

        let webhook_url = match &self.config.webhook_url {
            Some(url) if !url.trim().is_empty() => url.trim(),
            _ => return Ok(()),
        };

        let payload = serde_json::json!({ "content": text });
        let _ = self.http_client.post(webhook_url).json(&payload).send().await;
        Ok(())
    }
}
