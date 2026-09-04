use anyhow::{bail, Context, Result};
use log::info;
use std::path::Path;
use tokio::process::Command;

/// noaa-apt CLI 呼び出し用引数を構築
pub fn build_noaa_apt_args(input_wav: &Path, output_png: &Path) -> Vec<String> {
    vec![
        input_wav.to_string_lossy().to_string(),
        "-o".to_string(),
        output_png.to_string_lossy().to_string(),
    ]
}

pub struct Decoder;

impl Decoder {
    /// 録音された WAV ファイルから NOAA 気象衛星画像をデコードして PNG を生成
    pub async fn decode_apt(input_wav: &Path, output_png: &Path) -> Result<()> {
        info!(
            "画像デコード開始: 入力 {:?} -> 出力 {:?}",
            input_wav, output_png
        );

        if !input_wav.exists() {
            bail!("入力WAVファイルが存在しません: {:?}", input_wav);
        }

        if let Some(parent) = output_png.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("ディレクトリ作成失敗: {:?}", parent))?;
        }

        let args = build_noaa_apt_args(input_wav, output_png);

        let status = Command::new("noaa-apt")
            .args(&args)
            .status()
            .await
            .context("noaa-apt コマンドの実行に失敗しました。noaa-apt CLI がインストールされているか確認してください")?;

        if !status.success() {
            bail!("noaa-apt が異常終了しました: status {}", status);
        }

        if !output_png.exists() {
            bail!("デコード画像が生成されませんでした: {:?}", output_png);
        }

        info!("デコード成功: {:?}", output_png);
        Ok(())
    }
}
