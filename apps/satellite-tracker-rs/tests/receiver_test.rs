use satellite_tracker_rs::receiver::{build_rtl_fm_args, build_rtl_sdr_args, create_wav_header};
use std::path::Path;

#[test]
fn test_create_wav_header() {
    let sample_rate = 48_000u32;
    let data_size = 96_000u32; // 1秒分の 48kHz 16-bit モノラル
    let header = create_wav_header(sample_rate, data_size);

    // 1. "RIFF" チャンク
    assert_eq!(&header[0..4], b"RIFF");
    let file_size = u32::from_le_bytes([header[4], header[5], header[6], header[7]]);
    assert_eq!(file_size, 36 + data_size);
    assert_eq!(&header[8..12], b"WAVE");

    // 2. "fmt " サブチャンク
    assert_eq!(&header[12..16], b"fmt ");
    let subchunk1_size = u32::from_le_bytes([header[16], header[17], header[18], header[19]]);
    assert_eq!(subchunk1_size, 16);
    let audio_format = u16::from_le_bytes([header[20], header[21]]);
    assert_eq!(audio_format, 1); // 1 = PCM
    let num_channels = u16::from_le_bytes([header[22], header[23]]);
    assert_eq!(num_channels, 1); // Mono
    let rate = u32::from_le_bytes([header[24], header[25], header[26], header[27]]);
    assert_eq!(rate, sample_rate);
    let byte_rate = u32::from_le_bytes([header[28], header[29], header[30], header[31]]);
    assert_eq!(byte_rate, sample_rate * 2);
    let block_align = u16::from_le_bytes([header[32], header[33]]);
    assert_eq!(block_align, 2);
    let bits_per_sample = u16::from_le_bytes([header[34], header[35]]);
    assert_eq!(bits_per_sample, 16);

    // 3. "data" サブチャンク
    assert_eq!(&header[36..40], b"data");
    let read_data_size = u32::from_le_bytes([header[40], header[41], header[42], header[43]]);
    assert_eq!(read_data_size, data_size);
}

#[test]
fn test_build_rtl_fm_args() {
    let args = build_rtl_fm_args(145_825_000.0, 48_000, 45.0);
    assert_eq!(
        args,
        vec![
            "-M", "fm",
            "-f", "145825000",
            "-s", "48000",
            "-g", "45.0",
            "-F", "9",
            "-"
        ]
    );
}

#[test]
fn test_build_rtl_sdr_args() {
    let path = Path::new("/tmp/spool/METEOR_pass.raw");
    let args = build_rtl_sdr_args(137_900_000.0, 240_000, 45.0, path);
    assert_eq!(
        args,
        vec![
            "-f", "137900000",
            "-s", "240000",
            "-g", "45.0",
            "/tmp/spool/METEOR_pass.raw"
        ]
    );
}
