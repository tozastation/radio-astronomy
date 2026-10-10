use satellite_tracker_rs::sdr::SdrCollector;

#[test]
fn test_sdr_lifecycle_standby_warmup() {
    let mut sdr = SdrCollector::new(true, 2_400_000.0, 40.0);

    // 初期状態はスタンバイではない
    assert!(!sdr.is_standby());

    // 1. スタンバイ移行 (USB 給電遮断)
    sdr.standby().expect("standby succeeds");
    assert!(sdr.is_standby(), "スタンバイフラグが有効であること");

    // スタンバイ中に read_samples を試みるとエラーになること
    let res = sdr.read_samples(1024);
    assert!(res.is_err(), "スタンバイ中のサンプリングはエラーとなること");

    // 2. ウォームアップ復帰 (AOS 接近時の再チューニング)
    let target_freq = 145_800_000.0;
    sdr.warmup(target_freq).expect("warmup succeeds");
    assert!(!sdr.is_standby(), "スタンバイが解除されること");
    assert_eq!(sdr.center_freq_hz(), target_freq, "周波数が設定されること");

    // 3. サンプル読み込み
    let samples = sdr.read_samples(2048).expect("read_samples succeeds");
    assert_eq!(samples.len(), 2048);

    // サンプル値の妥当性 (NaN / Inf なし、適切な振幅範囲 [-2.0, 2.0])
    for &(i, q) in &samples {
        assert!(!i.is_nan() && !i.is_infinite());
        assert!(!q.is_nan() && !q.is_infinite());
        assert!(i.abs() < 5.0 && q.abs() < 5.0);
    }
}

#[test]
fn test_mock_sdr_sample_energy() {
    let mut sdr = SdrCollector::new(true, 2_400_000.0, 40.0);
    sdr.warmup(145_800_000.0).unwrap();

    let samples = sdr.read_samples(4096).unwrap();
    let power: f32 = samples.iter().map(|&(i, q)| i * i + q * q).sum::<f32>() / (samples.len() as f32);

    // 合成 IQ 信号 + ガウスノイズの平均電力が正の値で適正な範囲にあること
    assert!(power > 0.01 && power < 10.0, "平均パワーが適正範囲内であること: got {}", power);
}
