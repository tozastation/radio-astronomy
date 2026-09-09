use crate::config::Config;
use crate::decoder::{DecodeResult, DecoderEngine};
use crate::discord::DiscordClient;
use crate::orbit::{SatellitePass, SignalType};
use crate::voicevox::VoicevoxClient;
use anyhow::{bail, Context, Result};
use log::{error, info, warn};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::mpsc;

// =============================================================================
// ⚙️ 非同期デコードワーカ (Worker Pipeline)
// -----------------------------------------------------------------------------
// 【アーキテクチャ: 時分割並行パイプライン】
// SDR による電波録音 (I/O) は衛星通過の決められた時間に完了しなければなりませんが、
// satdump や gr-satellites による画像復調・DSP (CPU) は数分〜十数分かかることがあります。
// 本モジュールは Tokio MPSC キューを通じて録音完了イベント（DecodeJob）を非同期に受領し、
// 次の通過録音をブロックすることなくバックグラウンドでデコードと Discord / ずんだもん通知を
// 自律的に処理します。
// =============================================================================

#[derive(Debug, Clone)]
pub struct DecodeJob {
    pub pass: SatellitePass,
    pub raw_path: PathBuf,
    pub session_dir: PathBuf,
}

pub async fn run_worker(
    mut rx: mpsc::Receiver<DecodeJob>,
    discord: Arc<DiscordClient>,
    voicevox: Arc<VoicevoxClient>,
) {
    info!("非同期デコードワーカ起動完了 (バックグラウンド待機中...)");

    while let Some(job) = rx.recv().await {
        info!(
            "デコードジョブ受領: 衛星 {}, 信号方式 {}, 生ファイル {:?}",
            job.pass.satellite_name,
            job.pass.signal_type.name(),
            job.raw_path
        );

        let _ = process_decode_job(
            &job.pass,
            &job.raw_path,
            &job.session_dir,
            Some(&discord),
            Some(&voicevox),
        )
        .await;
    }

    info!("非同期デコードワーカ終了");
}

