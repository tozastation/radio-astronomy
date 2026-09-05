use crate::config::Config;
use crate::orbit::{azimuth_to_direction, fetch_weather_tles, OrbitPredictor};
use crate::receiver::ReceiverSession;
use crate::voicevox::VoicevoxClient;
use anyhow::Result;
use chrono::{DateTime, Duration, Local, Utc};
use log::{error, info, warn};
use reqwest::Client;
use std::path::PathBuf;

// =============================================================================
// ⏱️ 自律スケジューラ & ステートマシン (Scheduler)
// -----------------------------------------------------------------------------
// 【言語対比】
// - `tokio::select!`: Go 言語の `select { case <-timer: ... case <-ctx.Done(): ... }`
//   と全く同じ動作をする非同期多重化マクロです。
//   「指定秒数のスリープ待機」と「シグナル (SIGINT / SIGTERM) によるシャットダウン要求」を
//   同時に監視し、どちらか早く発生したイベントを処理します。systemd による停止 (SIGTERM)
//   または Ctrl+C が押された場合は直ちにスリープが安全にキャンセルされて Graceful Shutdown 処理へ移行します。
// =============================================================================

/// 今後24時間の通過予定一覧をコンソールにきれいな表で出力
pub async fn show_schedule(config: &Config) -> Result<()> {
    let http_client = Client::new();
    println!("🛰️  CelesTrak から最新 TLE を取得中...");
    let satellites = fetch_weather_tles(&http_client, &config.satellites).await?;

    let now = Utc::now();
    let passes = OrbitPredictor::predict_all_passes(
        &satellites,
        &config.observer,
        now,
        24,
        config.scheduler.min_elevation_deg,
    )?;

    println!("\n=====================================================================================================================");
    println!("📡 地上局 衛星通過予定スケジュール (今後24時間 / 観測地: 緯度 {:.4}, 経度 {:.4})", config.observer.latitude, config.observer.longitude);
    println!("=====================================================================================================================");
    println!("{:<15} | {:<12} | {:<28} | {:<20} | {:<20} | {:<18}", "衛星名", "周波数", "信号方式", "通過開始 (AOS / JST)", "通過終了 (LOS / JST)", "最大仰角 (ピーク方位)");
    println!("---------------------------------------------------------------------------------------------------------------------");

    if passes.is_empty() {
        println!("※ 仰角 {:.1}° 以上の通過パスは見つかりませんでした", config.scheduler.min_elevation_deg);
    } else {
        for pass in passes {
            let aos_local: DateTime<Local> = DateTime::from(pass.aos);
            let los_local: DateTime<Local> = DateTime::from(pass.los);
            let freq_mhz = pass.frequency_hz as f64 / 1_000_000.0;
            let dir = azimuth_to_direction(pass.peak_azimuth_deg);

            println!(
                "{:<15} | {:>7.4} MHz | {:<28} | {} | {} | {:>4.1}° ({})",
                pass.satellite_name,
                freq_mhz,
                pass.signal_type.name(),
                aos_local.format("%Y-%m-%d %H:%M:%S"),
                los_local.format("%Y-%m-%d %H:%M:%S"),
                pass.max_elevation_deg,
                dir
            );
        }
    }
    println!("=====================================================================================================================\n");

    Ok(())
}

/// 衛星通過パス一覧から、開始時刻(AOS)が指定されたJST日付に該当するものだけを抽出
pub fn filter_passes_for_jst_date(
    passes: &[crate::orbit::SatellitePass],
    target_date: chrono::NaiveDate,
) -> Vec<crate::orbit::SatellitePass> {
    passes
        .iter()
        .filter(|p| {
            let aos_jst: chrono::DateTime<chrono::Local> = chrono::DateTime::from(p.aos);
            aos_jst.date_naive() == target_date
        })
        .cloned()
        .collect()
}

/// 現在計算される今後24時間の通過予定一覧を Discord へ送信 (CLI / 手動トリガー用)
pub async fn send_schedule_to_discord(config: &Config) -> Result<()> {
    let http_client = Client::new();
    println!("🛰️  CelesTrak から最新 TLE を取得中...");
    let satellites = fetch_weather_tles(&http_client, &config.satellites).await?;

    let now = Utc::now();
    let passes = OrbitPredictor::predict_all_passes(
        &satellites,
        &config.observer,
        now,
        24,
        config.scheduler.min_elevation_deg,
    )?;

    let discord = crate::discord::DiscordClient::new(config.discord.clone());

    discord
        .send_24h_schedule(
            &passes,
            config.observer.latitude,
            config.observer.longitude,
            config.scheduler.min_elevation_deg,
        )
        .await?;

    println!("✨ Discord への今後24時間通過予定の送信が完了しました！ (対象パス: {} 件)", passes.len());
    Ok(())
}

