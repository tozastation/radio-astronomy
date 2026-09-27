use ground_station::solar::dsp::SolarSecondMetrics;
use ground_station::solar::storage::SolarStorage;
use std::fs;
use std::path::Path;

#[test]
fn test_solar_storage_record_metric() {
    let tmp_dir = Path::new("target/tmp_test_solar_storage");
    let _ = fs::remove_dir_all(tmp_dir);
    fs::create_dir_all(tmp_dir).unwrap();

    let storage = SolarStorage::new(tmp_dir);

    let metric = SolarSecondMetrics {
        timestamp: 1790510535, // 2026-09-28T02:42:15 UTC (approx 11:42:15 JST)
        total_power_db: -24.3,
        baseline_median_db: -38.5,
        snr_db: 14.2,
        is_burst: true,
        spectrum_db: vec![-30.0; 1024],
        sun_az_deg: 152.4,
        sun_el_deg: 52.1,
    };

    let log_path = storage.record_metric(&metric).expect("record_metric should succeed");
    assert!(log_path.exists());

    // 1行読み込んで検証
    let content = fs::read_to_string(&log_path).unwrap();
    let lines: Vec<&str> = content.lines().collect();
    assert_eq!(lines.len(), 1);

    let parsed: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(parsed["timestamp"], 1790510535);
    assert_eq!(parsed["is_burst"], true);
    assert!((parsed["snr_db"].as_f64().unwrap() - 14.2).abs() < 1e-4);

    let _ = fs::remove_dir_all(tmp_dir);
}

#[test]
fn test_solar_storage_event_waterfall() {
    let tmp_dir = Path::new("target/tmp_test_solar_storage_wf");
    let _ = fs::remove_dir_all(tmp_dir);
    fs::create_dir_all(tmp_dir).unwrap();

    let storage = SolarStorage::new(tmp_dir);
    let dummy_spectra = vec![vec![-40.0; 1024]; 30];

    let png_path = storage.save_event_waterfall(&dummy_spectra, 1790510535).expect("save_event_waterfall should succeed");
    assert!(png_path.exists());
    assert!(png_path.to_str().unwrap().contains("events"));

    let _ = fs::remove_dir_all(tmp_dir);
}
