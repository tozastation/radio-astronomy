use rustfft::num_complex::Complex;
use rustfft::{Fft, FftPlanner};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;

/// 1秒間の太陽電波集約メトリクス
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolarSecondMetrics {
    /// タイムスタンプ (Unix Time 秒)
    pub timestamp: i64,
    /// 帯域全体の総合電波強度 (dB)
    pub total_power_db: f32,
    /// 直近5分間の背景ノイズフロア中央値 (dB)
    pub baseline_median_db: f32,
    /// 背景ノイズに対する相対強度 (SNR = total_power - baseline_median) (dB)
    pub snr_db: f32,
    /// 太陽フレア電波バースト発生フラグ
    pub is_burst: bool,
    /// 1024ビンの平均パワースペクトル (dB)
    pub spectrum_db: Vec<f32>,
    /// 太陽の方位角 (度)
    #[serde(default)]
    pub sun_az_deg: f64,
    /// 太陽の高度 (度)
    #[serde(default)]
    pub sun_el_deg: f64,
}

/// 配列の中央値 (Median) および中央絶対偏差 (MAD: Median Absolute Deviation) を算出します。
/// 外れ値（急激なバースト信号）の影響をほとんど受けないロバスト統計量です。
pub fn calculate_median_and_mad(values: &mut [f32]) -> (f32, f32) {
    if values.is_empty() {
        return (0.0, 0.0);
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let n = values.len();
    let median = if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    };

    let mut deviations: Vec<f32> = values.iter().map(|&x| (x - median).abs()).collect();
    deviations.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let mad = if n % 2 == 1 {
        deviations[n / 2]
    } else {
        (deviations[n / 2 - 1] + deviations[n / 2]) / 2.0
    };

    (median, mad)
}

/// リアルタイムFFT積算および動的しきい値判定DSPエンジン
pub struct SolarDsp {
    fft_size: usize,
    sample_rate: u32,
    threshold_sigma: f64,
    min_jump_db: f64,

    fft: Arc<dyn Fft<f32>>,
    window: Vec<f32>,

    // 1秒間の積算アキュムレータ
    power_accumulator: Vec<f32>,
    num_accumulated_ffts: usize,
    accumulated_samples: usize,

    // 未処理のIQサンプルバッファ（1024サンプル未満の端数）
    residual_samples: Vec<Complex<f32>>,

    // 直近300秒（5分間）の総電力履歴リングバッファ
    power_history: VecDeque<f32>,
    last_power_db: Option<f32>,
}

impl SolarDsp {
    pub fn new(
        fft_size: usize,
        sample_rate: u32,
        threshold_sigma: f64,
        min_jump_db: f64,
    ) -> Self {
        let mut planner = FftPlanner::new();
        let fft = planner.plan_fft_forward(fft_size);

        // ハミング窓 (Hamming Window) の事前生成
        let window: Vec<f32> = (0..fft_size)
            .map(|i| {
                0.54 - 0.46 * (2.0 * std::f32::consts::PI * i as f32 / (fft_size - 1) as f32).cos()
            })
            .collect();

        Self {
            fft_size,
            sample_rate,
            threshold_sigma,
            min_jump_db,
            fft,
            window,
            power_accumulator: vec![0.0; fft_size],
            num_accumulated_ffts: 0,
            accumulated_samples: 0,
            residual_samples: Vec::with_capacity(fft_size),
            power_history: VecDeque::with_capacity(300),
            last_power_db: None,
        }
    }

