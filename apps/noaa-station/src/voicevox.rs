use crate::config::VoicevoxConfig;
use anyhow::{Context, Result};
use log::{info, warn};
use reqwest::Client;
use std::process::Command;

/// VOICEVOX Engine との通信および音声再生クライアント
pub struct VoicevoxClient {
    config: VoicevoxConfig,
    http_client: Client,
}

impl VoicevoxClient {
    pub fn new(config: VoicevoxConfig) -> Self {
        let http_client = Client::builder()
            .timeout(std::time::Duration::from_secs(3))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            config,
            http_client,
        }
    }

    /// 音声クエリ生成用のURLを構築
    pub fn audio_query_url(&self, text: &str) -> String {
        format!(
            "{}/audio_query?text={}&speaker={}",
            self.config.host.trim_end_matches('/'),
            urlencoding::encode(text),
            self.config.speaker_id
        )
    }

    /// 音声波形合成用のURLを構築
    pub fn synthesis_url(&self) -> String {
        format!(
            "{}/synthesis?speaker={}",
            self.config.host.trim_end_matches('/'),
            self.config.speaker_id
        )
    }

    /// 指定されたテキストをずんだもんの声で発話・再生する
    pub async fn speak(&self, text: &str) -> Result<()> {
        if !self.config.enabled {
            info!("[VOICEVOX無効] 発話テキスト: {}", text);
            return Ok(());
        }

        info!("ずんだもん発話開始: {}", text);

        // 1. audio_query API を呼び出して合成用クエリJSONを取得
        let query_url = self.audio_query_url(text);
        let query_resp = match self.http_client.post(&query_url).send().await {
            Ok(resp) => resp,
            Err(e) => {
                warn!("VOICEVOX接続失敗 (スキップして処理継続): {}", e);
                return Ok(());
            }
        };

        if !query_resp.status().is_success() {
            warn!("audio_query エラー: HTTP status {}", query_resp.status());
            return Ok(());
        }

        let query_json: serde_json::Value = query_resp
            .json()
            .await
            .context("クエリJSONのパースに失敗しました")?;

        // 2. synthesis API を呼び出して WAV 音声バイナリを取得
        let synth_url = self.synthesis_url();
        let synth_resp = match self.http_client.post(&synth_url).json(&query_json).send().await {
            Ok(resp) => resp,
            Err(e) => {
                warn!("VOICEVOX音声合成リクエスト失敗: {}", e);
                return Ok(());
            }
        };

        if !synth_resp.status().is_success() {
            warn!("synthesis エラー: HTTP status {}", synth_resp.status());
            return Ok(());
        }

        let wav_bytes = synth_resp
            .bytes()
            .await
            .context("音声WAVバイナリの受信に失敗しました")?;

        // 3. 一時ファイルに保存して aplay / ffplay で再生
        let tmp_wav = std::env::temp_dir().join("zundamon_notification.wav");
        tokio::fs::write(&tmp_wav, &wav_bytes)
            .await
            .context("一時WAVファイルの書き込みに失敗しました")?;

        let tmp_wav_clone = tmp_wav.clone();
        tokio::task::spawn_blocking(move || {
            let status = Command::new("aplay")
                .arg("-q")
                .arg(&tmp_wav_clone)
                .status()
                .or_else(|_| {
                    Command::new("ffplay")
                        .args(["-nodisp", "-autoexit", "-loglevel", "quiet"])
                        .arg(&tmp_wav_clone)
                        .status()
                });
            if let Err(e) = status {
                warn!("音声プレイヤー (aplay/ffplay) の実行に失敗しました: {}", e);
            }
        })
        .await?;

        Ok(())
    }
}
