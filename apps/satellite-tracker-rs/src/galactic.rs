use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use rustfft::{num_complex::Complex32, FftPlanner};
use serde::{Deserialize, Serialize};
use std::f32::consts::PI as PI_F32;
use std::path::PathBuf;

pub const HI_REST_FREQ_HZ: f64 = 1_420_405_751.768;
pub const HI_OFF_FREQ_HZ: f64 = 1_423_000_000.0;
pub const SPEED_OF_LIGHT_KM_S: f64 = 299_792.458;

// IAU J2000.0 銀河座標定数
const NGP_RA_DEG: f64 = 192.85948;
const NGP_DEC_DEG: f64 = 27.12825;
const NCP_GAL_LON_DEG: f64 = 122.93192;

/// アンテナビーム中心の天球・銀河ポインティング情報
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GalacticPointing {
    pub az_deg: f64,
    pub el_deg: f64,
    pub lst_deg: f64,
    pub ra_deg: f64,
    pub dec_deg: f64,
    pub gal_l_deg: f64,
    pub gal_b_deg: f64,
    pub region_label: String,
    pub sky_type: String,
}

/// 受信 RF メトリクス
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GalacticRfMetrics {
    pub center_freq_on_hz: f64,
    pub center_freq_off_hz: f64,
    pub continuum_power_db: f64,
    pub peak_line_snr_db: f64,
    pub peak_v_lsr_km_s: f64,
}

/// 視線速度スペクトル
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GalacticSpectrum {
    pub v_lsr_km_s: Vec<f64>,
    pub relative_temp_k: Vec<f64>,
}

/// 1スキャンサイクル (5分間) の測定結果
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GalacticScanResult {
    pub timestamp_utc: String,
    pub integration_sec: f64,
    pub preempted: bool,
    pub pointing: GalacticPointing,
    pub lsr_correction_km_s: f64,
    pub rf_metrics: GalacticRfMetrics,
    pub spectrum: GalacticSpectrum,
}

