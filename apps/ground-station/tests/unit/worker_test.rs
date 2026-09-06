use ground_station::config::SdrConfig;
use ground_station::decoder::build_noaa_apt_args;
use ground_station::receiver::{build_rtl_fm_args, create_wav_header};
use std::path::Path;

#[test]
fn test_rtl_fm_arguments_generation() {
    let sdr = SdrConfig {
        gain: 40.0,
        sample_rate: 60000,
        ppm_error: 0,
    };
    let args = build_rtl_fm_args(137_912_500, &sdr);
    assert!(args.contains(&"-f".to_string()));
    assert!(args.contains(&"137912500".to_string()));
    assert!(args.contains(&"-s".to_string()));
    assert!(args.contains(&"60000".to_string()));
    assert!(args.contains(&"-g".to_string()));
    assert!(args.contains(&"40.0".to_string()));
}

#[test]
fn test_noaa_apt_arguments_generation() {
    let wav = Path::new("data/noaa/test.wav");
    let out = Path::new("data/noaa/test.png");
    let args = build_noaa_apt_args(wav, out);
    assert_eq!(args[0], "data/noaa/test.wav");
    assert_eq!(args[1], "-o");
    assert_eq!(args[2], "data/noaa/test.png");
}

#[test]
fn test_wav_header_generation() {
    let header = create_wav_header(100);
    assert_eq!(&header[0..4], b"RIFF");
    assert_eq!(&header[8..12], b"WAVE");
    assert_eq!(&header[12..16], b"fmt ");
    assert_eq!(&header[36..40], b"data");
    assert_eq!(header.len(), 44);
}

#[test]
fn test_meteor_lrpt_arguments_generation() {
    let sdr = SdrConfig {
        gain: 45.0,
        sample_rate: 240000,
        ppm_error: 0,
    };
    let raw_out = Path::new("data/noaa/meteor.raw");
    let sdr_args = ground_station::receiver::build_rtl_sdr_args(137_900_000, &sdr, raw_out);
    assert!(sdr_args.contains(&"-f".to_string()));
    assert!(sdr_args.contains(&"137900000".to_string()));
    assert!(sdr_args.contains(&"-s".to_string()));
    assert!(sdr_args.contains(&"240000".to_string()));
    assert!(sdr_args.contains(&"-g".to_string()));
    assert!(sdr_args.contains(&"45.0".to_string()));

    let out_dir = Path::new("data/noaa");
    let satdump_args = ground_station::decoder::build_satdump_lrpt_args(raw_out, out_dir);
    assert_eq!(satdump_args[0], "meteor_m2-x_lrpt_80k");
    assert_eq!(satdump_args[1], "baseband");
    assert_eq!(satdump_args[2], "data/noaa/meteor.raw");
    assert_eq!(satdump_args[3], "data/noaa");
    assert!(satdump_args.contains(&"--samplerate".to_string()));
    assert!(satdump_args.contains(&"240000".to_string()));
    assert!(satdump_args.contains(&"--baseband_format".to_string()));
    assert!(satdump_args.contains(&"cu8".to_string()));
}

#[tokio::test]
async fn test_decoder_engine_routing() {
    use chrono::{Duration, Utc};
    use ground_station::decoder::DecoderEngine;
    use ground_station::orbit::{SatellitePass, SignalType};

    let pass = SatellitePass {
        satellite_name: "UmKA-1".to_string(),
        frequency_hz: 437_625_000,
        signal_type: SignalType::CubeSatSstv,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 50.0,
        peak_azimuth_deg: 90.0,
    };

    let test_dir = std::env::temp_dir().join("test_ground_station_cubesat_routing");
    let _ = std::fs::remove_dir_all(&test_dir);
    std::fs::create_dir_all(&test_dir).unwrap();
    let raw_path = test_dir.join("raw.wav");
    std::fs::write(&raw_path, b"dummy audio raw").unwrap();

    let result = DecoderEngine::decode(&pass, &raw_path, &test_dir).await;
    assert!(result.is_ok());
    let res = result.unwrap();
    let tel = res.telemetry.expect("telemetry が存在すること");
    assert_eq!(tel.status, ground_station::discord::PassStatus::RawPreserved);
    assert_eq!(tel.snr_db, None);
    assert!(tel.housekeeping.iter().any(|(k, v)| k == "生データ" && v.contains("保全完了")));
    assert!(tel.housekeeping.iter().any(|(k, v)| k == "デコード状況" && (v.contains("未導入") || v.contains("スキップ"))));
    assert!(!tel.housekeeping.iter().any(|(_, v)| v == "復調成功"));

    let _ = std::fs::remove_dir_all(&test_dir);
}

