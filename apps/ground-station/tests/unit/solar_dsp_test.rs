use ground_station::solar::dsp::{calculate_median_and_mad, SolarDsp};

#[test]
fn test_median_and_mad_odd_and_even() {
    // 奇数個のデータ
    let mut data_odd = vec![1.0, 2.0, 3.0, 4.0, 5.0];
    let (median, mad) = calculate_median_and_mad(&mut data_odd);
    assert_eq!(median, 3.0);
    // |1-3|=2, |2-3|=1, |3-3|=0, |4-3|=1, |5-3|=2 -> sorted: [0, 1, 1, 2, 2] -> median is 1.0
    assert_eq!(mad, 1.0);

    // 偶数個のデータ
    let mut data_even = vec![1.0, 2.0, 3.0, 4.0];
    let (median, _) = calculate_median_and_mad(&mut data_even);
    assert_eq!(median, 2.5);

    // 外れ値に対する頑健性（ロバスト性）
    let mut data_outlier = vec![10.0, 11.0, 10.5, 11.5, 10.8, 100.0]; // 100.0 は外れ値
    let (median, mad) = calculate_median_and_mad(&mut data_outlier);
    assert!((median - 10.9).abs() < 0.5, "Median should be near 10.9, got {}", median);
    assert!(mad > 0.0 && mad < 2.0, "MAD should be robust against single outlier, got {}", mad);
}

#[test]
fn test_dsp_1sec_aggregation_and_burst_detection() {
    let fft_size = 1024;
    let sample_rate = 2_400_000;
    let mut dsp = SolarDsp::new(fft_size, sample_rate, 4.0, 3.0);

    // 2.4MSPSで1秒あたり 2,400,000 サンプル（IQで 4,800,000 バイト）
    // 0.1秒チャンク（240,000 サンプル = 480,000 バイト）
    let chunk_size = 480_000;
    let baseline_chunk = vec![127u8; chunk_size]; // 無信号（ノイズフロア付近）

    // 10チャンク（1秒分）投入して、1秒メトリクスが出力されるか確認
    let mut metric_opt = None;
    for i in 0..10 {
        metric_opt = dsp.process_chunk(&baseline_chunk, 1000);
        if i < 9 {
            assert!(metric_opt.is_none(), "Chunks 0..8 should not produce 1s metric");
        }
    }
    assert!(metric_opt.is_some(), "10th chunk must produce 1-second metric");
    let metric = metric_opt.unwrap();
    assert_eq!(metric.spectrum_db.len(), fft_size);
    assert_eq!(metric.timestamp, 1000);
    assert!(!metric.is_burst, "Initial baseline noise should not be detected as burst");
}

#[test]
fn test_dsp_burst_detection_trigger_on_sharp_rise() {
    let fft_size = 1024;
    let sample_rate = 2_400_000;
    let mut dsp = SolarDsp::new(fft_size, sample_rate, 3.0, 3.0);

    let chunk_size = 480_000;
    // 静かなベースライン信号 (I=127, Q=127)
    let quiet_chunk = vec![127u8; chunk_size];

    // ベースライン学習用に20秒分の静穏データを流す
    for sec in 0..20 {
        for _ in 0..10 {
            dsp.process_chunk(&quiet_chunk, sec);
        }
    }

    // 突発フレアバースト信号（フルスケールの強い正弦波風信号）
    let mut burst_chunk = Vec::with_capacity(chunk_size);
    for i in 0..(chunk_size / 2) {
        let phase = 2.0 * std::f32::consts::PI * (i as f32) / 10.0;
        let i_val = (127.5 + 120.0 * phase.cos()).clamp(0.0, 255.0) as u8;
        let q_val = (127.5 + 120.0 * phase.sin()).clamp(0.0, 255.0) as u8;
        burst_chunk.push(i_val);
        burst_chunk.push(q_val);
    }

    // フレアバーストを2秒分流す
    let mut burst_detected = false;
    for sec in 20..23 {
        for _ in 0..10 {
            if let Some(m) = dsp.process_chunk(&burst_chunk, sec) {
                if m.is_burst {
                    burst_detected = true;
                    assert!(m.snr_db > 10.0, "Burst SNR should be > 10 dB, got {}", m.snr_db);
                }
            }
        }
    }

    assert!(burst_detected, "Sharp signal jump must trigger is_burst = true");
}
