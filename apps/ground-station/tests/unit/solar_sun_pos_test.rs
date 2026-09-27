use chrono::{TimeZone, Utc};
use ground_station::solar::sun_pos::calculate_sun_position;

#[test]
fn test_sun_position_tokyo_noon_equinox() {
    // 2026-03-20 02:45 UTC (approx 11:45 JST, solar noon in Tokyo lat: 35.68, lon: 139.76)
    let time = Utc.with_ymd_and_hms(2026, 3, 20, 2, 45, 0).unwrap();
    let pos = calculate_sun_position(35.68, 139.76, time);

    // At solar noon near spring equinox in Tokyo, Sun should be approximately South (Az ~ 180°) and El ~ 54° (90 - 35.68)
    assert!(
        pos.elevation_deg > 45.0 && pos.elevation_deg < 60.0,
        "Expected elevation near 54°, got {}",
        pos.elevation_deg
    );
    assert!(
        pos.azimuth_deg > 160.0 && pos.azimuth_deg < 200.0,
        "Expected azimuth near 180°, got {}",
        pos.azimuth_deg
    );
}

#[test]
fn test_sun_position_tokyo_summer_solstice_noon() {
    // 2026-06-21 02:45 UTC (summer solstice in Tokyo)
    // Elevation should reach approximately 90 - 35.68 + 23.44 = 77.76°
    let time = Utc.with_ymd_and_hms(2026, 6, 21, 2, 45, 0).unwrap();
    let pos = calculate_sun_position(35.68, 139.76, time);

    assert!(
        pos.elevation_deg > 70.0 && pos.elevation_deg < 85.0,
        "Expected summer solstice elevation near 78°, got {}",
        pos.elevation_deg
    );
}

#[test]
fn test_sun_position_midnight() {
    // Midnight in Tokyo (15:00 UTC)
    let time = Utc.with_ymd_and_hms(2026, 3, 20, 15, 0, 0).unwrap();
    let pos = calculate_sun_position(35.68, 139.76, time);
    // Sun is well below the horizon at midnight
    assert!(
        pos.elevation_deg < -30.0,
        "Expected midnight elevation < -30°, got {}",
        pos.elevation_deg
    );
}
