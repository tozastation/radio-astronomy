use crate::orbit::{SatellitePass, SignalType};
use anyhow::{bail, Context, Result};
use log::{info, warn};
use std::path::{Path, PathBuf};
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

/// satdump CLI 呼び出し用引数を構築 (Meteor-M LRPT用: デフォルト 80k OQPSK)
pub fn build_satdump_lrpt_args(input_raw: &Path, output_dir: &Path) -> Vec<String> {
    build_satdump_lrpt_args_with_pipeline("meteor_m2-x_lrpt_80k", input_raw, output_dir)
}

/// satdump CLI 呼び出し用引数を構築 (パイプライン名指定)
pub fn build_satdump_lrpt_args_with_pipeline(pipeline: &str, input_raw: &Path, output_dir: &Path) -> Vec<String> {
    vec![
        pipeline.to_string(),
        "baseband".to_string(),
        input_raw.to_string_lossy().to_string(),
        output_dir.to_string_lossy().to_string(),
        "--samplerate".to_string(),
        "240000".to_string(),
        "--baseband_format".to_string(),
        "cu8".to_string(),
    ]
}

/// 指定ディレクトリ（およびサブディレクトリ）から最もサイズの大きい復調画像（PNG/JPG）を探索
pub fn find_best_image_in_dir(dir: &Path) -> Option<std::path::PathBuf> {
    let mut best_image: Option<(std::path::PathBuf, u64)> = None;
    search_images_recursive(dir, &mut best_image);
    best_image.map(|(p, _)| p)
}

fn search_images_recursive(dir: &Path, best: &mut Option<(std::path::PathBuf, u64)>) {
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                search_images_recursive(&path, best);
            } else if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                let ext_lower = ext.to_lowercase();
                if ext_lower == "png" || ext_lower == "jpg" || ext_lower == "jpeg" {
                    if let Ok(meta) = entry.metadata() {
                        let len = meta.len();
                        if best.as_ref().map_or(true, |(_, max_len)| len > *max_len) {
                            *best = Some((path, len));
                        }
                    }
                }
            }
        }
    }
}

pub struct Decoder;

