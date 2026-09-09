use anyhow::Result;
use clap::{Parser, Subcommand};
use ground_station::config::Config;
use ground_station::scheduler::{run_daemon, show_schedule};
use ground_station::voicevox::VoicevoxClient;
use std::path::PathBuf;

// =============================================================================
// 🚀 CLI エントリーポイント (main)
// -----------------------------------------------------------------------------
// 【言語対比】
// - `clap` による CLI 定義:
//   Go の `spf13/cobra` や Python の `click` / `argparse`、TypeScript の `commander`
//   に相当する業界標準の CLI フレームワークです。
//   `#[derive(Parser)]` や `#[derive(Subcommand)]` のアノテーションをつけるだけで、
//   構造体と enum から `--help` のドキュメントや引数パースロジックがコンパイル時に自動生成されます。
// - `#[tokio::main]`:
//   Rust の `fn main()` は通常同期関数ですが、このマクロを付与することで
//   裏側でマルチスレッド非同期イベントループ（Goroutine スケジューラに相当）が起動し、
//   `main()` 関数内で直接 `.await` が使えるようになります。
// =============================================================================

#[derive(Parser)]
#[command(name = "ground-station")]
#[command(author = "tozastation")]
#[command(version = "0.1.0")]
#[command(about = "パーソナル自律衛星地上局デーモン (Meteor-M, CubeSat, ISS with ずんだもん通知)")]
struct Cli {
    /// 設定ファイルのパス (デフォルト: config.toml)
    #[arg(short, long, default_value = "config.toml")]
    config: PathBuf,

    /// 実行するサブコマンド
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// RTL-SDRデバイス、デコーダ、VOICEVOX、ストレージの事前ヘルスチェックを実行
    Check,
    /// 今後24時間の通過予定一覧をテーブル表示 (パス予測の即時確認)
    Schedule,
    /// 今後24時間の通過予定一覧を Discord へ送信 (デイリースケジュールの即時テスト)
    ScheduleDiscord,
    /// ずんだもん音声発話の疎通テスト (VOICEVOX 連携確認)
    TestVoice,
    /// Discord Webhook 通知の疎通テスト (スマホ通知確認)
    TestDiscord,
    /// ADS-B 航空機監視・実機写真・Discord/VOICEVOX通知の疎通テスト
    TestAdsb,
    /// 既存の録音セッションディレクトリを手動で再デコード (未復調の raw.u8 / raw.wav の再処理・Discord通知)
    DecodePass {
        /// 対象のセッションディレクトリのパス (例: data/noaa/20260909_074013_XW-2A)
        session_dir: PathBuf,
        /// Discord に通知を送るか (デフォルト: true)
        #[arg(long, default_value_t = true)]
        discord: bool,
    },
    /// 自律常駐監視デーモンを起動 (自動観測本番モード)
    Daemon,
}

#[tokio::main]
async fn main() -> Result<()> {
    // ログレベルの初期化 (環境変数 RUST_LOG で制御可能、デフォルトは info)
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let cli = Cli::parse();
    let config = Config::load_from_file(&cli.config)?;

    // データ保存先ディレクトリが存在しない場合は自動作成
    if let Err(e) = std::fs::create_dir_all(&config.storage.output_dir) {
        log::warn!("保存先ディレクトリの作成に失敗しました ({}): {}", config.storage.output_dir, e);
    }

    // 【言語対比】`match` によるサブコマンド分岐:
    // Go の `switch cmd` や TypeScript の `switch (command.type)` に相当。
    match cli.command {
        Commands::Check => {
            let report = ground_station::health::run_preflight_checks(&config).await?;
            report.print_table();
            if report.is_fatal() {
                anyhow::bail!("ヘルスチェックで致命的なエラーが検出されました。上記の対処法に従って解決してください。");
            }
        }
        Commands::Schedule => {
            show_schedule(&config).await?;
        }
        Commands::ScheduleDiscord => {
            ground_station::scheduler::send_schedule_to_discord(&config).await?;
        }
        Commands::TestVoice => {
            test_voice(&config).await?;
        }
        Commands::TestDiscord => {
            test_discord(&config).await?;
        }
        Commands::TestAdsb => {
            ground_station::adsb::test_adsb_alert(&config).await?;
        }
        Commands::DecodePass { session_dir, discord } => {
            decode_pass(&config, &session_dir, discord).await?;
        }
        Commands::Daemon => {
            println!("🔍 起動時事前ヘルスチェックを実行中...");
            let report = ground_station::health::run_preflight_checks(&config).await?;
            report.print_table();
            if report.is_fatal() {
                anyhow::bail!("ヘルスチェックで致命的なエラーが検出されたため、デーモン起動を中止しました。");
            }
            run_daemon(config).await?;
        }
    }

    Ok(())
}

