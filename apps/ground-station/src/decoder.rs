use crate::orbit::{SatellitePass, SignalType};
use anyhow::{bail, Context, Result};
use log::{info, warn};
use std::path::{Path, PathBuf};
use tokio::process::Command;

// =============================================================================
// 🖼️ 画像・テレメトリデコードモジュール (Decoder)
// -----------------------------------------------------------------------------
// 【背景と処理内容】
// 衛星から受信した電波（生IQデータ または 音声WAV）を各衛星の通信方式に合わせて復調し、
// 画像（PNG/JPG）やテレメトリ（JSON/パケット）、交信音声（WAV）を生成します。
// 各種外部デコーダ（noaa-apt, satdump）の有無を検知し、未導入時や電波微弱時も
// 誤った「復調成功」を表示せず、真実の受信ステータスを返します。
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

/// 衛星名から SatDump の対応パイプライン名を判定
pub fn satdump_pipeline_for_satellite(sat_name: &str) -> Option<&'static str> {
    let s = sat_name.to_lowercase();
    if s.contains("umka") || s.contains("rs40") || s.contains("rs-40") {
        Some("umka_1_dump")
    } else if s.contains("funcube") || s.contains("ao-73") || s.contains("ao73") {
        Some("funcube_1")
    } else if s.contains("sonate") {
        Some("sonate_2")
    } else if s.contains("cas-4a") || s.contains("cas_4a") {
        Some("cas_4a")
    } else if s.contains("meteor") {
        Some("meteor_m2-x_lrpt_80k")
    } else if s.contains("iss") {
        Some("iss_sstv")
    } else {
        None
    }
}

/// SatDump CLI 呼び出し用引数を構築 (CubeSat用: baseband または audio モード自動判定)
pub fn build_satdump_cubesat_args(
    pipeline: &str,
    input_file: &Path,
    output_dir: &Path,
    samplerate: u32,
) -> Vec<String> {
    let ext = input_file.extension().and_then(|e| e.to_str()).unwrap_or("");
    if ext.eq_ignore_ascii_case("wav") {
        vec![
            pipeline.to_string(),
            "audio".to_string(),
            input_file.to_string_lossy().to_string(),
            output_dir.to_string_lossy().to_string(),
        ]
    } else {
        vec![
            pipeline.to_string(),
            "baseband".to_string(),
            input_file.to_string_lossy().to_string(),
            output_dir.to_string_lossy().to_string(),
            "--samplerate".to_string(),
            samplerate.to_string(),
            "--baseband_format".to_string(),
            "cu8".to_string(),
        ]
    }
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
                        if best.as_ref().is_none_or(|(_, max_len)| len > *max_len) {
                            *best = Some((path, len));
                        }
                    }
                }
            }
        }
    }
}

/// 指定ディレクトリから telemetry.json を探索・解析し、キーと値のペアを抽出
pub fn extract_telemetry_from_dir(dir: &Path) -> Option<Vec<(String, String)>> {
    let tlm_path = dir.join("telemetry.json");
    if tlm_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&tlm_path) {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                let mut items = Vec::new();
                flatten_json_value("", &val, &mut items);
                if !items.is_empty() {
                    if items.len() > 10 {
                        items.truncate(10);
                    }
                    return Some(items);
                }
            }
        }
    }
    None
}

fn flatten_json_value(prefix: &str, val: &serde_json::Value, out: &mut Vec<(String, String)>) {
    match val {
        serde_json::Value::Object(map) => {
            for (k, v) in map {
                let key = if prefix.is_empty() {
                    k.clone()
                } else {
                    format!("{}.{}", prefix, k)
                };
                flatten_json_value(&key, v, out);
                if out.len() >= 10 {
                    return;
                }
            }
        }
        serde_json::Value::String(s) => {
            out.push((prefix.to_string(), s.clone()));
        }
        serde_json::Value::Number(n) => {
            out.push((prefix.to_string(), n.to_string()));
        }
        serde_json::Value::Bool(b) => {
            out.push((prefix.to_string(), b.to_string()));
        }
        serde_json::Value::Array(arr) => {
            out.push((prefix.to_string(), format!("[{} elements]", arr.len())));
        }
        serde_json::Value::Null => {
            out.push((prefix.to_string(), "null".to_string()));
        }
    }
}

