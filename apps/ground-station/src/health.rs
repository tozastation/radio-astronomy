use crate::config::Config;
use anyhow::Result;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

// =============================================================================
// 🩺 起動前ヘルスチェックモジュール (Preflight Health Check)
// -----------------------------------------------------------------------------
// 【背景と設計方針】
// 地上局システムは24時間完全自律で動作するため、起動時にハードウェア(RTL-SDR)、
// 外部デコーダ(satdump, gr_satellites)、音声合成(VOICEVOX)、ストレージが正常であるかを
// 事前確認(Fail-Fast & Graceful Degradation)します。
// - 致命的(Error): SDRドングル未検出、保存先書き込み不可 -> 起動中止
// - 警告(Warn): VOICEVOX未起動、外部デコーダ未検出 -> 生データ保存を優先して運用継続
// =============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HealthStatus {
    Ok,
    Warn,
    Error,
}

#[derive(Debug, Clone)]
pub struct HealthCheckItem {
    pub name: String,
    pub status: HealthStatus,
    pub message: String,
    pub remedy: Option<String>,
}

#[derive(Debug, Clone)]
pub struct HealthReport {
    pub items: Vec<HealthCheckItem>,
}

impl HealthReport {
    /// 致命的なエラー(Error)が存在するか判定
    pub fn is_fatal(&self) -> bool {
        self.items.iter().any(|item| item.status == HealthStatus::Error)
    }

    /// ヘルスチェック結果をコンソールに整形出力
    pub fn print_table(&self) {
        println!("============================================================");
        println!("📡 Ground Station Preflight Health Check");
        println!("============================================================");
        for item in &self.items {
            let status_badge = match item.status {
                HealthStatus::Ok => "[ OK ]",
                HealthStatus::Warn => "[WARN]",
                HealthStatus::Error => "[FAIL]",
            };
            println!("{:<6} {:<24}: {}", status_badge, item.name, item.message);
            if let Some(remedy) = &item.remedy {
                println!("       -> 対処法: {}", remedy);
            }
        }
        println!("============================================================");
        if self.is_fatal() {
            println!("❌ 致命的なエラーが検出されました。起動を中断します。");
        } else if self.items.iter().any(|item| item.status == HealthStatus::Warn) {
            println!("⚠️ 警告がありますが、運用を継続可能です（生データ保存モード）。");
        } else {
            println!("✅ すべてのヘルスチェック項目をクリアしました！");
        }
        println!("============================================================");
    }
}

/// コマンドが PATH 上に存在するかチェック
pub fn check_command_exists(cmd: &str) -> bool {
    Command::new("which")
        .arg(cmd)
        .output()
        .map(|o| o.status.success() && !o.stdout.is_empty())
        .unwrap_or(false)
}

/// RTL-SDR ドングルの接続と認識状況をプローブ
pub async fn probe_rtl_sdr() -> HealthCheckItem {
    let name = "RTL-SDR Device".to_string();

    if !check_command_exists("rtl_test") {
        return HealthCheckItem {
            name,
            status: HealthStatus::Error,
            message: "rtl_test コマンドが見つかりません".to_string(),
            remedy: Some("sudo apt install rtl-sdr を実行してください".to_string()),
        };
    }

    // rtl_test -t を短時間実行してデバイス認識を確認
    // デバイスが存在する場合、rtl_testは終了せずベンチマークを継続するため、
    // 1秒待機してタイムアウトした場合は「正常にデバイスを占有・動作中」とみなす
    let mut cmd = tokio::process::Command::new("rtl_test");
    cmd.arg("-t")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    match cmd.spawn() {
        Ok(mut child) => {
            let wait_res = tokio::time::timeout(Duration::from_millis(1200), child.wait()).await;
            match wait_res {
                Ok(Ok(status)) => {
                    // すぐに終了した場合: デバイスが見つからずエラー終了した可能性が高い
                    if status.success() {
                        HealthCheckItem {
                            name,
                            status: HealthStatus::Ok,
                            message: "デバイス認識正常".to_string(),
                            remedy: None,
                        }
                    } else {
                        HealthCheckItem {
                            name,
                            status: HealthStatus::Error,
                            message: "RTL-SDR デバイスが未検出です".to_string(),
                            remedy: Some(
                                "USB接続を確認するか、WSL2の場合は管理者PowerShellで 'usbipd attach --wsl --busid <BUSID>' を実行してください".to_string(),
                            ),
                        }
                    }
                }
                Ok(Err(e)) => HealthCheckItem {
                    name,
                    status: HealthStatus::Error,
                    message: format!("rtl_test 実行エラー: {}", e),
                    remedy: Some("rtl-sdr のドライバ設定を確認してください".to_string()),
                },
                Err(_) => {
                    // タイムアウト: デバイスが正常にオープンされてテストが継続している
                    let _ = child.kill().await;
                    HealthCheckItem {
                        name,
                        status: HealthStatus::Ok,
                        message: "正常 (RTL-SDR デバイス検出・動作確認完了)".to_string(),
                        remedy: None,
                    }
                }
            }
        }
        Err(e) => HealthCheckItem {
            name,
            status: HealthStatus::Error,
            message: format!("rtl_test プロセス起動失敗: {}", e),
            remedy: Some("sudo apt install rtl-sdr を実行してください".to_string()),
        },
    }
}

