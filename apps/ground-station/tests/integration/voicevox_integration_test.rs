use ground_station::config::VoicevoxConfig;
use ground_station::voicevox::VoicevoxClient;
use reqwest::Client;
use std::time::Duration;

// =============================================================================
// 🧪 VOICEVOX Engine コンテナ統合テスト (Integration Test)
// -----------------------------------------------------------------------------
// 【実行前提】
// ローカルまたは Docker で VOICEVOX Engine が起動している必要があります。
// 例:
//   docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
//
// コンテナが未起動の場合は、テストを失敗させずにスキップ（情報表示）します。
// コンテナが起動している場合は、実際に HTTP API を叩いてずんだもんの音声を合成し、
// 返ってきたバイナリが有効な WAV 音声（RIFF/WAVEヘッダ）であることを厳密に検証します。
// =============================================================================

#[tokio::test]
async fn test_voicevox_container_synthesis_integration() {
    let host = "http://localhost:50021";
    let http_client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    // 1. VOICEVOX Engine が起動しているかヘルスチェック (/version)
    let version_url = format!("{}/version", host);
    let is_running = match http_client.get(&version_url).send().await {
        Ok(resp) => resp.status().is_success(),
        Err(_) => false,
    };

    if !is_running {
        println!("\n==================================================================");
        println!("⚠️ [SKIP] VOICEVOX Engine コンテナ (localhost:50021) が起動していません。");
        println!("統合テストを実行するには以下のコマンドでコンテナを起動してください:");
        println!("  docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest");
        println!("==================================================================\n");
        return;
    }

    println!("✅ VOICEVOX Engine コンテナの稼働を確認しました。実APIテストを開始します。");

    let config = VoicevoxConfig {
        enabled: true,
        host: host.to_string(),
        speaker_id: 3, // ずんだもん
        timeout_secs: 15,
    };
    let client = VoicevoxClient::new(config);

    // 2. audio_query API の実行
    let test_text = "ずんだもんのインテグレーションテストなのだ！";
    let query_url = client.audio_query_url(test_text);
    let query_resp = http_client
        .post(&query_url)
        .send()
        .await
        .expect("audio_query リクエストに失敗しました");

    assert!(
        query_resp.status().is_success(),
        "audio_query API が成功ステータスを返す必要があります"
    );

    let query_json: serde_json::Value = query_resp
        .json()
        .await
        .expect("クエリJSONのパースに失敗しました");

    // クエリJSON内にアクセント句やテキスト情報が含まれていることを検証
    assert!(
        query_json.get("accent_phrases").is_some(),
        "クエリJSONに accent_phrases が含まれている必要があります"
    );

    // 3. synthesis API の実行 (実際の音声波形合成)
    let synth_url = client.synthesis_url();
    let synth_resp = http_client
        .post(&synth_url)
        .json(&query_json)
        .send()
        .await
        .expect("synthesis リクエストに失敗しました");

    assert!(
        synth_resp.status().is_success(),
        "synthesis API が成功ステータスを返す必要があります"
    );

    let wav_bytes = synth_resp
        .bytes()
        .await
        .expect("WAVバイナリの受信に失敗しました");

    // 4. 受信したバイナリが有効な WAV フォーマットか検証
    // WAV ファイルは先頭 4 バイトが "RIFF"、8〜11 バイトが "WAVE" である必要があります
    assert!(
        wav_bytes.len() > 44,
        "WAVファイルはヘッダ(44バイト)以上のサイズが必要です (実際: {} バイト)",
        wav_bytes.len()
    );
    assert_eq!(&wav_bytes[0..4], b"RIFF", "RIFF ヘッダシグネチャが一致しません");
    assert_eq!(&wav_bytes[8..12], b"WAVE", "WAVE フォーマットシグネチャが一致しません");

    println!(
        "🎉 ずんだもん音声合成テスト成功！受信WAVサイズ: {} バイト",
        wav_bytes.len()
    );
}