/// 地平座標 (Az, El) から赤道座標 (RA, Dec) および銀河座標 (l, b) を算出
pub fn compute_galactic_pointing(
    lat_deg: f64,
    lon_deg: f64,
    az_deg: f64,
    el_deg: f64,
    timestamp: DateTime<Utc>,
) -> GalacticPointing {
    let lat_rad = lat_deg.to_radians();
    let az_rad = az_deg.to_radians();
    let el_rad = el_deg.to_radians();

    // 1. GMST (IAU 1982 公式) と LST の算出
    let ts = timestamp.timestamp() as f64 + (timestamp.timestamp_subsec_nanos() as f64 / 1e9);
    let jd = (ts / 86400.0) + 2440587.5;
    let d = jd - 2451545.0;
    let gmst_deg = (280.46061837 + 360.98564736629 * d).rem_euclid(360.0);
    let lst_deg = (gmst_deg + lon_deg).rem_euclid(360.0);
    let _lst_rad = lst_deg.to_radians();

    // 2. 地平座標 (Az, El) -> 赤道座標 (HA, Dec)
    // sin(dec) = sin(el)*sin(lat) + cos(el)*cos(lat)*cos(az)
    let sin_dec = el_rad.sin() * lat_rad.sin() + el_rad.cos() * lat_rad.cos() * az_rad.cos();
    let dec_rad = sin_dec.clamp(-1.0, 1.0).asin();
    let dec_deg = dec_rad.to_degrees();

    // cos(H)*cos(dec) = cos(el)*sin(lat)*cos(az) - sin(el)*cos(lat)
    // sin(H)*cos(dec) = -cos(el)*sin(az)
    let y_h = -el_rad.cos() * az_rad.sin();
    let x_h = el_rad.cos() * lat_rad.sin() * az_rad.cos() - el_rad.sin() * lat_rad.cos();
    let ha_rad = y_h.atan2(x_h);
    let ha_deg = ha_rad.to_degrees().rem_euclid(360.0);

    // RA = LST - HA
    let ra_deg = (lst_deg - ha_deg).rem_euclid(360.0);
    let ra_rad = ra_deg.to_radians();

    // 3. 赤道座標 (RA, Dec) -> 銀河座標 (l, b) (IAU J2000.0)
    let ngp_ra_rad = NGP_RA_DEG.to_radians();
    let ngp_dec_rad = NGP_DEC_DEG.to_radians();
    let ncp_gal_lon_rad = NCP_GAL_LON_DEG.to_radians();

    let sin_b = dec_rad.sin() * ngp_dec_rad.sin()
        + dec_rad.cos() * ngp_dec_rad.cos() * (ra_rad - ngp_ra_rad).cos();
    let b_rad = sin_b.clamp(-1.0, 1.0).asin();
    let gal_b_deg = b_rad.to_degrees();

    let y_gal = dec_rad.cos() * (ra_rad - ngp_ra_rad).sin();
    let x_gal = dec_rad.sin() * ngp_dec_rad.cos()
        - dec_rad.cos() * ngp_dec_rad.sin() * (ra_rad - ngp_ra_rad).cos();
    let l_diff = y_gal.atan2(x_gal);
    let gal_l_deg = (ncp_gal_lon_rad - l_diff).to_degrees().rem_euclid(360.0);

    // 4. 天の川領域ラベリング
    let is_plane = gal_b_deg.abs() <= 15.0;
    let sky_type = if is_plane {
        "WARM_GALACTIC_PLANE".to_string()
    } else if gal_b_deg.abs() > 30.0 {
        "COLD_HALO".to_string()
    } else {
        "INTERMEDIATE".to_string()
    };

    let region_label = if is_plane {
        if (65.0..=95.0).contains(&gal_l_deg) {
            "はくちょう座領域（局所腕・オリオン腕）".to_string()
        } else if (95.0..=140.0).contains(&gal_l_deg) {
            "ケフェウス座・カシオペア座領域（ペルセウス腕）".to_string()
        } else if (140.0..=190.0).contains(&gal_l_deg) {
            "ペルセウス座・ぎょしゃ座領域（反銀河中心方向）".to_string()
        } else {
            format!("天の川銀河面 (l={:.1}°)", gal_l_deg)
        }
    } else {
        format!("銀河ハロー (b={:.1}°)", gal_b_deg)
    };

    GalacticPointing {
        az_deg: round_2(az_deg),
        el_deg: round_2(el_deg),
        lst_deg: round_2(lst_deg),
        ra_deg: round_2(ra_deg),
        dec_deg: round_2(dec_deg),
        gal_l_deg: round_2(gal_l_deg),
        gal_b_deg: round_2(gal_b_deg),
        region_label,
        sky_type,
    }
}

/// 太陽系の特異運動および地球公転運動による視線速度補正値 Δv_LSR (km/s) を算出
pub fn compute_lsr_velocity_correction(
    gal_l_deg: f64,
    gal_b_deg: f64,
    _ra_deg: f64,
    _dec_deg: f64,
    timestamp: DateTime<Utc>,
) -> f64 {
    let l_rad = gal_l_deg.to_radians();
    let b_rad = gal_b_deg.to_radians();

    // 1. 標準太陽運動ベクトル (U=10.0, V=15.4, W=7.8 km/s) の視線方向成分
    let u_sun = 10.0;
    let v_sun = 15.4;
    let w_sun = 7.8;
    let v_solar = u_sun * l_rad.cos() * b_rad.cos()
        + v_sun * l_rad.sin() * b_rad.cos()
        + w_sun * b_rad.sin();

    // 2. 地球公転運動による簡易視線速度成分 (最大約 ±30 km/s)
    let day_of_year = timestamp.format("%j").to_string().parse::<f64>().unwrap_or(290.0);
    let ecliptic_lon_sun = (280.0 + 360.0 * (day_of_year / 365.25)).rem_euclid(360.0);
    let earth_vel_angle = (ecliptic_lon_sun + 90.0).to_radians();
    let earth_speed = 29.78; // km/s
    let v_earth = earth_speed * (l_rad - earth_vel_angle).cos() * b_rad.cos();

    round_2(v_solar + v_earth * 0.5)
}

