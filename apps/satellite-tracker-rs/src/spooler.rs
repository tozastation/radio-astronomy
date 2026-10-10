use anyhow::{Context, Result};
use hound::{SampleFormat, WavSpec, WavWriter};
use log::{info, warn};
use std::fs::{self, File};
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

/// 狭帯域 48kHz WAV スプール録音 ＆ ディスク容量保護 (Circuit Breaker)
pub struct AudioSpooler {
    pub spool_dir: PathBuf,
    pub sample_rate: u32,
    pub max_spool_bytes: u64,
    pub min_free_percent: f64,
    pub current_wav_path: Option<PathBuf>,
    writer: Option<WavWriter<BufWriter<File>>>,
}

impl AudioSpooler {
    /// 新規 AudioSpooler を生成
    pub fn new(
        spool_dir: PathBuf,
        sample_rate: u32,
        max_spool_bytes: u64,
        min_free_percent: f64,
    ) -> Self {
        let _ = fs::create_dir_all(&spool_dir);
        Self {
            spool_dir,
            sample_rate,
            max_spool_bytes,
            min_free_percent,
            current_wav_path: None,
            writer: None,
        }
    }

    /// パス開始時に WAV ファイルを新規作成して初期化
    pub fn start_pass(&mut self, satellite: &str, pass_id: &str) -> Result<PathBuf> {
        self.enforce_circuit_breaker()?;

        fs::create_dir_all(&self.spool_dir)
            .with_context(|| format!("スプールディレクトリの作成に失敗: {:?}", self.spool_dir))?;

        let filename = format!("{}_{}.wav", satellite, pass_id);
        let wav_path = self.spool_dir.join(filename);

        let spec = WavSpec {
            channels: 1,
            sample_rate: self.sample_rate,
            bits_per_sample: 16,
            sample_format: SampleFormat::Int,
        };

        let writer = WavWriter::create(&wav_path, spec)
            .with_context(|| format!("WAVファイルの作成に失敗: {:?}", wav_path))?;

        self.writer = Some(writer);
        self.current_wav_path = Some(wav_path.clone());
        info!("AudioSpooler: パス録音を開始しました: {:?}", wav_path);

        Ok(wav_path)
    }

    /// 復調済み PCM 16-bit フレームを WAV ファイルへ追記
    pub fn write_frames(&mut self, pcm_samples: &[i16]) -> Result<()> {
        if let Some(writer) = &mut self.writer {
            for &sample in pcm_samples {
                writer.write_sample(sample)?;
            }
        }
        Ok(())
    }

    /// パス終了時に WAV ファイルをフラッシュ・クローズ
    pub fn finish_pass(&mut self) -> Result<Option<PathBuf>> {
        if let Some(writer) = self.writer.take() {
            writer.finalize().context("WAVファイルのファイナライズに失敗")?;
        }

        let finished = self.current_wav_path.clone();
        if let Some(ref p) = finished {
            info!("AudioSpooler: パス録音を完了しました: {:?}", p);
        }

        self.enforce_circuit_breaker()?;
        Ok(finished)
    }

    /// ディスク残量およびスプール容量閾値に基づく Circuit Breaker（最古ファイルパージ）を実行
    pub fn enforce_circuit_breaker(&self) -> Result<()> {
        if !self.spool_dir.exists() {
            return Ok(());
        }

        // 1. ホストファイルシステムの空き容量チェック
        if let Some(free_percent) = get_disk_free_percent(&self.spool_dir) {
            if free_percent < self.min_free_percent {
                warn!(
                    "Circuit Breaker 発動: ディスク空き容量 ({:.1}%) が閾値 ({:.1}%) 未満です。",
                    free_percent, self.min_free_percent
                );
                self.purge_oldest_file()?;
            }
        }

        // 2. スプールディレクトリ内の合計バイト数チェック
        let mut wav_files = self.list_spool_files()?;
        let mut total_bytes: u64 = wav_files.iter().map(|(_, size, _)| *size).sum();

        while total_bytes > self.max_spool_bytes && !wav_files.is_empty() {
            let (oldest_path, oldest_size, _) = wav_files.remove(0);
            // 録音中の現行ファイルは削除しないよう保護
            if let Some(ref current) = self.current_wav_path {
                if &oldest_path == current {
                    continue;
                }
            }

            warn!(
                "Circuit Breaker: スプール最大容量 ({} bytes) を超過したため最古ファイル {:?} ({} bytes) を削除します。",
                self.max_spool_bytes, oldest_path, oldest_size
            );

            if let Err(e) = fs::remove_file(&oldest_path) {
                warn!("ファイル削除失敗 ({:?}): {}", oldest_path, e);
                break;
            } else {
                total_bytes = total_bytes.saturating_sub(oldest_size);
            }
        }

        Ok(())
    }

    /// スプール内の WAV ファイル一覧を古い順（mtime 昇順）で取得
    fn list_spool_files(&self) -> Result<Vec<(PathBuf, u64, SystemTime)>> {
        let mut files = Vec::new();
        if let Ok(entries) = fs::read_dir(&self.spool_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_file() && path.extension().and_then(|s| s.to_str()) == Some("wav") {
                    if let Ok(meta) = entry.metadata() {
                        let size = meta.len();
                        let mtime = meta.modified().unwrap_or(SystemTime::UNIX_EPOCH);
                        files.push((path, size, mtime));
                    }
                }
            }
        }
        files.sort_by_key(|(_, _, mtime)| *mtime);
        Ok(files)
    }

    /// 最も古い WAV ファイルを 1 件緊急パージ
    fn purge_oldest_file(&self) -> Result<()> {
        let wav_files = self.list_spool_files()?;
        for (path, _, _) in wav_files {
            if let Some(ref current) = self.current_wav_path {
                if &path == current {
                    continue;
                }
            }
            warn!("Circuit Breaker: 緊急ディスク確保のため最古ファイル {:?} を削除します。", path);
            let _ = fs::remove_file(path);
            break;
        }
        Ok(())
    }
}

/// 指定パスのファイルシステムの空き容量パーセントを算出
#[cfg(unix)]
fn get_disk_free_percent(path: &Path) -> Option<f64> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let c_path = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(c_path.as_ptr(), stat.as_mut_ptr()) } == 0 {
        let stat = unsafe { stat.assume_init() };
        let total = stat.f_blocks as f64 * stat.f_frsize as f64;
        let free = stat.f_bavail as f64 * stat.f_frsize as f64;
        if total > 0.0 {
            return Some((free / total) * 100.0);
        }
    }
    None
}

#[cfg(not(unix))]
fn get_disk_free_percent(_path: &Path) -> Option<f64> {
    None
}