#[test]
fn test_find_best_image_in_dir_recursive() {
    use std::fs::{create_dir_all, remove_dir_all, File};
    use std::io::Write;

    let test_dir = std::env::temp_dir().join("test_ground_station_find_image");
    let _ = remove_dir_all(&test_dir);
    create_dir_all(test_dir.join("MSU-MR")).unwrap();

    let small_img = test_dir.join("small.png");
    let mut f1 = File::create(&small_img).unwrap();
    f1.write_all(&[0u8; 100]).unwrap();

    let large_img = test_dir.join("MSU-MR").join("large.jpg");
    let mut f2 = File::create(&large_img).unwrap();
    f2.write_all(&[0u8; 5000]).unwrap();

    let best = ground_station::decoder::find_best_image_in_dir(&test_dir);
    assert_eq!(best, Some(large_img));

    let _ = remove_dir_all(&test_dir);
}

#[test]
fn test_build_satdump_lrpt_args_with_pipeline() {
    let raw = Path::new("test.raw");
    let out = Path::new("out");
    let args = ground_station::decoder::build_satdump_lrpt_args_with_pipeline("meteor_m2-x_lrpt", raw, out);
    assert_eq!(args[0], "meteor_m2-x_lrpt");
    assert_eq!(args[1], "baseband");
}

#[tokio::test]
async fn test_meteor_lrpt_decoder_routing_and_fallback() {
    use chrono::{Duration, Utc};
    use ground_station::decoder::DecoderEngine;
    use ground_station::orbit::{SatellitePass, SignalType};

    let pass = SatellitePass {
        satellite_name: "Meteor-M N2-4".to_string(),
        frequency_hz: 137_900_000,
        signal_type: SignalType::Lrpt,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 38.0,
        peak_azimuth_deg: 240.0,
    };

    let session_dir = std::env::temp_dir().join("test_ground_station_meteor_lrpt");
    let _ = std::fs::remove_dir_all(&session_dir);
    std::fs::create_dir_all(&session_dir).unwrap();

    let raw_path = session_dir.join("raw.u8");
    std::fs::write(&raw_path, b"test raw iq").unwrap();

    let result = DecoderEngine::decode(&pass, &raw_path, &session_dir).await;
    assert!(result.is_ok());
    let res = result.unwrap();
    assert!(res.telemetry_summary.is_some());
    assert!(res.telemetry.is_some());
    let tel = res.telemetry.unwrap();
    assert_eq!(tel.status, ground_station::discord::PassStatus::RawPreserved);
    assert_eq!(tel.snr_db, None);

    let _ = std::fs::remove_dir_all(&session_dir);
}

#[tokio::test]
async fn test_decoder_engine_apt_audio_path_setting() {
    use chrono::{Duration, Utc};
    use ground_station::decoder::DecoderEngine;
    use ground_station::orbit::{SatellitePass, SignalType};

    let pass = SatellitePass {
        satellite_name: "NOAA 18".to_string(),
        frequency_hz: 137_912_500,
        signal_type: SignalType::Apt,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 52.0,
        peak_azimuth_deg: 120.0,
    };

    let session_dir = std::env::temp_dir().join("test_ground_station_apt_audio");
    let _ = std::fs::remove_dir_all(&session_dir);
    std::fs::create_dir_all(&session_dir).unwrap();

    let wav_path = session_dir.join("audio.wav");
    std::fs::write(&wav_path, b"dummy wav header").unwrap();

    let result = DecoderEngine::decode(&pass, &wav_path, &session_dir).await;
    assert!(result.is_ok());
    let res = result.unwrap();
    assert_eq!(res.audio_path, Some(wav_path));
    let tel = res.telemetry.unwrap();
    assert_eq!(tel.status, ground_station::discord::PassStatus::RawPreserved);
    assert_eq!(tel.snr_db, None);

    let _ = std::fs::remove_dir_all(&session_dir);
}

#[tokio::test]
async fn test_decoder_engine_fm_repeater_audio_path_and_telemetry() {
    use chrono::{Duration, Utc};
    use ground_station::decoder::DecoderEngine;
    use ground_station::orbit::{SatellitePass, SignalType};

    let pass = SatellitePass {
        satellite_name: "SO-50".to_string(),
        frequency_hz: 436_795_000,
        signal_type: SignalType::FmRepeater,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 70.1,
        peak_azimuth_deg: 90.0,
    };

    let session_dir = std::env::temp_dir().join("test_ground_station_fm_repeater");
    let _ = std::fs::remove_dir_all(&session_dir);
    std::fs::create_dir_all(&session_dir).unwrap();

    let wav_path = session_dir.join("raw.wav");
    std::fs::write(&wav_path, b"dummy wav header").unwrap();

    let result = DecoderEngine::decode(&pass, &wav_path, &session_dir).await;
    assert!(result.is_ok());
    let res = result.unwrap();
    assert_eq!(res.audio_path, Some(wav_path));
    assert!(res.image_path.is_none());
    assert!(res.telemetry.is_some());
    let tel = res.telemetry.unwrap();
    assert_eq!(tel.status, ground_station::discord::PassStatus::AudioRecorded);
    assert_eq!(tel.snr_db, None);
    assert!(tel.lines_or_packets.as_deref().unwrap_or("").contains("FM 音声復調完了"));
    let hk_map: std::collections::HashMap<_, _> = tel.housekeeping.into_iter().collect();
    assert_eq!(hk_map.get("中継方式").map(|s| s.as_str()), Some("FM ボイストランスポンダー"));
    assert!(hk_map.contains_key("アクセス仕様"));

    let _ = std::fs::remove_dir_all(&session_dir);
}

