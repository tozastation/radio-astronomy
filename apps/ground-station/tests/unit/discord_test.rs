use ground_station::discord::{DiscordClient, PassReport, PassStatus, SatelliteTelemetry};

#[test]
fn test_build_embed_with_image_and_telemetry() {
    let telemetry = SatelliteTelemetry {
        snr_db: Some(18.2),
        lines_or_packets: Some("2,180 有効走査線".to_string()),
        housekeeping: vec![
            ("バッテリ".to_string(), "8.24 V".to_string()),
            ("センサ温度".to_string(), "+14.2°C".to_string()),
        ],
        status: PassStatus::ImageDecoded,
    };

    let report = PassReport {
        satellite_name: "NOAA 18".to_string(),
        signal_type_name: "APT (2.4kHz AM)".to_string(),
        max_elevation_deg: 52.5,
        direction: "東南東 (ESE)".to_string(),
        frequency_hz: 137_912_500,
        pass_time_str: "2026-09-04 12:13:00 〜 12:20:00 (7分00秒)".to_string(),
        telemetry: Some(telemetry),
        has_image: true,
        has_audio: true,
        next_pass_info: Some("🛰️ **NOAA 19** (22:08 〜 40.7° 西南西)".to_string()),
    };

    let embed = DiscordClient::build_embed(&report);

    // ステータスカラー: 緑色 (0x2ECC71 = 3066993)
    assert_eq!(embed["color"], 0x2ECC71);
    assert_eq!(embed["title"], "🛰️ NOAA 18 [APT (2.4kHz AM)] 受信・デコード完了");

    // description にステータス要約が含まれること
    let desc = embed["description"].as_str().expect("description が存在すること");
    assert!(desc.contains("画像デコード成功"));
    assert!(desc.contains("NOAA 18"));

    // timestamp が設定されていること
    assert!(embed["timestamp"].is_string());

    // 画像URLが設定されていること
    assert_eq!(embed["image"]["url"], "attachment://satellite_image.png");

    let fields = embed["fields"].as_array().expect("fields は配列であること");
    let find_field = |name: &str| fields.iter().find(|f| f["name"].as_str() == Some(name));

    // 軌道ジオメトリフィールド (inline: true)
    let geom_field = find_field("📐 軌道ジオメトリ").expect("軌道ジオメトリフィールドが存在すること");
    assert_eq!(geom_field["inline"], true);
    assert!(geom_field["value"].as_str().unwrap().contains("52.5°"));
    assert!(geom_field["value"].as_str().unwrap().contains("東南東 (ESE)"));

    // 無線・SDR諸元フィールド (inline: true)
    let sdr_field = find_field("📡 無線・SDR諸元").expect("無線諸元フィールドが存在すること");
    assert_eq!(sdr_field["inline"], true);
    assert!(sdr_field["value"].as_str().unwrap().contains("137.9125 MHz"));

    // 復調成果 & ヘルスフィールド (inline: false, yamlコードブロック)
    let telemetry_field = find_field("⚡ 復調成果 & ヘルス").expect("復調成果フィールドが存在すること");
    assert_eq!(telemetry_field["inline"], false);
    let tel_val = telemetry_field["value"].as_str().unwrap();
    assert!(tel_val.contains("```yaml"));
    assert!(tel_val.contains("バッテリ: 8.24 V"));
    assert!(tel_val.contains("センサ温度: +14.2°C"));
    assert!(tel_val.contains("18.2 dB"));

    let audio_field = find_field("🎵 受信音声 (WAV)").expect("音声フィールドが存在すること");
    assert!(audio_field["value"].as_str().unwrap().contains("インライン再生可能"));

    let next_field = find_field("⏰ 次の通過予定").expect("次回パスフィールドが存在すること");
    assert!(next_field["value"].as_str().unwrap().contains("NOAA 19"));
}

