use crate::config::SdrConfig;
use anyhow::{Context, Result};
use log::{info, warn};
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::io::{AsyncSeekExt, AsyncWriteExt};
use tokio::process::{Child, Command};
use tokio::task::JoinHandle;

// =============================================================================
// 📻 SDR 受信・録音制御モジュール (Receiver)
// -----------------------------------------------------------------------------
// 【言語対比・SRE的設計】
// 1. 高利得チューニング (-g 45.0):
//    宇宙からの極めて微弱な電波（低SNR）をチューナーのノイズフロアから救い出すため、
//    高利得（40.0〜49.6dB）とFMデエンファシスフィルタ（-E deemp）を標準化。
// 2. Pure Rust による正規 RIFF/WAVE ヘッダの自動生成:
//    rtl_fm は標準出力にヘッダなし生PCMを出力します。本モジュールは先頭に44バイトの
//    WAVヘッダ枠を確保し、録音終了時にストリーミングバイト数を計測してヘッダを
//    確定（Seek&Flush）することで、soxやffmpeg等の外部依存ゼロで完全なWAVを出力します。
// =============================================================================

/// 11025Hz, 16bit, モノラルの標準 RIFF/WAVE ヘッダ (44バイト) を生成
/// 【数学・情報理論】
/// WAVフォーマットは先頭に RIFF チャンク、fmt サブチャンク、data サブチャンクが
/// リトルエンディアンで配置された標準規格です。
pub fn create_wav_header(data_size: u32) -> [u8; 44] {
    let mut header = [0u8; 44];

    // 1. "RIFF" チャンクヘッダ
    header[0..4].copy_from_slice(b"RIFF");
    let file_size = 36u32.saturating_add(data_size);
    header[4..8].copy_from_slice(&file_size.to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");

    // 2. "fmt " サブチャンク (リニアPCM)
    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes()); // サブチャンクサイズ: 16 (PCM)
    header[20..22].copy_from_slice(&1u16.to_le_bytes());  // 音声フォーマット: 1 (Linear PCM)
    header[22..24].copy_from_slice(&1u16.to_le_bytes());  // チャンネル数: 1 (Mono)
    header[24..28].copy_from_slice(&11025u32.to_le_bytes()); // サンプリングレート: 11025 Hz
    let byte_rate = 11025u32 * 1 * 2; // 11025 Hz * 1ch * 16bit/8 = 22050 bytes/sec
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&2u16.to_le_bytes());  // ブロック境界: 2 bytes (1 sample)
    header[34..36].copy_from_slice(&16u16.to_le_bytes()); // ビット深度: 16 bit

    // 3. "data" サブチャンク
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_size.to_le_bytes());

    header
}

/// rtl_fm 録音用引数を構築
/// - `-M fm`: 標準FM復調（NOAA-APT信号の約34kHzに最適化し、ワイドFMのような余計な広帯域ノイズをカット）
/// - `-s 60k`: SDRのサンプリングレート
/// - `-r 11025`: 音声サンプリングレート (noaa-apt デコーダ標準)
/// - `-g <gain>`: チューナー利得 (例: 45.0dB)
/// - `-E deemp`: デエンファシスフィルタ（高周波雑音を抑制）
/// - `-F 9`: 高精度リサンプリングフィルタ
/// - `-`: 標準出力へストリーミング
pub fn build_rtl_fm_args(freq_hz: u64, sdr: &SdrConfig) -> Vec<String> {
    let mut args = vec![
        "-M".to_string(),
        "fm".to_string(),
        "-f".to_string(),
        freq_hz.to_string(),
        "-s".to_string(),
        sdr.sample_rate.to_string(),
        "-r".to_string(),
        "11025".to_string(),
        "-g".to_string(),
        format!("{:.1}", sdr.gain),
        "-E".to_string(),
        "deemp".to_string(),
        "-F".to_string(),
        "9".to_string(),
    ];

    if sdr.ppm_error != 0 {
        args.push("-p".to_string());
        args.push(sdr.ppm_error.to_string());
    }

    // 標準出力に出力
    args.push("-".to_string());
    args
}

