use ground_station::config::ObserverConfig;
use ground_station::orbit::{azimuth_to_direction, OrbitPredictor, SatelliteInfo};

#[test]
fn test_tle_parsing_and_pass_prediction() {
    // CelesTrakから今取得した最新 TLE データ
    let tle_line1 = "1 33591U 09005A   26247.26050863 -.00000003  00000+0  22278-4 0  9994";
    let tle_line2 = "2 33591  98.9457 318.1680 0014124 162.6873 197.4784 14.13484468905655";
    let _sat = SatelliteInfo {
        name: "NOAA 19".to_string(),
        norad_id: 33591,
        frequency_hz: 137_100_000,
        signal_type: ground_station::orbit::SignalType::Apt,
        line1: tle_line1.to_string(),
        line2: tle_line2.to_string(),
    };

    let observer = ObserverConfig {
        latitude: 35.7903,
        longitude: 139.2584,
        altitude_m: 200.0,
    };

    // 2026-09-04 00:00:00 JST (2026-09-03 15:00:00 UTC) から 24時間をスキャン
    use chrono::TimeZone;
    let start_of_day = chrono::Utc.with_ymd_and_hms(2026, 9, 3, 15, 0, 0).unwrap();

    let satellites_config = ground_station::config::SatellitesConfig {
        noaa: ground_station::config::NoaaConfig { enabled: true },
        enable_meteor: true,
        enable_noaa: true,
        ..Default::default()
    };

    // 最新TLEの取得 (ネットワーク疎通またはフォールバック)
    let http_client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .unwrap_or_else(|_| reqwest::Client::new());
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let mut satellites = runtime.block_on(async {
        ground_station::orbit::fetch_weather_tles(&http_client, &satellites_config).await.unwrap_or_default()
    });
    if satellites.is_empty() {
        satellites.push(_sat);
    }

    let passes = OrbitPredictor::predict_all_passes(&satellites, &observer, start_of_day, 24, 15.0)
        .expect("パス計算に失敗しました");

    println!("\n========================================================================================================");
    println!("📅 本日 (2026年9月4日 00:00 〜 24:00 JST) の NOAA 気象衛星 真の通過スケジュール一覧");
    println!("========================================================================================================");
    println!("{:<10} | {:<12} | {:<20} | {:<20} | {:<20}", "衛星名", "周波数", "通過開始 (AOS / JST)", "通過終了 (LOS / JST)", "最大仰角 (ピーク方位)");
    println!("--------------------------------------------------------------------------------------------------------");
    for p in &passes {
        let aos_jst: chrono::DateTime<chrono::Local> = chrono::DateTime::from(p.aos);
        let los_jst: chrono::DateTime<chrono::Local> = chrono::DateTime::from(p.los);
        let freq_mhz = p.frequency_hz as f64 / 1_000_000.0;
        let dir = azimuth_to_direction(p.peak_azimuth_deg);
        println!(
            "{:<10} | {:>7.4} MHz | {} | {} | {:>4.1}° ({})",
            p.satellite_name, freq_mhz,
            aos_jst.format("%Y-%m-%d %H:%M:%S"),
            los_jst.format("%Y-%m-%d %H:%M:%S"),
            p.max_elevation_deg, dir
        );
    }
    println!("========================================================================================================\n");

    assert!(!passes.is_empty(), "24時間以内に少なくとも1回のパスが検出される必要があります");
    for pass in &passes {
        assert!(pass.max_elevation_deg >= 15.0);
        assert!(pass.peak_azimuth_deg >= 0.0 && pass.peak_azimuth_deg < 360.0);
        assert!(pass.los > pass.aos);
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

#[test]
fn test_signal_type_display_and_parsing() {
    let sig = ground_station::orbit::SignalType::CubeSatSsdv;
    assert_eq!(sig.name(), "CubeSat SSDV (カメラ画像)");
    assert_eq!(ground_station::orbit::SignalType::CubeSatSstv.name(), "CubeSat SSTV (カメラ画像)");
    assert_eq!(ground_station::orbit::SignalType::CubeSatTelemetry.name(), "CubeSat Telemetry (テレメトリ)");
    assert_eq!(ground_station::orbit::SignalType::MorseCw.name(), "CubeSat Morse (モールスCW)");
    assert_eq!(ground_station::orbit::SignalType::IssSstv.name(), "ISS SSTV (宇宙ステーション画像)");
    assert_eq!(ground_station::orbit::SignalType::FmRepeater.name(), "FM Repeater (音声中継器)");

    assert_eq!(ground_station::orbit::SignalType::from_str_type("FmRepeater"), ground_station::orbit::SignalType::FmRepeater);
    assert_eq!(ground_station::orbit::SignalType::from_str_type("fmvoice"), ground_station::orbit::SignalType::FmRepeater);
    assert_eq!(ground_station::orbit::SignalType::from_str_type("repeater"), ground_station::orbit::SignalType::FmRepeater);

    // is_raw_iq check
    assert!(!ground_station::orbit::SignalType::Apt.is_raw_iq());
    assert!(!ground_station::orbit::SignalType::IssSstv.is_raw_iq());
    assert!(!ground_station::orbit::SignalType::FmRepeater.is_raw_iq());
    assert!(!ground_station::orbit::SignalType::CubeSatSstv.is_raw_iq());
    assert!(ground_station::orbit::SignalType::Lrpt.is_raw_iq());
    assert!(ground_station::orbit::SignalType::CubeSatTelemetry.is_raw_iq());
    assert!(ground_station::orbit::SignalType::CubeSatSsdv.is_raw_iq());
    assert!(ground_station::orbit::SignalType::MorseCw.is_raw_iq());
}

#[test]
fn test_default_tles_loading_and_fallback() {
    use ground_station::orbit::{build_satellite_infos_from_db, parse_3line_tles, resolve_data_path, SignalType};
    use std::collections::HashMap;

    let path = resolve_data_path("data/default_tles.txt");
    assert!(path.exists(), "同梱の default_tles.txt が見つかりません: {:?}", path);

    let content = std::fs::read_to_string(&path).expect("default_tles.txtの読み込みに失敗しました");
    let mut db = HashMap::new();
    parse_3line_tles(&content, &mut db);

    // 必須主要衛星が含まれているか確認
    assert!(db.contains_key(&59051), "Meteor-M N2-4 (59051) が含まれている必要があります");
    assert!(db.contains_key(&57166), "Meteor-M N2-3 (57166) が含まれている必要があります");
    assert!(db.contains_key(&25544), "ISS (25544) が含まれている必要があります");
    assert!(db.contains_key(&39444), "FUNcube-1 (39444) が含まれている必要があります");
    assert!(db.contains_key(&57172), "UmKA-1 (57172) が含まれている必要があります");
    assert!(db.contains_key(&59112), "SONATE-2 (59112) が含まれている必要があります");
    assert!(db.contains_key(&27607) || db.contains_key(&27559), "SO-50 が含まれている必要があります");
    assert!(db.contains_key(&42761), "CAS-4A (42761) が含まれている必要があります");

    // SatelliteInfoの構築テスト
    let targets = vec![
        ("Meteor-M N2-4".to_string(), 59051, 137_900_000, SignalType::Lrpt),
        ("ISS (ZARYA)".to_string(), 25544, 145_800_000, SignalType::IssSstv),
        ("SO-50".to_string(), 27607, 436_795_000, SignalType::FmRepeater),
    ];
    let infos = build_satellite_infos_from_db(&targets, &db);
    assert_eq!(infos.len(), 3);
    assert_eq!(infos[0].name, "Meteor-M N2-4");
    assert!(infos[0].line1.starts_with("1 "));
    assert!(infos[0].line2.starts_with("2 "));
}

