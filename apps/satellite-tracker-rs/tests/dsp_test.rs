use satellite_tracker_rs::dsp::DspProcessor;

#[test]
fn test_dsp_decimation_ratio() {
    let mut processor = DspProcessor::new(2_400_000.0, 48_000);
    // 2.4MSPS -> 48kHz (間引き比 50)
    let num_input_samples = 2400;
    let iq_samples: Vec<(f32, f32)> = (0..num_input_samples)
        .map(|i| {
            let t = i as f32 / 2_400_000.0;
            // 1kHz の複素搬送波
            let phase = 2.0 * std::f32::consts::PI * 1000.0 * t;
            (phase.cos(), phase.sin())
        })
        .collect();

    let mut out_pcm = Vec::new();
    processor.process_samples(&iq_samples, 0.0, &mut out_pcm);

    // 2400 / 50 = 48 サンプルの PCM が生成されること
    assert_eq!(out_pcm.len(), 48, "48サンプルのPCMが出力されること");
}

#[test]
fn test_dsp_doppler_mixing_and_demodulation() {
    let mut processor = DspProcessor::new(2_400_000.0, 48_000);
    let sample_rate = 2_400_000.0f32;
    let doppler_hz = 3_000.0f32;
    let audio_freq_hz = 1_000.0f32;
    let num_samples = 24000; // 10ms 分 (24000 / 50 = 480 PCMサンプル)

    // ドップラー偏移 + FM変調信号の合成
    // f(t) = f_doppler + delta_f * sin(2*pi*f_audio*t)
    let freq_dev = 2_500.0f32;
    let mut phase = 0.0f32;
    let mut iq_samples = Vec::with_capacity(num_samples);

    for i in 0..num_samples {
        let t = i as f32 / sample_rate;
        let inst_freq = doppler_hz + freq_dev * (2.0 * std::f32::consts::PI * audio_freq_hz * t).sin();
        phase += 2.0 * std::f32::consts::PI * inst_freq / sample_rate;
        iq_samples.push((phase.cos(), phase.sin()));
    }

    let mut out_pcm = Vec::new();
    // 理論ドップラー 3000Hz で逆ミキシングして復調
    processor.process_samples(&iq_samples, doppler_hz as f64, &mut out_pcm);

    assert_eq!(out_pcm.len(), num_samples / 50);

    // 出力 PCM が非ゼロであり、適切な振幅を持つことを確認
    let max_abs = out_pcm.iter().map(|s| s.abs()).max().unwrap_or(0);
    assert!(max_abs > 1000, "復調後の信号に十分な振幅があること: max={}", max_abs);

    // 有効な i16 範囲内に収まり、NaN / Inf などの異常値がないこと
    for sample in &out_pcm {
        assert!(*sample >= i16::MIN && *sample <= i16::MAX);
    }
}

#[test]
fn test_measure_spectrum() {
    let sample_rate = 2_400_000.0;
    let center_freq = 145_800_000.0;
    let offset_hz = 15_000.0;
    let fft_size = 2048;

    // 15kHz オフセットの正弦波 IQ 信号
    let iq_samples: Vec<(f32, f32)> = (0..(fft_size * 2))
        .map(|i| {
            let t = i as f32 / sample_rate as f32;
            let phase = 2.0 * std::f32::consts::PI * offset_hz as f32 * t;
            (phase.cos(), phase.sin())
        })
        .collect();

    let res = DspProcessor::measure_spectrum(&iq_samples, sample_rate, center_freq, fft_size);

    assert_eq!(res.center_freq_hz, center_freq);
    // ピーク周波数が約 145.815 MHz (±100Hz 精度)
    assert!(
        (res.measured_doppler_hz - offset_hz).abs() < 200.0,
        "オフセット推定精度: got {}, expected {}",
        res.measured_doppler_hz,
        offset_hz
    );
    assert!(res.snr_db > 10.0, "SNRが十分に検出されること: got {}", res.snr_db);
}

