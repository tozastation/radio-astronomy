use ground_station::config::VoicevoxConfig;
use ground_station::voicevox::VoicevoxClient;

#[test]
fn test_voicevox_url_generation() {
    let config = VoicevoxConfig {
        enabled: true,
        host: "http://localhost:50021/".to_string(),
        speaker_id: 3,
        timeout_secs: 15,
    };
    let client = VoicevoxClient::new(config);
    let query_url = client.audio_query_url("こんにちは");
    assert!(query_url.contains("speaker=3"));
    assert!(query_url.contains("audio_query"));
    assert!(query_url.contains("%E3%81%93%E3%82%93%E3%81%AB%E3%81%A1%E3%81%AF"));

    let synth_url = client.synthesis_url();
    assert!(synth_url.contains("speaker=3"));
    assert!(synth_url.contains("synthesis"));
}
