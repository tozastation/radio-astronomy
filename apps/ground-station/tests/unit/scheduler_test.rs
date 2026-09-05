use chrono::{NaiveDate, TimeZone, Utc};
use ground_station::orbit::{SatellitePass, SignalType};
use ground_station::scheduler::filter_passes_for_jst_date;

#[test]
fn test_filter_passes_for_jst_date() {
    // 2026-09-05 14:00:00 UTC = 2026-09-05 23:00:00 JST (当日)
    let pass_today = SatellitePass {
        satellite_name: "NOAA 18".to_string(),
        frequency_hz: 137_912_500,
        signal_type: SignalType::Apt,
        aos: Utc.with_ymd_and_hms(2026, 9, 5, 14, 0, 0).unwrap(),
        los: Utc.with_ymd_and_hms(2026, 9, 5, 14, 10, 0).unwrap(),
        max_elevation_deg: 45.0,
        peak_azimuth_deg: 120.0,
    };

    // 2026-09-05 16:00:00 UTC = 2026-09-06 01:00:00 JST (翌日)
    let pass_tomorrow = SatellitePass {
        satellite_name: "Meteor-M N2-4".to_string(),
        frequency_hz: 137_900_000,
        signal_type: SignalType::Lrpt,
        aos: Utc.with_ymd_and_hms(2026, 9, 5, 16, 0, 0).unwrap(),
        los: Utc.with_ymd_and_hms(2026, 9, 5, 16, 12, 0).unwrap(),
        max_elevation_deg: 60.0,
        peak_azimuth_deg: 180.0,
    };

    let target_date = NaiveDate::from_ymd_opt(2026, 9, 5).unwrap();
    let filtered = filter_passes_for_jst_date(&[pass_today.clone(), pass_tomorrow], target_date);

    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].satellite_name, "NOAA 18");
}