/// 毎朝定時（または起動時）に本日の衛星受信スケジュールを Discord に自動配信する独立常駐タスク
/// 【非同期分離による堅牢性】
/// メインループ（SDR録音シーケンスの直列ステートマシン）から完全に分離して独立動作します。
/// SDRが深夜の長時間スリープ中であっても、毎朝設定された時刻（例: 07:00 JST）に正確に発火します。
pub async fn run_daily_scheduler(
    config: Config,
    discord: std::sync::Arc<crate::discord::DiscordClient>,
) -> Result<()> {
    if !config.scheduler.daily_schedule_enabled {
        info!("デイリースケジューラは設定により無効化されています");
        return Ok(());
    }

    let http_client = Client::new();
    let mut last_sent_date: Option<chrono::NaiveDate> = None;
    let target_hour = config.scheduler.daily_schedule_hour_jst;
    let target_minute = config.scheduler.daily_schedule_minute_jst;

    info!(
        "デイリースケジューラを起動しました (毎朝 {:02}:{:02} JST に配信予定)",
        target_hour, target_minute
    );

    // 起動時即時配信が有効な場合、本日分が未送信なら送信
    if config.scheduler.daily_schedule_send_on_startup {
        let today = Local::now().date_naive();
        info!("起動時デイリースケジュール送信を実行します (日付: {})", today);
        match send_daily_for_date(&config, &discord, &http_client, today).await {
            Ok(count) => {
                info!("起動時デイリースケジュール配信が完了しました ({} 件)", count);
                last_sent_date = Some(today);
            }
            Err(e) => {
                warn!("起動時デイリースケジュール配信エラー: {}", e);
            }
        }
    }

    loop {
        let now_local = Local::now();
        let today = now_local.date_naive();

        use chrono::TimeZone;
        let today_target_dt = match today.and_hms_opt(target_hour, target_minute, 0) {
            Some(naive) => match Local.from_local_datetime(&naive).single() {
                Some(dt) => dt,
                None => now_local + Duration::hours(24),
            },
            None => now_local + Duration::hours(24),
        };

        let next_target = if now_local < today_target_dt && last_sent_date != Some(today) {
            today_target_dt
        } else {
            let tomorrow = today + Duration::days(1);
            match tomorrow.and_hms_opt(target_hour, target_minute, 0) {
                Some(naive) => match Local.from_local_datetime(&naive).single() {
                    Some(dt) => dt,
                    None => now_local + Duration::hours(24),
                },
                None => now_local + Duration::hours(24),
            }
        };

        let wait_duration = match (next_target - now_local).to_std() {
            Ok(d) => d,
            Err(_) => std::time::Duration::from_secs(1),
        };

        info!(
            "次回のデイリースケジュール配信予定: {} (残り約 {:.1} 時間)",
            next_target.format("%Y-%m-%d %H:%M:%S"),
            wait_duration.as_secs_f64() / 3600.0
        );

        tokio::select! {
            _ = tokio::time::sleep(wait_duration) => {},
            sig = wait_for_shutdown_signal() => {
                info!("デイリースケジューラがシャットダウン要求 ({}) を受信しました", sig);
                break;
            }
        }

        let target_date = Local::now().date_naive();
        info!("デイリースケジュール配信処理を実行中... (対象日: {})", target_date);

        match send_daily_for_date(&config, &discord, &http_client, target_date).await {
            Ok(count) => {
                info!("✨ 本日のデイリースケジュール配信が完了しました (全 {} 件)", count);
                last_sent_date = Some(target_date);
                // 同一分内での多重発火防止のため60秒待機
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            }
            Err(e) => {
                warn!("デイリースケジュール配信に失敗しました ({}). 5分後に再試行します", e);
                tokio::select! {
                    _ = tokio::time::sleep(std::time::Duration::from_secs(300)) => {},
                    sig = wait_for_shutdown_signal() => {
                        info!("リトライ待機中にシャットダウン要求 ({}) を受信しました", sig);
                        break;
                    }
                }
            }
        }
    }

    info!("デイリースケジューラを停止しました");
    Ok(())
}

