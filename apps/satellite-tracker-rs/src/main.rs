use anyhow::Result;
use chrono::Utc;
use log::{info, warn};
use satellite_tracker_rs::dsp::DspProcessor;
use satellite_tracker_rs::galactic::{generate_synthetic_galactic_iq, GalacticScanSession};
use satellite_tracker_rs::metrics::MetricsExporter;
use satellite_tracker_rs::orbit::{parse_tle_catalog, OrbitPredictor};
use satellite_tracker_rs::receiver::{build_rtl_fm_args, build_rtl_sdr_args, create_wav_header, is_raw_iq_target};
use satellite_tracker_rs::s3::S3Uploader;
use satellite_tracker_rs::sdr::SdrCollector;
use satellite_tracker_rs::spooler::AudioSpooler;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::signal;

/// 稼働中の外部 SDR 録音プロセス
enum ActiveProcess {
    Raw {
        child: tokio::process::Child,
        path: PathBuf,
    },
    Fm {
        child: tokio::process::Child,
        path: PathBuf,
        writer_task: tokio::task::JoinHandle<std::io::Result<u64>>,
    },
}

/// 録音プロセスの優雅な終了と S3 アップロード
async fn stop_and_upload_active_process(
    active: Option<ActiveProcess>,
    sat_name: &str,
    uploader: &S3Uploader,
) {
    match active {
        Some(ActiveProcess::Raw { mut child, path }) => {
            info!("Stopping RAW recording process for {}", sat_name);
            if let Some(pid) = child.id() {
                unsafe {
                    libc::kill(pid as i32, libc::SIGINT);
                }
            }
            let _ = child.wait().await;
            if path.exists() {
                let s3_key = format!(
                    "raw/{}/{}",
                    sat_name,
                    path.file_name().unwrap().to_string_lossy()
                );
                let _ = uploader.upload_and_cleanup(&path, &s3_key).await;
            }
        }
        Some(ActiveProcess::Fm {
            mut child,
            path,
            writer_task,
        }) => {
            info!("Stopping FM recording process for {}", sat_name);
            if let Some(pid) = child.id() {
                unsafe {
                    libc::kill(pid as i32, libc::SIGINT);
                }
            }
            let _ = child.wait().await;
            match writer_task.await {
                Ok(Ok(data_bytes)) => {
                    if path.exists() {
                        if let Ok(mut f) = tokio::fs::OpenOptions::new().write(true).open(&path).await {
                            let _ = f.write_all(&create_wav_header(48_000, data_bytes as u32)).await;
                            let _ = f.flush().await;
                        }
                        let s3_key = format!(
                            "raw/{}/{}",
                            sat_name,
                            path.file_name().unwrap().to_string_lossy()
                        );
                        let _ = uploader.upload_and_cleanup(&path, &s3_key).await;
                    }
                }
                Ok(Err(e)) => warn!("FM streaming writer task error for {}: {}", sat_name, e),
                Err(e) => warn!("FM streaming writer join error for {}: {}", sat_name, e),
            }
        }
        None => {}
    }
}

/// 対象衛星の初期定義 (CelesTrak 等の外部通信不通時も自律稼働するフォールバック)
struct TargetSatellite {
    name: &'static str,
    norad_id: u32,
    line1: &'static str,
    line2: &'static str,
    freq_hz: f64,
}

#[derive(Clone)]
struct RuntimeTarget {
    name: &'static str,
    norad_id: u32,
    line1: String,
    line2: String,
    freq_hz: f64,
}

