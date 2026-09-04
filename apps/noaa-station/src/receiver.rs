use anyhow::{Context, Result};
use log::{info, warn};
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::process::{Child, Command};

// =============================================================================
// 📻 SDR 受信・録音制御モジュール (Receiver)
// -----------------------------------------------------------------------------
// 【言語対比】
// - `tokio::process::Command`: Go の `exec.Command` や Python の `subprocess.Popen` に相当。
//   ただし Tokio の非同期ランタイムに統合されており、非同期でプロセスの終了待ち（`.wait().await`）ができます。
// - 所有権による状態遷移の強制 (`pub async fn stop(mut self)`):
//   `&self` ではなく `self`（値渡し）で受け取ることで、「stop() を呼んだらインスタンスの所有権が消費され、
//   二度と録音停止や多重実行が呼べなくなる」というライフサイクル安全性をコンパイル時に保証します。
// =============================================================================

/// rtl_fm 録音用引数を構築
/// - `-M wfm`: 帯域の広いFM復調（NOAA-APT信号は帯域幅約34kHz〜40kHzあるため）
/// - `-s 60k`: SDRのサンプリングレート (60kSPS)
/// - `-r 11025`: 音声サンプリングレート (11025Hz。noaa-apt デコーダの標準フォーマット)
/// - `-E wav -F 9`: WAV形式で直接出力し、DCオフセットを除去
pub fn build_rtl_fm_args(freq_hz: u64, output_wav_path: &Path) -> Vec<String> {
    vec![
        "-M".to_string(),
        "wfm".to_string(),
        "-f".to_string(),
        freq_hz.to_string(),
        "-s".to_string(),
        "60k".to_string(),
        "-r".to_string(),
        "11025".to_string(),
        "-E".to_string(),
        "wav".to_string(),
        "-F".to_string(),
        "9".to_string(),
        output_wav_path.to_string_lossy().to_string(),
    ]
}

/// 衛星通過中の SDR 録音セッション
pub struct ReceiverSession {
    child: Child,           // 実行中の子プロセスハンドル
    output_path: PathBuf,   // 出力先WAVファイルパス
}

impl ReceiverSession {
    /// rtl_fm をバックグラウンド起動して WAV 録音を開始
    pub fn start(freq_hz: u64, output_wav_path: &Path) -> Result<Self> {
        info!(
            "SDR 録音開始: 周波数 {} Hz, 出力 {:?}",
            freq_hz, output_wav_path
        );

        // 出力先ディレクトリの存在確認・自動作成 (mkdir -p)
        if let Some(parent) = output_wav_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("ディレクトリ作成失敗: {:?}", parent))?;
        }

        let args = build_rtl_fm_args(freq_hz, output_wav_path);

        // rtl_fm 子プロセスを非同期起動
        let child = Command::new("rtl_fm")
            .args(&args)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .context("rtl_fm コマンドの起動に失敗しました。RTL-SDRドライバが導入されているか確認してください")?;

        Ok(Self {
            child,
            output_path: output_wav_path.to_path_buf(),
        })
    }

    /// 衛星通過終了時、SIGINT を送信して優雅に録音を終了する (Graceful Shutdown)
    /// 【SRE的背景】
    /// `SIGKILL` (kill -9) で強制終了すると、WAVファイルの末尾にあるデータサイズヘッダが
    /// 書き込まれずにファイルが破損（デコーダで読めなくなる）します。
    /// `SIGINT` (Ctrl+C と同等) を送ることで、rtl_fm は安全にバッファをフラッシュして正常なWAVとして閉じます。
    pub async fn stop(mut self) -> Result<PathBuf> {
        info!("SDR 録音停止要求送信中: {:?}", self.output_path);

        if let Some(id) = self.child.id() {
            let pid = Pid::from_raw(id as i32);
            if let Err(e) = kill(pid, Signal::SIGINT) {
                warn!("SIGINT 送信失敗 (すでに終了している可能性): {}", e);
            }
        }

        // プロセスの正常終了を非同期待機 (Go の cmd.Wait() に相当)
        match self.child.wait().await {
            Ok(status) => {
                info!("rtl_fm 正常終了: status {}", status);
            }
            Err(e) => {
                warn!("rtl_fm 待機エラー: {}", e);
            }
        }

        Ok(self.output_path)
    }
}
