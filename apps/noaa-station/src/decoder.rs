use anyhow::{bail, Context, Result};
use log::info;
use std::path::Path;
use tokio::process::Command;

// =============================================================================
// 🖼️ 画像デコードモジュール (Decoder)
// -----------------------------------------------------------------------------
// 【背景と処理内容】
// NOAA気象衛星が送信する APT (Automatic Picture Transmission) は、2400Hzの搬送波に
// 振幅変調(AM)された可視光と赤外線の2チャンネルのアナログファクシミリ信号です。
// `noaa-apt` CLI は、音声WAVから以下の処理を一括で行い、高品質なPNG地球画像を生成します：
// 1. 同期パルス（各ラインの先頭にある白黒バー）を検知して歪み・水平同期を補正
// 2. 衛星のセンサ較正データ（テレメトリ）を読み取り、赤外線温度・コントラストを正規化
// 3. 地形データ・昼夜判定に基づき、カラーパレットで美しいフォルスカラー着色
// =============================================================================

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

        // 出力先ディレクトリの自動作成
        if let Some(parent) = output_png.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("ディレクトリ作成失敗: {:?}", parent))?;
        }

        let args = build_noaa_apt_args(input_wav, output_png);

        // noaa-apt CLI を実行
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