/// 衛星通過中の SDR 録音セッション
pub struct ReceiverSession {
    child: Child,
    output_path: PathBuf,
    writer_task: JoinHandle<Result<u64>>,
}

impl ReceiverSession {
    /// rtl_fm をバックグラウンド起動し、生PCMを標準出力から直接ストリーミング受信して
    /// WAVヘッダ付きファイルにリアルタイム書き込み
    pub async fn start(freq_hz: u64, sdr: &SdrConfig, output_wav_path: &Path) -> Result<Self> {
        info!(
            "SDR 録音開始: 周波数 {} Hz, 利得 {:.1} dB, 出力 {:?}",
            freq_hz, sdr.gain, output_wav_path
        );

        // 出力先ディレクトリの存在確認・自動作成 (mkdir -p)
        if let Some(parent) = output_wav_path.parent() {
            tokio::fs::create_dir_all(parent)
                .await
                .with_context(|| format!("ディレクトリ作成失敗: {:?}", parent))?;
        }

        // 録音先ファイルを生成し、先頭に44バイトのダミーWAVヘッダを書き込む
        let mut file = tokio::fs::File::create(output_wav_path)
            .await
            .with_context(|| format!("WAVファイル作成失敗: {:?}", output_wav_path))?;
        file.write_all(&create_wav_header(0))
            .await
            .context("初期WAVヘッダの書き込みに失敗しました")?;

        let args = build_rtl_fm_args(freq_hz, sdr);

        // rtl_fm 子プロセスを起動 (標準出力をパイプで取得)
        let mut child = Command::new("rtl_fm")
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .context("rtl_fm コマンドの起動に失敗しました。RTL-SDRドライバが導入されているか確認してください")?;

        let mut stdout = child
            .stdout
            .take()
            .context("rtl_fm 標準出力の取得に失敗しました")?;

        // バックグラウンドで非同期ストリーミングコピー
        let writer_task = tokio::spawn(async move {
            let copied_bytes = tokio::io::copy(&mut stdout, &mut file)
                .await
                .context("ストリーミング書き込み中にエラーが発生しました")?;
            file.flush().await?;
            Ok(copied_bytes)
        });

        Ok(Self {
            child,
            output_path: output_wav_path.to_path_buf(),
            writer_task,
        })
    }

    /// 衛星通過終了時、SIGINT を送信して優雅に録音を終了し、ファイル先頭の WAV ヘッダを確定
    pub async fn stop(mut self) -> Result<PathBuf> {
        info!("SDR 録音停止要求送信中: {:?}", self.output_path);

        if let Some(id) = self.child.id() {
            let pid = Pid::from_raw(id as i32);
            if let Err(e) = kill(pid, Signal::SIGINT) {
                warn!("SIGINT 送信失敗 (すでに終了している可能性): {}", e);
            }
        }

        // プロセスの終了待機
        match self.child.wait().await {
            Ok(status) => {
                info!("rtl_fm 正常終了: status {}", status);
            }
            Err(e) => {
                warn!("rtl_fm 待機エラー: {}", e);
            }
        }

        // ストリーミングタスクの終了を待機し、書き込まれたデータバイト数を取得
        let data_bytes = match self.writer_task.await {
            Ok(Ok(bytes)) => bytes,
            Ok(Err(e)) => {
                warn!("書き込みタスクエラー: {}", e);
                0
            }
            Err(e) => {
                warn!("タスクJoinエラー: {}", e);
                0
            }
        };

        // ファイル先頭にシークして、正しいデータ長を記録した WAV ヘッダで上書き
        let header = create_wav_header(data_bytes as u32);
        let mut file = tokio::fs::OpenOptions::new()
            .write(true)
            .open(&self.output_path)
            .await
            .context("WAVヘッダ上書き用のファイルオープンに失敗しました")?;

        file.seek(std::io::SeekFrom::Start(0)).await?;
        file.write_all(&header).await?;
        file.flush().await?;

        info!(
            "WAVファイル確定完了: データサイズ {} バイト, {:?}",
            data_bytes, self.output_path
        );

        Ok(self.output_path)
    }
}

