use crate::config::Config;
use crate::decoder::Decoder;
use crate::orbit::{azimuth_to_direction, fetch_weather_tles, OrbitPredictor, SignalType};
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
//   「指定秒数のスリープ待機」と「Ctrl+C によるシャットダウン要求」を同時に監視し、
//   どちらか早く発生したイベントを処理します。待機中に Ctrl+C が押された場合は
//   直ちにスリープが安全にキャンセルされて Graceful Shutdown 処理へ移行します。
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
    println!("📡 気象衛星 通過予定スケジュール (今後24時間 / 観測地: 緯度 {:.4}, 経度 {:.4})", config.observer.latitude, config.observer.longitude);
    println!("=====================================================================================================================");
    println!("{:<15} | {:<12} | {:<24} | {:<20} | {:<20} | {:<18}", "衛星名", "周波数", "信号方式", "通過開始 (AOS / JST)", "通過終了 (LOS / JST)", "最大仰角 (ピーク方位)");
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
                "{:<15} | {:>7.4} MHz | {:<24} | {} | {} | {:>4.1}° ({})",
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

/// 自律常駐監視デーモンのメインループ
/// 【状態遷移ライフサイクル】
/// Idle (待機) ──► Approaching (事前発話) ──► Receiving (録音) ──► Decoding (画像化) ──► Notifying (事後発話)
pub async fn run_daemon(config: Config) -> Result<()> {
    let http_client = Client::new();
    let voice_client = VoicevoxClient::new(config.voicevox.clone());

    info!("NOAA 自律地上局デーモンを起動しました");
    info!(
        "観測地: 緯度 {}, 経度 {}, 最小仰角 {}°",
        config.observer.latitude, config.observer.longitude, config.scheduler.min_elevation_deg
    );

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
        let png_path = session_dir.join("image.png");

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
        // 9. 画像デコード & ずんだもん事後通知 & Discord画像自動送信
        // ---------------------------------------------------------------------
        info!("画像デコード処理を実行中: {:?}", saved_record);
        let discord_client = crate::discord::DiscordClient::new(config.discord.clone());
        let pass_time_str = format!(
            "{} 〜 {}",
            aos_local.format("%Y-%m-%d %H:%M:%S"),
            los_local.format("%H:%M:%S")
        );

        let decode_result = match pass.signal_type {
            SignalType::Apt => {
                match Decoder::decode_apt(&saved_record, &png_path).await {
                    Ok(()) => Ok(png_path.clone()),
                    Err(e) => Err(e),
                }
            }
            SignalType::Lrpt => {
                Decoder::decode_meteor_lrpt(&saved_record, &session_dir).await
            }
            SignalType::CubeSatSsdv | SignalType::CubeSatSstv | SignalType::CubeSatTelemetry | SignalType::MorseCw => {
                Decoder::decode_cubesat(&pass, &saved_record, &session_dir).await
            }
            SignalType::IssSstv => {
                Decoder::decode_iss_sstv(&saved_record, &session_dir).await
            }
        };

        match decode_result {
            Ok(image_path) => {
                info!("画像生成完了: {:?}", image_path);
                let success_text = format!(
                    "{}の受信とデコードに成功したのだ！新しい画像を確認するのだ！",
                    pass.satellite_name
                );
                let _ = voice_client.speak(&success_text).await;

                // 次の通過パス（未来のパス）を検索
                let next_upcoming_pass = passes.iter().find(|p| p.aos > pass.los);
                let next_pass_str = next_upcoming_pass.map(|np| {
                    let np_aos: DateTime<Local> = DateTime::from(np.aos);
                    let np_los: DateTime<Local> = DateTime::from(np.los);
                    let np_dir = azimuth_to_direction(np.peak_azimuth_deg);
                    format!(
                        "🛰️ **{}** (周波数: {:.4} MHz / {})\n時間: {} 〜 {}\n最大仰角: {:.1}° ({})",
                        np.satellite_name,
                        np.frequency_hz as f64 / 1_000_000.0,
                        np.signal_type.name(),
                        np_aos.format("%H:%M:%S"),
                        np_los.format("%H:%M:%S"),
                        np.max_elevation_deg,
                        np_dir
                    )
                });

                // Discord Webhook へ画像付きレポートを送信
                if config.discord.enabled {
                    let _ = discord_client
                        .send_satellite_pass_report(
                            &format!("{} ({})", pass.satellite_name, pass.signal_type.name()),
                            pass.max_elevation_deg,
                            peak_dir,
                            pass.frequency_hz,
                            &pass_time_str,
                            Some(&image_path),
                            next_pass_str.as_deref(),
                        )
                        .await;

                    let _ = voice_client
                        .speak("Discordに雲画像を送信したのだ！スマホを確認してみてほしいのだ！")
                        .await;
                }
            }
            Err(e) => {
                warn!("デコード失敗: {}", e);
                let fail_text = format!(
                    "画像のデコードに失敗したのだ…電波が弱かったかもしれないのだ (詳細: {})",
                    e
                );
                let _ = voice_client.speak(&fail_text).await;
            }
        }

        // 次のパス待機へ向けて少し休止 (10秒)
        tokio::time::sleep(tokio::time::Duration::from_secs(10)).await;
    }

    let _ = voice_client.speak("観測デーモンを終了するのだ！お疲れ様なのだ！").await;
    info!("デーモンを終了しました");
    Ok(())
}

/// 指定された UTC 時刻まで、短周期ポーリング (最大5秒刻み) で堅牢に待機する
/// 【SRE的堅牢性】
/// 1. 壁時計 (Wall Clock: UTC) を毎回評価するため、NTP補正やサスペンド復帰による時間ズレを即時検知。
/// 2. 定期的なハートビートログにより、パイプバッファリングやプロセスの生存状態を可視化。
/// 3. Ctrl+C による Graceful Shutdown に即応。
/// 戻り値: Ctrl+C を受信した場合は false、時刻に到達した場合は true
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
            _ = tokio::signal::ctrl_c() => {
                info!("待機中にシャットダウン要求 (Ctrl+C) を受信しました");
                return false;
            }
        }
    }

    true
}