/// 衛星パスと生録音データからデコードを実行し、ずんだもん音声および Discord に通知
pub async fn process_decode_job(
    pass: &SatellitePass,
    raw_path: &Path,
    session_dir: &Path,
    discord: Option<&DiscordClient>,
    voicevox: Option<&VoicevoxClient>,
) -> Result<DecodeResult> {
    let pass_name = pass.satellite_name.clone();
    let decode_res = DecoderEngine::decode(pass, raw_path, session_dir).await;

    match decode_res {
        Ok(result) => {
            info!("デコード完了: {:?}", result);

            // ずんだもん音声通知
            if let Some(vox) = voicevox {
                let voice_msg = if result.image_path.is_some() {
                    format!("{}の画像デコードが完了したのだ！画像を確認するのだ！", pass_name)
                } else if result.audio_path.is_some() {
                    format!("{}の交信音声の録音が完了したのだ！音声を確認するのだ！", pass_name)
                } else {
                    format!("{}のデータ保存が完了したのだ！", pass_name)
                };
                let _ = vox.speak(&voice_msg).await;
            }

            // Discord 通知
            if let Some(dc) = discord {
                let pass_time_str = format!(
                    "{} 〜 {}",
                    chrono::DateTime::<chrono::Local>::from(pass.aos).format("%Y-%m-%d %H:%M:%S"),
                    chrono::DateTime::<chrono::Local>::from(pass.los).format("%H:%M:%S")
                );
                let dir_str = format!(
                    "{} ({})",
                    crate::orbit::azimuth_to_direction(pass.peak_azimuth_deg),
                    pass.view_geometry_desc()
                );

                let (has_image, image_bytes) = if let Some(ref path) = result.image_path {
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

                let (has_audio, audio_bytes) = if let Some(ref path) = result.audio_path {
                    if path.exists() {
                        match tokio::fs::metadata(path).await {
                            Ok(meta) if meta.len() as usize <= crate::discord::MAX_DISCORD_AUDIO_BYTES => {
                                match tokio::fs::read(path).await {
                                    Ok(bytes) => (true, Some(bytes)),
                                    Err(e) => {
                                        warn!("Discord送信用の音声読み込みに失敗しました: {}", e);
                                        (false, None)
                                    }
                                }
                            }
                            Ok(meta) => {
                                info!(
                                    "音声ファイルがDiscord上限（8MB）過大のため送信をスキップします ({} bytes): {:?}",
                                    meta.len(),
                                    path
                                );
                                (false, None)
                            }
                            Err(e) => {
                                warn!("音声メタデータ取得失敗: {}", e);
                                (false, None)
                            }
                        }
                    } else {
                        (false, None)
                    }
                } else {
                    (false, None)
                };

                let report = crate::discord::PassReport {
                    satellite_name: pass_name.clone(),
                    signal_type_name: pass.signal_type.name().to_string(),
                    max_elevation_deg: pass.max_elevation_deg,
                    direction: dir_str,
                    frequency_hz: pass.frequency_hz,
                    pass_time_str,
                    telemetry: result.telemetry.clone(),
                    has_image,
                    has_audio,
                    next_pass_info: None,
                };

                let _ = dc.send_pass_report(&report, image_bytes, audio_bytes).await;
            }

            Ok(result)
        }
        Err(e) => {
            error!("デコード処理中に予期せぬエラーが発生しました: {}", e);
            if let Some(dc) = discord {
                let pass_time_str = format!(
                    "{} 〜 {}",
                    chrono::DateTime::<chrono::Local>::from(pass.aos).format("%Y-%m-%d %H:%M:%S"),
                    chrono::DateTime::<chrono::Local>::from(pass.los).format("%H:%M:%S")
                );
                let dir_str = format!(
                    "{} ({})",
                    crate::orbit::azimuth_to_direction(pass.peak_azimuth_deg),
                    pass.view_geometry_desc()
                );
                let report = crate::discord::PassReport {
                    satellite_name: pass_name.clone(),
                    signal_type_name: pass.signal_type.name().to_string(),
                    max_elevation_deg: pass.max_elevation_deg,
                    direction: dir_str,
                    frequency_hz: pass.frequency_hz,
                    pass_time_str,
                    telemetry: Some(crate::discord::SatelliteTelemetry {
                        snr_db: None,
                        lines_or_packets: None,
                        housekeeping: vec![("エラー詳細".to_string(), e.to_string())],
                        status: crate::discord::PassStatus::DecodeError,
                    }),
                    has_image: false,
                    has_audio: false,
                    next_pass_info: None,
                };
                let _ = dc.send_pass_report(&report, None, None).await;
            }
            Err(e)
        }
    }
}

/// セッションディレクトリの構造から衛星パス情報と生データパスを逆引き復元
pub fn resolve_pass_from_session_dir(
    config: &Config,
    session_dir: &Path,
) -> Result<(SatellitePass, PathBuf)> {
    let raw_u8 = session_dir.join("raw.u8");
    let raw_wav = session_dir.join("raw.wav");

    let raw_path = if raw_u8.exists() {
        raw_u8
    } else if raw_wav.exists() {
        raw_wav
    } else {
        bail!(
            "セッションディレクトリ内に raw.u8 または raw.wav が存在しません: {:?}",
            session_dir
        );
    };

    let dir_name = session_dir
        .file_name()
        .and_then(|n| n.to_str())
        .context("セッションディレクトリ名が無効です")?;

    // ディレクトリ名末尾から衛星名を抽出 (例: 20260909_074013_XW-2A -> XW-2A)
    let sat_name = if let Some(idx) = dir_name.rfind('_') {
        &dir_name[idx + 1..]
    } else {
        dir_name
    };

    let sat_norm = sat_name.to_lowercase().replace(['-', '_', ' '], "");

    // 衛星設定から検索
    let mut matched_freq = 145_660_000;
    let mut matched_type = SignalType::MorseCw;

    if sat_norm.contains("meteor") {
        matched_freq = 137_900_000;
        matched_type = SignalType::Lrpt;
    } else if sat_norm.contains("iss") {
        matched_freq = 145_825_000;
        matched_type = SignalType::AprsPacket;
    } else if sat_norm.contains("noaa") {
        matched_freq = 137_100_000;
        matched_type = SignalType::Apt;
    } else {
        for target in &config.satellites.cubesats.targets {
            let t_norm = target.name.to_lowercase().replace(['-', '_', ' '], "");
            if t_norm.contains(&sat_norm) || sat_norm.contains(&t_norm) {
                matched_freq = target.freq;
                matched_type = SignalType::from_str_type(&target.r#type);
                break;
            }
        }
    }

    let pass = SatellitePass {
        satellite_name: sat_name.to_string(),
        frequency_hz: matched_freq,
        signal_type: matched_type,
        aos: chrono::Utc::now() - chrono::Duration::minutes(10),
        los: chrono::Utc::now(),
        max_elevation_deg: 45.0,
        peak_azimuth_deg: 90.0,
    };

    Ok((pass, raw_path))
}
