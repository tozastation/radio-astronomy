use chrono::Utc;
use noaa_station::config::ObserverConfig;
use noaa_station::orbit::{azimuth_to_direction, OrbitPredictor, SatelliteInfo};

#[test]
fn test_tle_parsing_and_pass_prediction() {
    // 正しいチェックサムを持つ NOAA 19 の実 TLE データ
    let tle_line1 = "1 33591U 09005A   26246.83578895  .00000001  00000+0  24547-4 0  9994";
    let tle_line2 = "2 33591  98.9458 317.7433 0014104 163.8300 196.3324 14.13484440905594";
    let sat = SatelliteInfo {
        name: "NOAA 19".to_string(),
        norad_id: 33591,
        frequency_hz: 137_100_000,
        line1: tle_line1.to_string(),
        line2: tle_line2.to_string(),
    };

    let observer = ObserverConfig {
        latitude: 35.6895,
        longitude: 139.6917,
        altitude_m: 40.0,
    };

    // 現在時刻から今後24時間をスキャン
    let base_time = Utc::now();
    let passes = OrbitPredictor::predict_passes_for_satellite(&sat, &observer, base_time, 24, 15.0)
        .expect("パス計算に失敗しました");

    // 24時間あれば極軌道衛星は最低1回以上日本上空を通過する
    assert!(!passes.is_empty(), "24時間以内に少なくとも1回のパスが検出される必要があります");
    for pass in &passes {
        assert!(pass.max_elevation_deg >= 15.0);
        assert!(pass.peak_azimuth_deg >= 0.0 && pass.peak_azimuth_deg < 360.0);
        assert!(pass.los > pass.aos);
        assert_eq!(pass.frequency_hz, 137_100_000);
        assert_eq!(pass.satellite_name, "NOAA 19");
    }
}

#[test]
fn test_azimuth_to_direction() {
    assert_eq!(azimuth_to_direction(0.0), "北 (N)");
    assert_eq!(azimuth_to_direction(360.0), "北 (N)");
    assert_eq!(azimuth_to_direction(45.0), "北東 (NE)");
    assert_eq!(azimuth_to_direction(90.0), "東 (E)");
    assert_eq!(azimuth_to_direction(135.0), "南東 (SE)");
    assert_eq!(azimuth_to_direction(180.0), "南 (S)");
    assert_eq!(azimuth_to_direction(225.0), "南西 (SW)");
    assert_eq!(azimuth_to_direction(270.0), "西 (W)");
    assert_eq!(azimuth_to_direction(315.0), "北西 (NW)");
}

