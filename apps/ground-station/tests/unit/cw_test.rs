use std::f32::consts::PI;
use std::fs;

use ground_station::cw::{create_cw_wav, demodulate_cw_iq_to_pcm, generate_spectrogram_png};
use ground_station::decoder::DecoderEngine;
use ground_station::discord::PassStatus;
use ground_station::orbit::{SatellitePass, SignalType};

/// テスト用の合成 CW IQ 信号 (cu8: 8-bit unsigned interleaved IQ) を生成
/// - in_rate: 240,000 Hz
/// - tone_freq: 750 Hz
/// - duration: 0.1 秒 (24,000 IQ サンプル = 48,000 バイト)
/// - 前半 0.05秒 ON (短点)、後半 0.05秒 OFF (スペース)
fn generate_synthetic_cw_iq(sample_rate: u32, duration_secs: f32, tone_freq: f32) -> Vec<u8> {
    let num_samples = (sample_rate as f32 * duration_secs) as usize;
    let mut bytes = Vec::with_capacity(num_samples * 2);

    for n in 0..num_samples {
        let t = n as f32 / sample_rate as f32;
        // 前半のみキーダウン (振幅 100/128)
        let amp = if t < duration_secs * 0.5 { 100.0 } else { 0.0 };
        let phase = 2.0 * PI * tone_freq * t;
        let i = (128.0 + amp * phase.cos()).clamp(0.0, 255.0) as u8;
        let q = (128.0 + amp * phase.sin()).clamp(0.0, 255.0) as u8;
        bytes.push(i);
        bytes.push(q);
    }
    bytes
}

#[test]
fn test_demodulate_cw_iq_to_pcm() {
    let in_rate = 240_000;
    let out_rate = 11_025;
    let duration = 0.1; // 100ms
    let raw_iq = generate_synthetic_cw_iq(in_rate, duration, 750.0);

    let pcm = demodulate_cw_iq_to_pcm(&raw_iq, in_rate, out_rate, 750.0);

    let expected_samples = (out_rate as f32 * duration) as usize;
    // ダウンサンプリング後のサンプル数がほぼ一致するか
    assert!(
        (pcm.len() as isize - expected_samples as isize).abs() <= 2,
        "PCM サンプル長不一致: got {}, expected {}",
        pcm.len(),
        expected_samples
    );

    // 前半（トーンあり）の最大振幅と、後半（無音）の最大振幅を比較
    let half = pcm.len() / 2;
    let max_first_half = pcm[..half].iter().map(|&s| s.abs()).max().unwrap_or(0);
    let max_second_half = pcm[half..].iter().map(|&s| s.abs()).max().unwrap_or(0);

    assert!(
        max_first_half > 5000,
        "前半のトーン振幅が小さすぎます: {}",
        max_first_half
    );
    assert!(
        max_first_half > max_second_half * 5,
        "前半と後半のコントラストが不十分: first={}, second={}",
        max_first_half,
        max_second_half
    );
}

/// ノイズおよびDCオフセットが重畳された合成 CW IQ 信号を生成
fn generate_noisy_synthetic_cw_iq(
    sample_rate: u32,
    duration_secs: f32,
    tone_freq: f32,
    noise_amp: f32,
    dc_offset: f32,
) -> Vec<u8> {
    let num_samples = (sample_rate as f32 * duration_secs) as usize;
    let mut bytes = Vec::with_capacity(num_samples * 2);

    let mut lcg_state: u64 = 123456789;

    for n in 0..num_samples {
        let t = n as f32 / sample_rate as f32;
        let sig_amp = if t < duration_secs * 0.5 { 80.0 } else { 0.0 };
        let phase = 2.0 * PI * tone_freq * t;

        lcg_state = lcg_state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let n1 = ((lcg_state >> 32) as i32 as f32) / 2147483648.0;
        lcg_state = lcg_state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let n2 = ((lcg_state >> 32) as i32 as f32) / 2147483648.0;

        let i = (128.0 + dc_offset + sig_amp * phase.cos() + noise_amp * n1).clamp(0.0, 255.0) as u8;
        let q = (128.0 + dc_offset + sig_amp * phase.sin() + noise_amp * n2).clamp(0.0, 255.0) as u8;
        bytes.push(i);
        bytes.push(q);
    }
    bytes
}

#[test]
fn test_demodulate_noisy_cw_iq_suppresses_noise_floor() {
    let in_rate = 240_000;
    let out_rate = 11_025;
    let duration = 0.2; // 200ms
    // ノイズ振幅 20 (SNR 約 12dB)、DCオフセット +5LSB
    let raw_iq = generate_noisy_synthetic_cw_iq(in_rate, duration, 750.0, 20.0, 5.0);

    let pcm = demodulate_cw_iq_to_pcm(&raw_iq, in_rate, out_rate, 750.0);

    let half = pcm.len() / 2;
    let max_first_half = pcm[..half].iter().map(|&s| s.abs()).max().unwrap_or(0);
    let max_second_half = pcm[half..].iter().map(|&s| s.abs()).max().unwrap_or(0);

    assert!(
        max_first_half > 8000,
        "信号区間のトーン振幅が十分ではありません: {}",
        max_first_half
    );

    // 適応スケルチにより、ノイズ区間のトーン振幅が信号区間の1/5未満に抑圧されていることを検証
    assert!(
        max_second_half < max_first_half / 5,
        "ノイズ区間のスケルチ抑圧が不十分: first={}, second={}",
        max_first_half,
        max_second_half
    );
}

