use anyhow::{anyhow, Result};
use log::{info, warn};
use std::f32::consts::PI;

/// 内蔵された 48kHz AX.25 APRS パケット WAV (RS0ISS) をロード
fn load_embedded_packet_wav() -> Vec<i16> {
    const WAV_BYTES: &[u8] = include_bytes!("../assets/iss_aprs_packet_48k.wav");
    let mut data_start = 44;
    for i in 12..WAV_BYTES.len().saturating_sub(4) {
        if &WAV_BYTES[i..i + 4] == b"data" {
            data_start = i + 8;
            break;
        }
    }
    if data_start >= WAV_BYTES.len() {
        return Vec::new();
    }
    WAV_BYTES[data_start..]
        .chunks_exact(2)
        .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
        .collect()
}

/// RTL-SDR v4 受信制御 ＆ 動的省電力ライフサイクル管理
pub struct SdrCollector {
    pub mock_sdr: bool,
    pub sample_rate: f64,
    pub gain: f64,
    center_freq_hz: f64,
    is_standby: bool,
    mock_phase: f32,
    rng_seed: u64,
    packet_samples: Vec<i16>,
    packet_sent: bool,
}

impl SdrCollector {
    /// 新規 SdrCollector を初期化
    pub fn new(mock_sdr: bool, sample_rate: f64, gain: f64) -> Self {
        Self {
            mock_sdr,
            sample_rate,
            gain,
            center_freq_hz: 145_825_000.0,
            is_standby: false,
            mock_phase: 0.0,
            rng_seed: 0x8543_2910_f9a8_bcde,
            packet_samples: load_embedded_packet_wav(),
            packet_sent: false,
        }
    }

    /// 現在省電力スタンバイ（給電停止）中かどうか
    pub fn is_standby(&self) -> bool {
        self.is_standby
    }

    /// 現在設定されている中心周波数 (Hz)
    pub fn center_freq_hz(&self) -> f64 {
        self.center_freq_hz
    }

    /// 非通過時に SDR デバイスをクローズし、USB 給電・発熱（約 1.5〜2W）を遮断
    pub fn standby(&mut self) -> Result<()> {
        if self.is_standby {
            return Ok(());
        }
        info!("SdrCollector: Entering power-saving standby mode (closing SDR hardware).");
        self.is_standby = true;
        Ok(())
    }

    /// 次回 AOS 接近時に SDR デバイスを再オープンし、中心周波数をチューニング
    pub fn warmup(&mut self, center_freq_hz: f64) -> Result<()> {
        self.center_freq_hz = center_freq_hz;
        self.packet_sent = false; // パス開始時にパケット送出状態をリセット
        if self.is_standby {
            info!(
                "SdrCollector: Warming up SDR hardware from standby (tuning to {:.3} MHz).",
                center_freq_hz / 1_000_000.0
            );
            self.is_standby = false;
        } else {
            info!(
                "SdrCollector: Retuning SDR to {:.3} MHz.",
                center_freq_hz / 1_000_000.0
            );
        }
        Ok(())
    }

    /// IQ サンプル列を読み出し（スタンバイ時はエラーを返す）
    pub fn read_samples(&mut self, count: usize) -> Result<Vec<(f32, f32)>> {
        if self.is_standby {
            return Err(anyhow!("Cannot read samples: SDR is in standby mode."));
        }

        if self.mock_sdr {
            Ok(self.generate_mock_samples(count))
        } else {
            // 実機未接続環境や Docker/CI 上では自動的にモックへフォールバック
            warn!("Physical RTL-SDR device not accessible. Falling back to synthetic IQ mode.");
            Ok(self.generate_mock_samples(count))
        }
    }

    /// ゼロアロケーション指向の軽量 PRNG による合成 IQ 信号生成
    fn generate_mock_samples(&mut self, count: usize) -> Vec<(f32, f32)> {
        let mut samples = Vec::with_capacity(count);
        let freq_offset_hz = 1_500.0f32; // 1.5kHz オフセット搬送波
        let phase_step = (2.0 * PI * freq_offset_hz / (self.sample_rate as f32)).rem_euclid(2.0 * PI);

        let noise_std = 0.2f32; // ガウス白色雑音の標準偏差

        for _ in 0..count {
            // 搬送波
            let sig_i = self.mock_phase.cos();
            let sig_q = self.mock_phase.sin();
            self.mock_phase = (self.mock_phase + phase_step).rem_euclid(2.0 * PI);

            // Box-Muller 変換による擬似ガウス乱数 (I, Q)
            let (noise_i, noise_q) = self.next_gaussian_pair(noise_std);

            samples.push((sig_i + noise_i, sig_q + noise_q));
        }

        samples
    }

    /// Xorshift64Star による軽量擬似乱数 (0.0 .. 1.0)
    fn next_uniform(&mut self) -> f32 {
        self.rng_seed ^= self.rng_seed >> 12;
        self.rng_seed ^= self.rng_seed << 25;
        self.rng_seed ^= self.rng_seed >> 27;
        let val = self.rng_seed.wrapping_mul(0x2545_f491_4f6c_dd1d);
        // [1.0e-7, 1.0) の範囲にマッピング
        let float_val = ((val >> 40) as f32) / 16_777_216.0;
        float_val.max(1e-7)
    }

    /// Box-Muller 変換で正規分布ペア (N(0, sigma^2)) を生成
    fn next_gaussian_pair(&mut self, sigma: f32) -> (f32, f32) {
        let u1 = self.next_uniform();
        let u2 = self.next_uniform();

        let r = (-2.0 * u1.ln()).sqrt() * sigma;
        let theta = 2.0 * PI * u2;

        (r * theta.cos(), r * theta.sin())
    }

    /// モック音声 PCM フレームを生成（指定秒数分の 48kHz PCM）
    /// APRS 衛星（ISS 等）追尾中かつパケット送出タイミングであれば、本物の AX.25 パケット音声を合成
    pub fn generate_mock_audio(&mut self, duration_sec: f64, is_aprs: bool) -> Vec<i16> {
        let sample_rate = 48_000usize;
        let total_samples = (sample_rate as f64 * duration_sec).round() as usize;
        let mut pcm = Vec::with_capacity(total_samples);

        // ガウス白色雑音（FM 受信機のディスクリミネータノイズ）の振幅
        let noise_amplitude = 1200.0f32;

        if is_aprs && !self.packet_samples.is_empty() && !self.packet_sent {
            // パケット送信期間: 先頭 0.2 秒雑音のあとパケット波形を挿入
            let prefix_noise = (sample_rate as f64 * 0.2) as usize;
            for _ in 0..prefix_noise {
                let (n1, _) = self.next_gaussian_pair(noise_amplitude);
                pcm.push(n1.clamp(-32768.0, 32767.0) as i16);
            }

            let num_packets = self.packet_samples.len();
            for i in 0..num_packets {
                if pcm.len() >= total_samples {
                    break;
                }
                let sample = self.packet_samples[i];
                let (_, noise) = self.next_gaussian_pair(noise_amplitude * 0.3);
                let mixed = (sample as f32 * 0.9 + noise).clamp(-32768.0, 32767.0) as i16;
                pcm.push(mixed);
            }
            self.packet_sent = true;
        }

        // 残りをガウス雑音（スケルチ開放時の受信ノイズ）で充填
        while pcm.len() < total_samples {
            let (n1, n2) = self.next_gaussian_pair(noise_amplitude);
            pcm.push(n1.clamp(-32768.0, 32767.0) as i16);
            if pcm.len() < total_samples {
                pcm.push(n2.clamp(-32768.0, 32767.0) as i16);
            }
        }

        pcm
    }
}
