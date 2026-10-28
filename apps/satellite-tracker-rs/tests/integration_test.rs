use satellite_tracker_rs::dsp::DspProcessor;
use satellite_tracker_rs::metrics::MetricsExporter;
use satellite_tracker_rs::orbit::OrbitPredictor;
use satellite_tracker_rs::s3::S3Uploader;
use satellite_tracker_rs::sdr::SdrCollector;
use satellite_tracker_rs::spooler::AudioSpooler;
use std::fs;
use std::path::PathBuf;

fn get_integration_spool_dir() -> PathBuf {
    let dir = std::env::temp_dir().join("satellite_integration_test_spool");
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[tokio::test]
async fn test_tracker_e2e_pass_lifecycle() {
    let spool_dir = get_integration_spool_dir();

    // 1. 各モジュールの初期化
    let predictor = OrbitPredictor::new(35.6895, 139.6917, 30.0, "NORTH", 10.0);
    assert!(predictor.is_in_view(35.0, 15.0), "北天仰角35度は視界内");
    let mut sdr = SdrCollector::new(true, 2_400_000.0, 40.0);
    let mut dsp = DspProcessor::new(2_400_000.0, 48_000);
    let mut spooler = AudioSpooler::new(spool_dir.clone(), 48_000, 50_000_000, 5.0);
    let mut uploader = S3Uploader::new(
        "http://localhost:3900".to_string(),
        "satellite-recordings".to_string(),
        "test_key".to_string(),
        "test_secret".to_string(),
    );
    uploader.mock_mode = true;
    let exporter = MetricsExporter::new();

    // 初期状態はスタンバイ
    sdr.standby().unwrap();
    assert!(sdr.is_standby());

    // 2. AOS シミュレーション (衛星が北天視界内に現れる)
    let sat_name = "ISS (ZARYA)";
    let freq_hz = 145_800_000.0;
    let pass_id = "test_pass_001";

    // AOS 突入: SDR ウォームアップ & スプール開始
    sdr.warmup(freq_hz).unwrap();
    assert!(!sdr.is_standby());
    let wav_path = spooler.start_pass(sat_name, pass_id).unwrap();
    assert!(wav_path.exists());
    exporter.set_tracking_status(sat_name, true);

    // 3. 通過中のデータ処理 (IQ 受信 -> DSP FM復調 -> WAV追記 -> スペクトルメトリクス更新)
    let num_iq = 48_000; // 20ms 分
    let iq_samples = sdr.read_samples(num_iq).unwrap();
    assert_eq!(iq_samples.len(), num_iq);

    let mut pcm_buffer = Vec::new();
    let doppler_hz = -1500.0;
    dsp.process_samples(&iq_samples, doppler_hz, &mut pcm_buffer);
    assert_eq!(pcm_buffer.len(), num_iq / 50);

    spooler.write_frames(&pcm_buffer).unwrap();

    let spec = DspProcessor::measure_spectrum(&iq_samples, 2_400_000.0, freq_hz, 2048);
    exporter.update_orbit_metrics(sat_name, 35.0, 15.0, doppler_hz);
    exporter.update_rf_metrics(sat_name, spec.rssi_dbm, spec.snr_db, spec.measured_doppler_hz);

    let metrics_text = exporter.render_prometheus_text();
    assert!(metrics_text.contains("satellite_tracking_active{satellite=\"ISS (ZARYA)\"} 1"));
    assert!(metrics_text.contains("satellite_elevation_degrees{satellite=\"ISS (ZARYA)\"} 35"));

    // 4. LOS シミュレーション (衛星が地平線下へ沈む)
    let finished_wav = spooler.finish_pass().unwrap().expect("WAV path returned");
    assert!(finished_wav.exists());

    // S3 アップロード & クリーンアップ
    let s3_key = format!("raw/{}/{}", sat_name, finished_wav.file_name().unwrap().to_str().unwrap());
    let upload_success = uploader.upload_and_cleanup(&finished_wav, &s3_key).await.unwrap();
    assert!(upload_success, "S3 アップロードが成功すること");
    assert!(!finished_wav.exists(), "アップロード完了後にローカル WAV が削除されること");

    // メトリクス更新 (tracking_active=0, passes_total+1)
    exporter.set_tracking_status(sat_name, false);
    exporter.record_pass_completed(sat_name, "completed");

    // 省電力スタンバイへ移行 (次回 AOS まで余裕があるため)
    sdr.standby().unwrap();
    assert!(sdr.is_standby(), "LOS完了後は省電力スタンバイへ復帰すること");

    let final_metrics = exporter.render_prometheus_text();
    assert!(final_metrics.contains("satellite_tracking_active{satellite=\"ISS (ZARYA)\"} 0"));
    assert!(final_metrics.contains("satellite_passes_total{satellite=\"ISS (ZARYA)\",status=\"completed\"} 1"));

    let _ = fs::remove_dir_all(&spool_dir);
}

#[tokio::test]
async fn test_galactic_scan_cycle_and_s3_key_generation() {
    use chrono::{TimeZone, Utc};
    use satellite_tracker_rs::galactic::GalacticScanSession;

    let spool_dir = get_integration_spool_dir().join("galactic");
    fs::create_dir_all(&spool_dir).unwrap();

    let mut session = GalacticScanSession::new(
        spool_dir.clone(),
        300.0,
        30.0,
        35.0,
        35.6895,
        139.6917,
    );

    let start_time = Utc.with_ymd_and_hms(2026, 10, 28, 12, 0, 0).unwrap();

    // 1. ON チューニングでのスライス積算
    let on_samples = vec![(0.1f32, 0.1f32); 2048];
    session.accumulate_slice(&on_samples, start_time);
    assert_eq!(session.on_slices_count, 1);
    assert!(session.is_on_tuning);

    // 2. 15秒後の OFF チューニング切り替えとスライス積算
    let switch_time = start_time + chrono::Duration::seconds(16);
    let off_samples = vec![(0.05f32, 0.05f32); 2048];
    session.accumulate_slice(&off_samples, switch_time);
    assert_eq!(session.off_slices_count, 1);
    assert!(!session.is_on_tuning);

    // 3. 20秒後に早期フラッシュ (プリエンプション想定)
    let flush_time = start_time + chrono::Duration::seconds(20);
    let json_path = session.flush_result(flush_time, true).unwrap().expect("JSON output exists");
    assert!(json_path.exists());

    // JSON の内容検証
    let json_content = fs::read_to_string(&json_path).unwrap();
    assert!(json_content.contains("\"preempted\": true"));
    assert!(json_content.contains("\"gal_l_deg\""));
    assert!(json_content.contains("\"gal_b_deg\""));
    assert!(json_content.contains("\"spectrum\""));

    // 4. S3 へのアップロードとキー検証
    let mut uploader = S3Uploader::new(
        "http://localhost:3900".to_string(),
        "satellite-recordings".to_string(),
        "test_key".to_string(),
        "test_secret".to_string(),
    );
    uploader.mock_mode = true;

    let date_str = start_time.format("%Y-%m-%d").to_string();
    let file_name = json_path.file_name().unwrap().to_string_lossy().to_string();
    let s3_key = format!("galactic/{}/{}", date_str, file_name);

    let ok = uploader.upload_and_cleanup(&json_path, &s3_key).await.unwrap();
    assert!(ok);
    assert!(!json_path.exists());
    assert!(s3_key.starts_with("galactic/2026-10-28/scan_"));

    let _ = fs::remove_dir_all(&spool_dir);
}