/// IQ サンプル列をハニング窓 1024 点 Welch パワースペクトルとして積算
pub fn accumulate_welch_psd(iq_samples: &[(f32, f32)], fft_size: usize) -> Vec<f64> {
    if iq_samples.len() < fft_size {
        return vec![0.0; fft_size];
    }

    let mut planner = FftPlanner::new();
    let fft = planner.plan_fft_forward(fft_size);

    let window: Vec<f32> = (0..fft_size)
        .map(|i| 0.5 * (1.0 - (2.0 * PI_F32 * i as f32 / (fft_size as f32 - 1.0)).cos()))
        .collect();

    let num_segments = iq_samples.len() / fft_size;
    let mut psd = vec![0.0f64; fft_size];

    for seg in 0..num_segments {
        let offset = seg * fft_size;
        let mut buffer: Vec<Complex32> = (0..fft_size)
            .map(|i| {
                let (re, im) = iq_samples[offset + i];
                Complex32::new(re * window[i], im * window[i])
            })
            .collect();

        fft.process(&mut buffer);

        let half = fft_size / 2;
        for i in 0..fft_size {
            let shifted = (i + half) % fft_size;
            let pwr = buffer[i].norm_sqr() as f64;
            psd[shifted] += pwr / (fft_size as f64);
        }
    }

    for p in psd.iter_mut() {
        *p /= num_segments as f64;
    }

    psd
}

/// 周波数スイッチング (ON/OFF) パワースペクトルの較正と 21cm 視線速度プロファイル抽出
pub fn calibrate_frequency_switched_spectrum(
    psd_on: &[f64],
    psd_off: &[f64],
    sample_rate_hz: f64,
    center_freq_on_hz: f64,
    lsr_corr_km_s: f64,
    t_sys_k: f64,
) -> (GalacticSpectrum, GalacticRfMetrics) {
    let fft_size = psd_on.len();
    if fft_size == 0 || psd_off.len() != fft_size {
        return (
            GalacticSpectrum {
                v_lsr_km_s: Vec::new(),
                relative_temp_k: Vec::new(),
            },
            GalacticRfMetrics {
                center_freq_on_hz,
                center_freq_off_hz: HI_OFF_FREQ_HZ,
                continuum_power_db: -100.0,
                peak_line_snr_db: 0.0,
                peak_v_lsr_km_s: 0.0,
            },
        );
    }

    // 1. 相対温度 (P_ON - P_OFF) / P_OFF
    let mut diff_ratio = vec![0.0f64; fft_size];
    let mut total_pwr = 0.0f64;
    for i in 0..fft_size {
        total_pwr += psd_on[i];
        let off_val = psd_off[i];
        if off_val > 1e-12 {
            diff_ratio[i] = (psd_on[i] - off_val) / off_val;
        } else {
            diff_ratio[i] = 0.0;
        }
    }

    // 2. 1次線形ベースラインの推定と差し引き (両端 10% の平均から傾きを除去)
    let margin = fft_size / 10;
    let left_mean: f64 = diff_ratio[..margin].iter().sum::<f64>() / margin as f64;
    let right_mean: f64 = diff_ratio[fft_size - margin..].iter().sum::<f64>() / margin as f64;

    let mut calibrated_temp = vec![0.0f64; fft_size];
    for i in 0..fft_size {
        let slope = left_mean + (right_mean - left_mean) * (i as f64 / fft_size as f64);
        calibrated_temp[i] = (diff_ratio[i] - slope) * t_sys_k;
    }

    // 3. 各ビンの周波数 -> 視線速度 v_LSR (km/s)
    let freq_bin_hz = sample_rate_hz / fft_size as f64;
    let half = fft_size as f64 / 2.0;

    let mut v_lsr_vec = Vec::new();
    let mut temp_vec = Vec::new();

    let mut peak_temp = f64::NEG_INFINITY;
    let mut peak_v = 0.0f64;

    for i in 0..fft_size {
        let freq = center_freq_on_hz + (i as f64 - half) * freq_bin_hz;
        // ドップラー: v_obs = -c * (freq - nu_0) / nu_0
        let v_obs = -SPEED_OF_LIGHT_KM_S * (freq - HI_REST_FREQ_HZ) / HI_REST_FREQ_HZ;
        let v_lsr = v_obs + lsr_corr_km_s;

        // 関心領域: [-150.0, +150.0] km/s
        if (-150.0..=150.0).contains(&v_lsr) {
            let t_val = round_2(calibrated_temp[i]);
            if t_val > peak_temp {
                peak_temp = t_val;
                peak_v = v_lsr;
            }
            v_lsr_vec.push(round_2(v_lsr));
            temp_vec.push(t_val);
        }
    }

    let continuum_power_db = if total_pwr > 0.0 {
        round_2(10.0 * total_pwr.log10())
    } else {
        -100.0
    };

    let peak_line_snr_db = if peak_temp > 0.0 {
        round_2(10.0 * (1.0 + peak_temp / t_sys_k).log10())
    } else {
        0.0
    };

    (
        GalacticSpectrum {
            v_lsr_km_s: v_lsr_vec,
            relative_temp_k: temp_vec,
        },
        GalacticRfMetrics {
            center_freq_on_hz,
            center_freq_off_hz: HI_OFF_FREQ_HZ,
            continuum_power_db,
            peak_line_snr_db,
            peak_v_lsr_km_s: round_2(peak_v),
        },
    )
}

