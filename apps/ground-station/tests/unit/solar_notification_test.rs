use ground_station::discord::DiscordClient;
use ground_station::solar::dsp::SolarSecondMetrics;
use ground_station::solar::notification::SolarNotifier;
use std::sync::Arc;

#[test]
fn test_solar_notifier_cooldown() {
    let discord = Arc::new(DiscordClient::new(ground_station::config::DiscordConfig::default()));
    let mut notifier = SolarNotifier::new(discord, 180);

    // 初期状態: 通知可能
    assert!(notifier.should_notify(1000));

    // 通知イベント発生
    notifier.record_notification(1000);

    // 180秒未満: クールダウン中
    assert!(!notifier.should_notify(1050));
    assert!(!notifier.should_notify(1179));

    // 180秒経過: 再び通知可能
    assert!(notifier.should_notify(1181));
}

#[test]
fn test_solar_notifier_embed_construction() {
    let discord = Arc::new(DiscordClient::new(ground_station::config::DiscordConfig::default()));
    let notifier = SolarNotifier::new(discord, 180);

    let metric = SolarSecondMetrics {
        timestamp: 1790510535,
        total_power_db: -24.3,
        baseline_median_db: -38.5,
        snr_db: 14.2,
        is_burst: true,
        spectrum_db: vec![-30.0; 1024],
        sun_az_deg: 152.4,
        sun_el_deg: 52.1,
    };

    let embed = notifier.build_flare_alert_embed(&metric, 70.0e6, 2.4e6);
    assert_eq!(embed.title, "🚨 【太陽電波バースト検知】 70.0 MHz帯");
    assert!(embed.description.contains("太陽フレア"));
    assert_eq!(embed.color, 0xE67E22); // Orange / Flare color

    // フィールド確認
    let fields = embed.fields;
    assert!(fields.iter().any(|f| f.name == "⚡ 電波強度上昇" && f.value.contains("+14.2 dB")));
    assert!(fields.iter().any(|f| f.name == "☀️ 太陽位置" && f.value.contains("52.1°")));
}
