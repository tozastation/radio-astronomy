use ground_station::config::Config;

#[test]
fn test_solar_config_full_parsing() {
    let toml_str = r#"
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
output_dir = "data"

[solar]
enabled = true
center_freq = 70.0e6
sample_rate = 2.4e6
gain = 28.0
fft_size = 1024
integration_secs = 1
threshold_sigma = 4.0
min_jump_db = 3.0
cooldown_secs = 180
mode = "daylight_only"
min_elevation = 5.0
data_dir = "data/solar"
"#;

    let config: Config = toml::from_str(toml_str).expect("Failed to parse config with solar section");
    assert!(config.solar.is_some());
    let solar = config.solar.unwrap();
    assert!(solar.enabled);
    assert_eq!(solar.center_freq, 70.0e6);
    assert_eq!(solar.sample_rate, 2.4e6);
    assert_eq!(solar.gain, 28.0);
    assert_eq!(solar.fft_size, 1024);
    assert_eq!(solar.integration_secs, 1);
    assert_eq!(solar.threshold_sigma, 4.0);
    assert_eq!(solar.min_jump_db, 3.0);
    assert_eq!(solar.cooldown_secs, 180);
    assert_eq!(solar.mode, "daylight_only");
    assert_eq!(solar.min_elevation, 5.0);
    assert_eq!(solar.data_dir, "data/solar");
}

#[test]
fn test_solar_config_defaults() {
    let toml_str = r#"
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
output_dir = "data"

[solar]
enabled = true
"#;

    let config: Config = toml::from_str(toml_str).expect("Failed to parse minimal solar config");
    assert!(config.solar.is_some());
    let solar = config.solar.unwrap();
    assert!(solar.enabled);
    assert_eq!(solar.center_freq, 70.0e6);
    assert_eq!(solar.sample_rate, 2.4e6);
    assert_eq!(solar.gain, 28.0);
    assert_eq!(solar.fft_size, 1024);
    assert_eq!(solar.integration_secs, 1);
    assert_eq!(solar.threshold_sigma, 4.0);
    assert_eq!(solar.min_jump_db, 3.0);
    assert_eq!(solar.cooldown_secs, 180);
    assert_eq!(solar.mode, "daylight_only");
    assert_eq!(solar.min_elevation, 5.0);
    assert_eq!(solar.data_dir, "data/solar");
}

#[test]
fn test_solar_config_optional() {
    let toml_str = r#"
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
output_dir = "data"
"#;

    let config: Config = toml::from_str(toml_str).expect("Failed to parse config without solar");
    assert!(config.solar.is_none());
}