/// 模擬 IQ 信号生成 (モック・テスト用)
pub fn generate_synthetic_galactic_iq(
    count: usize,
    sample_rate: f64,
    is_on_band: bool,
    gal_l_deg: f64,
    gal_b_deg: f64,
    seed: &mut u64,
) -> Vec<(f32, f32)> {
    let mut samples = Vec::with_capacity(count);

    // 白色雑音
    let noise_sigma = 0.25f32;
    // 天の川銀河面 (|b| <= 15°) の場合、21cm 輝線を模擬重畳
    let is_plane = is_on_band && gal_b_deg.abs() <= 15.0;
    // カシオペア座・ペルセウス腕方向ならドップラー -45 km/s (約 +213 kHz)
    let line_freq_offset = if gal_l_deg >= 95.0 && gal_l_deg <= 140.0 {
        45.0 * (HI_REST_FREQ_HZ / SPEED_OF_LIGHT_KM_S) // +213 kHz
    } else {
        0.0
    };
    let line_amplitude = if is_plane { 0.08f32 } else { 0.0f32 };
    let phase_step = (2.0 * PI_F32 * line_freq_offset as f32 / sample_rate as f32).rem_euclid(2.0 * PI_F32);
    let mut phase = 0.0f32;

    for _ in 0..count {
        *seed ^= *seed >> 12;
        *seed ^= *seed << 25;
        *seed ^= *seed >> 27;
        let u1 = ((*seed >> 40) as f32 / 16_777_216.0).max(1e-7);
        *seed ^= *seed >> 12;
        *seed ^= *seed << 25;
        *seed ^= *seed >> 27;
        let u2 = (*seed >> 40) as f32 / 16_777_216.0;

        let r = (-2.0 * u1.ln()).sqrt() * noise_sigma;
        let theta = 2.0 * PI_F32 * u2;
        let n_i = r * theta.cos();
        let n_q = r * theta.sin();

        let sig_i = line_amplitude * phase.cos();
        let sig_q = line_amplitude * phase.sin();
        phase = (phase + phase_step).rem_euclid(2.0 * PI_F32);

        samples.push((sig_i + n_i, sig_q + n_q));
    }

    samples
}

/// 5分間の銀河スキャン積算セッションを管理するステートマシン
pub struct GalacticScanSession {
    pub spool_dir: PathBuf,
    pub cycle_duration_sec: f64,
    pub beam_az_deg: f64,
    pub beam_el_deg: f64,
    pub observer_lat: f64,
    pub observer_lon: f64,
    pub start_time: Option<DateTime<Utc>>,
    pub accumulated_on_psd: Vec<f64>,
    pub accumulated_off_psd: Vec<f64>,
    pub on_slices_count: usize,
    pub off_slices_count: usize,
    pub is_on_tuning: bool,
    pub last_switch_time: Option<DateTime<Utc>>,
    pub switch_interval_sec: f64,
}

impl GalacticScanSession {
    pub fn new(
        spool_dir: PathBuf,
        cycle_duration_sec: f64,
        beam_az_deg: f64,
        beam_el_deg: f64,
        observer_lat: f64,
        observer_lon: f64,
    ) -> Self {
        Self {
            spool_dir,
            cycle_duration_sec,
            beam_az_deg,
            beam_el_deg,
            observer_lat,
            observer_lon,
            start_time: None,
            accumulated_on_psd: vec![0.0; 1024],
            accumulated_off_psd: vec![0.0; 1024],
            on_slices_count: 0,
            off_slices_count: 0,
            is_on_tuning: true,
            last_switch_time: None,
            switch_interval_sec: 15.0,
        }
    }