/// 外部デコーダツールの存在確認
pub fn probe_external_tools(config: &Config) -> Vec<HealthCheckItem> {
    let mut items = Vec::new();

    // 1. rtl_fm (FM復調録音)
    if check_command_exists("rtl_fm") {
        items.push(HealthCheckItem {
            name: "rtl_fm (Receiver)".to_string(),
            status: HealthStatus::Ok,
            message: "利用可能".to_string(),
            remedy: None,
        });
    } else {
        items.push(HealthCheckItem {
            name: "rtl_fm (Receiver)".to_string(),
            status: HealthStatus::Error,
            message: "コマンド未検出".to_string(),
            remedy: Some("sudo apt install rtl-sdr を実行してください".to_string()),
        });
    }

    // 2. rtl_sdr (生IQベースバンド録音)
    if check_command_exists("rtl_sdr") {
        items.push(HealthCheckItem {
            name: "rtl_sdr (IQ Recorder)".to_string(),
            status: HealthStatus::Ok,
            message: "利用可能".to_string(),
            remedy: None,
        });
    } else {
        items.push(HealthCheckItem {
            name: "rtl_sdr (IQ Recorder)".to_string(),
            status: HealthStatus::Error,
            message: "コマンド未検出".to_string(),
            remedy: Some("sudo apt install rtl-sdr を実行してください".to_string()),
        });
    }

    // 3. satdump (Meteor / CubeSat LRPT/SSDV デコーダ)
    if config.satellites.is_meteor_enabled() || config.satellites.cubesats.enabled {
        if check_command_exists("satdump") {
            items.push(HealthCheckItem {
                name: "SatDump (Decoder)".to_string(),
                status: HealthStatus::Ok,
                message: "利用可能 (Meteor/CubeSat自動デコード)".to_string(),
                remedy: None,
            });
        } else {
            items.push(HealthCheckItem {
                name: "SatDump (Decoder)".to_string(),
                status: HealthStatus::Warn,
                message: "未検出 (生IQファイルのみ保存され、自動画像デコードはスキップされます)".to_string(),
                remedy: Some("SatDump をインストールしてください (公式リポジトリ: https://github.com/SatDump/SatDump)".to_string()),
            });
        }
    }

    // 4. gr-satellites (CubeSat 高度デコーダ)
    if config.satellites.cubesats.enabled {
        if check_command_exists("gr_satellites") {
            items.push(HealthCheckItem {
                name: "gr-satellites (CubeSat)".to_string(),
                status: HealthStatus::Ok,
                message: "利用可能".to_string(),
                remedy: None,
            });
        } else {
            items.push(HealthCheckItem {
                name: "gr-satellites (CubeSat)".to_string(),
                status: HealthStatus::Warn,
                message: "未検出 (GNU Radio サテライトデコーダ)".to_string(),
                remedy: Some("pip install gr-satellites を実行してください".to_string()),
            });
        }
    }

    items
}

/// VOICEVOX エンジンのヘルスチェック
pub async fn probe_voicevox(config: &Config) -> HealthCheckItem {
    let name = "VOICEVOX Engine".to_string();

    if !config.voicevox.enabled {
        return HealthCheckItem {
            name,
            status: HealthStatus::Ok,
            message: "無効化設定中 (ログ通知のみ)".to_string(),
            remedy: None,
        };
    }

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build();

    let client = match client {
        Ok(c) => c,
        Err(e) => {
            return HealthCheckItem {
                name,
                status: HealthStatus::Warn,
                message: format!("HTTPクライアント初期化失敗: {}", e),
                remedy: None,
            }
        }
    };

    let url = format!("{}/version", config.voicevox.host.trim_end_matches('/'));
    match client.get(&url).send().await {
        Ok(res) if res.status().is_success() => {
            let version = res.text().await.unwrap_or_else(|_| "不明".to_string());
            HealthCheckItem {
                name,
                status: HealthStatus::Ok,
                message: format!("正常稼働中 (version {})", version.trim()),
                remedy: None,
            }
        }
        _ => HealthCheckItem {
            name,
            status: HealthStatus::Warn,
            message: format!("接続不能 ({} に接続できません。音声通知はスキップされます)", config.voicevox.host),
            remedy: Some("docker compose up -d voicevox_engine を実行してください".to_string()),
        },
    }
}

/// ストレージ保存先の書き込みテスト
pub fn probe_storage(config: &Config) -> HealthCheckItem {
    let name = "Storage Access".to_string();
    let dir = Path::new(&config.storage.output_dir);

    if let Err(e) = std::fs::create_dir_all(dir) {
        return HealthCheckItem {
            name,
            status: HealthStatus::Error,
            message: format!("ディレクトリ作成失敗 ({}): {}", dir.display(), e),
            remedy: Some("ストレージパスのパーミッションを確認してください".to_string()),
        };
    }

    let test_file = dir.join(".preflight_test");
    match std::fs::write(&test_file, b"ground-station preflight health check") {
        Ok(_) => {
            let _ = std::fs::remove_file(&test_file);
            HealthCheckItem {
                name,
                status: HealthStatus::Ok,
                message: format!("書き込み・削除確認完了 ({})", dir.display()),
                remedy: None,
            }
        }
        Err(e) => HealthCheckItem {
            name,
            status: HealthStatus::Error,
            message: format!("書き込みテスト失敗 ({}): {}", test_file.display(), e),
            remedy: Some("保存先ディレクトリの書き込み権限を確認してください".to_string()),
        },
    }
}

/// 全項目の事前ヘルスチェックを一括実行
pub async fn run_preflight_checks(config: &Config) -> Result<HealthReport> {
    let mut items = Vec::new();

    // 1. RTL-SDR デバイス
    items.push(probe_rtl_sdr().await);

    // 2. 外部デコードツール群
    items.extend(probe_external_tools(config));

    // 3. VOICEVOX
    items.push(probe_voicevox(config).await);

    // 4. ストレージ
    items.push(probe_storage(config));

    Ok(HealthReport { items })
}