const DEFAULT_TARGETS: &[TargetSatellite] = &[
    TargetSatellite {
        name: "ISS (ZARYA)",
        norad_id: 25544,
        line1: "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996",
        line2: "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263",
        freq_hz: 145_825_000.0,
    },
    TargetSatellite {
        name: "METEOR-M2 3",
        norad_id: 57166,
        line1: "1 57166U 23091A   26296.25648911 -.00000011  00000+0  13974-4 0  9991",
        line2: "2 57166  98.5934 348.5131 0004559 145.3247 214.8230 14.24054780172745",
        freq_hz: 137_900_000.0,
    },
    TargetSatellite {
        name: "METEOR-M2 4",
        norad_id: 59051,
        line1: "1 59051U 24039A   26296.27566674  .00000005  00000+0  21993-4 0  9998",
        line2: "2 59051  98.7198 254.0636 0007418 151.3257 208.8330 14.22441005137473",
        freq_hz: 137_900_000.0,
    },
    TargetSatellite {
        name: "FUNCUBE-1 (AO-73)",
        norad_id: 39444,
        line1: "1 39444U 13066AE  26294.20736285  .00008239  00000+0  48957-3 0  9993",
        line2: "2 39444  97.8439 268.5385 0036409 132.3370 228.0953 15.10994851698683",
        freq_hz: 145_935_000.0,
    },
];

