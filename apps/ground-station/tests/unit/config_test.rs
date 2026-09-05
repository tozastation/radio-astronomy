use ground_station::config::Config;

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

#[test]
fn test_cubesat_and_iss_config_parsing() {
    let toml_str = r#"
        [observer]
        latitude = 35.7903
        longitude = 139.2584
        altitude_m = 200.0

        [scheduler]
        min_elevation_deg = 20.0
        pre_alert_minutes = 3.0
        tle_update_interval_hours = 24

        [voicevox]
        enabled = false
        host = "http://localhost:50021"
        speaker_id = 3

        [storage]
        output_dir = "data/noaa"

        [satellites.meteor]
        enabled = true

        [satellites.cubesats]
        enabled = true
        targets = [
            { name = "FUNcube-1", norad_id = 39444, freq = 145935000, type = "BpskTelemetry" },
            { name = "UmKA-1", norad_id = 57172, freq = 437625000, type = "CameraSstv" }
        ]

        [satellites.iss]
        enabled = true
        norad_id = 25544
        freq = 145800000
    "#;

    let config = Config::from_str(toml_str).expect("パース成功");
    assert!(config.satellites.meteor.enabled);
    assert!(config.satellites.cubesats.enabled);
    assert_eq!(config.satellites.cubesats.targets.len(), 2);
    assert_eq!(config.satellites.cubesats.targets[0].name, "FUNcube-1");
    assert_eq!(config.satellites.cubesats.targets[0].r#type, "BpskTelemetry");
    assert_eq!(config.satellites.cubesats.targets[1].name, "UmKA-1");
    assert_eq!(config.satellites.cubesats.targets[1].freq, 437625000);
    assert!(config.satellites.iss.enabled);
    assert_eq!(config.satellites.iss.freq, 145800000);
    assert_eq!(config.satellites.iss.norad_id, 25544);
}

#[test]
fn test_load_real_config_file() {
    let config = Config::load_from_file("config.toml")
        .or_else(|_| Config::load_from_file("apps/ground-station/config.toml"))
        .expect("実ファイルの読み込み成功");
    assert!(config.satellites.meteor.enabled);
    assert!(config.satellites.cubesats.enabled);
    assert_eq!(config.satellites.cubesats.targets.len(), 5);
    assert_eq!(config.satellites.cubesats.targets[0].name, "FUNcube-1");
    assert!(config.satellites.iss.enabled);
}

#[test]
fn test_daily_schedule_config_defaults() {
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
        enabled = false
        host = "http://localhost:50021"
        speaker_id = 3

        [storage]
        output_dir = "data/noaa"
    "#;
    let config = Config::from_str(toml_str).expect("パース成功すること");
    assert!(config.scheduler.daily_schedule_enabled);
    assert_eq!(config.scheduler.daily_schedule_hour_jst, 7);
    assert_eq!(config.scheduler.daily_schedule_minute_jst, 0);
    assert!(!config.scheduler.daily_schedule_send_on_startup);
}

