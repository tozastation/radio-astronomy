use ground_station::health::{HealthCheckItem, HealthReport, HealthStatus};

#[test]
fn test_health_report_fatal_check() {
    let report_err = HealthReport {
        items: vec![
            HealthCheckItem {
                name: "RTL-SDR Device".to_string(),
                status: HealthStatus::Error,
                message: "デバイス未検出".to_string(),
                remedy: Some("usbipd attach を実行してください".to_string()),
            },
            HealthCheckItem {
                name: "VOICEVOX".to_string(),
                status: HealthStatus::Warn,
                message: "未起動".to_string(),
                remedy: None,
            },
        ],
    };

    assert!(report_err.is_fatal());

    let report_ok = HealthReport {
        items: vec![
            HealthCheckItem {
                name: "RTL-SDR Device".to_string(),
                status: HealthStatus::Ok,
                message: "正常 (RTL2838UHIDIR)".to_string(),
                remedy: None,
            },
            HealthCheckItem {
                name: "VOICEVOX".to_string(),
                status: HealthStatus::Warn,
                message: "未起動 (ログ出力のみ)".to_string(),
                remedy: None,
            },
        ],
    };

    assert!(!report_ok.is_fatal());
}