#[test]
fn test_build_embed_without_image_does_not_have_image_url() {
    let telemetry = SatelliteTelemetry {
        snr_db: Some(12.0),
        lines_or_packets: Some("42 パケット復調".to_string()),
        housekeeping: vec![("電圧".to_string(), "4.1 V".to_string())],
        status: PassStatus::TelemetryDecoded,
    };

    let report = PassReport {
        satellite_name: "FUNcube-1".to_string(),
        signal_type_name: "BPSK Telemetry".to_string(),
        max_elevation_deg: 35.0,
        direction: "北東 (NE)".to_string(),
        frequency_hz: 145_935_000,
        pass_time_str: "2026-09-04 15:00:00 〜 15:08:00".to_string(),
        telemetry: Some(telemetry),
        has_image: false,
        has_audio: false,
        next_pass_info: None,
    };

    let embed = DiscordClient::build_embed(&report);

    // テレメトリデコードカラー: 宇宙ブルー (0x3498DB = 3447003)
    assert_eq!(embed["color"], 0x3498DB);
    // 画像フィールドが存在しないこと (Discord側のBroken Image防止)
    assert!(embed.get("image").is_none());
}

#[test]
fn test_create_test_sample_image_returns_valid_png() {
    let image_bytes = DiscordClient::create_test_sample_image();
    assert!(!image_bytes.is_empty());
    // PNG マジックナンバー (8バイト: 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A)
    assert_eq!(&image_bytes[0..8], b"\x89PNG\r\n\x1a\n");
}

#[test]
fn test_create_test_sample_wav_returns_valid_wav() {
    let wav_bytes = DiscordClient::create_test_sample_wav();
    assert!(wav_bytes.len() > 44);
    // RIFF / WAVE マジックナンバーの検証
    assert_eq!(&wav_bytes[0..4], b"RIFF");
    assert_eq!(&wav_bytes[8..12], b"WAVE");
    assert_eq!(&wav_bytes[12..16], b"fmt ");
    assert_eq!(&wav_bytes[36..40], b"data");
}

#[test]
fn test_build_daily_schedule_embed() {
    use chrono::{Duration, Utc};
    use ground_station::orbit::{SatellitePass, SignalType};

    let pass = SatellitePass {
        satellite_name: "NOAA 18".to_string(),
        frequency_hz: 137_912_500,
        signal_type: SignalType::Apt,
        aos: Utc::now(),
        los: Utc::now() + Duration::minutes(10),
        max_elevation_deg: 52.5,
        peak_azimuth_deg: 120.0,
    };

    let embed = DiscordClient::build_daily_schedule_embed(&[pass], "2026-09-06", 35.68, 139.76, 25.0);

    assert_eq!(embed["color"], 0x3498DB);
    assert!(embed["title"].as_str().unwrap().contains("2026-09-06"));
    let fields = embed["fields"].as_array().expect("fields は配列であること");
    assert_eq!(fields.len(), 1);
    assert!(fields[0]["name"].as_str().unwrap().contains("NOAA 18"));
    assert!(fields[0]["value"].as_str().unwrap().contains("52.5°"));
}

#[test]
fn test_build_daily_schedule_embed_truncation_footer() {
    use chrono::{Duration, Utc};
    use ground_station::orbit::{SatellitePass, SignalType};

    let mut passes = Vec::new();
    for i in 0..30 {
        passes.push(SatellitePass {
            satellite_name: format!("Sat-{}", i + 1),
            frequency_hz: 137_912_500,
            signal_type: SignalType::Apt,
            aos: Utc::now() + Duration::minutes(i * 30),
            los: Utc::now() + Duration::minutes(i * 30 + 10),
            max_elevation_deg: 45.0,
            peak_azimuth_deg: 90.0,
        });
    }

    let embed = DiscordClient::build_daily_schedule_embed(&passes, "2026-09-06", 35.68, 139.76, 20.0);
    let fields = embed["fields"].as_array().expect("fields は配列であること");
    assert_eq!(fields.len(), 25);
    let footer_text = embed["footer"]["text"].as_str().expect("footer textが存在すること");
    assert!(footer_text.contains("全 30 件中 25 件を表示"));
    assert!(footer_text.contains("他 5 件"));
}

#[test]
fn test_build_24h_schedule_embed_with_next_day_passes() {
    use chrono::{Duration, Utc};
    use ground_station::orbit::{SatellitePass, SignalType};

    // 翌日のパス
    let tomorrow_pass = SatellitePass {
        satellite_name: "Meteor-M N2-4".to_string(),
        frequency_hz: 137_900_000,
        signal_type: SignalType::Lrpt,
        aos: Utc::now() + Duration::hours(20),
        los: Utc::now() + Duration::hours(20) + Duration::minutes(12),
        max_elevation_deg: 65.0,
        peak_azimuth_deg: 180.0,
    };

    let embed = DiscordClient::build_24h_schedule_embed(&[tomorrow_pass], 35.68, 139.76, 20.0);
    assert!(embed["title"].as_str().unwrap().contains("今後24時間の衛星通過予定"));
    let fields = embed["fields"].as_array().expect("fields は配列であること");
    assert_eq!(fields.len(), 1);
    assert!(fields[0]["name"].as_str().unwrap().contains("Meteor-M N2-4"));
}

