use anyhow::{Context, Result};
use image::{ImageBuffer, Rgb};
use rustfft::num_complex::Complex;
use rustfft::FftPlanner;
use std::f32::consts::PI;
use std::io::Cursor;

// =============================================================================
// 📻 CubeSat CW (Morse) 復調 & スペクトログラム生成モジュール (CW DSP)
// -----------------------------------------------------------------------------
// 【デジタル信号処理 (DSP) の物理・数学的背景】
// 1. A1A 電信 (CW / Continuous Wave):
//    搬送波（Carrier）をキーイング（ON/OFF）することで情報を伝達する方式（OOK: On-Off Keying）。
//    周波数変調（FM）や位相変調（PSK）と異なり、搬送波そのものの断続であるため、
//    そのままAM検波するとDCパルス音にしかならず耳で聞き取れません。
// 2. 包絡線検波 (Envelope Detection) & BFO トーン再合成:
//    直交ベースバンド信号 s(t) = I(t) + j Q(t) の瞬時振幅（包絡線）は:
//        r(t) = sqrt(I(t)^2 + Q(t)^2)
//    衛星通過時のドップラー効果 (±3.5kHz) の影響を受けずにモールス符号の短点・長点を
//    最も堅牢に抽出するため、DCオフセット除去後の包絡線 r(t) で 750Hz の可聴ビートトーン
//    (BFO: Beat Frequency Oscillator) を変調・再合成します。
//        s_audio(t) = r_lpf(t) * sin(2π * f_bfo * t)
// 3. 短時間フーリエ変換 (STFT) によるウォーターフォール / スペクトログラム可視化:
//    復調された可聴音声信号を時間窓 (Hann Window) で切り出し、FFT (高速フーリエ変換)
//    を連続計算することで、横軸=時間、縦軸=周波数、輝度=信号強度のヒートマップ画像を生成します。
// =============================================================================

/// 生IQ (cu8: 8-bit unsigned interleaved IQ) から 16-bit モノラル PCM を復調
/// - `raw_u8`: SDR がキャプチャした unsigned 8-bit IQ データ (I, Q, I, Q, ...)
/// - `in_rate`: 入力サンプリングレート (例: 240,000 Hz)
/// - `out_rate`: 出力音声サンプリングレート (例: 11,025 Hz)
/// - `bfo_hz`: 再合成する可聴ビート周波数 (例: 750.0 Hz)
pub fn demodulate_cw_iq_to_pcm(
    raw_u8: &[u8],
    in_rate: u32,
    out_rate: u32,
    bfo_hz: f32,
) -> Vec<i16> {
    let num_iq_samples = raw_u8.len() / 2;
    if num_iq_samples == 0 {
        return Vec::new();
    }

    let decimation = in_rate as f64 / out_rate as f64;
    let out_samples = (num_iq_samples as f64 / decimation).floor() as usize;
    if out_samples == 0 {
        return Vec::new();
    }

    // 1. 各出力区間ごとの包絡線 (Envelope) を計算
    let mut envelopes = Vec::with_capacity(out_samples);
    let mut max_envelope = 0.0f32;

    for m in 0..out_samples {
        let start_idx = (m as f64 * decimation) as usize;
        let end_idx = (((m + 1) as f64 * decimation) as usize).min(num_iq_samples);

        let mut sum_amp = 0.0f32;
        let count = (end_idx - start_idx).max(1);

        for k in start_idx..end_idx {
            let i_raw = raw_u8[2 * k] as f32;
            let q_raw = raw_u8[2 * k + 1] as f32;

            // 0..255 を -1.0..+1.0 に正規化
            let i = (i_raw - 128.0) / 128.0;
            let q = (q_raw - 128.0) / 128.0;

            let amp = (i * i + q * q).sqrt();
            sum_amp += amp;
        }

        let avg_amp = sum_amp / (count as f32);
        if avg_amp > max_envelope {
            max_envelope = avg_amp;
        }
        envelopes.push(avg_amp);
    }

    // 2. 移動平均による平滑化 (急峻なクリックノイズを低減)
    let window_size = 5;
    let mut smoothed_envelopes = Vec::with_capacity(out_samples);
    for i in 0..out_samples {
        let start = i.saturating_sub(window_size / 2);
        let end = (i + window_size / 2 + 1).min(out_samples);
        let sum: f32 = envelopes[start..end].iter().sum();
        smoothed_envelopes.push(sum / (end - start) as f32);
    }

    // 3. BFO トーン合成と 16-bit PCM スケーリング
    // 目標のピーク振幅: 約 20,000 (最大 32,767 に対して適度なヘッドルームを確保)
    let gain = if max_envelope > 1e-4 {
        20_000.0 / max_envelope
    } else {
        1.0
    };

    let mut pcm = Vec::with_capacity(out_samples);
    for (m, &env) in smoothed_envelopes.iter().enumerate() {
        let t = m as f32 / out_rate as f32;
        let tone = (2.0 * PI * bfo_hz * t).sin();
        let sample = (env * gain * tone).clamp(-32767.0, 32767.0) as i16;
        pcm.push(sample);
    }

    pcm
}

