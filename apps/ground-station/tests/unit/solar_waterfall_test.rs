use ground_station::solar::waterfall::render_waterfall_png;
use std::fs;
use std::path::Path;

#[test]
fn test_render_waterfall_png_valid_output() {
    let output_dir = Path::new("target/tmp_test_solar_waterfall");
    fs::create_dir_all(output_dir).unwrap();
    let output_path = output_dir.join("test_solar_burst.png");

    // 60秒分の 1024ビン スペクトルデータを作成
    let mut spectra = Vec::new();
    for t in 0..60 {
        let mut row = vec![-40.0f32; 1024];
        if (20..35).contains(&t) {
            // フレアバーストを模した高強度ピーク（斜めドリフト）
            let peak_bin = 200 + (t - 20) * 30;
            for delta in 0..15 {
                let bin = (peak_bin + delta).min(1023);
                row[bin] = -15.0; // +25 dB の強いバースト
            }
        }
        spectra.push(row);
    }

    let result = render_waterfall_png(&spectra, 800, 400, &output_path);
    assert!(result.is_ok(), "render_waterfall_png should succeed");
    assert!(output_path.exists(), "Output PNG file must exist");

    let metadata = fs::metadata(&output_path).unwrap();
    assert!(metadata.len() > 1000, "PNG file must not be empty (got {} bytes)", metadata.len());

    // ヘッダー確認 (PNGマジックナンバー: 0x89, 'P', 'N', 'G')
    let bytes = fs::read(&output_path).unwrap();
    assert_eq!(&bytes[0..4], &[0x89, 0x50, 0x4E, 0x47]);
}

#[test]
fn test_render_waterfall_png_empty_spectra() {
    let output_dir = Path::new("target/tmp_test_solar_waterfall");
    fs::create_dir_all(output_dir).unwrap();
    let output_path = output_dir.join("test_empty.png");

    let spectra: Vec<Vec<f32>> = Vec::new();
    let result = render_waterfall_png(&spectra, 400, 200, &output_path);
    // 空配列でも黒背景画像を出力して成功する
    assert!(result.is_ok());
    assert!(output_path.exists());
}