async fn test_voice(config: &Config) -> Result<()> {
    let client = VoicevoxClient::new(config.voicevox.clone());
    println!("🔊 ずんだもん発話テストを実行中...");
    client.speak("テストなのだ！正常に通信できているのだ！").await?;
    println!("✨ 発話リクエストが完了しました。");
    Ok(())
}

async fn test_discord(config: &Config) -> Result<()> {
    println!("📲 Discord Webhook 通知テストを実行中...");
    let client = ground_station::discord::DiscordClient::new(config.discord.clone());

    if !config.discord.enabled {
        println!("⚠️  Discord通知が無効化されているか、Webhook URLが設定されていません。");
        println!("   .local.env または環境変数 DISCORD_WEBHOOK_URL を確認してください。");
        return Ok(());
    }

    let sample_image = ground_station::discord::DiscordClient::create_test_sample_image();
    let sample_wav = ground_station::discord::DiscordClient::create_test_sample_wav();
    let report = ground_station::discord::PassReport {
        satellite_name: "NOAA 18 (テスト通知)".to_string(),
        signal_type_name: "APT (2.4kHz AM)".to_string(),
        max_elevation_deg: 52.5,
        direction: "東南東 (ESE)".to_string(),
        frequency_hz: 137_912_500,
        pass_time_str: "2026-09-05 17:15:00 〜 17:22:00 (7分00秒)".to_string(),
        telemetry: Some(ground_station::discord::SatelliteTelemetry {
            snr_db: Some(18.5),
            lines_or_packets: Some("2,180 有効走査線 (同期率 98%)".to_string()),
            housekeeping: vec![
                ("バッテリ".to_string(), "8.24 V".to_string()),
                ("太陽電池".to_string(), "140 mA".to_string()),
                ("センサ温度".to_string(), "+14.2°C".to_string()),
            ],
            status: ground_station::discord::PassStatus::ImageDecoded,
        }),
        has_image: true,
        has_audio: true,
        next_pass_info: Some("🛰️ **NOAA 19** (22:08 〜 40.7° 西南西 (WSW))".to_string()),
    };

    client.send_pass_report(&report, Some(sample_image), Some(sample_wav)).await?;

    println!("✨ Discord 通知リクエストが完了しました！スマホまたはPCのDiscordを確認してください。");
    Ok(())
}

async fn decode_pass(config: &Config, session_dir: &std::path::Path, send_discord: bool) -> Result<()> {
    println!("🔍 セッションディレクトリの解析中: {:?}", session_dir);
    let (pass, raw_path) = ground_station::worker::resolve_pass_from_session_dir(config, session_dir)?;

    println!("🛰️ 衛星通過情報の特定成功:");
    println!("   衛星名: {}", pass.satellite_name);
    println!("   信号方式: {}", pass.signal_type.name());
    println!("   下り周波数: {:.4} MHz", pass.frequency_hz as f64 / 1_000_000.0);
    println!("   生データ: {:?}", raw_path);

    let discord_client = if send_discord && config.discord.enabled {
        Some(ground_station::discord::DiscordClient::new(config.discord.clone()))
    } else {
        None
    };

    let voicevox_client = if config.voicevox.enabled {
        Some(ground_station::voicevox::VoicevoxClient::new(config.voicevox.clone()))
    } else {
        None
    };

    println!("⚡ デコード処理を実行中...");
    let result = ground_station::worker::process_decode_job(
        &pass,
        &raw_path,
        session_dir,
        discord_client.as_ref(),
        voicevox_client.as_ref(),
    )
    .await?;

    println!("✨ デコード完了:");
    if let Some(summary) = result.telemetry_summary {
        println!("   要約: {}", summary);
    }
    if let Some(img) = result.image_path {
        println!("   🖼️ 画像生成: {:?}", img);
    }
    if let Some(audio) = result.audio_path {
        println!("   🎵 音声生成: {:?}", audio);
    }

    Ok(())
}