impl Decoder {
    /// 録音された WAV ファイルから NOAA 気象衛星画像をデコードして PNG を生成
    pub async fn decode_apt(input_wav: &Path, output_png: &Path) -> Result<()> {
        info!(
            "NOAA APT 画像デコード開始: 入力 {:?} -> 出力 {:?}",
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

        info!("NOAA APT デコード成功: {:?}", output_png);
        Ok(())
    }

    /// 録音された生IQデータから Meteor-M 気象衛星画像（LRPT デジタルQPSK/OQPSK）をデコード
    pub async fn decode_meteor_lrpt(input_raw: &Path, output_dir: &Path) -> Result<std::path::PathBuf> {
        info!(
            "Meteor-M LRPT デコード開始: 入力 {:?} -> 出力ディレクトリ {:?}",
            input_raw, output_dir
        );

        if !input_raw.exists() {
            bail!("入力生IQファイルが存在しません: {:?}", input_raw);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        // 現行の Meteor-M N2-3 / N2-4 は 80k OQPSK が標準。
        // 運用状況によって 72k OQPSK に切り替わる場合があるため、80k -> 72k の順に自動適応試行
        let pipelines = ["meteor_m2-x_lrpt_80k", "meteor_m2-x_lrpt"];
        let mut last_status = None;

        for pipeline in pipelines {
            info!("SatDump パイプライン実行試行: {}", pipeline);
            let args = build_satdump_lrpt_args_with_pipeline(pipeline, input_raw, output_dir);

            let status = Command::new("satdump")
                .args(&args)
                .status()
                .await
                .context("satdump コマンドの実行に失敗しました。satdump CLI が導入されているか確認してください")?;

            if status.success() {
                if let Some(image_path) = find_best_image_in_dir(output_dir) {
                    info!("Meteor-M デコード成功 (パイプライン: {}): {:?}", pipeline, image_path);
                    return Ok(image_path);
                }
            } else {
                warn!("SatDump パイプライン {} 終了 (非ゼロ終了コード): status {}", pipeline, status);
                last_status = Some(status);
            }
        }

        if let Some(image_path) = find_best_image_in_dir(output_dir) {
            info!("Meteor-M デコード画像確認: {:?}", image_path);
            Ok(image_path)
        } else if let Some(status) = last_status {
            bail!("satdump が異常終了しました: status {}", status);
        } else {
            bail!("satdump による画像ファイルが生成されませんでした: {:?}", output_dir);
        }
    }

    /// キューブサット生IQ信号のデコード (satdump / gr-satellites)
    pub async fn decode_cubesat(
        pass: &crate::orbit::SatellitePass,
        input_raw: &Path,
        output_dir: &Path,
    ) -> Result<std::path::PathBuf> {
        info!(
            "CubeSat デコード開始: 衛星 {}, 方式 {:?}, 入力 {:?} -> 出力 {:?}",
            pass.satellite_name, pass.signal_type, input_raw, output_dir
        );

        if !input_raw.exists() {
            bail!("入力生IQファイルが存在しません: {:?}", input_raw);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        // satdump または gr-satellites がインストールされていれば実行
        if crate::health::check_command_exists("satdump") {
            let status = Command::new("satdump")
                .arg("live")
                .arg(&pass.satellite_name)
                .arg(input_raw)
                .arg(output_dir)
                .status()
                .await;
            if let Ok(st) = status {
                if st.success() {
                    if let Ok(entries) = std::fs::read_dir(output_dir) {
                        for entry in entries.flatten() {
                            let p = entry.path();
                            if let Some(ext) = p.extension().and_then(|e| e.to_str()) {
                                if ext == "png" || ext == "jpg" {
                                    return Ok(p);
                                }
                            }
                        }
                    }
                }
            }
        }

        // 外部デコーダ未導入または画像未生成時は生IQファイルのパスを返す (Graceful Degradation)
        Ok(input_raw.to_path_buf())
    }

    /// ISS SSTV (音声WAVから画像復調)
    pub async fn decode_iss_sstv(
        input_wav: &Path,
        output_dir: &Path,
    ) -> Result<std::path::PathBuf> {
        info!("ISS SSTV デコード開始: 入力 {:?} -> 出力 {:?}", input_wav, output_dir);

        if !input_wav.exists() {
            bail!("入力WAVファイルが存在しません: {:?}", input_wav);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        let out_png = output_dir.join("iss_sstv.png");
        if crate::health::check_command_exists("satdump") {
            let _ = Command::new("satdump")
                .args(&["iss_sstv", "audio", &input_wav.to_string_lossy(), &output_dir.to_string_lossy()])
                .status()
                .await;
            if out_png.exists() {
                return Ok(out_png);
            }
        }

        Ok(input_wav.to_path_buf())
    }
}

/// デコード結果 (画像パス、要約テキスト)
#[derive(Debug, Clone)]
pub struct DecodeResult {
    pub image_path: Option<PathBuf>,
    pub telemetry_summary: Option<String>,
}

/// プラグイン型デコードエンジン
pub struct DecoderEngine;

impl DecoderEngine {
    /// 衛星パスと生録音データから適切なデコーダをルーティング実行
    pub async fn decode(
        pass: &SatellitePass,
        raw_path: &Path,
        session_dir: &Path,
    ) -> Result<DecodeResult> {
        match pass.signal_type {
            SignalType::Apt => {
                let png_path = session_dir.join("image.png");
                match Decoder::decode_apt(raw_path, &png_path).await {
                    Ok(()) => Ok(DecodeResult {
                        image_path: Some(png_path),
                        telemetry_summary: Some("NOAA APT 画像デコード成功".to_string()),
                    }),
                    Err(e) => {
                        log::warn!("NOAA APTデコード失敗 (生データ保存): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                        })
                    }
                }
            }
            SignalType::Lrpt => {
                match Decoder::decode_meteor_lrpt(raw_path, session_dir).await {
                    Ok(img) => Ok(DecodeResult {
                        image_path: Some(img),
                        telemetry_summary: Some("Meteor-M LRPT デジタル画像復調成功".to_string()),
                    }),
                    Err(e) => {
                        log::warn!("Meteor LRPTデコード失敗 (生データ保存): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                        })
                    }
                }
            }
            SignalType::CubeSatSsdv | SignalType::CubeSatSstv | SignalType::CubeSatTelemetry | SignalType::MorseCw => {
                match Decoder::decode_cubesat(pass, raw_path, session_dir).await {
                    Ok(p) => {
                        let is_img = p.extension().map_or(false, |ext| ext == "png" || ext == "jpg");
                        Ok(DecodeResult {
                            image_path: if is_img { Some(p) } else { None },
                            telemetry_summary: Some(format!(
                                "CubeSat {} ({}) データ取得完了",
                                pass.satellite_name,
                                pass.signal_type.name()
                            )),
                        })
                    }
                    Err(e) => {
                        log::warn!("CubeSatデコード失敗 (生データ保存): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                        })
                    }
                }
            }
            SignalType::IssSstv => {
                match Decoder::decode_iss_sstv(raw_path, session_dir).await {
                    Ok(p) => {
                        let is_img = p.extension().map_or(false, |ext| ext == "png" || ext == "jpg");
                        Ok(DecodeResult {
                            image_path: if is_img { Some(p) } else { None },
                            telemetry_summary: Some("ISS SSTV 宇宙画像デコード完了".to_string()),
                        })
                    }
                    Err(e) => {
                        log::warn!("ISS SSTVデコード失敗 (生データ保存): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                        })
                    }
                }
            }
        }
    }
}
