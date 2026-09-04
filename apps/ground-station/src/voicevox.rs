use crate::config::VoicevoxConfig;
use anyhow::{Context, Result};
use log::{info, warn};
use reqwest::Client;
use std::process::Command;

// =============================================================================
// VOICEVOX API クライアント (ずんだもん音声合成)
// -----------------------------------------------------------------------------
// 【言語対比】
// - struct にフィールド（設定やHTTPクライアント）を持たせ、`impl` でメソッドを定義するのは
//   Go で構造体にレシーバ関数を生やす設計（例: `type VoicevoxClient struct`）と全く同じです。
// - `reqwest::Client` は内部にコネクションプールを保持しており、スレッドセーフ（Go の `http.Client` と同等）
//   のため、インスタンスを使い回すのが Rust のベストプラクティスです。
// =============================================================================

/// VOICEVOX Engine との通信および音声再生クライアント
pub struct VoicevoxClient {
    config: VoicevoxConfig,
    http_client: Client,
}

impl VoicevoxClient {
    /// クライアントの初期化
    pub fn new(config: VoicevoxConfig) -> Self {
        // HTTPクライアントのタイムアウトを設定値（デフォルト15秒）に設定。
        // GPD Pocket3 のような低電力CPU環境での長文音声合成推論（4〜8秒程度）
        // でもタイムアウトせず、確実に音声を生成できるようにします。
        let timeout_secs = config.timeout_secs;
        let http_client = Client::builder()
            .timeout(std::time::Duration::from_secs(timeout_secs))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            config,
            http_client,
        }
    }

    /// 音声クエリ生成用のURLを構築
    /// 【言語対比】format! マクロは Python の f-string (`f"{host}/audio_query..."`)
    /// や Go の `fmt.Sprintf` に相当します。
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
    /// 【言語対比】
    /// - `async fn`: TypeScript や Python と同様の非同期関数構文。呼び出し側は `.await` で待機します。
    /// - `&self`: Go でいうレシーバ `(c *VoicevoxClient)` のポインタ参照に相当（所有権を消費しない借用）。
    pub async fn speak(&self, text: &str) -> Result<()> {
        // 設定で無効化されている場合は即時リターン
        if !self.config.enabled {
            info!("[VOICEVOX無効] 発話テキスト: {}", text);
            return Ok(());
        }

        info!("ずんだもん発話開始: {}", text);

        // ---------------------------------------------------------------------
        // 1. audio_query API を呼び出して合成用クエリJSONを取得
        // ---------------------------------------------------------------------
        let query_url = self.audio_query_url(text);

        // 【言語対比】`match` 式は Go の `if resp, err := client.Post(...); err != nil`
        // を網羅的にパターンマッチする構文です。
        let query_resp = match self.http_client.post(&query_url).send().await {
            Ok(resp) => resp,
            Err(e) => {
                // VOICEVOX Engine が起動していない場合でもデーモンを落とさず、
                // 警告ログを出力して正常終了（Ok）とする安全フォールバック設計。
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

        // ---------------------------------------------------------------------
        // 2. synthesis API を呼び出して WAV 音声バイナリを取得
        // ---------------------------------------------------------------------
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

        // ---------------------------------------------------------------------
        // 3. 一時ファイルに保存して aplay / ffplay で再生
        // ---------------------------------------------------------------------
        let tmp_wav = std::env::temp_dir().join("zundamon_notification.wav");
        tokio::fs::write(&tmp_wav, &wav_bytes)
            .await
            .context("一時WAVファイルの書き込みに失敗しました")?;

        let tmp_wav_clone = tmp_wav.clone();

        // 【言語対比】`tokio::task::spawn_blocking`:
        // Go では全ての Goroutine が自動的にOSスレッドを融通しますが、Tokioの非同期ループ内で
        // `Command::status()` のような「同期的で重いOSプロセス呼び出し」を行うと非同期ワーカースレッドが
        // 占有（ブロック）されてしまいます。
        // 【言語対比】`tokio::task::spawn_blocking`:
        // 重い同期コマンド実行（ffplay等の外部プロセス）をブロッキング専用スレッドプールに委譲。
        tokio::task::spawn_blocking(move || {
            play_audio_file(&tmp_wav_clone);
        })
        .await?;

        Ok(())
    }
}

/// 音声WAVファイルを複数のプレイヤー候補で順次再生試行する
/// 【SRE的堅牢性】
/// 1. `ffplay` (WSLg / PulseAudio に完全対応・最優先)
/// 2. `paplay` (PulseAudio 専用クライアント)
/// 3. `aplay` (Linux ALSA 直接出力)
/// 4. `powershell.exe` (WSL2からWindowsホスト側のSoundPlayerを呼び出す究極のフォールバック)
fn play_audio_file(path: &std::path::Path) {
    // 1. ffplay (WSLg / PulseAudio)
    if let Ok(status) = Command::new("ffplay")
        .args(["-nodisp", "-autoexit", "-loglevel", "quiet"])
        .arg(path)
        .status()
    {
        if status.success() {
            return;
        }
    }

    // 2. paplay (PulseAudio)
    if let Ok(status) = Command::new("paplay").arg(path).status() {
        if status.success() {
            return;
        }
    }

    // 3. aplay (ALSA)
    if let Ok(status) = Command::new("aplay").arg("-q").arg(path).status() {
        if status.success() {
            return;
        }
    }

    // 4. Windows 側 PowerShell SoundPlayer へのフォールバック (WSL2環境)
    if let Ok(win_path_out) = Command::new("wslpath").arg("-w").arg(path).output() {
        if win_path_out.status.success() {
            let win_path = String::from_utf8_lossy(&win_path_out.stdout).trim().to_string();
            let ps_script = format!("(New-Object Media.SoundPlayer '{}').PlaySync()", win_path);
            let _ = Command::new("powershell.exe")
                .args(["-NoProfile", "-Command", &ps_script])
                .status();
            return;
        }
    }

    warn!("音声プレイヤー (ffplay/paplay/aplay/powershell) による再生に失敗しました");
}
