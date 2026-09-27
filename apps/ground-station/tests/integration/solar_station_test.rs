use ground_station::config::Config;
use ground_station::solar::manager::SolarStationManager;
use std::fs;
use std::path::Path;

#[tokio::test]
async fn test_solar_station_pipeline_simulated() {
    let tmp_data_dir = "target/tmp_test_solar_station_integ";
    let _ = fs::remove_dir_all(tmp_data_dir);
    fs::create_dir_all(tmp_data_dir).unwrap();

    let toml_str = format!(r#"
[observer]
latitude = 35.68
longitude = 139.76
altitude_m = 50.0

[scheduler]
min_elevation_deg = 15.0
pre_alert_minutes = 3.0
tle_update_interval_hours = 24

[voicevox]
enabled = false
host = "http://localhost:50021"
speaker_id = 3

[storage]
output_dir = "{}"

[solar]
enabled = true
center_freq = 70.0e6
sample_rate = 2.4e6
gain = 28.0
fft_size = 1024
integration_secs = 1
threshold_sigma = 3.0
min_jump_db = 3.0
cooldown_secs = 60
mode = "continuous_24h"
min_elevation = 5.0
data_dir = "{}/solar"
"#, tmp_data_dir, tmp_data_dir);

    let config: Config = toml::from_str(&toml_str).unwrap();
    let manager = SolarStationManager::new(config);

    // dry_run モードで5秒間シミュレーション実行
    let result = manager.run_simulation_seconds(3).await;
    assert!(result.is_ok(), "run_simulation_seconds should succeed: {:?}", result);

    // 1秒メトリクスファイルが生成されていることを確認
    let solar_dir = Path::new(tmp_data_dir).join("solar");
    assert!(solar_dir.exists(), "solar data directory must be created");

    let entries: Vec<_> = fs::read_dir(&solar_dir).unwrap().collect();
    let jsonl_found = entries.iter().any(|e| {
        let name = e.as_ref().unwrap().file_name().to_string_lossy().to_string();
        name.starts_with("metrics_") && name.ends_with(".jsonl")
    });
    assert!(jsonl_found, "metrics_YYYY-MM-DD.jsonl must be created during simulation");

    let _ = fs::remove_dir_all(tmp_data_dir);
}
