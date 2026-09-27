use anyhow::{Context, Result};
use chrono::Utc;
use log::{error, info, warn};
use std::collections::VecDeque;
use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex as TokioMutex;

use crate::config::{Config, SolarConfig};
use crate::discord::DiscordClient;
use super::dsp::SolarDsp;
use super::notification::SolarNotifier;
use super::storage::SolarStorage;
use super::sun_pos::calculate_sun_position;

/// 太陽電波観測ステーションのメイン統合マネージャー
pub struct SolarStationManager {
    config: Config,
    solar_config: SolarConfig,
    storage: SolarStorage,
    discord_client: Arc<DiscordClient>,
    running: Arc<AtomicBool>,
}

impl SolarStationManager {
    pub fn new(config: Config) -> Self {
        let solar_config = config.solar.clone().unwrap_or_default();
        let storage = SolarStorage::new(&solar_config.data_dir);
        let discord_client = Arc::new(DiscordClient::new(config.discord.clone()));

        Self {
            config,
            solar_config,
            storage,
            discord_client,
            running: Arc::new(AtomicBool::new(true)),
        }
    }

    /// 停止フラグを設定
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }

    /// 観測ループを実行します。
    pub async fn run(&self, dry_run: bool, continuous: bool) -> Result<()> {
        info!("============================================================");
        info!("☀️ Solar Radio Observatory (solar-station) 起動");
        info!("   中心周波数: {:.2} MHz (帯域幅: {:.2} MHz)", self.solar_config.center_freq / 1e6, self.solar_config.sample_rate / 1e6);
        info!("   RFゲイン:   {:.1} dB | しきい値: +{:.1}σ", self.solar_config.gain, self.solar_config.threshold_sigma);
        info!("   観測モード: {} (常時観測フラグ: {})", self.solar_config.mode, continuous);
        info!("============================================================");

        if dry_run {
            info!("🔬 ドライランモード: 疑似IQストリームで10秒間のシミュレーションを実行します");
            return self.run_simulation_seconds(10).await;
        }

        // Ctrl+C ハンドラの登録
        let running_clone = self.running.clone();
        tokio::spawn(async move {
            if let Ok(()) = tokio::signal::ctrl_c().await {
                info!("🛑 Ctrl+C を受信しました。安全にシャットダウンを開始します...");
                running_clone.store(false, Ordering::SeqCst);
            }
        });

        while self.running.load(Ordering::SeqCst) {
            let now = Utc::now();
            let sun_pos = calculate_sun_position(self.config.observer.latitude, self.config.observer.longitude, now);

            let is_daylight = sun_pos.elevation_deg >= self.solar_config.min_elevation;
            if !continuous && self.solar_config.mode == "daylight_only" && !is_daylight {
                info!(
                    "🌙 日没中 (太陽高度: {:.1}° < {:.1}°)。日照時間まで待機します (30秒スリープ)...",
                    sun_pos.elevation_deg, self.solar_config.min_elevation
                );
                tokio::time::sleep(Duration::from_secs(30)).await;
                continue;
            }

            info!(
                "☀️ 観測条件合致: 太陽方位角 {:.1}°, 太陽高度 {:.1}°。SDR受信ストリームを開始します",
                sun_pos.azimuth_deg, sun_pos.elevation_deg
            );

            // SDRプロセスを起動して観測実行
            if let Err(e) = self.run_sdr_stream_session().await {
                error!("SDR観測セッション中にエラーが発生しました: {:?}", e);
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        }

        info!("👋 solar-station を正常に終了しました");
        Ok(())
    }

    /// `rtl_sdr` プロセスを起動し、パイプラインを実行する1セッション
    async fn run_sdr_stream_session(&self) -> Result<()> {
        let freq_str = format!("{}", self.solar_config.center_freq as u64);
        let rate_str = format!("{}", self.solar_config.sample_rate as u32);
        let gain_str = format!("{:.1}", self.solar_config.gain);

        let mut child: Child = Command::new("rtl_sdr")
            .arg("-f").arg(&freq_str)
            .arg("-s").arg(&rate_str)
            .arg("-g").arg(&gain_str)
            .arg("-")
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .with_context(|| "rtl_sdr プロセスの起動に失敗しました。RTL-SDR ドングルが接続されているか確認してください")?;

        let mut stdout = child.stdout.take().context("rtl_sdr stdout の取得に失敗しました")?;

        // 3層パイプライン用チャネル
        let (tx, rx): (SyncSender<Vec<u8>>, Receiver<Vec<u8>>) = sync_channel(32);
        let running_reader = self.running.clone();
        let chunk_size = (self.solar_config.sample_rate as usize / 10) * 2; // 0.1秒分

        // Thread 1: Stdout Reader
        let reader_handle = std::thread::spawn(move || {
            let mut buf = vec![0u8; chunk_size];
            while running_reader.load(Ordering::SeqCst) {
                match stdout.read_exact(&mut buf) {
                    Ok(()) => {
                        if tx.send(buf.clone()).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        warn!("rtl_sdr stdout read error: {:?}", e);
                        break;
                    }
                }
            }
        });

        // Thread 2: DSP Worker & Storage / Notifier
        let pipeline_result = self.run_dsp_worker(rx).await;

        // 子プロセスの終了処理
        self.stop();
        let _ = child.kill();
        let _ = child.wait();
        let _ = reader_handle.join();

        pipeline_result
    }

    /// DSPスレッドとイベント通知ループ
    async fn run_dsp_worker(&self, rx: Receiver<Vec<u8>>) -> Result<()> {
        let mut dsp = SolarDsp::new(
            self.solar_config.fft_size,
            self.solar_config.sample_rate as u32,
            self.solar_config.threshold_sigma,
            self.solar_config.min_jump_db,
        );

        let notifier = Arc::new(TokioMutex::new(SolarNotifier::new(
            self.discord_client.clone(),
            self.solar_config.cooldown_secs,
        )));

        // 直近60秒分のスペクトル履歴リングバッファ
        let mut spectra_history: VecDeque<Vec<f32>> = VecDeque::with_capacity(60);

        while self.running.load(Ordering::SeqCst) {
            let chunk = match rx.recv_timeout(Duration::from_millis(200)) {
                Ok(c) => c,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            };

            let now = Utc::now();
            let now_ts = now.timestamp();

            if let Some(mut metric) = dsp.process_chunk(&chunk, now_ts) {
                // 太陽位置の付与
                let pos = calculate_sun_position(self.config.observer.latitude, self.config.observer.longitude, now);
                metric.sun_az_deg = pos.azimuth_deg;
                metric.sun_el_deg = pos.elevation_deg;

                // 1秒メトリクスを永続化
                if let Err(e) = self.storage.record_metric(&metric) {
                    warn!("メトリクスの永続化に失敗しました: {:?}", e);
                }

                // スペクトル履歴の更新
                if spectra_history.len() >= 60 {
                    spectra_history.pop_front();
                }
                spectra_history.push_back(metric.spectrum_db.clone());

                // 太陽フレア電波バーストの判定
                if metric.is_burst {
                    info!(
                        "🚨 【太陽フレア電波バースト検知！】 時刻: {} SNR: +{:.1}dB (急上昇検知)",
                        now.to_rfc3339(), metric.snr_db
                    );

                    let notifier_clone = notifier.clone();
                    let should_notify = {
                        let notif = notifier_clone.lock().await;
                        notif.should_notify(metric.timestamp)
                    };

                    if should_notify {
                        // 前後60秒のスペクトルからウォーターフォール PNG を生成
                        let history_vec: Vec<Vec<f32>> = spectra_history.iter().cloned().collect();
                        match self.storage.save_event_waterfall(&history_vec, metric.timestamp) {
                            Ok(png_path) => {
                                let center_freq = self.solar_config.center_freq;
                                let sample_rate = self.solar_config.sample_rate;
                                tokio::spawn(async move {
                                    let mut notif = notifier_clone.lock().await;
                                    if let Err(e) = notif.send_flare_alert(&metric, center_freq, sample_rate, &png_path).await {
                                        warn!("Discord アラート送信エラー: {:?}", e);
                                    }
                                });
                            }
                            Err(e) => {
                                warn!("ウォーターフォール PNG の保存に失敗しました: {:?}", e);
                            }
                        }
                    }
                }
            }
        }

        Ok(())
    }

    /// 疑似IQデータを用いたシミュレーション実行（テスト・検証用）
    pub async fn run_simulation_seconds(&self, seconds: u64) -> Result<()> {
        let (tx, rx): (SyncSender<Vec<u8>>, Receiver<Vec<u8>>) = sync_channel(32);
        let running_sim = self.running.clone();
        let chunk_size = (self.solar_config.sample_rate as usize / 10) * 2;

        // シミュレーションデータ生成スレッド
        let sim_handle = std::thread::spawn(move || {
            let quiet_chunk = vec![127u8; chunk_size];
            // 1秒あたり10チャンク
            for _ in 0..(seconds * 10) {
                if !running_sim.load(Ordering::SeqCst) {
                    break;
                }
                if tx.send(quiet_chunk.clone()).is_err() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(5)); // 高速シミュレーション
            }
        });

        // DSP処理
        let dsp_result = self.run_dsp_worker(rx).await;
        self.stop();
        let _ = sim_handle.join();

        dsp_result
    }
}
