use rustfft::{FftPlanner, num_complex::Complex32};
use std::f32::consts::PI;

/// スペクトル解析結果（RSSI、実測ドップラー、SNR 等）
#[derive(Debug, Clone, PartialEq)]
pub struct SpectrumResult {
    pub center_freq_hz: f64,
    pub peak_freq_hz: f64,
    pub measured_doppler_hz: f64,
    pub rssi_dbm: f64,
    pub snr_db: f64,
}

/// ゼロコピー DSP 処理エンジン
/// - 理論ドップラー逆ミキシング
/// - クワドラチャ検波 (FM 復調)
/// - CIC / ボックスカー低域通過・デシメーション (2.4MSPS -> 48kHz)
/// - 16-bit PCM スケーリング
pub struct DspProcessor {
    pub input_sample_rate: f64,
    pub target_sample_rate: u32,
    pub decimation_factor: usize,
    current_phase: f32,
    prev_sample: (f32, f32),
    accum_theta: f32,
    accum_count: usize,
}

impl DspProcessor {
    /// 新規 DspProcessor を初期化
    pub fn new(input_sample_rate: f64, target_sample_rate: u32) -> Self {
        let decimation_factor = (input_sample_rate / target_sample_rate as f64).round() as usize;
        Self {
            input_sample_rate,
            target_sample_rate,
            decimation_factor: if decimation_factor == 0 { 1 } else { decimation_factor },
            current_phase: 0.0,
            prev_sample: (1.0, 0.0),
            accum_theta: 0.0,
            accum_count: 0,
        }
    }

    /// 受信 IQ サンプル列をドップラー逆ミキシング・FM復調・デシメーション処理
    /// `out_pcm` バッファをインプレースで再利用し、アロケーションを極小化する
    pub fn process_samples(
        &mut self,
        iq_samples: &[(f32, f32)],
        doppler_hz: f64,
        out_pcm: &mut Vec<i16>,
    ) {
        let phase_step = (-2.0 * PI * (doppler_hz as f32) / (self.input_sample_rate as f32))
            .rem_euclid(2.0 * PI);

        // フルスケール定格 FM 周波数偏移 (5kHz 偏移で振幅 ±32767)
        let nominal_deviation_hz = 5_000.0f32;
        let scale_factor = (self.input_sample_rate as f32 / (2.0 * PI * nominal_deviation_hz)) * 32_767.0;

        for &(i_raw, q_raw) in iq_samples {
            // 1. 理論ドップラー逆ミキシング (複素回転子 e^{j current_phase} の乗算)
            let cos_p = self.current_phase.cos();
            let sin_p = self.current_phase.sin();
            let i_mix = i_raw * cos_p - q_raw * sin_p;
            let q_mix = i_raw * sin_p + q_raw * cos_p;

            self.current_phase = (self.current_phase + phase_step).rem_euclid(2.0 * PI);

            // 2. クワドラチャ検波 (位相差分角周波数の算出)
            // Delta theta = atan2(Q_n I_{n-1} - I_n Q_{n-1}, I_n I_{n-1} + Q_n Q_{n-1})
            let (prev_i, prev_q) = self.prev_sample;
            let delta_theta = (q_mix * prev_i - i_mix * prev_q).atan2(i_mix * prev_i + q_mix * prev_q);
            self.prev_sample = (i_mix, q_mix);

            // 3. ボックスカー積分によるデシメーションフィルタ
            self.accum_theta += delta_theta;
            self.accum_count += 1;

            if self.accum_count >= self.decimation_factor {
                let avg_theta = self.accum_theta / (self.decimation_factor as f32);
                let pcm_float = avg_theta * scale_factor;
                let pcm_clamped = pcm_float.clamp(-32_768.0, 32_767.0) as i16;
                out_pcm.push(pcm_clamped);

                self.accum_theta = 0.0;
                self.accum_count = 0;
            }
        }
    }

