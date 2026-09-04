use anyhow::Result;
use clap::{Parser, Subcommand};
use noaa_station::config::Config;
use noaa_station::scheduler::{run_daemon, show_schedule};
use noaa_station::voicevox::VoicevoxClient;
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
#[command(name = "noaa-station")]
#[command(author = "tozastation")]
#[command(version = "0.1.0")]
#[command(about = "NOAA気象衛星 自律自動受信・デコード地上局デーモン (with ずんだもん通知)")]
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
    /// 今後24時間の通過予定一覧をテーブル表示 (パス予測の即時確認)
    Schedule,
    /// ずんだもん音声発話の疎通テスト (VOICEVOX 連携確認)
    TestVoice,
    /// Discord Webhook 通知の疎通テスト (スマホ通知確認)
    TestDiscord,
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
        Commands::Schedule => {
            show_schedule(&config).await?;
        }
        Commands::TestVoice => {
            test_voice(&config).await?;
        }
        Commands::TestDiscord => {
            test_discord(&config).await?;
        }
        Commands::Daemon => {
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
    let client = noaa_station::discord::DiscordClient::new(config.discord.clone());

    if !config.discord.enabled {
        println!("⚠️  Discord通知が無効化されているか、Webhook URLが設定されていません。");
        println!("   .local.env または環境変数 DISCORD_WEBHOOK_URL を確認してください。");
        return Ok(());
    }

    client
        .send_satellite_pass_report(
            "NOAA 18 (テスト通知)",
            52.5,
            "東南東 (ESE)",
            137_912_500,
            "2026-09-04 12:13:00 〜 12:20:00",
            None,
        )
        .await?;

    println!("✨ Discord 通知リクエストが完了しました！スマホまたはPCのDiscordを確認してください。");
    Ok(())
}