/// 汎用 RIFF/WAVE ヘッダ (44バイト) を生成
pub fn create_wav_header(sample_rate: u32, channels: u16, bits_per_sample: u16, data_size: u32) -> [u8; 44] {
    let mut header = [0u8; 44];
    let byte_rate = sample_rate * (channels as u32) * (bits_per_sample as u32) / 8;
    let block_align = channels * bits_per_sample / 8;
    let total_size = 36 + data_size;

    header[0..4].copy_from_slice(b"RIFF");
    header[4..8].copy_from_slice(&total_size.to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");

    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes()); // Subchunk1Size (16 for PCM)
    header[20..22].copy_from_slice(&1u16.to_le_bytes());  // AudioFormat (1 = PCM)
    header[22..24].copy_from_slice(&channels.to_le_bytes());
    header[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&block_align.to_le_bytes());
    header[34..36].copy_from_slice(&bits_per_sample.to_le_bytes());

    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_size.to_le_bytes());

    header
}

/// 生IQデータから復調した CW 音声 WAV バイナリを作成
pub fn create_cw_wav(
    raw_u8: &[u8],
    in_rate: u32,
    out_rate: u32,
    bfo_hz: f32,
) -> Vec<u8> {
    let pcm = demodulate_cw_iq_to_pcm(raw_u8, in_rate, out_rate, bfo_hz);
    let data_size = (pcm.len() * 2) as u32;
    let header = create_wav_header(out_rate, 1, 16, data_size);

    let mut wav_bytes = Vec::with_capacity(44 + data_size as usize);
    wav_bytes.extend_from_slice(&header);

    for sample in pcm {
        wav_bytes.extend_from_slice(&sample.to_le_bytes());
    }

    wav_bytes
}

