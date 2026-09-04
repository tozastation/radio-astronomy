use crate::config::Config;
use crate::decoder::Decoder;
use crate::orbit::{fetch_weather_tles, OrbitPredictor};
use crate::receiver::ReceiverSession;
use crate::voicevox::VoicevoxClient;
use anyhow::Result;
use chrono::{DateTime, Duration, Local, Utc};
use log::{error, info, warn};
use reqwest::Client;
use std::path::PathBuf;

/// 今後24時間の通過予定一覧をコンソールに出力
pub async fn show_schedule(config: &Config) -> Result<()> {
    let http_client = Client::new();
    println!("🛰️  CelesTrak から最新 TLE を取得中...");
    let satellites = fetch_weather_tles(&http_client).await?;

    let now = Utc::now();
    let passes = OrbitPredictor::predict_all_passes(
        &satellites,
        &config.observer,
        now,
        24,
        config.scheduler.min_elevation_deg,
    )?;

    println!("\n==========================================================================================");
    println!("📡 NOAA 気象衛星 通過予定スケジュール (今後24時間 / 観測地: 緯度 {:.4}, 経度 {:.4})", config.observer.latitude, config.observer.longitude);
    println!("==========================================================================================");
    println!("{:<10} | {:<12} | {:<20} | {:<20} | {:<10}", "衛星名", "周波数", "通過開始 (AOS / JST)", "通過終了 (LOS / JST)", "最大仰角");
    println!("------------------------------------------------------------------------------------------");

    if passes.is_empty() {
        println!("※ 仰角 {:.1}° 以上の通過パスは見つかりませんでした", config.scheduler.min_elevation_deg);
    } else {
        for pass in passes {
            let aos_local: DateTime<Local> = DateTime::from(pass.aos);
            let los_local: DateTime<Local> = DateTime::from(pass.los);
            let freq_mhz = pass.frequency_hz as f64 / 1_000_000.0;

            println!(
                "{:<10} | {:>7.4} MHz | {} | {} | {:>4.1}°",
                pass.satellite_name,
                freq_mhz,
                aos_local.format("%Y-%m-%d %H:%M:%S"),
                los_local.format("%Y-%m-%d %H:%M:%S"),
                pass.max_elevation_deg
            );
        }
    }
    println!("==========================================================================================\n");

    Ok(())
}

