use satellite_tracker_rs::spooler::AudioSpooler;
use std::fs;
use std::path::PathBuf;

fn get_temp_spool_dir(test_name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("satellite_spool_test_{}", test_name));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn test_audio_spooler_record_and_finish() {
    let spool_dir = get_temp_spool_dir("record_and_finish");
    let mut spooler = AudioSpooler::new(spool_dir.clone(), 48_000, 10_000_000, 5.0);

    let wav_path = spooler
        .start_pass("ISS", "20261010_120000")
        .expect("start_pass succeeds");
    assert!(wav_path.exists());

    // 1000サンプルの PCM フレームを書き込み
    let pcm_data: Vec<i16> = (0..1000).map(|i| (i % 1000) as i16).collect();
    spooler.write_frames(&pcm_data).expect("write_frames succeeds");

    let finished_path = spooler.finish_pass().expect("finish_pass succeeds");
    assert_eq!(finished_path, Some(wav_path.clone()));

    // 生成された WAV ファイルを hound で検証
    let reader = hound::WavReader::open(&wav_path).expect("valid WAV file");
    let spec = reader.spec();
    assert_eq!(spec.channels, 1, "モノラル");
    assert_eq!(spec.sample_rate, 48_000, "48kHz");
    assert_eq!(spec.bits_per_sample, 16, "16-bit PCM");

    let read_samples: Vec<i16> = reader.into_samples::<i16>().map(|s| s.unwrap()).collect();
    assert_eq!(read_samples.len(), 1000);
    assert_eq!(read_samples[0], 0);
    assert_eq!(read_samples[100], 100);

    let _ = fs::remove_dir_all(&spool_dir);
}

#[test]
fn test_circuit_breaker_max_spool_bytes() {
    let spool_dir = get_temp_spool_dir("circuit_breaker");
    // 最大 2000 バイトのスプール容量
    let max_bytes = 2000;
    let mut spooler = AudioSpooler::new(spool_dir.clone(), 48_000, max_bytes, 1.0);

    // 1ファイルあたり 600 サンプル (約 1200 バイト + WAVヘッダ 44 バイト = 約 1244 バイト)
    let pcm_samples: Vec<i16> = vec![1234; 600];

    // 1つ目のファイル
    let f1 = spooler.start_pass("SAT1", "pass1").unwrap();
    spooler.write_frames(&pcm_samples).unwrap();
    spooler.finish_pass().unwrap();
    assert!(f1.exists());

    // 少し時間を空けてタイムスタンプに差をつける
    std::thread::sleep(std::time::Duration::from_millis(50));

    // 2つ目のファイル (合計約 2488 バイト -> 2000 バイト超過で f1 がパージされるはず)
    let f2 = spooler.start_pass("SAT2", "pass2").unwrap();
    spooler.write_frames(&pcm_samples).unwrap();
    spooler.finish_pass().unwrap();

    // サーキットブレーカー発動確認
    assert!(!f1.exists(), "最古のファイル f1 はパージされること");
    assert!(f2.exists(), "最新のファイル f2 は保持されること");

    let _ = fs::remove_dir_all(&spool_dir);
}
