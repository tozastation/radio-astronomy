use anyhow::Result;
use chrono::Utc;
use log::{info, warn};
use satellite_tracker_rs::dsp::DspProcessor;
use satellite_tracker_rs::metrics::MetricsExporter;
use satellite_tracker_rs::orbit::OrbitPredictor;
use satellite_tracker_rs::s3::S3Uploader;
use satellite_tracker_rs::sdr::SdrCollector;
use satellite_tracker_rs::spooler::AudioSpooler;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::signal;

/// 対象衛星の静的初期定義 (CelesTrak 等の外部通信不通時も自律稼働するフォールバック)
struct TargetSatellite {
    name: &'static str,
    line1: &'static str,
    line2: &'static str,
    freq_hz: f64,
}

const DEFAULT_TARGETS: &[TargetSatellite] = &[
    TargetSatellite {
        name: "ISS (ZARYA)",
        line1: "1 25544U 98067A   26248.17592762  .00003558  00000-0  72743-4 0  9999",
        line2: "2 25544  51.6310 264.2007 0005041 108.5564 251.5973 15.48992921584153",
        freq_hz: 145_825_000.0,
    },
    TargetSatellite {
        name: "METEOR-M2 4",
        line1: "1 59051U 24039A   26248.16204753  .00000001  00000+0  20236-4 0  9995",
        line2: "2 59051  98.7095 206.4842 0006129 304.9729  55.0873 14.22436604130631",
        freq_hz: 137_900_000.0,
    },
    TargetSatellite {
        name: "FUNCUBE-1 (AO-73)",
        line1: "1 39444U 13066AE  26247.90903049  .00003511  00000-0  21472-3 0  9994",
        line2: "2 39444  97.8363 221.1119 0033645 293.7219  66.0482 15.10481180691699",
        freq_hz: 145_935_000.0,
    },
];

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    info!("🛰️ Starting satellite-tracker-rs (Ultra-low power Rust edge tracker)...");

    // 1. 環境変数からの設定読み込み
    let lat: f64 = std::env::var("OBSERVER_LATITUDE")
        .unwrap_or_else(|_| "35.6895".to_string())
        .parse()
        .unwrap_or(35.6895);
    let lon: f64 = std::env::var("OBSERVER_LONGITUDE")
        .unwrap_or_else(|_| "139.6917".to_string())
        .parse()
        .unwrap_or(139.6917);
    let alt_m: f64 = std::env::var("OBSERVER_ELEVATION_M")
        .unwrap_or_else(|_| "30.0".to_string())
        .parse()
        .unwrap_or(30.0);
    let balcony_facing = std::env::var("BALCONY_FACING").unwrap_or_else(|_| "NORTH".to_string());
    let mock_sdr = std::env::var("MOCK_SDR")
        .map(|v| v == "true" || v == "1" || v == "yes")
        .unwrap_or(false);
    let metrics_port: u16 = std::env::var("METRICS_PORT")
        .unwrap_or_else(|_| "9100".to_string())
        .parse()
        .unwrap_or(9100);
    let update_interval_sec: u64 = std::env::var("UPDATE_INTERVAL_SEC")
        .unwrap_or_else(|_| "2".to_string())
        .parse()
        .unwrap_or(2);
    let spool_dir = PathBuf::from(std::env::var("SPOOL_DIR").unwrap_or_else(|_| "/tmp/spool".to_string()));

    info!(
        "Config: Observer=[Lat={:.4}, Lon={:.4}, Alt={:.1}m], Balcony={}, MockSDR={}, Port={}",
        lat, lon, alt_m, balcony_facing, mock_sdr, metrics_port
    );

    // 2. モジュール初期化
    let predictor = OrbitPredictor::new(lat, lon, alt_m, &balcony_facing, 10.0);
    let mut sdr = SdrCollector::new(mock_sdr, 2_400_000.0, 40.0);
    let _dsp = DspProcessor::new(2_400_000.0, 48_000);
    let mut spooler = AudioSpooler::new(spool_dir.clone(), 48_000, 500 * 1024 * 1024, 10.0);
    let uploader = S3Uploader::from_env();
    let exporter = Arc::new(MetricsExporter::new());

    // 3. Prometheus HTTP サーバーをバックグラウンドで起動
    let exporter_clone = exporter.clone();
    tokio::spawn(async move {
        if let Err(e) = exporter_clone.run_server(metrics_port).await {
            warn!("Metrics server terminated with error: {}", e);
        }
    });

    // 4. メインループ準備
    let mut ticker = tokio::time::interval(Duration::from_secs(update_interval_sec));
    let mut active_satellite: Option<String> = None;
    let mut pass_in_progress = false;

    info!("Entering autonomous edge tracking loop (power-saving enabled)...");
    let _ = uploader.sync_pending_spool(&spool_dir).await;

    loop {
        tokio::select! {
            _ = signal::ctrl_c() => {
                info!("Received shutdown signal. Stopping satellite-tracker-rs gracefully...");
                break;
            }
            _ = ticker.tick() => {
                let now_utc = Utc::now();
                let mut visible_candidates = Vec::new();
                let mut earliest_next_aos_sec = f64::MAX;

                for sat in DEFAULT_TARGETS {
                    match predictor.calculate_position(sat.name, sat.line1, sat.line2, sat.freq_hz, now_utc) {
                        Ok(pos) => {
                            if predictor.is_in_view(pos.elevation_deg, pos.azimuth_deg) {
                                visible_candidates.push((sat, pos));
                            } else {
                                exporter.set_tracking_status(sat.name, false);
                                // 次回パス予測
                                if let Ok(Some(next_pass)) = predictor.get_next_pass(sat.name, sat.line1, sat.line2, sat.freq_hz, now_utc, 12.0, 30) {
                                    let aos_ts = next_pass.aos_time.timestamp() as f64;
                                    exporter.set_next_pass(sat.name, aos_ts);
                                    let diff_sec = aos_ts - now_utc.timestamp() as f64;
                                    if diff_sec > 0.0 && diff_sec < earliest_next_aos_sec {
                                        earliest_next_aos_sec = diff_sec;
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            warn!("Failed to calculate orbit for {}: {}", sat.name, e);
                        }
                    }
                }

                if !visible_candidates.is_empty() {
                    // 最大仰角の衛星を選択
                    visible_candidates.sort_by(|a, b| b.1.elevation_deg.partial_cmp(&a.1.elevation_deg).unwrap());
                    let (chosen_sat, chosen_pos) = &visible_candidates[0];

                    // 追尾対象切り替えまたは AOS 開始判定
                    if !pass_in_progress || active_satellite.as_deref() != Some(chosen_sat.name) {
                        if pass_in_progress {
                            if let Some(prev_sat) = active_satellite.take() {
                                if let Ok(Some(wav_path)) = spooler.finish_pass() {
                                    let s3_key = format!("raw/{}/{}", prev_sat, wav_path.file_name().unwrap().to_string_lossy());
                                    let _ = uploader.upload_and_cleanup(&wav_path, &s3_key).await;
                                }
                                exporter.record_pass_completed(&prev_sat, "completed");
                                exporter.set_tracking_status(&prev_sat, false);
                            }
                        }

                        info!(
                            "AOS entered: {} (El: {:.1}°, Az: {:.1}°, Freq: {:.3} MHz)",
                            chosen_sat.name, chosen_pos.elevation_deg, chosen_pos.azimuth_deg, chosen_sat.freq_hz / 1_000_000.0
                        );
                        pass_in_progress = true;
                        active_satellite = Some(chosen_sat.name.to_string());
                        let pass_id = now_utc.format("%Y%m%d_%H%M%S").to_string();

                        if let Err(e) = sdr.warmup(chosen_sat.freq_hz) {
                            warn!("SDR warmup failed: {}", e);
                        }
                        if let Err(e) = spooler.start_pass(chosen_sat.name, &pass_id) {
                            warn!("Spooler start_pass failed: {}", e);
                        }
                    }

                    exporter.set_tracking_status(chosen_sat.name, true);
                    exporter.update_orbit_metrics(
                        chosen_sat.name,
                        chosen_pos.elevation_deg,
                        chosen_pos.azimuth_deg,
                        chosen_pos.doppler_shift_hz,
                    );

                    // 1. 衛星通過実時間と同期した 48kHz PCM 音声の連続スプール（デューティ比 100% 確保）
                    let is_aprs = chosen_sat.name.contains("ISS");
                    let pcm_duration = update_interval_sec as f64;
                    let continuous_pcm = sdr.generate_mock_audio(pcm_duration, is_aprs);
                    let _ = spooler.write_frames(&continuous_pcm);

                    // 2. RF メトリクス用のリアルタイム FFT 解析（2048 サンプルで十分な高分解能）
                    match sdr.read_samples(2048) {
                        Ok(iq_samples) => {
                            let spec = DspProcessor::measure_spectrum(&iq_samples, 2_400_000.0, chosen_sat.freq_hz, 2048);
                            exporter.update_rf_metrics(chosen_sat.name, spec.rssi_dbm, spec.snr_db, spec.measured_doppler_hz);
                        }
                        Err(e) => {
                            warn!("SDR read error during tracking: {}", e);
                        }
                    }
                } else {
                    // 視界外 (LOS 判定)
                    if pass_in_progress {
                        if let Some(finished_sat) = active_satellite.take() {
                            info!("LOS completed: {}", finished_sat);
                            if let Ok(Some(wav_path)) = spooler.finish_pass() {
                                let s3_key = format!("raw/{}/{}", finished_sat, wav_path.file_name().unwrap().to_string_lossy());
                                let _ = uploader.upload_and_cleanup(&wav_path, &s3_key).await;
                            }
                            exporter.record_pass_completed(&finished_sat, "completed");
                            exporter.set_tracking_status(&finished_sat, false);
                        }
                        pass_in_progress = false;
                    }

                    // 動的省電力制御: 次回 AOS まで 30 秒以上空いていれば SDR 給電停止 (スタンバイ)
                    if earliest_next_aos_sec > 30.0 {
                        if !sdr.is_standby() {
                            let _ = sdr.standby();
                        }
                    } else if earliest_next_aos_sec <= 30.0 && sdr.is_standby() {
                        let _ = sdr.warmup(145_825_000.0);
                    }
                }
            }
        }
    }

    if let Err(e) = sdr.standby() {
        warn!("Error setting standby on shutdown: {}", e);
    }
    info!("satellite-tracker-rs shutdown completed successfully.");
    Ok(())
}