/// 復調 PCM 音声から STFT (短時間フーリエ変換) によりスペクトログラム PNG を生成
/// - `pcm`: 16-bit PCM サンプル列
/// - `sample_rate`: サンプリングレート (Hz)
/// - `width`: 画像の横幅 (時間軸ピクセル数)
/// - `height`: 画像の高さ (周波数軸ピクセル数)
pub fn generate_spectrogram_png(
    pcm: &[i16],
    _sample_rate: u32,
    width: u32,
    height: u32,
) -> Result<Vec<u8>> {
    let width = width.max(64);
    let height = height.max(64);

    let fft_size = 512;
    let mut planner = FftPlanner::new();
    let fft = planner.plan_fft_forward(fft_size);

    // Hann 窓を事前計算
    let hann_window: Vec<f32> = (0..fft_size)
        .map(|n| 0.5 * (1.0 - (2.0 * PI * (n as f32) / (fft_size as f32 - 1.0)).cos()))
        .collect();

    let total_samples = pcm.len();
    if total_samples < fft_size {
        // サンプル数が少なすぎる場合は黒背景の画像を返す
        let img: ImageBuffer<Rgb<u8>, _> = ImageBuffer::from_pixel(width, height, Rgb([10, 15, 30]));
        let mut png_bytes = Vec::new();
        img.write_to(&mut Cursor::new(&mut png_bytes), image::ImageFormat::Png)
            .context("空画像のPNGエンコードに失敗しました")?;
        return Ok(png_bytes);
    }

    // 各時間ステップ（横軸）でのパワースペクトルを格納するバッファ
    let half_fft = fft_size / 2;
    let mut spectrogram_data = vec![vec![0.0f32; half_fft]; width as usize];

    let step = (total_samples - fft_size) as f64 / (width as f64 - 1.0).max(1.0);

    let mut global_min_db = f32::INFINITY;
    let mut global_max_db = f32::NEG_INFINITY;

    for col in 0..width as usize {
        let start_idx = (col as f64 * step).round() as usize;
        let mut buffer: Vec<Complex<f32>> = (0..fft_size)
            .map(|i| {
                let sample_val = pcm[start_idx + i] as f32 / 32768.0;
                Complex::new(sample_val * hann_window[i], 0.0)
            })
            .collect();

        fft.process(&mut buffer);

        for bin in 0..half_fft {
            let c = buffer[bin];
            let power = c.re * c.re + c.im * c.im;
            let db = 10.0 * (power + 1e-12).log10();
            spectrogram_data[col][bin] = db;

            if db < global_min_db {
                global_min_db = db;
            }
            if db > global_max_db {
                global_max_db = db;
            }
        }
    }

    // ダイナミックレンジの正規化 (コントラスト調整: 上位 40dB を強調)
    let dynamic_range = (global_max_db - global_min_db).max(20.0);
    let floor_db = global_max_db - dynamic_range.min(50.0);

    let mut img = ImageBuffer::new(width, height);

    // 天文学・SDR 定番 Waterfall カラーマップ:
    // 暗紺色 (ノイズフロア) -> 青 -> シアン -> 黄色 -> 白 (ピーク信号)
    for x in 0..width {
        let col_data = &spectrogram_data[x as usize];
        for y in 0..height {
            // y=0 が低周波 (下側), y=height-1 が高周波 (上側)
            let freq_ratio = (height - 1 - y) as f32 / (height - 1) as f32;
            let bin_idx = ((freq_ratio * (half_fft - 1) as f32).round() as usize).min(half_fft - 1);

            let val_db = col_data[bin_idx];
            let norm = ((val_db - floor_db) / (global_max_db - floor_db).max(1e-3)).clamp(0.0, 1.0);

            let rgb = colormap_waterfall(norm);
            img.put_pixel(x, y, rgb);
        }
    }

    let mut png_bytes = Vec::new();
    img.write_to(&mut Cursor::new(&mut png_bytes), image::ImageFormat::Png)
        .context("スペクトログラム画像のPNGエンコードに失敗しました")?;

    Ok(png_bytes)
}

/// 0.0〜1.0 の正規化強度を Waterfall カラー (暗紺〜青〜シアン〜黄〜白) に変換
fn colormap_waterfall(val: f32) -> Rgb<u8> {
    let v = val.clamp(0.0, 1.0);
    if v < 0.25 {
        // 0.0 .. 0.25: 暗紺色 (10, 15, 35) -> 濃青 (20, 40, 120)
        let t = v / 0.25;
        let r = (10.0 + 10.0 * t) as u8;
        let g = (15.0 + 25.0 * t) as u8;
        let b = (35.0 + 85.0 * t) as u8;
        Rgb([r, g, b])
    } else if v < 0.5 {
        // 0.25 .. 0.5: 濃青 (20, 40, 120) -> シアン (0, 180, 220)
        let t = (v - 0.25) / 0.25;
        let r = (20.0 * (1.0 - t)) as u8;
        let g = (40.0 + 140.0 * t) as u8;
        let b = (120.0 + 100.0 * t) as u8;
        Rgb([r, g, b])
    } else if v < 0.75 {
        // 0.5 .. 0.75: シアン (0, 180, 220) -> 黄色 (240, 230, 40)
        let t = (v - 0.5) / 0.25;
        let r = (240.0 * t) as u8;
        let g = (180.0 + 50.0 * t) as u8;
        let b = (220.0 * (1.0 - t) + 40.0 * t) as u8;
        Rgb([r, g, b])
    } else {
        // 0.75 .. 1.0: 黄色 (240, 230, 40) -> 純白 (255, 255, 255)
        let t = (v - 0.75) / 0.25;
        let r = (240.0 + 15.0 * t) as u8;
        let g = (230.0 + 25.0 * t) as u8;
        let b = (40.0 + 215.0 * t) as u8;
        Rgb([r, g, b])
    }
}