    /// FFT によるパワースペクトル解析を実行し、実測ピーク周波数・RSSI・SNR を算出
    pub fn measure_spectrum(
        iq_samples: &[(f32, f32)],
        sample_rate: f64,
        center_freq_hz: f64,
        fft_size: usize,
    ) -> SpectrumResult {
        if iq_samples.len() < fft_size {
            return SpectrumResult {
                center_freq_hz,
                peak_freq_hz: center_freq_hz,
                measured_doppler_hz: 0.0,
                rssi_dbm: -100.0,
                snr_db: 0.0,
            };
        }

        let mut planner = FftPlanner::new();
        let fft = planner.plan_fft_forward(fft_size);

        // Hanning 窓の事前計算
        let window: Vec<f32> = (0..fft_size)
            .map(|i| 0.5 * (1.0 - (2.0 * PI * i as f32 / (fft_size as f32 - 1.0)).cos()))
            .collect();

        let num_segments = iq_samples.len() / fft_size;
        let mut power_spectrum = vec![0.0f32; fft_size];

        for seg_idx in 0..num_segments {
            let offset = seg_idx * fft_size;
            let mut buffer: Vec<Complex32> = (0..fft_size)
                .map(|i| {
                    let (re, im) = iq_samples[offset + i];
                    Complex32::new(re * window[i], im * window[i])
                })
                .collect();

            fft.process(&mut buffer);

            // FFT shift (中心周波数を中央に配置)
            let half = fft_size / 2;
            for i in 0..fft_size {
                let shifted_idx = (i + half) % fft_size;
                let mag_sq = buffer[i].norm_sqr();
                power_spectrum[shifted_idx] += mag_sq / (fft_size as f32);
            }
        }

        for p in power_spectrum.iter_mut() {
            *p /= num_segments as f32;
        }

        // DC オフセットマスク (中央の数ビンを周囲の中央値で置換)
        let center_bin = fft_size / 2;
        let mask_radius = 4;
        let surrounding_val = power_spectrum[center_bin.saturating_sub(mask_radius * 2)];
        for i in center_bin.saturating_sub(mask_radius)..=center_bin.saturating_add(mask_radius) {
            if i < fft_size {
                power_spectrum[i] = surrounding_val;
            }
        }

        // ピーク検出
        let mut peak_idx = 0;
        let mut peak_val = -1.0f32;
        for (idx, &val) in power_spectrum.iter().enumerate() {
            if val > peak_val {
                peak_val = val;
                peak_idx = idx;
            }
        }

        // 二次補間 (Quadratic Interpolation) によるサブビン精度周波数推定
        let mut delta = 0.0f32;
        if peak_idx > 0 && peak_idx < fft_size - 1 {
            let alpha = power_spectrum[peak_idx - 1];
            let beta = power_spectrum[peak_idx];
            let gamma = power_spectrum[peak_idx + 1];
            let denom = alpha - 2.0 * beta + gamma;
            if denom.abs() > 1e-12 {
                delta = 0.5 * (alpha - gamma) / denom;
            }
        }

        let freq_resolution = (sample_rate / fft_size as f64) as f32;
        let measured_offset_hz = (peak_idx as f32 + delta - center_bin as f32) * freq_resolution;
        let peak_freq_hz = center_freq_hz + measured_offset_hz as f64;

        // ノイズフロアおよび SNR 推定
        let mut sorted_power = power_spectrum.clone();
        sorted_power.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let noise_floor = sorted_power[sorted_power.len() / 2].max(1e-12);
        let snr_db = (10.0 * (peak_val.max(1e-12) / noise_floor).log10()) as f64;

        // RSSI 近似算出 (-10 dBFS ~ -10 dBm)
        let rssi_dbm = (10.0 * peak_val.max(1e-12).log10() - 50.0) as f64;

        SpectrumResult {
            center_freq_hz,
            peak_freq_hz,
            measured_doppler_hz: measured_offset_hz as f64,
            rssi_dbm,
            snr_db: snr_db.max(0.0),
        }
    }
}
