use anyhow::{Context, Result};
use image::{ImageBuffer, Rgb};
use std::fs;
use std::io::Cursor;
use std::path::Path;

/// 太陽電波スペクトルの時系列配列からウォーターフォール（Spectrogram）PNG画像を生成・保存します。
///
/// # 引数
/// - `spectra`: 各秒のパワースペクトル（dB値）の時系列配列（サイズ: [時間T][FFTビン数]）
/// - `width`: 出力画像の横幅ピクセル（時間軸）
/// - `height`: 出力画像の高さピクセル（周波数軸）
/// - `output_path`: 保存先のPNGファイルパス
pub fn render_waterfall_png(
    spectra: &[Vec<f32>],
    width: u32,
    height: u32,
    output_path: &Path,
) -> Result<()> {
    let width = width.max(64);
    let height = height.max(64);

    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("ディレクトリの作成に失敗しました: {:?}", parent))?;
    }

    if spectra.is_empty() {
        // 空データの場合は暗紺色の背景画像を生成して保存
        let img: ImageBuffer<Rgb<u8>, _> =
            ImageBuffer::from_pixel(width, height, Rgb([10, 15, 30]));
        img.save(output_path)
            .with_context(|| format!("空画像の保存に失敗しました: {:?}", output_path))?;
        return Ok(());
    }

    let time_steps = spectra.len();
    let num_bins = spectra[0].len();
    if num_bins == 0 {
        let img: ImageBuffer<Rgb<u8>, _> =
            ImageBuffer::from_pixel(width, height, Rgb([10, 15, 30]));
        img.save(output_path)?;
        return Ok(());
    }

    // ダイナミックレンジの算出（外れ値に強い分位点ベース）
    let mut all_values = Vec::with_capacity(time_steps * num_bins);
    for row in spectra {
        all_values.extend_from_slice(row);
    }
    all_values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let floor_idx = (all_values.len() as f32 * 0.20) as usize;
    let peak_idx = (all_values.len() as f32 * 0.99) as usize;
    let floor_db = all_values[floor_idx.min(all_values.len() - 1)];
    let peak_db = all_values[peak_idx.min(all_values.len() - 1)];
    let dynamic_range = (peak_db - floor_db).max(15.0);
    let max_display_db = floor_db + dynamic_range;

    let mut img = ImageBuffer::new(width, height);

    // 時間軸（横軸 x）と周波数軸（縦軸 y）のマッピング
    // x: 0 が最古, width-1 が最新
    // y: 0 が高周波 (上), height-1 が低周波 (下)
    for x in 0..width {
        let time_ratio = x as f32 / (width - 1).max(1) as f32;
        let t_idx = ((time_ratio * (time_steps - 1) as f32).round() as usize).min(time_steps - 1);
        let spectrum = &spectra[t_idx];

        for y in 0..height {
            // y=0 を最高周波数 (ビン末尾), y=height-1 を最低周波数 (ビン0) にマッピング
            let freq_ratio = (height - 1 - y) as f32 / (height - 1).max(1) as f32;
            let bin_idx = ((freq_ratio * (num_bins - 1) as f32).round() as usize).min(num_bins - 1);

            let val_db = spectrum[bin_idx];
            let norm = ((val_db - floor_db) / (max_display_db - floor_db).max(1e-3)).clamp(0.0, 1.0);

            let rgb = colormap_solar_flare(norm);
            img.put_pixel(x, y, rgb);
        }
    }

    let mut png_bytes = Vec::new();
    img.write_to(&mut Cursor::new(&mut png_bytes), image::ImageFormat::Png)
        .context("ウォーターフォール画像のPNGエンコードに失敗しました")?;

    fs::write(output_path, png_bytes)
        .with_context(|| format!("ウォーターフォールPNGの保存に失敗しました: {:?}", output_path))?;

    Ok(())
}

/// 太陽フレア電波スペクトログラム用カラーパレット (Inferno/熱放射カラー)
/// 0.0〜1.0 の正規化強度を [暗紺 -> 紫 -> 赤橙 -> 黄金色 -> 白熱白] に変換
fn colormap_solar_flare(val: f32) -> Rgb<u8> {
    let v = val.clamp(0.0, 1.0);
    if v < 0.20 {
        // 0.00 .. 0.20: 暗紺色 (10, 15, 30) -> 濃紫 (60, 15, 90)
        let t = v / 0.20;
        let r = (10.0 + 50.0 * t) as u8;
        let g = (15.0 + 0.0 * t) as u8;
        let b = (30.0 + 60.0 * t) as u8;
        Rgb([r, g, b])
    } else if v < 0.45 {
        // 0.20 .. 0.45: 濃紫 (60, 15, 90) -> 赤紫・真紅 (180, 20, 60)
        let t = (v - 0.20) / 0.25;
        let r = (60.0 + 120.0 * t) as u8;
        let g = (15.0 + 5.0 * t) as u8;
        let b = (90.0 - 30.0 * t) as u8;
        Rgb([r, g, b])
    } else if v < 0.75 {
        // 0.45 .. 0.75: 真紅 (180, 20, 60) -> 橙朱色 (245, 120, 20)
        let t = (v - 0.45) / 0.30;
        let r = (180.0 + 65.0 * t) as u8;
        let g = (20.0 + 100.0 * t) as u8;
        let b = (60.0 - 40.0 * t) as u8;
        Rgb([r, g, b])
    } else if v < 0.92 {
        // 0.75 .. 0.92: 橙朱色 (245, 120, 20) -> 黄金色 (255, 220, 50)
        let t = (v - 0.75) / 0.17;
        let r = 255;
        let g = (120.0 + 100.0 * t) as u8;
        let b = (20.0 + 30.0 * t) as u8;
        Rgb([r, g, b])
    } else {
        // 0.92 .. 1.00: 黄金色 (255, 220, 50) -> 白熱白 (255, 255, 255)
        let t = (v - 0.92) / 0.08;
        let r = 255;
        let g = (220.0 + 35.0 * t) as u8;
        let b = (50.0 + 205.0 * t) as u8;
        Rgb([r, g, b])
    }
}
