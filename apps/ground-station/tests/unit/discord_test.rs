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

    // 画像URLが設定されていること
    assert_eq!(embed["image"]["url"], "attachment://satellite_image.png");

    let fields = embed["fields"].as_array().expect("fields は配列であること");
    
    // フィールドの存在検証
    let find_field = |name: &str| {
        fields.iter().find(|f| f["name"].as_str() == Some(name))
    };

    let sat_field = find_field("🛰️ 衛星・方式").expect("衛星・方式フィールドが存在すること");
    assert!(sat_field["value"].as_str().unwrap().contains("NOAA 18"));

    let freq_field = find_field("📡 受信周波数").expect("周波数フィールドが存在すること");
    assert_eq!(freq_field["value"], "137.9125 MHz");

    let snr_field = find_field("📶 信号品質 (SNR)").expect("SNRフィールドが存在すること");
    assert!(snr_field["value"].as_str().unwrap().contains("18.2 dB"));

    let audio_field = find_field("🎵 受信音声 (WAV)").expect("音声フィールドが存在すること");
    assert!(audio_field["value"].as_str().unwrap().contains("インライン再生可能"));

    let telemetry_field = find_field("⚡ 衛星ヘルス・テレメトリ").expect("テレメトリフィールドが存在すること");
    assert!(telemetry_field["value"].as_str().unwrap().contains("8.24 V"));
    assert!(telemetry_field["value"].as_str().unwrap().contains("+14.2°C"));

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
