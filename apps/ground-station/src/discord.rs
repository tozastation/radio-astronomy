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
    /// FM交信音声録音完了 (0x1ABC9C: ターコイズグリーン)
    AudioRecorded,
    /// 生データ保全完了・未復調 (0x9B59B6: アメジスト紫)
    RawPreserved,
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
            PassStatus::AudioRecorded => 0x1ABC9C,
            PassStatus::RawPreserved => 0x9B59B6,
            PassStatus::WeakSignal => 0xF39C12,
            PassStatus::DecodeError => 0xE74C3C,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            PassStatus::ImageDecoded => "画像デコード成功",
            PassStatus::TelemetryDecoded => "テレメトリ取得完了",
            PassStatus::AudioRecorded => "交信音声録音完了",
            PassStatus::RawPreserved => "生データ保存完了 (未復調)",
            PassStatus::WeakSignal => "電波微弱 (生データ保存)",
            PassStatus::DecodeError => "デコード異常",
        }
    }

    pub fn emoji(&self) -> &'static str {
        match self {
            PassStatus::ImageDecoded => "🟢",
            PassStatus::TelemetryDecoded => "🔵",
            PassStatus::AudioRecorded => "🟢",
            PassStatus::RawPreserved => "🟣",
            PassStatus::WeakSignal => "🟠",
            PassStatus::DecodeError => "🔴",
        }
    }
}

/// 仰角に応じた幾何学評価テキスト
pub fn elevation_evaluation(el_deg: f64) -> &'static str {
    if el_deg >= 70.0 {
        "天頂付近・極めて良好"
    } else if el_deg >= 45.0 {
        "高仰角・良好"
    } else if el_deg >= 20.0 {
        "中仰角"
    } else {
        "低仰角 (建物遮蔽に注意)"
    }
}

/// 周波数に応じた無線バンド区分テキスト
pub fn frequency_band_desc(freq_hz: u64) -> &'static str {
    let mhz = freq_hz as f64 / 1_000_000.0;
    if (137.0..138.5).contains(&mhz) {
        "137MHz 気象衛星帯"
    } else if (144.0..146.0).contains(&mhz) {
        "VHF 2m アマチュア宇宙無線帯"
    } else if (435.0..438.0).contains(&mhz) {
        "UHF 70cm アマチュア宇宙無線帯"
    } else {
        "宇宙無線帯"
    }
}