/// 指定された JST 日付における当日の全パスを計算し Discord へ送信
async fn send_daily_for_date(
    config: &Config,
    discord: &crate::discord::DiscordClient,
    http_client: &Client,
    date: chrono::NaiveDate,
) -> Result<usize> {
    let satellites = fetch_weather_tles(http_client, &config.satellites).await?;

    use chrono::TimeZone;
    let start_of_day_local = match date.and_hms_opt(0, 0, 0) {
        Some(naive) => Local.from_local_datetime(&naive).single().unwrap_or_else(Local::now),
        None => Local::now(),
    };
    let start_of_day_utc: DateTime<Utc> = start_of_day_local.with_timezone(&Utc);

    // 当日 00:00:00 JST から 24時間をスキャン
    let passes = OrbitPredictor::predict_all_passes(
        &satellites,
        &config.observer,
        start_of_day_utc,
        24,
        config.scheduler.min_elevation_deg,
    )?;

    let day_passes = filter_passes_for_jst_date(&passes, date);
    let date_str = date.format("%Y-%m-%d").to_string();

    discord
        .send_daily_schedule(
            &day_passes,
            &date_str,
            config.observer.latitude,
            config.observer.longitude,
            config.scheduler.min_elevation_deg,
        )
        .await?;

    Ok(day_passes.len())
}

