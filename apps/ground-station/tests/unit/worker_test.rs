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
    use std::path::Path;

    let pass = SatellitePass {
        satellite_name: "UmKA-1".to_string(),
        frequency_hz: 437_625_000,
        signal_type: SignalType::CubeSatSstv,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(5),
        max_elevation_deg: 50.0,
        peak_azimuth_deg: 90.0,
    };

    let raw_path = Path::new("tests/fixtures/nonexistent.raw");
    let session_dir = Path::new("tests/fixtures/session");
    let result = DecoderEngine::decode(&pass, raw_path, session_dir).await;
    assert!(result.is_ok());
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
    assert_eq!(tel.status, ground_station::discord::PassStatus::DecodeError);

    let _ = std::fs::remove_dir_all(&session_dir);
}
