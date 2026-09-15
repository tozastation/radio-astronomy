use ground_station::discord::PassStatus;
use ground_station::metrics::{MetricsCollector, PassMetricRecord, PassMetricsSummary, PassOutcome};

#[test]
fn test_pass_outcome_mapping_data_viewable_criteria() {
    // 成功（データの中身が見える）: 画像デコード成功、テレメトリ/パケット復調成功
    let outcome_img = PassOutcome::from_status(PassStatus::ImageDecoded);
    assert_eq!(outcome_img, PassOutcome::Success);
    assert!(outcome_img.is_content_viewable());

    let outcome_tlm = PassOutcome::from_status(PassStatus::TelemetryDecoded);
    assert_eq!(outcome_tlm, PassOutcome::Success);
    assert!(outcome_tlm.is_content_viewable());

    // 失敗（中身が見えなかった）: 電波微弱・パケット0件・デコード異常
    let outcome_weak = PassOutcome::from_status(PassStatus::WeakSignal);
    assert_eq!(outcome_weak, PassOutcome::Failure);
    assert!(!outcome_weak.is_content_viewable());

    let outcome_err = PassOutcome::from_status(PassStatus::DecodeError);
    assert_eq!(outcome_err, PassOutcome::Failure);
    assert!(!outcome_err.is_content_viewable());

    // あきらめる枠（判定困難または未復調保全）: FM交信音声録音、生データ保全のみ
    let outcome_audio = PassOutcome::from_status(PassStatus::AudioRecorded);
    assert_eq!(outcome_audio, PassOutcome::Unknown);
    assert!(!outcome_audio.is_content_viewable());

    let outcome_raw = PassOutcome::from_status(PassStatus::RawPreserved);
    assert_eq!(outcome_raw, PassOutcome::Unknown);
    assert!(!outcome_raw.is_content_viewable());
}

#[test]
fn test_metric_record_serialization() {
    let record = PassMetricRecord {
        timestamp: "2026-09-15T18:45:00+09:00".to_string(),
        satellite: "XW-2A".to_string(),
        frequency_hz: 145_660_000,
        signal_type: "MorseCw".to_string(),
        max_elevation_deg: 22.7,
        peak_azimuth_deg: 65.4,
        status: PassStatus::TelemetryDecoded,
        outcome: PassOutcome::Success,
        content_viewable: true,
        content_summary: Some("CWモールステキスト復号完了 (CAS-3A)".to_string()),
        session_dir: "data/noaa/20260915_184300_XW-2A".to_string(),
        has_image: false,
        has_audio: false,
        has_telemetry_or_text: true,
        duration_secs: Some(120),
    };

    let json_str = serde_json::to_string(&record).expect("JSON serialization failed");
    assert!(json_str.contains("\"satellite\":\"XW-2A\""));
    assert!(json_str.contains("\"outcome\":\"success\""));
    assert!(json_str.contains("\"content_viewable\":true"));

    let deserialized: PassMetricRecord = serde_json::from_str(&json_str).expect("JSON deserialization failed");
    assert_eq!(deserialized.satellite, "XW-2A");
    assert_eq!(deserialized.outcome, PassOutcome::Success);
    assert!(deserialized.content_viewable);
}

#[tokio::test]
async fn test_metrics_collector_record_and_summary() {
    let temp_dir = std::env::temp_dir().join(format!("gs_test_metrics_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&temp_dir);
    std::fs::create_dir_all(&temp_dir).unwrap();

    let jsonl_path = temp_dir.join("passes.jsonl");
    let collector = MetricsCollector::new(jsonl_path.clone());

    // 1件目: 成功 (Meteor-M 画像デコード)
    let rec1 = PassMetricRecord {
        timestamp: "2026-09-15T10:00:00+09:00".to_string(),
        satellite: "Meteor-M N2-3".to_string(),
        frequency_hz: 137_900_000,
        signal_type: "LRPT".to_string(),
        max_elevation_deg: 58.0,
        peak_azimuth_deg: 45.0,
        status: PassStatus::ImageDecoded,
        outcome: PassOutcome::Success,
        content_viewable: true,
        content_summary: Some("LRPT 画像デコード成功 (RGB 可視光)".to_string()),
        session_dir: "data/noaa/session1".to_string(),
        has_image: true,
        has_audio: false,
        has_telemetry_or_text: false,
        duration_secs: Some(600),
    };
    collector.record_pass(&rec1).await.expect("Failed to record pass 1");

    // 2件目: 失敗 (FUNcube-1 微弱信号・パケット0件)
    let rec2 = PassMetricRecord {
        timestamp: "2026-09-15T12:00:00+09:00".to_string(),
        satellite: "FUNcube-1".to_string(),
        frequency_hz: 145_935_000,
        signal_type: "BpskTelemetry".to_string(),
        max_elevation_deg: 18.0,
        peak_azimuth_deg: 110.0,
        status: PassStatus::WeakSignal,
        outcome: PassOutcome::Failure,
        content_viewable: false,
        content_summary: Some("電波微弱のためパケット未検出".to_string()),
        session_dir: "data/noaa/session2".to_string(),
        has_image: false,
        has_audio: false,
        has_telemetry_or_text: false,
        duration_secs: Some(400),
    };
    collector.record_pass(&rec2).await.expect("Failed to record pass 2");

    // 3件目: あきらめる枠 (SO-50 FM交信録音)
    let rec3 = PassMetricRecord {
        timestamp: "2026-09-15T14:00:00+09:00".to_string(),
        satellite: "SO-50".to_string(),
        frequency_hz: 436_795_000,
        signal_type: "FmRepeater".to_string(),
        max_elevation_deg: 25.0,
        peak_azimuth_deg: 320.0,
        status: PassStatus::AudioRecorded,
        outcome: PassOutcome::Unknown,
        content_viewable: false,
        content_summary: Some("FM 音声録音完了 (内容検知あきらめ)".to_string()),
        session_dir: "data/noaa/session3".to_string(),
        has_image: false,
        has_audio: true,
        has_telemetry_or_text: false,
        duration_secs: Some(300),
    };
    collector.record_pass(&rec3).await.expect("Failed to record pass 3");

    // ファイル存在と行数を確認
    assert!(jsonl_path.exists());
    let content = std::fs::read_to_string(&jsonl_path).unwrap();
    let lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
    assert_eq!(lines.len(), 3);

    // サマリー集計の検証
    let summary: PassMetricsSummary = collector.get_summary().await.expect("Failed to get summary");
    assert_eq!(summary.total_passes, 3);
    assert_eq!(summary.success_count, 1);
    assert_eq!(summary.failure_count, 1);
    assert_eq!(summary.unknown_count, 1);
    // 判定対象 (success + failure = 2) のうち success は 1 件 -> 50.0%
    assert!((summary.success_rate - 50.0).abs() < 0.1);

    // 衛星別内訳
    assert_eq!(summary.satellite_stats.get("Meteor-M N2-3").unwrap().success, 1);
    assert_eq!(summary.satellite_stats.get("FUNcube-1").unwrap().failure, 1);
    assert_eq!(summary.satellite_stats.get("SO-50").unwrap().unknown, 1);

    let _ = std::fs::remove_dir_all(&temp_dir);
}