/// ローカル TLE キャッシュファイルが存在すれば優先読込し、不在時は DEFAULT_TARGETS にフォールバック
fn load_runtime_targets() -> Vec<RuntimeTarget> {
    let mut candidates = Vec::new();
    if let Ok(env_path) = std::env::var("TLE_FILE_PATH") {
        candidates.push(PathBuf::from(env_path));
    }
    candidates.push(PathBuf::from("/etc/satellite-tracker/tles.txt"));
    candidates.push(PathBuf::from("apps/ground-station/data/cache/tles.txt"));
    candidates.push(PathBuf::from("data/cache/tles.txt"));

    let mut catalog = std::collections::HashMap::new();
    for path in candidates {
        if path.exists() {
            if let Ok(text) = std::fs::read_to_string(&path) {
                let parsed = parse_tle_catalog(&text);
                if !parsed.is_empty() {
                    catalog = parsed;
                    break;
                }
            }
        }
    }

    DEFAULT_TARGETS
        .iter()
        .map(|t| {
            if let Some((_, l1, l2)) = catalog.get(&t.norad_id) {
                RuntimeTarget {
                    name: t.name,
                    norad_id: t.norad_id,
                    line1: l1.clone(),
                    line2: l2.clone(),
                    freq_hz: t.freq_hz,
                }
            } else {
                RuntimeTarget {
                    name: t.name,
                    norad_id: t.norad_id,
                    line1: t.line1.to_string(),
                    line2: t.line2.to_string(),
                    freq_hz: t.freq_hz,
                }
            }
        })
        .collect()
}

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
    let min_meteor_el_deg: f64 = std::env::var("MIN_MAX_EL_METEOR_DEG")
        .unwrap_or_else(|_| "25.0".to_string())
        .parse()
        .unwrap_or(25.0);
    let min_default_el_deg: f64 = std::env::var("MIN_MAX_EL_DEFAULT_DEG")
        .unwrap_or_else(|_| "20.0".to_string())
        .parse()
        .unwrap_or(20.0);
    let beam_az_deg: f64 = std::env::var("BEAM_AZ_DEG")
        .unwrap_or_else(|_| "30.0".to_string())
        .parse()
        .unwrap_or(30.0);
    let beam_el_deg: f64 = std::env::var("BEAM_EL_DEG")
        .unwrap_or_else(|_| "35.0".to_string())
        .parse()
        .unwrap_or(35.0);
    let galactic_cycle_sec: f64 = std::env::var("GALACTIC_CYCLE_SEC")
        .unwrap_or_else(|_| "300.0".to_string())
        .parse()
        .unwrap_or(300.0);

    info!(
        "Config: Observer=[Lat={:.4}, Lon={:.4}, Alt={:.1}m], Balcony={}, MockSDR={}, Port={}, Gating=[Meteor={:.1}°, Default={:.1}°], Beam=[Az={:.1}°, El={:.1}°]",
        lat, lon, alt_m, balcony_facing, mock_sdr, metrics_port, min_meteor_el_deg, min_default_el_deg, beam_az_deg, beam_el_deg
    );

    // 2. モジュール初期化
    let predictor = OrbitPredictor::new(lat, lon, alt_m, &balcony_facing, 10.0);
    let mut sdr = SdrCollector::new(mock_sdr, 2_400_000.0, 40.0);
    let _dsp = DspProcessor::new(2_400_000.0, 48_000);
    let mut spooler = AudioSpooler::new(spool_dir.clone(), 48_000, 500 * 1024 * 1024, 10.0);
    let mut galactic_session = GalacticScanSession::new(
        spool_dir.clone(),
        galactic_cycle_sec,
        beam_az_deg,
        beam_el_deg,
        lat,
        lon,
    );
    let mut prng_seed = 0x9e37_79b9_7f4a_7c15;
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
    let mut active_process: Option<ActiveProcess> = None;

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

                let runtime_targets = load_runtime_targets();
                for sat in &runtime_targets {
                    let _ = sat.norad_id;
                    match predictor.calculate_position(sat.name, &sat.line1, &sat.line2, sat.freq_hz, now_utc) {
                        Ok(pos) => {
                            if predictor.is_in_view(pos.elevation_deg, pos.azimuth_deg) {
                                let est_max = predictor
                                    .estimate_active_pass_max_el(sat.name, &sat.line1, &sat.line2, sat.freq_hz, now_utc)
                                    .unwrap_or(pos.elevation_deg);
                                if OrbitPredictor::is_qualifying_pass(sat.name, est_max, min_meteor_el_deg, min_default_el_deg) {
                                    visible_candidates.push((sat, pos));
                                } else {
                                    // 足切り (Pass Gating)
                                    exporter.set_tracking_status(sat.name, false);
                                }
                            } else {
                                exporter.set_tracking_status(sat.name, false);
                                // 次回パス予測
                                if let Ok(Some(next_pass)) = predictor.get_next_pass(sat.name, &sat.line1, &sat.line2, sat.freq_hz, now_utc, 12.0, 30) {
                                    if OrbitPredictor::is_qualifying_pass(sat.name, next_pass.max_elevation_deg, min_meteor_el_deg, min_default_el_deg) {
                                        let aos_ts = next_pass.aos_time.timestamp() as f64;
                                        exporter.set_next_pass(sat.name, aos_ts);
                                        let diff_sec = aos_ts - now_utc.timestamp() as f64;
                                        if diff_sec > 0.0 && diff_sec < earliest_next_aos_sec {
                                            earliest_next_aos_sec = diff_sec;
                                        }
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
                    // 衛星パス優先突入に伴う銀河スキャンの早期フラッシュ
                    if galactic_session.elapsed_seconds(now_utc) >= 10.0 {
                        if let Ok(Some(json_path)) = galactic_session.flush_result(now_utc, true) {
                            let s3_key = format!("galactic/{}/{}", now_utc.format("%Y-%m-%d"), json_path.file_name().unwrap().to_string_lossy());
                            let _ = uploader.upload_and_cleanup(&json_path, &s3_key).await;
                        }
                    }

                    // 最大仰角の衛星を選択
                    visible_candidates.sort_by(|a, b| b.1.elevation_deg.partial_cmp(&a.1.elevation_deg).unwrap());
                    let (chosen_sat, chosen_pos) = &visible_candidates[0];

                    // 追尾対象切り替えまたは AOS 開始判定
                    if !pass_in_progress || active_satellite.as_deref() != Some(chosen_sat.name) {
                        if pass_in_progress {
                            if let Some(prev_sat) = active_satellite.take() {
                                if active_process.is_some() {
                                    stop_and_upload_active_process(active_process.take(), &prev_sat, &uploader).await;
                                } else if let Ok(Some(wav_path)) = spooler.finish_pass() {
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

                        if is_raw_iq_target(chosen_sat.name) {
                            let raw_filename = format!("{}_{}.raw", chosen_sat.name, pass_id);
                            let raw_path = spool_dir.join(&raw_filename);
                            info!(
                                "Starting rtl_sdr RAW I/Q recording (240 kSPS) for {} -> {:?}",
                                chosen_sat.name, raw_path
                            );
                            let args = build_rtl_sdr_args(chosen_sat.freq_hz, 240_000, 45.0, &raw_path);
                            match tokio::process::Command::new("rtl_sdr")
                                .args(&args)
                                .spawn()
                            {
                                Ok(child) => {
                                    active_process = Some(ActiveProcess::Raw { child, path: raw_path });
                                }
                                Err(e) => {
                                    warn!(
                                        "Failed to spawn rtl_sdr command for {}: {}. Falling back to audio spooler.",
                                        chosen_sat.name, e
                                    );
                                    let _ = spooler.start_pass(chosen_sat.name, &pass_id);
                                }
                            }
                        } else if !mock_sdr {
                            // ISS またはその他音声衛星の実電波受信 (rtl_fm による 48kSPS NFM 復調)
                            let wav_filename = format!("{}_{}.wav", chosen_sat.name, pass_id);
                            let wav_path = spool_dir.join(&wav_filename);
                            info!(
                                "Starting rtl_fm real RF recording (48 kSPS) for {} -> {:?}",
                                chosen_sat.name, wav_path
                            );

                            match tokio::fs::File::create(&wav_path).await {
                                Ok(mut file) => {
                                    if let Err(e) = file.write_all(&create_wav_header(48_000, 0)).await {
                                        warn!("Failed to write initial WAV header for {}: {}", chosen_sat.name, e);
                                    }
                                    let args = build_rtl_fm_args(chosen_sat.freq_hz, 48_000, 45.0);
                                    match tokio::process::Command::new("rtl_fm")
                                        .args(&args)
                                        .stdout(std::process::Stdio::piped())
                                        .stderr(std::process::Stdio::null())
                                        .spawn()
                                    {
                                        Ok(mut child) => {
                                            let mut stdout = child.stdout.take().expect("Failed to open rtl_fm stdout");
                                            let writer_task = tokio::spawn(async move {
                                                let copied = tokio::io::copy(&mut stdout, &mut file).await?;
                                                file.flush().await?;
                                                Ok(copied)
                                            });
                                            active_process = Some(ActiveProcess::Fm { child, path: wav_path, writer_task });
                                        }
                                        Err(e) => {
                                            warn!(
                                                "Failed to spawn rtl_fm command for {}: {}. Falling back to audio spooler.",
                                                chosen_sat.name, e
                                            );
                                            let _ = spooler.start_pass(chosen_sat.name, &pass_id);
                                        }
                                    }
                                }
                                Err(e) => {
                                    warn!("Failed to create WAV file for {}: {}. Falling back to audio spooler.", chosen_sat.name, e);
                                    let _ = spooler.start_pass(chosen_sat.name, &pass_id);
                                }
                            }
                        } else {
                            // MOCK_SDR=true モード: 合成音声スプール
                            if let Err(e) = sdr.warmup(chosen_sat.freq_hz) {
                                warn!("SDR warmup failed: {}", e);
                            }
                            if let Err(e) = spooler.start_pass(chosen_sat.name, &pass_id) {
                                warn!("Spooler start_pass failed: {}", e);
                            }
                        }
                    }

                    exporter.set_tracking_status(chosen_sat.name, true);
                    exporter.update_orbit_metrics(
                        chosen_sat.name,
                        chosen_pos.elevation_deg,
                        chosen_pos.azimuth_deg,
                        chosen_pos.doppler_shift_hz,
                    );

                    // 外部録音プロセス（rtl_sdr, rtl_fm）が未稼働の場合（MOCK_SDR=true 時）のみモック音声を生成
                    if active_process.is_none() {
                        let is_aprs = chosen_sat.name.contains("ISS");
                        let pcm_duration = update_interval_sec as f64;
                        let continuous_pcm = sdr.generate_mock_audio(pcm_duration, is_aprs);
                        let _ = spooler.write_frames(&continuous_pcm);

                        // RF メトリクス用のリアルタイム FFT 解析
                        match sdr.read_samples(2048) {
                            Ok(iq_samples) => {
                                let spec = DspProcessor::measure_spectrum(&iq_samples, 2_400_000.0, chosen_sat.freq_hz, 2048);
                                exporter.update_rf_metrics(chosen_sat.name, spec.rssi_dbm, spec.snr_db, spec.measured_doppler_hz);
                            }
                            Err(e) => {
                                warn!("SDR read error during tracking: {}", e);
                            }
                        }
                    }
                } else {
                    // 視界外 (LOS 判定)
                    if pass_in_progress {
                        if let Some(finished_sat) = active_satellite.take() {
                            info!("LOS completed: {}", finished_sat);
                            if active_process.is_some() {
                                stop_and_upload_active_process(active_process.take(), &finished_sat, &uploader).await;
                            } else if let Ok(Some(wav_path)) = spooler.finish_pass() {
                                let s3_key = format!("raw/{}/{}", finished_sat, wav_path.file_name().unwrap().to_string_lossy());
                                let _ = uploader.upload_and_cleanup(&wav_path, &s3_key).await;
                            }
                            exporter.record_pass_completed(&finished_sat, "completed");
                            exporter.set_tracking_status(&finished_sat, false);
                        }
                        pass_in_progress = false;
                    }

                    // 次回好条件パスの AOS まで 30 秒以上空いていれば天の川銀河ドリフトスキャンを実行
                    if earliest_next_aos_sec > 30.0 {
                        let current_tuning = galactic_session.current_tuning_freq_hz();
                        let is_on = galactic_session.is_on_tuning;

                        let samples = if mock_sdr {
                            let pt = satellite_tracker_rs::galactic::compute_galactic_pointing(lat, lon, beam_az_deg, beam_el_deg, now_utc);
                            generate_synthetic_galactic_iq(2048, 2_048_000.0, is_on, pt.gal_l_deg, pt.gal_b_deg, &mut prng_seed)
                        } else {
                            if sdr.is_standby() || (sdr.center_freq_hz() - current_tuning).abs() > 100.0 {
                                let _ = sdr.warmup(current_tuning);
                            }
                            sdr.read_samples(2048).unwrap_or_else(|_| {
                                let pt = satellite_tracker_rs::galactic::compute_galactic_pointing(lat, lon, beam_az_deg, beam_el_deg, now_utc);
                                generate_synthetic_galactic_iq(2048, 2_048_000.0, is_on, pt.gal_l_deg, pt.gal_b_deg, &mut prng_seed)
                            })
                        };

                        galactic_session.accumulate_slice(&samples, now_utc);

                        // サイクル完了判定
                        if galactic_session.elapsed_seconds(now_utc) >= galactic_cycle_sec {
                            info!("Galactic 21cm drift scan cycle completed ({:.0}s). Uploading scan JSON...", galactic_session.elapsed_seconds(now_utc));
                            if let Ok(Some(json_path)) = galactic_session.flush_result(now_utc, false) {
                                let s3_key = format!("galactic/{}/{}", now_utc.format("%Y-%m-%d"), json_path.file_name().unwrap().to_string_lossy());
                                let _ = uploader.upload_and_cleanup(&json_path, &s3_key).await;
                            }
                        }
                    } else if earliest_next_aos_sec <= 30.0 {
                        // 衛星 AOS 30秒前のプリエンプション (早期フラッシュ)
                        if galactic_session.elapsed_seconds(now_utc) >= 10.0 {
                            info!("Preempting Galactic scan for approaching satellite pass (AOS in {:.1}s)", earliest_next_aos_sec);
                            if let Ok(Some(json_path)) = galactic_session.flush_result(now_utc, true) {
                                let s3_key = format!("galactic/{}/{}", now_utc.format("%Y-%m-%d"), json_path.file_name().unwrap().to_string_lossy());
                                let _ = uploader.upload_and_cleanup(&json_path, &s3_key).await;
                            }
                        }
                        if sdr.is_standby() {
                            let _ = sdr.warmup(145_825_000.0);
                        }
                    }
                }
            }
        }
    }

    if active_process.is_some() {
        if let Some(sat_name) = active_satellite.as_deref() {
            stop_and_upload_active_process(active_process.take(), sat_name, &uploader).await;
        }
    }

    if let Err(e) = sdr.standby() {
        warn!("Error setting standby on shutdown: {}", e);
    }
    info!("satellite-tracker-rs shutdown completed successfully.");
    Ok(())
}
