use crate::decoder::DecoderEngine;
use crate::discord::DiscordClient;
use crate::orbit::SatellitePass;
use crate::voicevox::VoicevoxClient;
use log::{error, info};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::mpsc;

// =============================================================================
// ⚙️ 非同期デコードワーカ (Worker Pipeline)
// -----------------------------------------------------------------------------
// 【アーキテクチャ: 時分割並行パイプライン】
// SDR による電波録音 (I/O) は衛星通過の決められた時間に完了しなければなりませんが、
// satdump や gr-satellites による画像復調・DSP (CPU) は数分〜十数分かかることがあります。
// 本モジュールは Tokio MPSC キューを通じて録音完了イベント（DecodeJob）を非同期に受領し、
// 次の通過録音をブロックすることなくバックグラウンドでデコードと Discord / ずんだもん通知を
// 自律的に処理します。
// =============================================================================

#[derive(Debug, Clone)]
pub struct DecodeJob {
    pub pass: SatellitePass,
    pub raw_path: PathBuf,
    pub session_dir: PathBuf,
}

pub async fn run_worker(
    mut rx: mpsc::Receiver<DecodeJob>,
    discord: Arc<DiscordClient>,
    voicevox: Arc<VoicevoxClient>,
) {
    info!("非同期デコードワーカ起動完了 (バックグラウンド待機中...)");

    while let Some(job) = rx.recv().await {
        info!(
            "デコードジョブ受領: 衛星 {}, 信号方式 {}, 生ファイル {:?}",
            job.pass.satellite_name,
            job.pass.signal_type.name(),
            job.raw_path
        );

        let pass_name = job.pass.satellite_name.clone();
        let decode_res = DecoderEngine::decode(&job.pass, &job.raw_path, &job.session_dir).await;

        match decode_res {
            Ok(result) => {
                info!("デコード完了: {:?}", result);

                // ずんだもん音声通知
                let voice_msg = if result.image_path.is_some() {
                    format!("{}の画像デコードが完了したのだ！画像を確認するのだ！", pass_name)
                } else {
                    format!("{}のデータ保存が完了したのだ！", pass_name)
                };
                let _ = voicevox.speak(&voice_msg).await;

                // Discord 通知
                let pass_time_str = format!(
                    "{} 〜 {}",
                    chrono::DateTime::<chrono::Local>::from(job.pass.aos).format("%Y-%m-%d %H:%M:%S"),
                    chrono::DateTime::<chrono::Local>::from(job.pass.los).format("%H:%M:%S")
                );
                let dir_str = crate::orbit::azimuth_to_direction(job.pass.peak_azimuth_deg);
                let freq_mhz = job.pass.frequency_hz as f64 / 1_000_000.0;

                let summary = result.telemetry_summary.as_deref().unwrap_or("デコード完了");
                let content = format!(
                    "🛰️ **[{}] 受信・デコード完了**\n\
                    - 方式: {}\n\
                    - 周波数: {:.4} MHz\n\
                    - 通過時刻: {}\n\
                    - 最大仰角: {:.1}° ({})\n\
                    - 概要: {}",
                    pass_name,
                    job.pass.signal_type.name(),
                    freq_mhz,
                    pass_time_str,
                    job.pass.max_elevation_deg,
                    dir_str,
                    summary
                );

                if result.image_path.is_some() {
                    let _ = discord
                        .send_satellite_pass_report(
                            &pass_name,
                            job.pass.max_elevation_deg,
                            dir_str,
                            job.pass.frequency_hz,
                            &pass_time_str,
                            result.image_path.as_deref(),
                            None,
                        )
                        .await;
                } else {
                    let _ = discord.send_text(&content).await;
                }
            }
            Err(e) => {
                error!("デコード処理中に予期せぬエラーが発生しました: {}", e);
                let err_msg = format!("⚠️ **[{}] デコードエラー**: {}", pass_name, e);
                let _ = discord.send_text(&err_msg).await;
            }
        }
    }

    info!("非同期デコードワーカ終了");
}