#[test]
fn test_satdump_pipeline_for_all_target_satellites() {
    use ground_station::decoder::satdump_pipeline_for_satellite;

    assert_eq!(satdump_pipeline_for_satellite("UmKA-1"), Some("umka_1_dump"));
    assert_eq!(satdump_pipeline_for_satellite("RS40S"), Some("umka_1_dump"));
    assert_eq!(satdump_pipeline_for_satellite("FUNcube-1"), Some("funcube_1"));
    assert_eq!(satdump_pipeline_for_satellite("AO-73"), Some("funcube_1"));
    assert_eq!(satdump_pipeline_for_satellite("SONATE-2"), Some("sonate_2"));
    assert_eq!(satdump_pipeline_for_satellite("CAS-4A"), Some("cas_4a"));
    assert_eq!(satdump_pipeline_for_satellite("Meteor-M N2-4"), Some("meteor_m2-x_lrpt_80k"));
    assert_eq!(satdump_pipeline_for_satellite("ISS (ZARYA)"), Some("iss_sstv"));
    assert_eq!(satdump_pipeline_for_satellite("UNKNOWN_SAT"), None);
}

#[test]
fn test_build_satdump_cubesat_args_baseband_and_audio() {
    use ground_station::decoder::build_satdump_cubesat_args;

    // baseband モード (.u8)
    let raw_file = Path::new("data/session/raw.u8");
    let out_dir = Path::new("data/session");
    let args = build_satdump_cubesat_args("umka_1_dump", raw_file, out_dir, 240000);
    assert_eq!(args[0], "umka_1_dump");
    assert_eq!(args[1], "baseband");
    assert_eq!(args[2], "data/session/raw.u8");
    assert_eq!(args[3], "data/session");
    assert!(args.contains(&"--samplerate".to_string()));
    assert!(args.contains(&"240000".to_string()));
    assert!(args.contains(&"--baseband_format".to_string()));
    assert!(args.contains(&"cu8".to_string()));

    // audio モード (.wav)
    let wav_file = Path::new("data/session/raw.wav");
    let args_wav = build_satdump_cubesat_args("umka_1_dump", wav_file, out_dir, 60000);
    assert_eq!(args_wav[0], "umka_1_dump");
    assert_eq!(args_wav[1], "audio");
    assert_eq!(args_wav[2], "data/session/raw.wav");
    assert_eq!(args_wav[3], "data/session");
}

#[test]
fn test_extract_telemetry_from_dir_parses_json() {
    use ground_station::decoder::extract_telemetry_from_dir;
    use std::io::Write;

    let test_dir = std::env::temp_dir().join("test_ground_station_extract_tlm");
    let _ = std::fs::remove_dir_all(&test_dir);
    std::fs::create_dir_all(&test_dir).unwrap();

    let json_path = test_dir.join("telemetry.json");
    let mut f = std::fs::File::create(&json_path).unwrap();
    write!(
        f,
        r#"{{
            "battery_voltage": 3.98,
            "temp_c": 15.4,
            "transmitter_on": true,
            "callsign": "RS40S",
            "frame_id": 142
        }}"#
    ).unwrap();

    let tlm = extract_telemetry_from_dir(&test_dir);
    assert!(tlm.is_some());
    let items = tlm.unwrap();
    let map: std::collections::HashMap<_, _> = items.into_iter().collect();

    assert_eq!(map.get("battery_voltage").map(|s| s.as_str()), Some("3.98"));
    assert_eq!(map.get("temp_c").map(|s| s.as_str()), Some("15.4"));
    assert_eq!(map.get("transmitter_on").map(|s| s.as_str()), Some("true"));
    assert_eq!(map.get("callsign").map(|s| s.as_str()), Some("RS40S"));
    assert_eq!(map.get("frame_id").map(|s| s.as_str()), Some("142"));

    let _ = std::fs::remove_dir_all(&test_dir);
}
