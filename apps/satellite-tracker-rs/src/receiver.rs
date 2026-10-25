use std::path::Path;

/// 16-bit モノラルの標準 RIFF/WAVE ヘッダ (44バイト) を生成
///
/// 【数学・情報理論】
/// WAV フォーマットは先頭に RIFF チャンク (44バイト)、fmt サブチャンク、data サブチャンクが
/// リトルエンディアンで配置された標準規格です。
/// Carson の規則により 48kSPS でサンプリングされた 16-bit 復調音声を正しく再生・解析するために必須。
pub fn create_wav_header(sample_rate: u32, data_size: u32) -> [u8; 44] {
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
    header[24..28].copy_from_slice(&sample_rate.to_le_bytes()); // サンプリングレート
    let byte_rate = sample_rate * 1 * 2; // sample_rate * 1ch * 16bit/8
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&2u16.to_le_bytes());  // ブロック境界: 2 bytes (1 sample)
    header[34..36].copy_from_slice(&16u16.to_le_bytes()); // ビット深度: 16 bit

    // 3. "data" サブチャンク
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_size.to_le_bytes());

    header
}

/// rtl_fm コマンド実行用引数を構築 (ISS APRS / FM 音声衛星用)
///
/// - `-M fm`: 狭帯域 FM (NFM) 復調
/// - `-f <freq>`: 受信周波数 (Hz)
/// - `-s <rate>`: サンプリングレート (例: 48000)
/// - `-g <gain>`: チューナー利得 (例: 45.0)
/// - `-F 9`: 高精度リサンプリングフィルタ (ローリークダウンサンプリング)
/// - `-`: 標準出力へ生 PCM ストリーミング
pub fn build_rtl_fm_args(freq_hz: f64, sample_rate: u32, gain: f64) -> Vec<String> {
    vec![
        "-M".to_string(),
        "fm".to_string(),
        "-f".to_string(),
        format!("{:.0}", freq_hz),
        "-s".to_string(),
        sample_rate.to_string(),
        "-g".to_string(),
        format!("{:.1}", gain),
        "-F".to_string(),
        "9".to_string(),
        "-".to_string(),
    ]
}

/// rtl_sdr コマンド実行用引数を構築 (Meteor-M LRPT 等の生 IQ ベースバンド録音用)
pub fn build_rtl_sdr_args(freq_hz: f64, sample_rate: u32, gain: f64, output_path: &Path) -> Vec<String> {
    vec![
        "-f".to_string(),
        format!("{:.0}", freq_hz),
        "-s".to_string(),
        sample_rate.to_string(),
        "-g".to_string(),
        format!("{:.1}", gain),
        output_path.to_string_lossy().to_string(),
    ]
}

/// 衛星種別に応じて生 IQ ベースバンド (rtl_sdr 240kSPS) で録音すべきかを判定
pub fn is_raw_iq_target(sat_name: &str) -> bool {
    let name_upper = sat_name.to_uppercase();
    name_upper.contains("METEOR") || name_upper.contains("FUNCUBE") || name_upper.contains("AO-73")
}
