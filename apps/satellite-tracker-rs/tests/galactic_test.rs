use chrono::{TimeZone, Utc};
use satellite_tracker_rs::galactic::{
    accumulate_welch_psd, calibrate_frequency_switched_spectrum, compute_galactic_pointing,
    compute_lsr_velocity_correction, HI_REST_FREQ_HZ,
};

#[test]
fn test_galactic_pointing_normalizes_angles_and_detects_plane_vs_halo() {
    let dt = Utc.with_ymd_and_hms(2026, 10, 28, 12, 0, 0).unwrap();
    // 北東 (Az=30°, El=35°) はベランダ開口ウィンドウ内であり、天の川銀河面とハローの両方を通過する
    let p = compute_galactic_pointing(35.6895, 139.6917, 30.0, 35.0, dt);
    assert!((0.0..360.0).contains(&p.ra_deg));
    assert!((-90.0..=90.0).contains(&p.dec_deg));
    assert!((0.0..360.0).contains(&p.gal_l_deg));
    assert!((-90.0..=90.0).contains(&p.gal_b_deg));
    assert!(!p.region_label.is_empty());

    // 24時間（1時間刻み）回したとき、天の川銀河面 (|b| <= 15°) と銀河ハロー (|b| > 30°) の両方を通過すること
    let mut saw_plane = false;
    let mut saw_halo = false;
    for h in 0..24 {
        let t = dt + chrono::Duration::hours(h);
        let pt = compute_galactic_pointing(35.6895, 139.6917, 30.0, 35.0, t);
        if pt.gal_b_deg.abs() <= 15.0 {
            saw_plane = true;
            assert_eq!(pt.sky_type, "WARM_GALACTIC_PLANE");
        }
        if pt.gal_b_deg.abs() > 30.0 {
            saw_halo = true;
            assert_eq!(pt.sky_type, "COLD_HALO");
        }
    }
    assert!(saw_plane, "North balcony drift scan must cross the Galactic plane");
    assert!(saw_halo, "North balcony drift scan must cross the Galactic halo");
}

#[test]
fn test_frequency_switching_cancels_bandpass_and_handles_zero_off_power() {
    let fft_size = 1024;
    let sample_rate = 2_048_000.0;
    // パラボラ型のベースライン歪み + 中心付近の模擬21cmガウス信号
    let mut psd_off = vec![0.0; fft_size];
    let mut psd_on = vec![0.0; fft_size];
    for i in 0..fft_size {
        let x = (i as f64 - 512.0) / 512.0;
        let bandpass = 1.0 - 0.3 * x * x;
        psd_off[i] = bandpass;
        // ビン 532 (+40 kHz ≈ -8.4 km/s) に 10% の水素輝線ピークを重畳
        let line = 0.10 * (-((i as f64 - 532.0) / 6.0).powi(2)).exp();
        psd_on[i] = bandpass * (1.0 + line);
    }
    // ゼロ除算耐性の検証: 端の1ビンを 0.0 に設定
    psd_off[0] = 0.0;

    let (spec, metrics) = calibrate_frequency_switched_spectrum(
        &psd_on,
        &psd_off,
        sample_rate,
        HI_REST_FREQ_HZ,
        0.0,
        300.0,
    );
    assert!(!spec.v_lsr_km_s.is_empty());
    assert_eq!(spec.v_lsr_km_s.len(), spec.relative_temp_k.len());
    assert!(spec.relative_temp_k.iter().all(|v| v.is_finite()));
    // 10% * 300K ≈ 30K 付近のピークが検出されること
    let max_temp = spec.relative_temp_k.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    assert!(max_temp > 15.0 && max_temp < 45.0, "max_temp was {}", max_temp);
    assert!((metrics.peak_v_lsr_km_s - (-8.4)).abs() < 3.0);
}

#[test]
fn test_welch_psd_and_lsr_velocity_correction() {
    let dt = Utc.with_ymd_and_hms(2026, 10, 28, 12, 0, 0).unwrap();
    let lsr_corr = compute_lsr_velocity_correction(120.0, 5.0, 45.0, 60.0, dt);
    assert!(lsr_corr.is_finite());
    assert!(lsr_corr.abs() < 50.0);

    // 単純な定常白色サンプルの Welch PSD
    let samples: Vec<(f32, f32)> = (0..2048).map(|i| (0.1 * (i as f32).sin(), 0.1 * (i as f32).cos())).collect();
    let psd = accumulate_welch_psd(&samples, 1024);
    assert_eq!(psd.len(), 1024);
    assert!(psd.iter().all(|p| *p >= 0.0 && p.is_finite()));
}
