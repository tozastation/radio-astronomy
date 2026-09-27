use anyhow::{Context, Result};
use clap::Parser;
use log::{error, info};
use std::path::PathBuf;

use ground_station::config::Config;
use ground_station::solar::manager::SolarStationManager;

#[derive(Parser, Debug)]
#[command(
    name = "solar-station",
    author = "tozastation",
    version = "0.1.0",
    about = "☀️ 自律型パーソナル太陽電波観測ステーション (RTL-SDR Blog V4 + 70MHz帯)"
)]
struct Cli {
    /// 設定ファイルのパス
    #[arg(short, long, default_value = "config.toml")]
    config: PathBuf,

    /// 疑似IQ信号によるドライラン（SDRなしでの動作確認用）
    #[arg(long, default_value_t = false)]
    dry_run: bool,

    /// 日没時も停止せず24時間連続観測するモード
    #[arg(long, default_value_t = false)]
    continuous: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    // ログ初期化
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let cli = Cli::parse();

    info!("設定ファイルを読み込み中: {:?}", cli.config);
    let config = Config::load_from_file(&cli.config)
        .with_context(|| format!("設定ファイルのロードに失敗しました: {:?}", cli.config))?;

    if let Some(solar) = &config.solar {
        if !solar.enabled {
            info!("config.toml で solar.enabled = false に設定されています。観測を開始せず終了します。");
            return Ok(());
        }
    } else {
        info!("config.toml に [solar] セクションが未定義です。デフォルト設定で観測を準備します。");
    }

    let manager = SolarStationManager::new(config);

    if let Err(e) = manager.run(cli.dry_run, cli.continuous).await {
        error!("solar-station 実行中に致命的エラーが発生しました: {:?}", e);
        std::process::exit(1);
    }

    Ok(())
}