/// 指定ディレクトリに .cadu ファイルが存在するか判定
pub fn has_cadu_files(dir: &Path) -> bool {
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            if let Some(ext) = entry.path().extension().and_then(|e| e.to_str()) {
                if ext == "cadu" {
                    return true;
                }
            }
        }
    }
    false
}

/// 標準エラー／標準出力から末尾の有用なエラー行を抽出
pub fn extract_error_snippet(stderr: &str, stdout: &str) -> String {
    let text = if !stderr.trim().is_empty() { stderr } else { stdout };
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.is_empty() {
        "詳細ログなし".to_string()
    } else {
        lines.iter().rev().take(3).rev().cloned().collect::<Vec<_>>().join("; ")
    }
}

/// CubeSat のデコード結果状態
#[derive(Debug, Clone)]
pub enum CubeSatDecodeOutcome {
    Image(PathBuf),
    Telemetry(Vec<(String, String)>),
    PacketsSaved,
    WeakSignal,
    DecoderNotInstalled,
    NoPipelineConfigured,
    Error(String),
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

        if let Some(parent) = output_png.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("ディレクトリ作成失敗: {:?}", parent))?;
        }

        let args = build_noaa_apt_args(input_wav, output_png);

        let output = Command::new("noaa-apt")
            .args(&args)
            .output()
            .await
            .context("noaa-apt コマンドの実行に失敗しました。noaa-apt CLI がインストールされているか確認してください")?;

        if !output.status.success() {
            let snippet = extract_error_snippet(
                &String::from_utf8_lossy(&output.stderr),
                &String::from_utf8_lossy(&output.stdout),
            );
            bail!("noaa-apt が異常終了しました (status {}): {}", output.status, snippet);
        }

        if !output_png.exists() {
            bail!("デコード画像が生成されませんでした: {:?}", output_png);
        }

        info!("NOAA APT デコード成功: {:?}", output_png);
        Ok(())
    }

    /// 録音された生IQデータから Meteor-M 気象衛星画像（LRPT デジタルQPSK/OQPSK）をデコード
    pub async fn decode_meteor_lrpt(input_raw: &Path, output_dir: &Path) -> Result<Option<std::path::PathBuf>> {
        info!(
            "Meteor-M LRPT デコード開始: 入力 {:?} -> 出力ディレクトリ {:?}",
            input_raw, output_dir
        );

        if !input_raw.exists() {
            bail!("入力生IQファイルが存在しません: {:?}", input_raw);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        let pipelines = ["meteor_m2-x_lrpt_80k", "meteor_m2-x_lrpt"];
        let mut last_error = None;
        let mut executed_pipeline = false;

        for pipeline in pipelines {
            info!("SatDump パイプライン実行試行: {}", pipeline);
            let args = build_satdump_lrpt_args_with_pipeline(pipeline, input_raw, output_dir);

            let output = Command::new("satdump")
                .args(&args)
                .output()
                .await
                .context("satdump コマンドの実行に失敗しました。satdump CLI が導入されているか確認してください")?;

            executed_pipeline = true;
            let stdout_str = String::from_utf8_lossy(&output.stdout);
            let stderr_str = String::from_utf8_lossy(&output.stderr);

            if output.status.success() {
                if let Some(image_path) = find_best_image_in_dir(output_dir) {
                    info!("Meteor-M デコード成功 (パイプライン: {}): {:?}", pipeline, image_path);
                    return Ok(Some(image_path));
                }
            } else {
                let is_low_snr = stdout_str.contains("Lines  : 0")
                    || stderr_str.contains("Lines  : 0")
                    || stdout_str.contains("Skipping")
                    || stderr_str.contains("Skipping")
                    || output_dir.join("telemetry.json").exists()
                    || has_cadu_files(output_dir);

                if is_low_snr {
                    info!(
                        "SatDump パイプライン {} 完了 (有効走査線 0 行 / 信号微弱): status {}",
                        pipeline, output.status
                    );
                } else {
                    let snippet = extract_error_snippet(&stderr_str, &stdout_str);
                    warn!(
                        "SatDump パイプライン {} 終了 (非ゼロ終了コード {}): {}",
                        pipeline, output.status, snippet
                    );
                    last_error = Some(format!("status {}: {}", output.status, snippet));
                }
            }
        }

        if let Some(image_path) = find_best_image_in_dir(output_dir) {
            info!("Meteor-M デコード画像確認: {:?}", image_path);
            Ok(Some(image_path))
        } else if output_dir.join("telemetry.json").exists() || has_cadu_files(output_dir) || executed_pipeline {
            info!("Meteor-M デコード完了: 有効走査線なし (生IQおよびCADUパケット保全)");
            Ok(None)
        } else if let Some(err) = last_error {
            bail!("satdump が異常終了しました: {}", err);
        } else {
            Ok(None)
        }
    }

    /// キューブサット生IQ / 音声信号のデコード (SatDump)
    pub async fn decode_cubesat(
        pass: &crate::orbit::SatellitePass,
        input_file: &Path,
        output_dir: &Path,
    ) -> Result<CubeSatDecodeOutcome> {
        info!(
            "CubeSat デコード開始: 衛星 {}, 方式 {:?}, 入力 {:?} -> 出力 {:?}",
            pass.satellite_name, pass.signal_type, input_file, output_dir
        );

        if !input_file.exists() {
            bail!("入力生データファイルが存在しません: {:?}", input_file);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        // 1. 外部デコーダ satdump の存在確認
        if !crate::health::check_command_exists("satdump") {
            info!("satdump 未導入のため CubeSat 生データを保全: {:?}", input_file);
            return Ok(CubeSatDecodeOutcome::DecoderNotInstalled);
        }

        // 2. パイプラインの有無確認
        let pipeline = match satdump_pipeline_for_satellite(&pass.satellite_name) {
            Some(p) => p,
            None => {
                info!("衛星 {} 用の SatDump パイプライン未定義のため生データを保全", pass.satellite_name);
                return Ok(CubeSatDecodeOutcome::NoPipelineConfigured);
            }
        };

        // 3. SatDump 実行
        let args = build_satdump_cubesat_args(pipeline, input_file, output_dir, 240000);
        info!("SatDump 実行: satdump {}", args.join(" "));

        let output = Command::new("satdump")
            .args(&args)
            .output()
            .await
            .context("satdump コマンドの実行に失敗しました")?;

        // 4. 生成成果物の確認
        if let Some(img) = find_best_image_in_dir(output_dir) {
            info!("CubeSat デコード画像検出: {:?}", img);
            return Ok(CubeSatDecodeOutcome::Image(img));
        }

        if let Some(tlm) = extract_telemetry_from_dir(output_dir) {
            info!("CubeSat テレメトリ JSON 検出 ({} 項目)", tlm.len());
            return Ok(CubeSatDecodeOutcome::Telemetry(tlm));
        }

        if has_cadu_files(output_dir) {
            info!("CubeSat CADU パケット検出");
            return Ok(CubeSatDecodeOutcome::PacketsSaved);
        }

        let stdout_str = String::from_utf8_lossy(&output.stdout);
        let stderr_str = String::from_utf8_lossy(&output.stderr);
        let is_weak_signal = stdout_str.contains("0 packets")
            || stdout_str.contains("Lines  : 0")
            || stdout_str.contains("Skipping")
            || stderr_str.contains("Skipping")
            || output.status.success();

        if is_weak_signal {
            info!("CubeSat デコード完了: パケット未検出 (電波微弱または未送信)");
            Ok(CubeSatDecodeOutcome::WeakSignal)
        } else {
            let err_snippet = extract_error_snippet(&stderr_str, &stdout_str);
            warn!("CubeSat SatDump 異常終了 (status {}): {}", output.status, err_snippet);
            Ok(CubeSatDecodeOutcome::Error(err_snippet))
        }
    }

    /// ISS SSTV (音声WAVから画像復調)
    pub async fn decode_iss_sstv(
        input_wav: &Path,
        output_dir: &Path,
    ) -> Result<Option<PathBuf>> {
        info!("ISS SSTV デコード開始: 入力 {:?} -> 出力 {:?}", input_wav, output_dir);

        if !input_wav.exists() {
            bail!("入力WAVファイルが存在しません: {:?}", input_wav);
        }

        std::fs::create_dir_all(output_dir)
            .with_context(|| format!("出力ディレクトリ作成失敗: {:?}", output_dir))?;

        if !crate::health::check_command_exists("satdump") {
            info!("satdump 未導入のため ISS SSTV 音声WAVを保全: {:?}", input_wav);
            return Ok(None);
        }

        let output = Command::new("satdump")
            .args(["iss_sstv", "audio", &input_wav.to_string_lossy(), &output_dir.to_string_lossy()])
            .output()
            .await;

        if let Ok(out) = output {
            if !out.status.success() {
                let err = extract_error_snippet(
                    &String::from_utf8_lossy(&out.stderr),
                    &String::from_utf8_lossy(&out.stdout),
                );
                warn!("ISS SSTV SatDump 終了 (status {}): {}", out.status, err);
            }
        }

        Ok(find_best_image_in_dir(output_dir))
    }
}

use crate::discord::{PassStatus, SatelliteTelemetry};

/// デコード結果 (画像パス、音声パス、要約テキスト、テレメトリ情報)
#[derive(Debug, Clone)]
pub struct DecodeResult {
    pub image_path: Option<PathBuf>,
    pub audio_path: Option<PathBuf>,
    pub telemetry_summary: Option<String>,
    pub telemetry: Option<SatelliteTelemetry>,
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
                let audio_path = if raw_path.exists() && raw_path.extension().is_some_and(|e| e == "wav") {
                    Some(raw_path.to_path_buf())
                } else {
                    None
                };

                // noaa-apt CLI の存在確認
                if !crate::health::check_command_exists("noaa-apt") {
                    info!("noaa-apt 未導入のためデコードをスキップし生WAVを保全: {:?}", raw_path);
                    return Ok(DecodeResult {
                        image_path: None,
                        audio_path,
                        telemetry_summary: Some(format!(
                            "{} APT 音声WAV保全完了 (noaa-apt未導入)",
                            pass.satellite_name
                        )),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("WAV 音声ファイル保存完了".to_string()),
                            housekeeping: vec![
                                ("生データ保存".to_string(), "保全完了 (WAV 60kSPS)".to_string()),
                                ("デコード状況".to_string(), "noaa-apt CLI 未導入 (手動解析待機)".to_string()),
                                ("保存ファイル".to_string(), raw_path.file_name().unwrap_or_default().to_string_lossy().to_string()),
                            ],
                            status: PassStatus::RawPreserved,
                        }),
                    });
                }

                match Decoder::decode_apt(raw_path, &png_path).await {
                    Ok(()) => Ok(DecodeResult {
                        image_path: Some(png_path.clone()),
                        audio_path,
                        telemetry_summary: Some(format!("{} APT 画像デコード成功", pass.satellite_name)),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("APT スキャン同期完了 (Ch A/B 可視光・赤外線)".to_string()),
                            housekeeping: vec![
                                ("復調方式".to_string(), "AM 2.4kHz Subcarrier (WAV 60kSPS)".to_string()),
                                ("チャンネル".to_string(), "Ch A (可視光) / Ch B (赤外線)".to_string()),
                                ("生成画像".to_string(), png_path.file_name().unwrap_or_default().to_string_lossy().to_string()),
                            ],
                            status: PassStatus::ImageDecoded,
                        }),
                    }),
                    Err(e) => {
                        warn!("NOAA APT デコード失敗 (生WAV保全): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("生データ保存済み (デコード未完: {})", e)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("画像未生成 (電波微弱または同期未確立)".to_string()),
                                housekeeping: vec![
                                    ("生データ保存".to_string(), "保全完了 (WAV 60kSPS)".to_string()),
                                    ("エラー詳細".to_string(), e.to_string()),
                                ],
                                status: PassStatus::WeakSignal,
                            }),
                        })
                    }
                }
            }
            SignalType::Lrpt => {
                // satdump CLI の存在確認
                if !crate::health::check_command_exists("satdump") {
                    info!("SatDump 未導入のためデコードをスキップし生IQを保全: {:?}", raw_path);
                    return Ok(DecodeResult {
                        image_path: None,
                        audio_path: None,
                        telemetry_summary: Some(format!(
                            "{} LRPT 生IQ保全完了 (SatDump未導入)",
                            pass.satellite_name
                        )),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("生IQ (240kSPS cu8) 保存完了".to_string()),
                            housekeeping: vec![
                                ("生データ保存".to_string(), "保全完了 (生IQ cu8 240kSPS)".to_string()),
                                ("デコード状況".to_string(), "SatDump CLI 未導入 (手動解析待機)".to_string()),
                                ("保存ファイル".to_string(), raw_path.file_name().unwrap_or_default().to_string_lossy().to_string()),
                            ],
                            status: PassStatus::RawPreserved,
                        }),
                    });
                }

                match Decoder::decode_meteor_lrpt(raw_path, session_dir).await {
                    Ok(Some(img)) => {
                        let mut hk = vec![
                            ("変調方式".to_string(), "72k/80k OQPSK".to_string()),
                            ("フレーム同期".to_string(), "CADU ロック完了".to_string()),
                            ("復調画像".to_string(), img.file_name().unwrap_or_default().to_string_lossy().to_string()),
                        ];
                        if let Some(items) = extract_telemetry_from_dir(session_dir) {
                            hk.extend(items);
                        }
                        Ok(DecodeResult {
                            image_path: Some(img),
                            audio_path: None,
                            telemetry_summary: Some(format!("{} LRPT デジタル画像復調成功", pass.satellite_name)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("MSU-MR デジタル走査線 復元完了".to_string()),
                                housekeeping: hk,
                                status: PassStatus::ImageDecoded,
                            }),
                        })
                    }
                    Ok(None) => {
                        if let Some(tlm_items) = extract_telemetry_from_dir(session_dir) {
                            let mut hk = vec![
                                ("変調方式".to_string(), "72k/80k OQPSK".to_string()),
                                ("テレメトリ".to_string(), "telemetry.json 抽出完了".to_string()),
                            ];
                            hk.extend(tlm_items);
                            Ok(DecodeResult {
                                image_path: None,
                                audio_path: None,
                                telemetry_summary: Some(format!("{} テレメトリパケット復元完了 (画像未生成)", pass.satellite_name)),
                                telemetry: Some(SatelliteTelemetry {
                                    snr_db: None,
                                    lines_or_packets: Some("テレメトリデータ抽出完了".to_string()),
                                    housekeeping: hk,
                                    status: PassStatus::TelemetryDecoded,
                                }),
                            })
                        } else if has_cadu_files(session_dir) {
                            Ok(DecodeResult {
                                image_path: None,
                                audio_path: None,
                                telemetry_summary: Some(format!("{} 有効走査線なし (CADUパケット保存完了)", pass.satellite_name)),
                                telemetry: Some(SatelliteTelemetry {
                                    snr_db: None,
                                    lines_or_packets: Some("0 lines (CADU部分取得 / 走査線未生成)".to_string()),
                                    housekeeping: vec![
                                        ("フレーム同期".to_string(), "不完全 (電波微弱)".to_string()),
                                        ("生データ保全".to_string(), "生IQおよびCADUパケット保存済み".to_string()),
                                    ],
                                    status: PassStatus::WeakSignal,
                                }),
                            })
                        } else {
                            Ok(DecodeResult {
                                image_path: None,
                                audio_path: None,
                                telemetry_summary: Some(format!("{} 有効走査線なし (電波微弱または未送信)", pass.satellite_name)),
                                telemetry: Some(SatelliteTelemetry {
                                    snr_db: None,
                                    lines_or_packets: Some("0 lines (電波微弱)".to_string()),
                                    housekeeping: vec![
                                        ("復調状況".to_string(), "有効走査線 0 行 (同期未確立)".to_string()),
                                        ("生データ保全".to_string(), "生IQデータ保存済み".to_string()),
                                    ],
                                    status: PassStatus::WeakSignal,
                                }),
                            })
                        }
                    }
                    Err(e) => {
                        warn!("Meteor LRPT デコード失敗 (生データ保存): {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path: None,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: None,
                                housekeeping: vec![("エラー詳細".to_string(), e.to_string())],
                                status: PassStatus::DecodeError,
                            }),
                        })
                    }
                }
            }
            SignalType::CubeSatSsdv | SignalType::CubeSatSstv | SignalType::CubeSatTelemetry | SignalType::MorseCw => {
                let audio_path = if raw_path.exists() && raw_path.extension().is_some_and(|e| e == "wav") {
                    Some(raw_path.to_path_buf())
                } else {
                    None
                };

                match Decoder::decode_cubesat(pass, raw_path, session_dir).await {
                    Ok(CubeSatDecodeOutcome::Image(img)) => {
                        let mut hk = vec![
                            ("プロダクト".to_string(), "カメラ画像復元完了".to_string()),
                            ("画像形式".to_string(), img.file_name().unwrap_or_default().to_string_lossy().to_string()),
                        ];
                        if let Some(items) = extract_telemetry_from_dir(session_dir) {
                            hk.extend(items);
                        }
                        Ok(DecodeResult {
                            image_path: Some(img),
                            audio_path,
                            telemetry_summary: Some(format!("CubeSat {} 画像復元完了", pass.satellite_name)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("画像パケット復元完了".to_string()),
                                housekeeping: hk,
                                status: PassStatus::ImageDecoded,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::Telemetry(tlm_items)) => {
                        let pipeline = satdump_pipeline_for_satellite(&pass.satellite_name).unwrap_or("cubesat");
                        let mut hk = vec![("復調方式".to_string(), format!("SatDump [{}]", pipeline))];
                        hk.extend(tlm_items);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("CubeSat {} テレメトリ復調成功", pass.satellite_name)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("テレメトリパケット取得完了".to_string()),
                                housekeeping: hk,
                                status: PassStatus::TelemetryDecoded,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::PacketsSaved) => {
                        let pipeline = satdump_pipeline_for_satellite(&pass.satellite_name).unwrap_or("cubesat");
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("CubeSat {} パケットフレーム取得完了", pass.satellite_name)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("パケットフレーム保全完了".to_string()),
                                housekeeping: vec![
                                    ("復調方式".to_string(), format!("SatDump [{}]", pipeline)),
                                    ("パケット保全".to_string(), "CADU/フレーム保存完了".to_string()),
                                ],
                                status: PassStatus::TelemetryDecoded,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::WeakSignal) => {
                        let pipeline = satdump_pipeline_for_satellite(&pass.satellite_name).unwrap_or("cubesat");
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("CubeSat {} パケット未検出 (電波微弱または未送信)", pass.satellite_name)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("0 packets (同期未確立)".to_string()),
                                housekeeping: vec![
                                    ("復調方式".to_string(), format!("SatDump [{}]", pipeline)),
                                    ("パケット検出".to_string(), "0 パケット (信号微弱または未送信)".to_string()),
                                    ("生データ".to_string(), "保全完了 (生IQ保存)".to_string()),
                                ],
                                status: PassStatus::WeakSignal,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::DecoderNotInstalled) => {
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!(
                                "CubeSat {} ({}) 生データ保存完了 (SatDump未導入)",
                                pass.satellite_name,
                                pass.signal_type.name()
                            )),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("生録音データ保存完了 (外部デコーダ未導入)".to_string()),
                                housekeeping: vec![
                                    ("生データ".to_string(), "保全完了 (ディスク保存)".to_string()),
                                    ("デコード状況".to_string(), "SatDump CLI 未導入のためスキップ (手動解析可能)".to_string()),
                                    ("保存ファイル".to_string(), raw_path.file_name().unwrap_or_default().to_string_lossy().to_string()),
                                ],
                                status: PassStatus::RawPreserved,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::NoPipelineConfigured) => {
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!(
                                "CubeSat {} ({}) 生データ保存完了 (専用デコーダ未定義)",
                                pass.satellite_name,
                                pass.signal_type.name()
                            )),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: Some("生録音データ保存完了".to_string()),
                                housekeeping: vec![
                                    ("生データ".to_string(), "保全完了 (ディスク保存)".to_string()),
                                    ("デコード状況".to_string(), format!("{} 用パイプライン未設定 (生IQ保全)", pass.satellite_name)),
                                    ("保存ファイル".to_string(), raw_path.file_name().unwrap_or_default().to_string_lossy().to_string()),
                                ],
                                status: PassStatus::RawPreserved,
                            }),
                        })
                    }
                    Ok(CubeSatDecodeOutcome::Error(err)) => {
                        warn!("CubeSat デコードエラー (生データ保存): {}", err);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("CubeSat {} 生データ保存済み (デコードエラー: {})", pass.satellite_name, err)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: None,
                                housekeeping: vec![
                                    ("生データ".to_string(), "保全完了 (ディスク保存)".to_string()),
                                    ("エラー詳細".to_string(), err),
                                ],
                                status: PassStatus::DecodeError,
                            }),
                        })
                    }
                    Err(e) => {
                        warn!("CubeSat 処理エラー: {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("生データ保存済み (エラー: {})", e)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: None,
                                housekeeping: vec![("エラー詳細".to_string(), e.to_string())],
                                status: PassStatus::DecodeError,
                            }),
                        })
                    }
                }
            }
            SignalType::IssSstv => {
                let audio_path = if raw_path.exists() && raw_path.extension().is_some_and(|e| e == "wav") {
                    Some(raw_path.to_path_buf())
                } else {
                    None
                };

                if !crate::health::check_command_exists("satdump") {
                    info!("SatDump 未導入のためデコードをスキップし音声WAVを保全: {:?}", raw_path);
                    return Ok(DecodeResult {
                        image_path: None,
                        audio_path,
                        telemetry_summary: Some("ISS SSTV 音声録音完了 (SatDump未導入)".to_string()),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("FM音声WAV保全完了 (外部デコーダ未導入)".to_string()),
                            housekeeping: vec![
                                ("送信元".to_string(), "国際宇宙ステーション (ARISS)".to_string()),
                                ("録音状態".to_string(), "WAV保全完了 (Discord添付)".to_string()),
                                ("デコード状況".to_string(), "SatDump 未導入 (手動復調可能)".to_string()),
                            ],
                            status: PassStatus::RawPreserved,
                        }),
                    });
                }

                match Decoder::decode_iss_sstv(raw_path, session_dir).await {
                    Ok(Some(p)) => Ok(DecodeResult {
                        image_path: Some(p.clone()),
                        audio_path,
                        telemetry_summary: Some("ISS SSTV 宇宙画像デコード完了".to_string()),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("Robot36 カラースキャン同期完了".to_string()),
                            housekeeping: vec![
                                ("送信元".to_string(), "国際宇宙ステーション (ARISS)".to_string()),
                                ("復調モード".to_string(), "SSTV Robot36".to_string()),
                                ("生成画像".to_string(), p.file_name().unwrap_or_default().to_string_lossy().to_string()),
                            ],
                            status: PassStatus::ImageDecoded,
                        }),
                    }),
                    Ok(None) => Ok(DecodeResult {
                        image_path: None,
                        audio_path,
                        telemetry_summary: Some("ISS SSTV 音声録音完了 (画像未検出)".to_string()),
                        telemetry: Some(SatelliteTelemetry {
                            snr_db: None,
                            lines_or_packets: Some("FM音声録音完了 (画像信号なし/無音)".to_string()),
                            housekeeping: vec![
                                ("送信元".to_string(), "国際宇宙ステーション (ARISS)".to_string()),
                                ("録音状態".to_string(), "WAV保全完了 (画像信号未検出)".to_string()),
                            ],
                            status: PassStatus::RawPreserved,
                        }),
                    }),
                    Err(e) => {
                        warn!("ISS SSTV デコード失敗: {}", e);
                        Ok(DecodeResult {
                            image_path: None,
                            audio_path,
                            telemetry_summary: Some(format!("生データ保存済み (デコードエラー: {})", e)),
                            telemetry: Some(SatelliteTelemetry {
                                snr_db: None,
                                lines_or_packets: None,
                                housekeeping: vec![("エラー詳細".to_string(), e.to_string())],
                                status: PassStatus::DecodeError,
                            }),
                        })
                    }
                }
            }
            SignalType::FmRepeater => {
                let audio_path = if raw_path.exists() && raw_path.extension().is_some_and(|e| e == "wav") {
                    Some(raw_path.to_path_buf())
                } else {
                    None
                };

                let access_spec = if pass.satellite_name.contains("SO-50") {
                    "Uplink: 145.850MHz (CTCSS 67.0Hz) / Downlink: 436.795MHz FM".to_string()
                } else {
                    format!("Downlink: {:.4} MHz FM", pass.frequency_hz as f64 / 1_000_000.0)
                };

                let housekeeping = vec![
                    ("中継方式".to_string(), "FM ボイストランスポンダー".to_string()),
                    ("アクセス仕様".to_string(), access_spec),
                    ("音声データ".to_string(), "Discord添付 / ローカル保全完了".to_string()),
                ];

                Ok(DecodeResult {
                    image_path: None,
                    audio_path,
                    telemetry_summary: Some(format!(
                        "{} FM 音声中継（交信音声）録音完了",
                        pass.satellite_name
                    )),
                    telemetry: Some(SatelliteTelemetry {
                        snr_db: None,
                        lines_or_packets: Some("FM 音声復調完了 (WAV 添付)".to_string()),
                        housekeeping,
                        status: PassStatus::AudioRecorded,
                    }),
                })
            }
        }
    }
}