    /// スライス IQ サンプルを追加積算
    pub fn accumulate_slice(&mut self, iq_samples: &[(f32, f32)], now: DateTime<Utc>) {
        if self.start_time.is_none() {
            self.start_time = Some(now);
            self.last_switch_time = Some(now);
        } else if let Some(last_switch) = self.last_switch_time {
            let elapsed = (now - last_switch).num_milliseconds() as f64 / 1000.0;
            if elapsed >= self.switch_interval_sec {
                self.is_on_tuning = !self.is_on_tuning;
                self.last_switch_time = Some(now);
            }
        }

        let psd = accumulate_welch_psd(iq_samples, 1024);
        if self.is_on_tuning {
            for i in 0..1024 {
                self.accumulated_on_psd[i] += psd[i];
            }
            self.on_slices_count += 1;
        } else {
            for i in 0..1024 {
                self.accumulated_off_psd[i] += psd[i];
            }
            self.off_slices_count += 1;
        }
    }

    /// 現在設定すべき中心周波数 (Hz) を取得
    pub fn current_tuning_freq_hz(&self) -> f64 {
        if self.is_on_tuning {
            HI_REST_FREQ_HZ
        } else {
            HI_OFF_FREQ_HZ
        }
    }

    /// 経過秒数を取得
    pub fn elapsed_seconds(&self, now: DateTime<Utc>) -> f64 {
        if let Some(start) = self.start_time {
            (now - start).num_milliseconds() as f64 / 1000.0
        } else {
            0.0
        }
    }

    /// サイクル完了またはプリエンプション時に結果 JSON を生成してローカルファイルへ出力
    pub fn flush_result(&mut self, now: DateTime<Utc>, preempted: bool) -> Result<Option<PathBuf>> {
        let elapsed = self.elapsed_seconds(now);
        if elapsed < 10.0 || (self.on_slices_count == 0 && self.off_slices_count == 0) {
            self.reset();
            return Ok(None);
        }

        let start_dt = self.start_time.unwrap_or(now);
        let mid_dt = start_dt + chrono::Duration::milliseconds((elapsed * 500.0) as i64);

        let pointing = compute_galactic_pointing(
            self.observer_lat,
            self.observer_lon,
            self.beam_az_deg,
            self.beam_el_deg,
            mid_dt,
        );

        let lsr_corr = compute_lsr_velocity_correction(
            pointing.gal_l_deg,
            pointing.gal_b_deg,
            pointing.ra_deg,
            pointing.dec_deg,
            mid_dt,
        );

        let on_div = self.on_slices_count.max(1) as f64;
        let off_div = self.off_slices_count.max(1) as f64;
        let mut avg_on = vec![0.0; 1024];
        let mut avg_off = vec![0.0; 1024];
        for i in 0..1024 {
            avg_on[i] = self.accumulated_on_psd[i] / on_div;
            avg_off[i] = self.accumulated_off_psd[i] / off_div;
        }

        let (spectrum, rf_metrics) = calibrate_frequency_switched_spectrum(
            &avg_on,
            &avg_off,
            2_048_000.0,
            HI_REST_FREQ_HZ,
            lsr_corr,
            300.0,
        );

        let result = GalacticScanResult {
            timestamp_utc: start_dt.to_rfc3339(),
            integration_sec: round_2(elapsed),
            preempted,
            pointing,
            lsr_correction_km_s: lsr_corr,
            rf_metrics,
            spectrum,
        };

        let filename = format!("scan_{}.json", start_dt.format("%Y%m%d_%H%M%S"));
        let out_path = self.spool_dir.join(&filename);
        let json_str = serde_json::to_string_pretty(&result)
            .context("GalacticScanResult の JSON シリアライズに失敗")?;
        std::fs::write(&out_path, json_str)
            .with_context(|| format!("スキャン結果ファイルの書き込みに失敗: {:?}", out_path))?;

        self.reset();
        Ok(Some(out_path))
    }

    pub fn reset(&mut self) {
        self.start_time = None;
        self.last_switch_time = None;
        self.accumulated_on_psd = vec![0.0; 1024];
        self.accumulated_off_psd = vec![0.0; 1024];
        self.on_slices_count = 0;
        self.off_slices_count = 0;
        self.is_on_tuning = true;
    }
}

fn round_2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}