#[test]
fn test_build_embed_with_raw_preserved_status() {
    let telemetry = SatelliteTelemetry {
        snr_db: None,
        lines_or_packets: Some("生録音データ保存完了 (デコード未実施)".to_string()),
        housekeeping: vec![
            ("生データ".to_string(), "保全完了 (ディスク保存)".to_string()),
            ("デコード状況".to_string(), "未復調 (生IQ/音声アーカイブ)".to_string()),
        ],
        status: PassStatus::RawPreserved,
    };

    let report = PassReport {
        satellite_name: "UmKA-1".to_string(),
        signal_type_name: "CubeSat SSTV (カメラ画像)".to_string(),
        max_elevation_deg: 73.4,
        direction: "西北西 (WNW)".to_string(),
        frequency_hz: 437_625_000,
        pass_time_str: "2026-09-06 09:37:29 〜 09:42:29".to_string(),
        telemetry: Some(telemetry),
        has_image: false,
        has_audio: true,
        next_pass_info: None,
    };

    let embed = DiscordClient::build_embed(&report);

    // 生データ保存カラー: アメジスト紫 (0x9B59B6 = 10180918)
    assert_eq!(embed["color"], 0x9B59B6);
    // デコード完了ではなく「受信・生データ保存完了」となること
    assert_eq!(
        embed["title"],
        "🛰️ UmKA-1 [CubeSat SSTV (カメラ画像)] 受信・生データ保存完了"
    );

    let desc = embed["description"].as_str().expect("description が存在すること");
    assert!(desc.contains("生データ保存完了"));

    let fields = embed["fields"].as_array().expect("fields は配列であること");
    let find_field = |name: &str| fields.iter().find(|f| f["name"].as_str() == Some(name));

    // 復調成果 & ヘルスフィールドに「保全完了」および「未復調」が記載されていること
    let telemetry_field = find_field("⚡ 復調成果 & ヘルス").expect("復調成果フィールドが存在すること");
    let val = telemetry_field["value"].as_str().unwrap();
    assert!(val.contains("保全完了"));
    assert!(val.contains("未復調"));
}

#[test]
fn test_build_embed_with_audio_recorded_status() {
    let telemetry = SatelliteTelemetry {
        snr_db: None,
        lines_or_packets: Some("FM 音声復調完了 (WAV 添付)".to_string()),
        housekeeping: vec![
            ("中継方式".to_string(), "FM ボイストランスポンダー".to_string()),
            ("アクセス仕様".to_string(), "Uplink: 145.850MHz / Downlink: 436.795MHz".to_string()),
        ],
        status: PassStatus::AudioRecorded,
    };

    let report = PassReport {
        satellite_name: "SO-50".to_string(),
        signal_type_name: "FM Repeater (音声中継器)".to_string(),
        max_elevation_deg: 70.1,
        direction: "東 (E)".to_string(),
        frequency_hz: 436_795_000,
        pass_time_str: "2026-09-06 10:00:00 〜 10:10:00".to_string(),
        telemetry: Some(telemetry),
        has_image: false,
        has_audio: true,
        next_pass_info: None,
    };

    let embed = DiscordClient::build_embed(&report);

    // 音声録音カラー: ターコイズ (0x1ABC9C = 1752220)
    assert_eq!(embed["color"], 0x1ABC9C);
    assert_eq!(
        embed["title"],
        "🛰️ SO-50 [FM Repeater (音声中継器)] 交信音声録音完了"
    );

    let desc = embed["description"].as_str().expect("description が存在すること");
    assert!(desc.contains("交信音声録音完了"));

    let fields = embed["fields"].as_array().expect("fields は配列であること");
    let find_field = |name: &str| fields.iter().find(|f| f["name"].as_str() == Some(name));

    assert!(find_field("🎵 受信音声 (WAV)").is_some());
    let telemetry_field = find_field("⚡ 復調成果 & ヘルス").expect("復調成果フィールドが存在すること");
    assert!(telemetry_field["value"].as_str().unwrap().contains("FM ボイストランスポンダー"));
}

