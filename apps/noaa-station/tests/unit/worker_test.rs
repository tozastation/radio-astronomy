use noaa_station::config::SdrConfig;
use noaa_station::decoder::build_noaa_apt_args;
use noaa_station::receiver::{build_rtl_fm_args, create_wav_header};
use std::path::Path;

#[test]
fn test_command_args_construction() {
    let wav = Path::new("/tmp/test.wav");
    let png = Path::new("/tmp/test.png");
    let sdr = SdrConfig {
        gain: 45.0,
        sample_rate: 60000,
        ppm_error: 0,
    };

    let fm_args = build_rtl_fm_args(137_100_000, &sdr);
    assert!(fm_args.contains(&"-f".to_string()));
    assert!(fm_args.contains(&"137100000".to_string()));
    assert!(fm_args.contains(&"-M".to_string()));
    assert!(fm_args.contains(&"fm".to_string()));
    assert!(fm_args.contains(&"-s".to_string()));
    assert!(fm_args.contains(&"60000".to_string()));
    assert!(fm_args.contains(&"-r".to_string()));
    assert!(fm_args.contains(&"11025".to_string()));
    assert!(fm_args.contains(&"-g".to_string()));
    assert!(fm_args.contains(&"45.0".to_string()));
    assert!(fm_args.contains(&"-E".to_string()));
    assert!(fm_args.contains(&"deemp".to_string()));
    assert_eq!(fm_args.last().unwrap(), "-");

    let apt_args = build_noaa_apt_args(wav, png);
    assert_eq!(apt_args[0], "/tmp/test.wav");
    assert_eq!(apt_args[1], "-o");
    assert_eq!(apt_args[2], "/tmp/test.png");

    // WAV ヘッダ生成の検証 (44バイト, RIFF/WAVE フォーマット)
    let header = create_wav_header(1000);
    assert_eq!(&header[0..4], b"RIFF");
    assert_eq!(&header[8..12], b"WAVE");
    assert_eq!(&header[12..16], b"fmt ");
    assert_eq!(&header[36..40], b"data");
}
