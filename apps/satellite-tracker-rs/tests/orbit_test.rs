use chrono::{TimeZone, Utc};
use satellite_tracker_rs::orbit::{OrbitPredictor, SatellitePosition, PassInfo};

// テスト用 ISS (ZARYA) TLE (有効なチェックサム付き)
const ISS_TLE_LINE1: &str = "1 25544U 98067A   26248.17592762  .00003558  00000-0  72743-4 0  9999";
const ISS_TLE_LINE2: &str = "2 25544  51.6310 264.2007 0005041 108.5564 251.5973 15.48992921584153";
const ISS_FREQ_HZ: f64 = 145_800_000.0; // 145.800 MHz (FM/APRS)

// 東京 (Tokyo) 観測局
const TOKYO_LAT: f64 = 35.6895;
const TOKYO_LON: f64 = 139.6917;
const TOKYO_ALT_M: f64 = 30.0;

#[test]
fn test_is_in_view_north_balcony() {
    let predictor = OrbitPredictor::new(TOKYO_LAT, TOKYO_LON, TOKYO_ALT_M, "NORTH", 10.0);

    // 仰角10度以上、北天(270°〜360° または 0°〜90°)
    assert!(predictor.is_in_view(15.0, 0.0), "真北は視界内");
    assert!(predictor.is_in_view(15.0, 45.0), "北東は視界内");
    assert!(predictor.is_in_view(10.0, 90.0), "真東境界は視界内");
    assert!(predictor.is_in_view(20.0, 270.0), "真西境界は視界内");
    assert!(predictor.is_in_view(30.0, 315.0), "北西は視界内");

    // 視界外 (南天)
    assert!(!predictor.is_in_view(15.0, 180.0), "真南は視界外");
    assert!(!predictor.is_in_view(15.0, 120.0), "東南は視界外");
    assert!(!predictor.is_in_view(15.0, 240.0), "西南は視界外");

    // 仰角不足
    assert!(!predictor.is_in_view(9.9, 0.0), "仰角10度未満は視界外");
    assert!(!predictor.is_in_view(-5.0, 0.0), "地平線下は視界外");
}

#[test]
fn test_calculate_position_and_doppler() {
    let predictor = OrbitPredictor::new(TOKYO_LAT, TOKYO_LON, TOKYO_ALT_M, "NORTH", 10.0);
    let time = Utc.with_ymd_and_hms(2026, 9, 4, 12, 0, 0).unwrap();

    let pos: SatellitePosition = predictor
        .calculate_position("ISS (ZARYA)", ISS_TLE_LINE1, ISS_TLE_LINE2, ISS_FREQ_HZ, time)
        .expect("軌道計算が成功すること");

    // 地平座標・距離の妥当性検証
    assert!(pos.elevation_deg >= -90.0 && pos.elevation_deg <= 90.0);
    assert!(pos.azimuth_deg >= 0.0 && pos.azimuth_deg <= 360.0);
    assert!(pos.range_km > 300.0, "LEO衛星との距離は300km以上");

    // ドップラー偏移の妥当性検証 (145.8MHz における LEO ドップラーは概ね ±5kHz 以内)
    assert!(
        pos.doppler_shift_hz.abs() < 10_000.0,
        "ドップラー偏移が妥当な範囲内 (±10kHz以内) であること: got {} Hz",
        pos.doppler_shift_hz
    );
    // 視線速度の妥当性 (LEO 衛星の相対速度は最大 ±8 km/s 程度)
    assert!(
        pos.radial_velocity_km_s.abs() < 10.0,
        "視線速度が妥当な範囲内であること: got {} km/s",
        pos.radial_velocity_km_s
    );
}

#[test]
fn test_get_next_pass() {
    let predictor = OrbitPredictor::new(TOKYO_LAT, TOKYO_LON, TOKYO_ALT_M, "ALL", 10.0);
    let start_time = Utc.with_ymd_and_hms(2026, 9, 4, 0, 0, 0).unwrap();

    // 48時間探索すれば、LEO 衛星は必ず日本上空を複数回通過する
    let pass: Option<PassInfo> = predictor
        .get_next_pass(
            "ISS (ZARYA)",
            ISS_TLE_LINE1,
            ISS_TLE_LINE2,
            ISS_FREQ_HZ,
            start_time,
            48.0,
            30,
        )
        .expect("パス探索が成功すること");

    assert!(pass.is_some(), "48時間以内にパスが検出されること");
    let p = pass.unwrap();
    assert_eq!(p.satellite_name, "ISS (ZARYA)");
    assert!(p.aos_time < p.los_time, "AOSはLOSより前であること");
    assert!(p.aos_time <= p.tca_time && p.tca_time <= p.los_time, "TCAはAOSとLOSの間であること");
    assert!(p.max_elevation_deg >= 10.0, "最大仰角は10度以上であること");
}
