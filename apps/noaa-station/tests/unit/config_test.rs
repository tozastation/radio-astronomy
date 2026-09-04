use noaa_station::config::Config;

#[test]
fn test_load_default_config() {
    let toml_str = r#"
        [observer]
        latitude = 35.6895
        longitude = 139.6917
        altitude_m = 40.0

        [scheduler]
        min_elevation_deg = 20.0
        pre_alert_minutes = 3.0
        tle_update_interval_hours = 24

        [voicevox]
        enabled = true
        host = "http://localhost:50021"
        speaker_id = 3

        [storage]
        output_dir = "data/noaa"
    "#;
    let config = Config::from_str(toml_str).expect("Failed to parse config");
    assert_eq!(config.observer.latitude, 35.6895);
    assert_eq!(config.scheduler.min_elevation_deg, 20.0);
    assert_eq!(config.voicevox.speaker_id, 3);
    assert_eq!(config.storage.output_dir, "data/noaa");
    assert!(config.satellites.enable_meteor);
    assert!(!config.satellites.enable_noaa);
}
