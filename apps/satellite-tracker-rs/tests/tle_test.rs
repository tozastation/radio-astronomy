use satellite_tracker_rs::orbit::parse_tle_epoch;
use satellite_tracker_rs::tle::{is_tle_cache_fresh, load_runtime_targets, DEFAULT_TARGETS};
use sgp4::Elements;
use std::fs;

#[test]
fn test_default_targets_have_valid_recent_tles() {
    assert_eq!(DEFAULT_TARGETS.len(), 4);
    for t in DEFAULT_TARGETS {
        let parsed = Elements::from_tle(
            Some(t.name.to_string()),
            t.line1.as_bytes(),
            t.line2.as_bytes(),
        );
        assert!(parsed.is_ok(), "Invalid DEFAULT_TARGET TLE for {}", t.name);
        let ep = parse_tle_epoch(t.line1);
        assert!(
            ep >= 2026319.0,
            "Expected fresh epoch >= 2026319.0 for {}, got {}",
            t.name,
            ep
        );
    }
}

#[test]
fn test_load_runtime_targets_prefers_latest_epoch_over_stale_spool_file() {
    let tmp = std::env::temp_dir().join(format!("sat_tle_test_{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    fs::create_dir_all(&tmp).unwrap();

    // 古い Epoch (26200) を含むキャッシュファイルを配置
    let stale_cache = "\
ISS (ZARYA)
1 25544U 98067A   26200.00000000  .00009575  00000+0  18188-3 0  9994
2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263
";
    fs::write(tmp.join("tles.txt"), stale_cache).unwrap();

    let targets = load_runtime_targets(&tmp);
    let iss = targets.iter().find(|t| t.norad_id == 25544).unwrap();
    // DEFAULT_TARGETS (26319) より古いキャッシュは上書き採用されないこと
    assert!(parse_tle_epoch(&iss.line1) >= 2026319.0);

    // 新しい Epoch (26350) のキャッシュファイルなら優先採用されること
    let newer_cache = "\
ISS (ZARYA)
1 25544U 98067A   26350.50000000  .00009214  00000+0  17269-3 0  9994
2 25544  51.6321 269.2481 0007362  30.2971 329.8442 15.49402183595279
";
    fs::write(tmp.join("tles.txt"), newer_cache).unwrap();
    let targets_new = load_runtime_targets(&tmp);
    let iss_new = targets_new.iter().find(|t| t.norad_id == 25544).unwrap();
    assert!((parse_tle_epoch(&iss_new.line1) - 2026350.5).abs() < 1e-6);
    assert!(is_tle_cache_fresh(&tmp.join("tles.txt"), 3600));

    let _ = fs::remove_dir_all(&tmp);
}
