use anyhow::{Context, Result};
use axum::{extract::State, routing::get, Router};
use std::collections::HashMap;
use std::future::Future;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tokio::net::TcpListener;

#[derive(Default)]
struct MetricsState {
    tracking_active: HashMap<String, f64>,
    elevation: HashMap<String, f64>,
    azimuth: HashMap<String, f64>,
    doppler_predicted: HashMap<String, f64>,
    doppler_measured: HashMap<String, f64>,
    rssi: HashMap<String, f64>,
    snr: HashMap<String, f64>,
    next_aos_timestamp: HashMap<String, f64>,
    passes_total: HashMap<(String, String), u64>,
}

/// 超軽量 Prometheus メトリクスエクスポーター
#[derive(Clone)]
pub struct MetricsExporter {
    state: Arc<Mutex<MetricsState>>,
}

impl Default for MetricsExporter {
    fn default() -> Self {
        Self::new()
    }
}

impl MetricsExporter {
    /// 新規 MetricsExporter を初期化
    pub fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(MetricsState::default())),
        }
    }

    /// 衛星の追尾アクティブ状態を設定
    pub fn set_tracking_status(&self, satellite: &str, active: bool) {
        let mut s = self.state.lock().unwrap();
        s.tracking_active.insert(satellite.to_string(), if active { 1.0 } else { 0.0 });
    }

    /// 次期 AOS タイムスタンプ（秒）を設定
    pub fn set_next_pass(&self, satellite: &str, aos_timestamp: f64) {
        let mut s = self.state.lock().unwrap();
        s.next_aos_timestamp.insert(satellite.to_string(), aos_timestamp);
    }

    /// 軌道力学メトリクス（仰角、方位角、理論ドップラー）を更新
    pub fn update_orbit_metrics(
        &self,
        satellite: &str,
        elevation_deg: f64,
        azimuth_deg: f64,
        doppler_predicted_hz: f64,
    ) {
        let mut s = self.state.lock().unwrap();
        s.elevation.insert(satellite.to_string(), elevation_deg);
        s.azimuth.insert(satellite.to_string(), azimuth_deg);
        s.doppler_predicted.insert(satellite.to_string(), doppler_predicted_hz);
    }

    /// 無線 RF メトリクス（RSSI、SNR、実測ドップラー）を更新
    pub fn update_rf_metrics(
        &self,
        satellite: &str,
        rssi_dbm: f64,
        snr_db: f64,
        doppler_measured_hz: f64,
    ) {
        let mut s = self.state.lock().unwrap();
        s.rssi.insert(satellite.to_string(), rssi_dbm);
        s.snr.insert(satellite.to_string(), snr_db);
        s.doppler_measured.insert(satellite.to_string(), doppler_measured_hz);
    }

    /// パス完了カウンターをインクリメント
    pub fn record_pass_completed(&self, satellite: &str, status: &str) {
        let mut s = self.state.lock().unwrap();
        let key = (satellite.to_string(), status.to_string());
        *s.passes_total.entry(key).or_insert(0) += 1;
    }

    /// Prometheus テキスト形式の文字列を生成
    pub fn render_prometheus_text(&self) -> String {
        let s = self.state.lock().unwrap();
        let mut out = String::new();

        // 1. satellite_tracking_active
        out.push_str("# HELP satellite_tracking_active Indicates whether a satellite pass is currently being tracked\n");
        out.push_str("# TYPE satellite_tracking_active gauge\n");
        for (sat, val) in &s.tracking_active {
            out.push_str(&format!("satellite_tracking_active{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 2. satellite_elevation_degrees
        out.push_str("# HELP satellite_elevation_degrees Current elevation angle of the satellite in degrees\n");
        out.push_str("# TYPE satellite_elevation_degrees gauge\n");
        for (sat, val) in &s.elevation {
            out.push_str(&format!("satellite_elevation_degrees{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 3. satellite_azimuth_degrees
        out.push_str("# HELP satellite_azimuth_degrees Current azimuth angle of the satellite in degrees\n");
        out.push_str("# TYPE satellite_azimuth_degrees gauge\n");
        for (sat, val) in &s.azimuth {
            out.push_str(&format!("satellite_azimuth_degrees{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 4. satellite_doppler_predicted_hz
        out.push_str("# HELP satellite_doppler_predicted_hz Theoretical Doppler frequency shift in Hz\n");
        out.push_str("# TYPE satellite_doppler_predicted_hz gauge\n");
        for (sat, val) in &s.doppler_predicted {
            out.push_str(&format!("satellite_doppler_predicted_hz{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 5. satellite_doppler_measured_hz
        out.push_str("# HELP satellite_doppler_measured_hz Actual Doppler frequency shift measured in Hz\n");
        out.push_str("# TYPE satellite_doppler_measured_hz gauge\n");
        for (sat, val) in &s.doppler_measured {
            out.push_str(&format!("satellite_doppler_measured_hz{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 6. satellite_rssi_dbm
        out.push_str("# HELP satellite_rssi_dbm Received signal strength indicator in dBm\n");
        out.push_str("# TYPE satellite_rssi_dbm gauge\n");
        for (sat, val) in &s.rssi {
            out.push_str(&format!("satellite_rssi_dbm{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 7. satellite_snr_db
        out.push_str("# HELP satellite_snr_db Signal-to-noise ratio in dB\n");
        out.push_str("# TYPE satellite_snr_db gauge\n");
        for (sat, val) in &s.snr {
            out.push_str(&format!("satellite_snr_db{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 8. satellite_next_aos_timestamp_seconds
        out.push_str("# HELP satellite_next_aos_timestamp_seconds Unix timestamp of the next anticipated AOS\n");
        out.push_str("# TYPE satellite_next_aos_timestamp_seconds gauge\n");
        for (sat, val) in &s.next_aos_timestamp {
            out.push_str(&format!("satellite_next_aos_timestamp_seconds{{satellite=\"{}\"}} {}\n", sat, val));
        }

        // 9. satellite_passes_total
        out.push_str("# HELP satellite_passes_total Total number of satellite passes observed\n");
        out.push_str("# TYPE satellite_passes_total counter\n");
        for ((sat, status), val) in &s.passes_total {
            out.push_str(&format!(
                "satellite_passes_total{{satellite=\"{}\",status=\"{}\"}} {}\n",
                sat, status, val
            ));
        }

        out
    }

    /// axum による HTTP サーバーをポートにバインドして Future と SocketAddr を返す
    pub async fn bind_server(
        self: Arc<Self>,
        port: u16,
    ) -> Result<(impl Future<Output = Result<()>>, SocketAddr)> {
        let listener = TcpListener::bind(format!("0.0.0.0:{}", port))
            .await
            .with_context(|| format!("ポート {} へのバインドに失敗しました", port))?;

        let addr = listener.local_addr()?;
        let app = Router::new()
            .route("/metrics", get(metrics_handler))
            .with_state(self);

        let server_future = async move {
            axum::serve(listener, app).await.context("HTTPサーバーエラー")?;
            Ok(())
        };

        Ok((server_future, addr))
    }

    /// HTTP サーバーを起動してリスニング開始
    pub async fn run_server(self: Arc<Self>, port: u16) -> Result<()> {
        let (fut, addr) = self.bind_server(port).await?;
        log::info!("Prometheus metrics server listening on http://{}/metrics", addr);
        fut.await
    }
}

async fn metrics_handler(State(exporter): State<Arc<MetricsExporter>>) -> String {
    exporter.render_prometheus_text()
}