#[test]
fn test_create_cw_wav() {
    let in_rate = 240_000;
    let out_rate = 11_025;
    let raw_iq = generate_synthetic_cw_iq(in_rate, 0.05, 750.0);

    let wav_bytes = create_cw_wav(&raw_iq, in_rate, out_rate, 750.0);

    // RIFF/WAVE ヘッダ検証 (44バイト)
    assert!(wav_bytes.len() > 44);
    assert_eq!(&wav_bytes[0..4], b"RIFF");
    assert_eq!(&wav_bytes[8..12], b"WAVE");
    assert_eq!(&wav_bytes[12..16], b"fmt ");
    assert_eq!(&wav_bytes[36..40], b"data");

    let data_len = u32::from_le_bytes(wav_bytes[40..44].try_into().unwrap());
    assert_eq!(wav_bytes.len(), 44 + data_len as usize);
}

#[test]
fn test_generate_spectrogram_png() {
    let sample_rate = 11_025;
    let num_samples = 11_025; // 1秒
    let mut pcm = Vec::with_capacity(num_samples);
    for n in 0..num_samples {
        let t = n as f32 / sample_rate as f32;
        let s = (15000.0 * (2.0 * PI * 800.0 * t).sin()) as i16;
        pcm.push(s);
    }

    let png_bytes = generate_spectrogram_png(&pcm, sample_rate, 400, 200).expect("スペクトログラム生成失敗");

    // PNG シグネチャ検証
    assert!(png_bytes.len() > 8);
    assert_eq!(&png_bytes[0..8], b"\x89PNG\r\n\x1a\n");

    // image クレートで正しく読めるか確認
    let img = image::load_from_memory(&png_bytes).expect("PNG デコード失敗");
    assert_eq!(img.width(), 400);
    assert_eq!(img.height(), 200);
}

#[tokio::test]
async fn test_decoder_morse_cw_end_to_end() {
    let session_dir = std::env::temp_dir().join("test_ground_station_cw_e2e");
    let _ = fs::remove_dir_all(&session_dir);
    fs::create_dir_all(&session_dir).expect("session_dir 作成失敗");
    let raw_path = session_dir.join("raw.u8");

    // 0.1秒の CW IQ データを保存
    let raw_data = generate_synthetic_cw_iq(240_000, 0.1, 750.0);
    fs::write(&raw_path, raw_data).expect("raw.u8 保存失敗");

    let pass = SatellitePass {
        satellite_name: "XW-2A".to_string(),
        frequency_hz: 145_660_000,
        signal_type: SignalType::MorseCw,
        aos: chrono::Utc::now(),
        los: chrono::Utc::now() + chrono::Duration::seconds(180),
        max_elevation_deg: 45.0,
        peak_azimuth_deg: 90.0,
    };

    let result = DecoderEngine::decode(&pass, &raw_path, &session_dir)
        .await
        .expect("decode 失敗");

    // 成果物の検証
    assert!(result.audio_path.is_some(), "WAV 音声パスが返却されていません");
    let audio = result.audio_path.unwrap();
    assert!(audio.exists(), "WAV ファイルが実体として存在しません: {:?}", audio);

    assert!(result.image_path.is_some(), "スペクトログラム画像パスが返却されていません");
    let img = result.image_path.unwrap();
    assert!(img.exists(), "PNG 画像が実体として存在しません: {:?}", img);

    assert_eq!(result.telemetry.as_ref().unwrap().status, PassStatus::AudioRecorded);
    let summary = result.telemetry_summary.unwrap();
    assert!(summary.contains("XW-2A") && summary.contains("CW"));

    let _ = fs::remove_dir_all(&session_dir);
}

#[test]
fn test_decode_morse_text() {
    // 1Dit = 50ms (sample_rate = 200 Hz -> 10 samples)
    let dit_samples = 10;
    let dash_samples = 30;
    let elem_gap = 10;
    let char_gap = 30;
    let word_gap = 70;

    let mut envelopes = Vec::new();

    // "DF" を生成:
    // 'D': -..  (Dash, elem, Dit, elem, Dit)
    // char_gap
    // 'F': ..-. (Dit, elem, Dit, elem, Dash, elem, Dit)

    // D: Dash
    envelopes.extend(vec![1.0f32; dash_samples]);
    envelopes.extend(vec![0.0f32; elem_gap]);
    // Dit
    envelopes.extend(vec![1.0f32; dit_samples]);
    envelopes.extend(vec![0.0f32; elem_gap]);
    // Dit
    envelopes.extend(vec![1.0f32; dit_samples]);

    // Char gap
    envelopes.extend(vec![0.0f32; char_gap]);

    // F: Dit
    envelopes.extend(vec![1.0f32; dit_samples]);
    envelopes.extend(vec![0.0f32; elem_gap]);
    // Dit
    envelopes.extend(vec![1.0f32; dit_samples]);
    envelopes.extend(vec![0.0f32; elem_gap]);
    // Dash
    envelopes.extend(vec![1.0f32; dash_samples]);
    envelopes.extend(vec![0.0f32; elem_gap]);
    // Dit
    envelopes.extend(vec![1.0f32; dit_samples]);

    // 末尾余白
    envelopes.extend(vec![0.0f32; word_gap]);

    let decoded = ground_station::cw::decode_morse_from_envelope_samples(&envelopes, 200.0);
    assert_eq!(decoded, Some("DF".to_string()));
}

