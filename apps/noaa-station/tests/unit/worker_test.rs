use noaa_station::decoder::build_noaa_apt_args;
use noaa_station::receiver::build_rtl_fm_args;
use std::path::Path;

#[test]
fn test_command_args_construction() {
    let wav = Path::new("/tmp/test.wav");
    let png = Path::new("/tmp/test.png");

    let fm_args = build_rtl_fm_args(137_100_000, wav);
    assert!(fm_args.contains(&"-f".to_string()));
    assert!(fm_args.contains(&"137100000".to_string()));
    assert!(fm_args.contains(&"-M".to_string()));
    assert!(fm_args.contains(&"wfm".to_string()));
    assert!(fm_args.contains(&"-s".to_string()));
    assert!(fm_args.contains(&"60k".to_string()));
    assert!(fm_args.contains(&"-r".to_string()));
    assert!(fm_args.contains(&"11025".to_string()));
    assert_eq!(fm_args.last().unwrap(), "/tmp/test.wav");

    let apt_args = build_noaa_apt_args(wav, png);
    assert_eq!(apt_args[0], "/tmp/test.wav");
    assert_eq!(apt_args[1], "-o");
    assert_eq!(apt_args[2], "/tmp/test.png");
}