/// 衛星名に基づく概要・役割テキスト
pub fn satellite_summary_desc(sat_name: &str) -> &'static str {
    let lower = sat_name.to_lowercase();
    if lower.contains("meteor") {
        "極軌道ロシア気象衛星 (MSU-MR デジタル地球観測)"
    } else if lower.contains("noaa") {
        "極軌道米国気象衛星 (AVHRR アナログ雲画像)"
    } else if lower.contains("umka") {
        "超小型反射望遠鏡搭載 3U CubeSat (RS40S / NORAD 57172)"
    } else if lower.contains("funcube") {
        "高感度VHFビーコン・宇宙環境WODテレメトリ CubeSat (AO-73 / NORAD 39444)"
    } else if lower.contains("sonate") {
        "AI画像認識実証 6U CubeSat (NORAD 59112)"
    } else if lower.contains("so-50") {
        "FMボイストランスポンダー中継器 (SaudiSat 1C / NORAD 27607)"
    } else if lower.contains("cas-4") {
        "高利得CWモールステレメトリビーコン CubeSat (NORAD 42761)"
    } else if lower.contains("iss") || lower.contains("zarya") {
        "国際宇宙ステーション (ARISS アマチュア無線局 / NORAD 25544)"
    } else if lower.contains("xw-2") || lower.contains("cas-3") {
        "超高SNR常時CWビーコン微小衛星 (XW-2A / NORAD 40903)"
    } else {
        "軌道周回宇宙機 (Radio Astronomy 自律地上局追尾)"
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

/// ADS-B 航空機近接アラートデータ
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AircraftAlert {
    pub icao_hex: String,
    pub callsign: String,
    pub airline: Option<String>,
    pub aircraft_type: Option<String>,
    pub origin: Option<String>,
    pub destination: Option<String>,
    pub altitude_m: f64,
    pub speed_kmh: f64,
    pub distance_km: f64,
    pub photo_url: Option<String>,
    pub photographer: Option<String>,
    pub tar1090_url: String,
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
        let title = match status {
            PassStatus::ImageDecoded => format!(
                "🛰️ {} [{}] 受信・デコード完了",
                report.satellite_name, report.signal_type_name
            ),
            PassStatus::TelemetryDecoded => format!(
                "🛰️ {} [{}] テレメトリ復調完了",
                report.satellite_name, report.signal_type_name
            ),
            PassStatus::AudioRecorded => format!(
                "🛰️ {} [{}] 交信音声録音完了",
                report.satellite_name, report.signal_type_name
            ),
            PassStatus::RawPreserved => format!(
                "🛰️ {} [{}] 受信・生データ保存完了",
                report.satellite_name, report.signal_type_name
            ),
            PassStatus::WeakSignal => format!(
                "🛰️ {} [{}] 電波微弱 (生データ保存)",
                report.satellite_name, report.signal_type_name
            ),
            PassStatus::DecodeError => format!(
                "🛰️ {} [{}] デコード異常",
                report.satellite_name, report.signal_type_name
            ),
        };

        let status_desc = format!(
            "> {} **ステータス**: {}\n> **{}**: {}",
            status.emoji(),
            status.label(),
            report.satellite_name,
            satellite_summary_desc(&report.satellite_name)
        );

        let mut fields = vec![
            serde_json::json!({
                "name": "📐 軌道ジオメトリ",
                "value": format!(
                    "• **最大仰角**: {:.1}° ({})\n• **ピーク方位**: {}\n• **通過時間**: {}",
                    report.max_elevation_deg,
                    elevation_evaluation(report.max_elevation_deg),
                    report.direction,
                    report.pass_time_str
                ),
                "inline": true
            }),
            serde_json::json!({
                "name": "📡 無線・SDR諸元",
                "value": format!(
                    "• **受信周波数**: `{:.4} MHz`\n• **周波数帯**: {}\n• **信号方式**: {}",
                    freq_mhz,
                    frequency_band_desc(report.frequency_hz),
                    report.signal_type_name
                ),
                "inline": true
            }),
        ];

        // ⚡ 復調成果 & ヘルス (全幅・yamlコードブロック)
        let mut yaml_lines = Vec::new();
        if let Some(ref tel) = report.telemetry {
            if let Some(ref lines_packets) = tel.lines_or_packets {
                yaml_lines.push(format!("復調実績: {}", lines_packets));
            }
            if let Some(snr) = tel.snr_db {
                let quality_desc = if snr >= 15.0 {
                    "極めて明瞭"
                } else if snr >= 8.0 {
                    "良好"
                } else {
                    "微弱"
                };
                yaml_lines.push(format!("信号品質 (SNR): {:.1} dB ({})", snr, quality_desc));
            }
            for (k, v) in &tel.housekeeping {
                yaml_lines.push(format!("{}: {}", k, v));
            }
        }
        let yaml_content = if yaml_lines.is_empty() {
            "```yaml\nデコード状況: 未復調 (生データ保存完了)\n```".to_string()
        } else {
            format!("```yaml\n{}\n```", yaml_lines.join("\n"))
        };
        fields.push(serde_json::json!({
            "name": "⚡ 復調成果 & ヘルス",
            "value": yaml_content,
            "inline": false
        }));

        if report.has_audio {
            fields.push(serde_json::json!({
                "name": "🎵 受信音声 (WAV)",
                "value": "添付プレーヤーでインライン再生可能",
                "inline": true
            }));
        }

        if let Some(ref next_info) = report.next_pass_info {
            fields.push(serde_json::json!({
                "name": "⏰ 次の通過予定",
                "value": next_info,
                "inline": false
            }));
        }

        let mut embed = serde_json::json!({
            "title": title,
            "description": status_desc,
            "color": status.color_code(),
            "timestamp": chrono::Utc::now().to_rfc3339(),
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

        let status = report
            .telemetry
            .as_ref()
            .map(|t| t.status)
            .unwrap_or(if report.has_image {
                PassStatus::ImageDecoded
            } else {
                PassStatus::TelemetryDecoded
            });

        let content_text = match status {
            PassStatus::ImageDecoded => format!(
                "🛰️ **{}** の画像デコードが完了したのだ！宇宙からの最新観測データをお届けするのだ！",
                report.satellite_name
            ),
            PassStatus::TelemetryDecoded => format!(
                "🛰️ **{}** のテレメトリ復調が完了したのだ！宇宙からの最新観測データをお届けするのだ！",
                report.satellite_name
            ),
            PassStatus::AudioRecorded => format!(
                "🛰️ **{}** の交信音声の録音・復調が完了したのだ！音声を確認するのだ！",
                report.satellite_name
            ),
            PassStatus::RawPreserved => format!(
                "🛰️ **{}** の通過録音が完了したのだ！生データを保存したのだ！",
                report.satellite_name
            ),
            PassStatus::WeakSignal => format!(
                "🛰️ **{}** の信号を受信したが微弱だったのだ。生データを保存したのだ！",
                report.satellite_name
            ),
            PassStatus::DecodeError => format!(
                "🛰️ **{}** のデコード中に異常が発生したのだ。ログを確認してほしいのだ！",
                report.satellite_name
            ),
        };

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

    /// デイリーの衛星受信スケジュール Embed を構築
    pub fn build_daily_schedule_embed(
        passes: &[crate::orbit::SatellitePass],
        date_str: &str,
        observer_lat: f64,
        observer_lon: f64,
        min_elev: f64,
    ) -> serde_json::Value {
        let title = format!("📅 【本日の衛星受信スケジュール】 {} (JST)", date_str);
        let description = if passes.is_empty() {
            format!(
                "本日は仰角 {:.1}° 以上の観測対象パスはありません。\n観測地: 北緯 {:.2}°, 東経 {:.2}°",
                min_elev, observer_lat, observer_lon
            )
        } else {
            format!(
                "本日ベランダ上空を通過する予定の観測パス（全 {} 件）です。\n観測地: 北緯 {:.2}°, 東経 {:.2}° | 最小仰角: {:.1}° 以上",
                passes.len(), observer_lat, observer_lon, min_elev
            )
        };

        let mut fields = Vec::new();
        // Discord Embed のフィールド数上限（25件）に対応
        for (i, pass) in passes.iter().take(25).enumerate() {
            let aos_local: chrono::DateTime<chrono::Local> = chrono::DateTime::from(pass.aos);
            let los_local: chrono::DateTime<chrono::Local> = chrono::DateTime::from(pass.los);
            let duration_min = (pass.los - pass.aos).num_minutes();
            let freq_mhz = pass.frequency_hz as f64 / 1_000_000.0;
            let dir = crate::orbit::azimuth_to_direction(pass.peak_azimuth_deg);
            let view_badge = if pass.is_east_view_favorable() {
                "☀️東見通し良好"
            } else {
                "🏢西遮蔽注意"
            };

            let field_name = format!(
                "{}. 🛰️ {} [{}]",
                i + 1,
                pass.satellite_name,
                pass.signal_type.name()
            );
            let field_value = format!(
                "⏱️ {} 〜 {} ({}分間)\n📐 最大 {:.1}° ({} / {}) | 📡 {:.4} MHz",
                aos_local.format("%H:%M"),
                los_local.format("%H:%M"),
                duration_min,
                pass.max_elevation_deg,
                dir,
                view_badge,
                freq_mhz
            );

            fields.push(serde_json::json!({
                "name": field_name,
                "value": field_value,
                "inline": false
            }));
        }

        let footer_text = if passes.len() > 25 {
            format!(
                "Radio Astronomy • GPD Pocket3 自律地上局 (全 {} 件中 25 件を表示 / 他 {} 件)",
                passes.len(),
                passes.len() - 25
            )
        } else {
            format!("Radio Astronomy • GPD Pocket3 自律地上局 (全 {} 件)", passes.len())
        };

        serde_json::json!({
            "title": title,
            "description": description,
            "color": 0x3498DB, // 宇宙ブルー (3447003)
            "fields": fields,
            "footer": {
                "text": footer_text
            }
        })
    }

    /// 今後24時間の通過予定一覧 Embed を構築 (手動CLI / オンデマンド用)
    pub fn build_24h_schedule_embed(
        passes: &[crate::orbit::SatellitePass],
        observer_lat: f64,
        observer_lon: f64,
        min_elev: f64,
    ) -> serde_json::Value {
        let title = "📡 【今後24時間の衛星通過予定】 (JST)".to_string();
        let description = if passes.is_empty() {
            format!(
                "今後24時間に仰角 {:.1}° 以上の観測対象パスはありません。\n観測地: 北緯 {:.2}°, 東経 {:.2}°",
                min_elev, observer_lat, observer_lon
            )
        } else {
            format!(
                "現在から24時間以内に到来する予定の観測パス（全 {} 件）です。\n観測地: 北緯 {:.2}°, 東経 {:.2}° | 最小仰角: {:.1}° 以上",
                passes.len(), observer_lat, observer_lon, min_elev
            )
        };

        let today_jst = chrono::Local::now().date_naive();
        let mut fields = Vec::new();
        for (i, pass) in passes.iter().take(25).enumerate() {
            let aos_local: chrono::DateTime<chrono::Local> = chrono::DateTime::from(pass.aos);
            let los_local: chrono::DateTime<chrono::Local> = chrono::DateTime::from(pass.los);
            let duration_min = (pass.los - pass.aos).num_minutes();
            let freq_mhz = pass.frequency_hz as f64 / 1_000_000.0;
            let dir = crate::orbit::azimuth_to_direction(pass.peak_azimuth_deg);
            let view_badge = if pass.is_east_view_favorable() {
                "☀️東見通し良好"
            } else {
                "🏢西遮蔽注意"
            };

            let time_str = if aos_local.date_naive() == today_jst {
                format!("{} 〜 {}", aos_local.format("%H:%M"), los_local.format("%H:%M"))
            } else {
                format!("{} 〜 {}", aos_local.format("%m/%d %H:%M"), los_local.format("%H:%M"))
            };

            let field_name = format!(
                "{}. 🛰️ {} [{}]",
                i + 1,
                pass.satellite_name,
                pass.signal_type.name()
            );
            let field_value = format!(
                "⏱️ {} ({}分間)\n📐 最大 {:.1}° ({} / {}) | 📡 {:.4} MHz",
                time_str,
                duration_min,
                pass.max_elevation_deg,
                dir,
                view_badge,
                freq_mhz
            );

            fields.push(serde_json::json!({
                "name": field_name,
                "value": field_value,
                "inline": false
            }));
        }

        let footer_text = if passes.len() > 25 {
            format!(
                "Radio Astronomy • GPD Pocket3 自律地上局 (全 {} 件中 25 件を表示 / 他 {} 件)",
                passes.len(),
                passes.len() - 25
            )
        } else {
            format!("Radio Astronomy • GPD Pocket3 自律地上局 (全 {} 件)", passes.len())
        };

        serde_json::json!({
            "title": title,
            "description": description,
            "color": 0x3498DB,
            "fields": fields,
            "footer": {
                "text": footer_text
            }
        })
    }

    /// デイリーの衛星受信スケジュールを Discord に送信
    pub async fn send_daily_schedule(
        &self,
        passes: &[crate::orbit::SatellitePass],
        date_str: &str,
        observer_lat: f64,
        observer_lon: f64,
        min_elev: f64,
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

        info!("Discord に本日の受信スケジュールを送信中 (対象パス: {} 件)", passes.len());

        let embed = Self::build_daily_schedule_embed(passes, date_str, observer_lat, observer_lon, min_elev);
        let content_text = format!(
            "🌅 おはようございますなのだ！本日の衛星受信スケジュールをお届けするのだ！（予定パス: {}件）",
            passes.len()
        );

        let payload = serde_json::json!({
            "content": content_text,
            "embeds": [embed]
        });

        match self.http_client.post(webhook_url).json(&payload).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                    info!("✨ Discord へのデイリースケジュール送信が完了しました！");
                    Ok(())
                } else {
                    let status = resp.status();
                    let text = resp.text().await.unwrap_or_default();
                    warn!("Discord 送信失敗 (HTTP {}): {}", status, text);
                    anyhow::bail!("Discord 送信失敗 (HTTP {}): {}", status, text);
                }
            }
            Err(e) => {
                warn!("Discord 送信エラー: {}", e);
                Err(anyhow::anyhow!("Discord 送信エラー: {}", e))
            }
        }
    }

    /// 今後24時間の通過予定一覧を Discord に送信 (手動CLI / オンデマンド用)
    pub async fn send_24h_schedule(
        &self,
        passes: &[crate::orbit::SatellitePass],
        observer_lat: f64,
        observer_lon: f64,
        min_elev: f64,
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

        info!("Discord に今後24時間の通過予定を送信中 (対象パス: {} 件)", passes.len());

        let embed = Self::build_24h_schedule_embed(passes, observer_lat, observer_lon, min_elev);
        let content_text = format!(
            "📡 今後24時間の衛星通過予定をお届けするのだ！（予定パス: {}件）",
            passes.len()
        );

        let payload = serde_json::json!({
            "content": content_text,
            "embeds": [embed]
        });

        match self.http_client.post(webhook_url).json(&payload).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                    info!("✨ Discord への24時間通過予定送信が完了しました！");
                    Ok(())
                } else {
                    let status = resp.status();
                    let text = resp.text().await.unwrap_or_default();
                    warn!("Discord 送信失敗 (HTTP {}): {}", status, text);
                    anyhow::bail!("Discord 送信失敗 (HTTP {}): {}", status, text);
                }
            }
            Err(e) => {
                warn!("Discord 送信エラー: {}", e);
                Err(anyhow::anyhow!("Discord 送信エラー: {}", e))
            }
        }
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

    /// 航空機近接通知用の Discord Embed JSON オブジェクトを構築
    pub fn build_aircraft_embed(alert: &AircraftAlert) -> serde_json::Value {
        let altitude_ft = alert.altitude_m / 0.3048;
        let flight_title = match &alert.airline {
            Some(al) => format!("✈️ {} {}便 が頭上を通過中！", al, alert.callsign),
            None => format!("✈️ 航空機 {}便 が頭上を通過中！", alert.callsign),
        };

        let tar_url = format!(
            "{}/?icao={}",
            alert.tar1090_url.trim_end_matches('/'),
            alert.icao_hex
        );

        let desc = match (&alert.origin, &alert.destination) {
            (Some(orig), Some(dest)) => format!("🛫 {} ➜ 🛬 {}", orig, dest),
            (Some(orig), None) => format!("🛫 {} 発", orig),
            (None, Some(dest)) => format!("🛬 {} 行き", dest),
            (None, None) => match &alert.airline {
                Some(al) => format!("航空会社: {}", al),
                None => format!("コールサイン: {}", alert.callsign),
            },
        };

        let aircraft_type_str = alert
            .aircraft_type
            .as_deref()
            .unwrap_or("機種不明");

        let fields = vec![
            serde_json::json!({
                "name": "🏷️ 機体",
                "value": format!("{} (`{}`)", aircraft_type_str, alert.icao_hex),
                "inline": true
            }),
            serde_json::json!({
                "name": "📏 最接近距離",
                "value": format!("{:.1} km", alert.distance_km),
                "inline": true
            }),
            serde_json::json!({
                "name": "🧭 対地速度",
                "value": format!("{:.0} km/h", alert.speed_kmh),
                "inline": true
            }),
            serde_json::json!({
                "name": "📐 飛行高度",
                "value": format!("{:.0} m ({:.0} ft)", alert.altitude_m, altitude_ft),
                "inline": true
            }),
        ];

        let mut embed_obj = serde_json::json!({
            "color": 0x3498DB,
            "title": flight_title,
            "url": tar_url,
            "description": desc,
            "fields": fields,
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });

        if let Some(photo_url) = &alert.photo_url {
            if !photo_url.trim().is_empty() {
                embed_obj["image"] = serde_json::json!({ "url": photo_url });
            }
        }

        let footer_text = match &alert.photographer {
            Some(photog) => format!("Photo by {} (Planespotters.net) • tar1090 Radar", photog),
            None => "tar1090 Radar".to_string(),
        };
        embed_obj["footer"] = serde_json::json!({ "text": footer_text });

        embed_obj
    }

    /// 航空機近接通知を Discord に送信
    pub async fn send_aircraft_alert(&self, alert: &AircraftAlert) -> Result<()> {
        if !self.config.enabled {
            return Ok(());
        }

        let webhook_url = match &self.config.webhook_url {
            Some(url) if !url.trim().is_empty() => url.trim(),
            _ => return Ok(()),
        };

        let embed = Self::build_aircraft_embed(alert);
        let payload = serde_json::json!({
            "username": "NOAA Ground Station",
            "embeds": [embed]
        });

        let resp = self.http_client
            .post(webhook_url)
            .json(&payload)
            .send()
            .await
            .context("Discord 航空機通知の送信に失敗しました")?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            warn!("Discord 航空機通知エラー ({}): {}", status, body);
        } else {
            info!("✨ Discord 航空機近接通知を送信しました (便名: {})", alert.callsign);
        }

        Ok(())
    }
}