/// 自律常駐監視デーモンのメインループ
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

    loop {
        let now = Utc::now();

        // 1. TLE の更新 (起動時および指定時間ごと)
        if now.signed_duration_since(last_tle_update)
            >= Duration::hours(config.scheduler.tle_update_interval_hours as i64)
            || cached_satellites.is_empty()
        {
            match fetch_weather_tles(&http_client).await {
                Ok(sats) => {
                    info!("TLE の更新に成功しました ({} 機)", sats.len());
                    cached_satellites = sats;
                    last_tle_update = now;
                }
                Err(e) => {
                    warn!("TLE 更新失敗 (キャッシュを継続使用): {}", e);
                    if cached_satellites.is_empty() {
                        tokio::select! {
                            _ = tokio::time::sleep(tokio::time::Duration::from_secs(60)) => continue,
                            _ = tokio::signal::ctrl_c() => break,
                        }
                    }
                }
            }
        }

        // 2. 今後のパスを計算
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

        // 次に到来するパス（LOS が現在時刻より未来のもの）を特定
        let next_pass = passes.into_iter().find(|p| p.los > now);
        let pass = match next_pass {
            Some(p) => p,
            None => {
                info!("直近24時間に対象パスがありません。1時間待機します");
                tokio::select! {
                    _ = tokio::time::sleep(tokio::time::Duration::from_secs(3600)) => continue,
                    _ = tokio::signal::ctrl_c() => break,
                }
            }
        };

        let aos_local: DateTime<Local> = DateTime::from(pass.aos);
        let los_local: DateTime<Local> = DateTime::from(pass.los);
        info!(
            "次の通過予定: {} (AOS: {}, LOS: {}, 最大仰角: {:.1}°)",
            pass.satellite_name,
            aos_local.format("%H:%M:%S"),
            los_local.format("%H:%M:%S"),
            pass.max_elevation_deg
        );

        // 3. 事前通知時刻 (AOS - pre_alert_minutes) の計算
        let pre_alert_time = pass.aos - Duration::milliseconds((config.scheduler.pre_alert_minutes * 60_000.0) as i64);

        // 事前通知時刻まで待機
        if pre_alert_time > now {
            let wait_secs = (pre_alert_time - now).num_seconds().max(0) as u64;
            info!("事前通知まで待機中 ({} 秒)...", wait_secs);

            tokio::select! {
                _ = tokio::time::sleep(tokio::time::Duration::from_secs(wait_secs)) => {},
                _ = tokio::signal::ctrl_c() => {
                    info!("シャットダウン要求を受信しました");
                    break;
                }
            }
        }

        // 4. ずんだもん事前通知発話
        let alert_text = format!(
            "まもなく{}が通過するのだ！最大仰角は{:.0}度、受信を試みるのだ！",
            pass.satellite_name, pass.max_elevation_deg
        );
        let _ = voice_client.speak(&alert_text).await;

        // 5. 実際の AOS (録音開始時刻) まで待機
        let now_before_aos = Utc::now();
        if pass.aos > now_before_aos {
            let wait_secs = (pass.aos - now_before_aos).num_seconds().max(0) as u64;
            info!("録音開始 (AOS) まで待機中 ({} 秒)...", wait_secs);

            tokio::select! {
                _ = tokio::time::sleep(tokio::time::Duration::from_secs(wait_secs)) => {},
                _ = tokio::signal::ctrl_c() => {
                    info!("シャットダウン要求を受信しました");
                    break;
                }
            }
        }

        // 6. 受信・録音開始
        let session_dir = PathBuf::from(&config.storage.output_dir).join(format!(
            "{}_{}",
            aos_local.format("%Y%m%d_%H%M%S"),
            pass.satellite_name.replace(' ', "")
        ));
        let wav_path = session_dir.join("raw.wav");
        let png_path = session_dir.join("image.png");

        let receiver = match ReceiverSession::start(pass.frequency_hz, &wav_path) {
            Ok(r) => r,
            Err(e) => {
                error!("録音プロセスの起動に失敗しました: {}", e);
                continue;
            }
        };

        // 7. LOS 到達まで録音継続
        let now_during_recording = Utc::now();
        if pass.los > now_during_recording {
            let record_duration = (pass.los - now_during_recording).num_seconds().max(1) as u64;
            info!("衛星通過録音中 (残り {} 秒)...", record_duration);

            tokio::select! {
                _ = tokio::time::sleep(tokio::time::Duration::from_secs(record_duration)) => {},
                _ = tokio::signal::ctrl_c() => {
                    info!("録音中にシャットダウン要求を受信。プロセスを停止します");
                    let _ = receiver.stop().await;
                    break;
                }
            }
        }

        // 8. 録音停止
        let saved_wav = match receiver.stop().await {
            Ok(p) => p,
            Err(e) => {
                warn!("録音停止処理警告: {}", e);
                wav_path.clone()
            }
        };

        // 9. 画像デコード
        info!("画像デコード処理を実行中: {:?}", saved_wav);
        match Decoder::decode_apt(&saved_wav, &png_path).await {
            Ok(()) => {
                info!("画像生成完了: {:?}", png_path);
                let success_text = format!(
                    "{}の受信とデコードに成功したのだ！新しい画像を確認するのだ！",
                    pass.satellite_name
                );
                let _ = voice_client.speak(&success_text).await;
            }
            Err(e) => {
                warn!("デコード失敗: {}", e);
                let fail_text = "画像のデコードに失敗したのだ…電波が弱かったかもしれないのだ".to_string();
                let _ = voice_client.speak(&fail_text).await;
            }
        }

        // 1パス完了、少し休止して次へ
        tokio::time::sleep(tokio::time::Duration::from_secs(10)).await;
    }

    info!("デーモンを終了しました");
    Ok(())
}
