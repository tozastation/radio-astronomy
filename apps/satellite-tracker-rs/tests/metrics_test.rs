use satellite_tracker_rs::metrics::MetricsExporter;
use std::sync::Arc;

#[test]
fn test_metrics_render_text() {
    let exporter = MetricsExporter::new();

    exporter.set_tracking_status("ISS (ZARYA)", true);
    exporter.update_orbit_metrics("ISS (ZARYA)", 45.2, 310.5, -2450.0);
    exporter.update_rf_metrics("ISS (ZARYA)", -42.1, 18.5, -2430.0);
    exporter.set_next_pass("ISS (ZARYA)", 1728561234.0);
    exporter.record_pass_completed("ISS (ZARYA)", "completed");

    let text = exporter.render_prometheus_text();

    assert!(text.contains("satellite_tracking_active{satellite=\"ISS (ZARYA)\"} 1"));
    assert!(text.contains("satellite_elevation_degrees{satellite=\"ISS (ZARYA)\"} 45.2"));
    assert!(text.contains("satellite_azimuth_degrees{satellite=\"ISS (ZARYA)\"} 310.5"));
    assert!(text.contains("satellite_doppler_predicted_hz{satellite=\"ISS (ZARYA)\"} -2450"));
    assert!(text.contains("satellite_doppler_measured_hz{satellite=\"ISS (ZARYA)\"} -2430"));
    assert!(text.contains("satellite_rssi_dbm{satellite=\"ISS (ZARYA)\"} -42.1"));
    assert!(text.contains("satellite_snr_db{satellite=\"ISS (ZARYA)\"} 18.5"));
    assert!(text.contains("satellite_next_aos_timestamp_seconds{satellite=\"ISS (ZARYA)\"} 1728561234"));
    assert!(text.contains("satellite_passes_total{satellite=\"ISS (ZARYA)\",status=\"completed\"} 1"));
}

#[tokio::test]
async fn test_metrics_http_endpoint() {
    let exporter = Arc::new(MetricsExporter::new());
    exporter.set_tracking_status("NOAA 19", false);

    let (server_future, addr) = exporter.clone().bind_server(0).await.expect("bind server succeeds");
    tokio::spawn(server_future);

    let resp = reqwest::get(format!("http://{}/metrics", addr))
        .await
        .expect("metrics request succeeds");

    assert_eq!(resp.status(), reqwest::StatusCode::OK);
    let body = resp.text().await.unwrap();
    assert!(body.contains("satellite_tracking_active{satellite=\"NOAA 19\"} 0"));
}