    /// 生IQデータチャンク（u8形式: I0, Q0, I1, Q1, ...）を処理します。
    /// 1秒分（sample_rate個のIQペア）が蓄積された場合に `Some(SolarSecondMetrics)` を返します。
    pub fn process_chunk(&mut self, iq_u8: &[u8], timestamp_secs: i64) -> Option<SolarSecondMetrics> {
        let num_iq_pairs = iq_u8.len() / 2;
        if num_iq_pairs == 0 {
            return None;
        }

        let mut offset = 0;
        let mut second_metrics = None;

        while offset < num_iq_pairs {
            // 端数バッファを 1024 個になるまで埋める
            let needed = self.fft_size - self.residual_samples.len();
            let available = num_iq_pairs - offset;
            let take = needed.min(available);

            for i in 0..take {
                let idx = (offset + i) * 2;
                let i_val = (iq_u8[idx] as f32 - 127.5) / 127.5;
                let q_val = (iq_u8[idx + 1] as f32 - 127.5) / 127.5;
                self.residual_samples.push(Complex::new(i_val, q_val));
            }
            offset += take;
            self.accumulated_samples += take;

            // 1024サンプル溜まったら窓掛けしてFFTを実行
            if self.residual_samples.len() == self.fft_size {
                let mut buffer: Vec<Complex<f32>> = self
                    .residual_samples
                    .iter()
                    .zip(&self.window)
                    .map(|(c, &w)| *c * w)
                    .collect();

                self.fft.process(&mut buffer);

                // パワースペクトル (|X[k]|^2) の加算
                for k in 0..self.fft_size {
                    self.power_accumulator[k] += buffer[k].norm_sqr();
                }
                self.num_accumulated_ffts += 1;
                self.residual_samples.clear();
            }

            // 1秒分（sample_rate サンプル以上）積算が完了したか判定
            if self.accumulated_samples >= self.sample_rate as usize && self.num_accumulated_ffts > 0 {
                let metrics = self.finalize_one_second(timestamp_secs);
                second_metrics = Some(metrics);
            }
        }

        second_metrics
    }

    /// 1秒間の積算パワースペクトルを集約し、動的しきい値判定を実行します。
    fn finalize_one_second(&mut self, timestamp_secs: i64) -> SolarSecondMetrics {
        let norm_factor = 1.0 / self.num_accumulated_ffts as f32;
        let half = self.fft_size / 2;
        let mut spectrum_db = vec![0.0; self.fft_size];
        let mut total_linear_power = 0.0f32;

        // FFTシフト（低周波〜中心周波数〜高周波の順序に並び替え）
        for k in 0..self.fft_size {
            let shifted_idx = (k + half) % self.fft_size;
            let avg_power = self.power_accumulator[shifted_idx] * norm_factor;
            total_linear_power += avg_power;
            spectrum_db[k] = 10.0 * (avg_power + 1e-12).log10();
        }

        let total_power_db = 10.0 * (total_linear_power + 1e-12).log10();

        // 過去のリングバッファからベースライン統計量を計算
        let (baseline_median_db, robust_sigma) = if self.power_history.len() >= 3 {
            let mut hist: Vec<f32> = self.power_history.iter().copied().collect();
            let (med, mad) = calculate_median_and_mad(&mut hist);
            let sigma = 1.4826 * mad;
            (med, sigma.max(0.2)) // 最小分散リミッタ
        } else {
            (total_power_db, 1.0)
        };

        let snr_db = total_power_db - baseline_median_db;
        let jump_db = if let Some(last_p) = self.last_power_db {
            total_power_db - last_p
        } else {
            0.0
        };

        // 動的しきい値判定:
        // 1. 背景ノイズフロアの中央値に対して + (k * σ) 以上
        // 2. 直前1秒からの急上昇率が min_jump_db 以上
        let is_burst = self.power_history.len() >= 5
            && snr_db > (self.threshold_sigma as f32 * robust_sigma)
            && jump_db >= self.min_jump_db as f32;

        // 履歴を更新（最新300秒を保持）
        if self.power_history.len() >= 300 {
            self.power_history.pop_front();
        }
        self.power_history.push_back(total_power_db);
        self.last_power_db = Some(total_power_db);

        // 積算カウンタをリセット
        self.power_accumulator.fill(0.0);
        self.num_accumulated_ffts = 0;
        self.accumulated_samples = 0;

        SolarSecondMetrics {
            timestamp: timestamp_secs,
            total_power_db,
            baseline_median_db,
            snr_db,
            is_burst,
            spectrum_db,
            sun_az_deg: 0.0,
            sun_el_deg: 0.0,
        }
    }
}