/// 自律常駐監視デーモンのメインループ
/// 【状態遷移ライフサイクル】
/// Idle (待機) ──► Approaching (事前発話) ──► Receiving (録音) ──► Decoding (画像化) ──► Notifying (事後発話)
pub async fn run_daemon(config: Config) -> Result<()> {
    let http_client = Client::new();
    let voice_client = VoicevoxClient::new(config.voicevox.clone());

    info!("パーソナル自律衛星地上局デーモンを起動しました");
    info!(
        "観測地: 緯度 {}, 経度 {}, 最小仰角 {}°",
        config.observer.latitude, config.observer.longitude, config.scheduler.min_elevation_deg
    );

    // 非同期デコードワーカの起動 (時分割並行パイプライン: 録音完了直後にSDRを解放)
    let (decode_tx, decode_rx) = tokio::sync::mpsc::channel::<crate::worker::DecodeJob>(32);
    let discord_arc = std::sync::Arc::new(crate::discord::DiscordClient::new(config.discord.clone()));
    let voice_arc = std::sync::Arc::new(voice_client.clone());
    tokio::spawn(crate::worker::run_worker(decode_rx, discord_arc.clone(), voice_arc));

    // デイリースケジューラの独立起動 (毎朝定時に本日のスケジュールをDiscord自動配信)
    let config_clone = config.clone();
    let discord_for_scheduler = discord_arc.clone();
    tokio::spawn(async move {
        if let Err(e) = run_daily_scheduler(config_clone, discord_for_scheduler).await {
            error!("デイリースケジューラで予期せぬエラーが発生しました: {}", e);
        }
    });

    let mut last_tle_update = Utc::now() - Duration::hours(25);
    let mut cached_satellites = Vec::new();
    let mut is_first_run = true;

    loop {
        let now = Utc::now();

        // ---------------------------------------------------------------------
        // 1. TLE の更新 (初回起動時、または設定された更新間隔が経過したとき)
        // ---------------------------------------------------------------------
        if now.signed_duration_since(last_tle_update)
            >= Duration::hours(config.scheduler.tle_update_interval_hours as i64)
            || cached_satellites.is_empty()
        {
            match fetch_weather_tles(&http_client, &config.satellites).await {
                Ok(sats) => {
                    info!("TLE の更新に成功しました ({} 機)", sats.len());
                    cached_satellites = sats;
                    last_tle_update = now;
                }
                Err(e) => {
                    warn!("TLE 更新失敗 (ローカルキャッシュを継続使用): {}", e);
                    if cached_satellites.is_empty() {
                        // 初回取得すら失敗した場合は1分待機してリトライ
                        tokio::select! {
                            _ = tokio::time::sleep(tokio::time::Duration::from_secs(60)) => continue,
                            _ = tokio::signal::ctrl_c() => break,
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // 2. 直近24時間の通過パスを計算し、次に到来する衛星を特定
        // ---------------------------------------------------------------------
        let passes = match OrbitPredictor::predict_all_passes(
            &cached_satellites,
            &config.observer,
            now,
            24,
            config.scheduler.min_elevation_deg,
        ) {
            Ok(p) => p,
            Err(e) => {
                error!("パス計算エラー: {}", e);
                tokio::select! {
                    _ = tokio::time::sleep(tokio::time::Duration::from_secs(60)) => continue,
                    _ = tokio::signal::ctrl_c() => break,
                }
            }
        };

        // LOS（通過終了）が現在より未来にある最も近いパスを特定
        let next_pass = passes.iter().find(|p| p.los > now).cloned();

        let pass = match next_pass {
            Some(p) => p,
            None => {
                info!("直近24時間に対象パスがありません。1時間省電力待機します");
                tokio::select! {
                    _ = tokio::time::sleep(tokio::time::Duration::from_secs(3600)) => continue,
                    _ = tokio::signal::ctrl_c() => break,
                }
            }
        };

        let aos_local: DateTime<Local> = DateTime::from(pass.aos);
        let los_local: DateTime<Local> = DateTime::from(pass.los);
        let peak_dir = azimuth_to_direction(pass.peak_azimuth_deg);
        info!(
            "次の通過予定: {} (AOS: {}, LOS: {}, 最大仰角: {:.1}° 方角: {})",
            pass.satellite_name,
            aos_local.format("%H:%M:%S"),
            los_local.format("%H:%M:%S"),
            pass.max_elevation_deg,
            peak_dir
        );

        // 初回起動時のみ、ずんだもんが元気に挨拶と次回の予定をアナウンス
        if is_first_run {
            is_first_run = false;
            let startup_text = format!(
                "NOAA自律地上局デーモンを起動したのだ！次の通過予定は{}、{}なのだ！",
                aos_local.format("%H時%M分"),
                pass.satellite_name
            );
            let _ = voice_client.speak(&startup_text).await;
        }

        // ---------------------------------------------------------------------
        // 3. 事前通知時刻 (AOS - pre_alert_minutes) の待機
        // ---------------------------------------------------------------------
        let pre_alert_time = pass.aos - Duration::milliseconds((config.scheduler.pre_alert_minutes * 60_000.0) as i64);

        if pre_alert_time > Utc::now() {
            if !wait_until(pre_alert_time, "事前通知").await {
                break;
            }
        }

        // ---------------------------------------------------------------------
        // 4. ずんだもん事前通知発話
        // ---------------------------------------------------------------------
        let alert_text = format!(
            "まもなく{}が通過するのだ！最大仰角は{:.0}度、{}の空なのだ！",
            pass.satellite_name, pass.max_elevation_deg, peak_dir
        );
        let _ = voice_client.speak(&alert_text).await;

        // ---------------------------------------------------------------------
        // 5. 実際の AOS (録音開始時刻) まで待機
        // ---------------------------------------------------------------------
        if pass.aos > Utc::now() {
            if !wait_until(pass.aos, "録音開始 (AOS)").await {
                break;
            }
        }

        // ---------------------------------------------------------------------
        // 6. 受信・録音開始 (SDR プロセス起動)
        // ---------------------------------------------------------------------
        let session_dir = PathBuf::from(&config.storage.output_dir).join(format!(
            "{}_{}",
            aos_local.format("%Y%m%d_%H%M%S"),
            pass.satellite_name.replace(' ', "")
        ));

        let record_path = if pass.signal_type.is_raw_iq() {
            session_dir.join("raw.u8")
        } else {
            session_dir.join("raw.wav")
        };

        // AOS 受信開始アナウンス
        let aos_text = format!(
            "{}が地平線から昇ってきたのだ！{}の受信録音を開始するのだ！",
            pass.satellite_name,
            pass.signal_type.name()
        );
        let _ = voice_client.speak(&aos_text).await;

        let receiver = match ReceiverSession::start(pass.frequency_hz, pass.signal_type, &config.sdr, &record_path).await {
            Ok(r) => r,
            Err(e) => {
                error!("録音プロセスの起動に失敗しました: {}", e);
                continue;
            }
        };

        // ---------------------------------------------------------------------
        // 7. LOS 到達まで録音継続
        // ---------------------------------------------------------------------
        if pass.los > Utc::now() {
            if !wait_until(pass.los, "衛星通過録音 (LOS)").await {
                info!("録音中にシャットダウン要求を受信。プロセスを停止します");
                let _ = receiver.stop().await;
                break;
            }
        }

        // ---------------------------------------------------------------------
        // 8. 録音停止 (SIGINT 送信 & 正常クローズ)
        // ---------------------------------------------------------------------
        let saved_record = match receiver.stop().await {
            Ok(p) => p,
            Err(e) => {
                warn!("録音停止処理警告: {}", e);
                record_path.clone()
            }
        };

        // LOS 録音終了・デコード開始アナウンス
        let los_text = format!(
            "{}が地平線下に沈んだのだ！録音完了、画像デコードを開始するのだ！",
            pass.satellite_name
        );
        let _ = voice_client.speak(&los_text).await;

        // ---------------------------------------------------------------------
        // 9. 非同期デコードワーカへジョブ投入 (時分割並行パイプライン)
        // ---------------------------------------------------------------------
        info!("非同期ワーカへデコードジョブを投入: {:?}", saved_record);
        if let Err(e) = decode_tx
            .send(crate::worker::DecodeJob {
                pass: pass.clone(),
                raw_path: saved_record,
                session_dir: session_dir.clone(),
            })
            .await
        {
            error!("ワーカへのジョブ投入に失敗しました: {}", e);
        }

        // 次のパス待機へ向けて少し休止 (10秒)
        tokio::select! {
            _ = tokio::time::sleep(tokio::time::Duration::from_secs(10)) => {},
            sig = wait_for_shutdown_signal() => {
                info!("パス間待機中にシャットダウン要求 ({}) を受信しました", sig);
                break;
            }
        }
    }

    let _ = voice_client.speak("観測デーモンを終了するのだ！お疲れ様なのだ！").await;
    info!("デーモンを終了しました");
    Ok(())
}

/// 指定された UTC 時刻まで、短周期ポーリング (最大5秒刻み) で堅牢に待機する
/// 【SRE的堅牢性】
/// 1. 壁時計 (Wall Clock: UTC) を毎回評価するため、NTP補正やサスペンド復帰による時間ズレを即時検知。
/// 2. 定期的なハートビートログにより、パイプバッファリングやプロセスの生存状態を可視化。
/// 3. Ctrl+C (SIGINT) および systemd 停止要求 (SIGTERM) による Graceful Shutdown に即応。
/// 戻り値: シャットダウンシグナルを受信した場合は false、時刻に到達した場合は true
async fn wait_until(target_time: DateTime<Utc>, label: &str) -> bool {
    let mut last_log_time = Utc::now();
    let initial_wait = (target_time - Utc::now()).num_seconds().max(0);
    info!(
        "{}まで待機を開始します (目標: {}, 残り {} 秒)",
        label,
        target_time.with_timezone(&Local).format("%H:%M:%S"),
        initial_wait
    );

    loop {
        let now = Utc::now();
        if now >= target_time {
            break;
        }

        let remaining_secs = (target_time - now).num_seconds().max(0) as u64;

        // 60秒以上待つ場合は、1分ごとにハートビートログを出力
        if (now - last_log_time).num_seconds() >= 60 && remaining_secs >= 30 {
            info!(
                "{}まで待機中... (残り {} 秒 / 約 {:.1} 分)",
                label,
                remaining_secs,
                remaining_secs as f64 / 60.0
            );
            last_log_time = now;
        }

        // 次の確認まで最大 5 秒スリープ (残り時間が短い場合はその秒数)
        let sleep_secs = remaining_secs.min(5).max(1);

        tokio::select! {
            _ = tokio::time::sleep(tokio::time::Duration::from_secs(sleep_secs)) => {},
            sig = wait_for_shutdown_signal() => {
                info!("待機中にシャットダウン要求 ({}) を受信しました", sig);
                return false;
            }
        }
    }

    true
}

/// シャットダウンシグナル (SIGINT または SIGTERM) を待ち受けるヘルパー関数
/// 【systemd 安定動作と Graceful Shutdown】
/// - `systemctl stop` や `systemctl restart` 時、systemd は対象サービスに `SIGTERM` を送信します。
/// - Linux の標準動作では SIGTERM をハンドリングしないと即時異常終了となり、
///   録音中のSDR子プロセスが孤立したり、WAVヘッダが未確定のまま破損する原因となります。
/// - 従来の Ctrl+C (`SIGINT`) に加え、Unix シグナルとして `SIGTERM` を非同期イベントループで監視し、
///   どちらを受信しても統一的に安全な終了シーケンス（SDR停止・ヘッダ確定）へ移行させます。
pub async fn wait_for_shutdown_signal() -> &'static str {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
        "SIGINT (Ctrl+C)"
    };

    #[cfg(unix)]
    let sigterm = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut stream) => {
                stream.recv().await;
                "SIGTERM (systemd stop/restart)"
            }
            Err(e) => {
                error!("SIGTERM ハンドラの初期化に失敗しました: {}", e);
                std::future::pending::<&'static str>().await
            }
        }
    };

    #[cfg(not(unix))]
    let sigterm = std::future::pending::<&'static str>();

    tokio::select! {
        sig = ctrl_c => sig,
        sig = sigterm => sig,
    }
}

