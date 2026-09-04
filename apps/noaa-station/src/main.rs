use anyhow::Result;
use clap::{Parser, Subcommand};
use noaa_station::config::Config;
use noaa_station::scheduler::{run_daemon, show_schedule};
use noaa_station::voicevox::VoicevoxClient;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "noaa-station")]
#[command(author = "tozastation")]
#[command(version = "0.1.0")]
#[command(about = "NOAA気象衛星 自律自動受信・デコード地上局デーモン (with ずんだもん通知)")]
struct Cli {
    /// 設定ファイルのパス
    #[arg(short, long, default_value = "config.toml")]
    config: PathBuf,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// 今後24時間の通過予定一覧をテーブル表示
    Schedule,
    /// ずんだもん音声発話の疎通テスト
    TestVoice,
    /// 自律常駐監視デーモンを起動
    Daemon,
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let cli = Cli::parse();
    let config = Config::load_from_file(&cli.config)?;

    match cli.command {
        Commands::Schedule => {
            show_schedule(&config).await?;
        }
        Commands::TestVoice => {
            let client = VoicevoxClient::new(config.voicevox);
            println!("🔊 ずんだもん発話テストを実行中...");
            client.speak("テストなのだ！正常に通信できているのだ！").await?;
            println!("✨ 発話リクエストが完了しました。");
        }
        Commands::Daemon => {
            run_daemon(config).await?;
        }
    }

    Ok(())
}
