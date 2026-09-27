#!/usr/bin/env bash
# ==============================================================================
# ☀️ Solar Station (太陽電波観測ステーション) systemd 自動インストーラスクリプト
# ------------------------------------------------------------------------------
# 【機能概要】
# 1. SDR競合プロセス (ground-station サービス / ultrafeeder Docker) の停止
# 2. solar-station の release バイナリをビルド (cargo build --release --bin solar-station)
# 3. 実行ユーザーとリポジトリパスを自動検出し、systemd ユニットファイルを生成
# 4. /etc/systemd/system/solar-station.service に配置し、systemd を再読み込み
# 5. 有効化・起動コマンドと journald ログ確認コマンドの案内
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/../../.." && pwd))"
APPS_DIR="${REPO_ROOT}/apps/ground-station"
SERVICE_NAME="solar-station.service"
TARGET_SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

# 実行ユーザーとグループの検出 (sudo 経由の場合は呼び出し元一般ユーザーを取得)
if [ -n "${SUDO_USER:-}" ]; then
    RUN_USER="${SUDO_USER}"
    RUN_GROUP="$(id -gn "${SUDO_USER}")"
else
    RUN_USER="$(id -un)"
    RUN_GROUP="$(id -gn)"
fi
RUN_UID="$(id -u "${RUN_USER}")"

echo "================================================================="
echo "☀️  Solar Station (太陽電波観測ステーション) systemd インストーラ"
echo "================================================================="
echo "・実行ユーザー: ${RUN_USER}:${RUN_GROUP} (UID: ${RUN_UID})"
echo "・リポジトリ  : ${REPO_ROOT}"
echo "・作業ディレクトリ: ${APPS_DIR}"
echo "================================================================="

# ------------------------------------------------------------------------------
# 1. 競合プロセスの停止・SDRデバイスの解放
# ------------------------------------------------------------------------------
echo "📡 SDR デバイスの競合プロセスをチェック・停止しています..."

# 既存の ground-station.service が稼働中なら停止
if systemctl is-active --quiet ground-station.service 2>/dev/null; then
    echo "⏸️  稼働中の ground-station.service を停止・無効化します..."
    sudo systemctl stop ground-station.service || true
    sudo systemctl disable ground-station.service || true
    echo "✅ ground-station.service を停止しました。"
fi

# Docker コンテナ ultrafeeder が稼働中なら停止
if command -v docker &>/dev/null; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^ultrafeeder$'; then
        echo "⏸️  稼働中の Docker コンテナ ultrafeeder を停止します..."
        docker stop ultrafeeder || true
        echo "✅ ultrafeeder を停止しました。"
    fi
fi

# ------------------------------------------------------------------------------
# 2. Release バイナリのビルド確認
# ------------------------------------------------------------------------------
echo "🔨 solar-station release バイナリをビルドしています..."
if [ -n "${SUDO_USER:-}" ]; then
    # sudo 実行時は一般ユーザー権限でビルド (パーミッション汚染防止)
    su - "${RUN_USER}" -c "cd '${REPO_ROOT}' && cargo build --release --bin solar-station"
else
    cd "${REPO_ROOT}" && cargo build --release --bin solar-station
fi

BINARY_PATH="${REPO_ROOT}/target/release/solar-station"
if [ ! -f "${BINARY_PATH}" ]; then
    echo "❌ エラー: release バイナリの生成に失敗しました: ${BINARY_PATH}"
    exit 1
fi
echo "✅ release バイナリの確認完了: ${BINARY_PATH}"

# ------------------------------------------------------------------------------
# 3. ユニットファイルの動的生成
# ------------------------------------------------------------------------------
TMP_UNIT="$(mktemp)"
cat <<EOF > "${TMP_UNIT}"
[Unit]
Description=Solar Radio Burst Station Daemon (70.0MHz VHF Solar Flare Watch)
Documentation=https://github.com/tozastation/radio-astronomy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_ROOT}
ExecStart=${BINARY_PATH} -c apps/ground-station/config.toml
Restart=always
RestartSec=10s
TimeoutStopSec=30s
KillMode=mixed
KillSignal=SIGTERM

# 環境変数の設定 (RUST_LOGでログレベル制御)
Environment="RUST_LOG=info"
# .local.env が存在する場合は環境変数（DISCORD_WEBHOOK_URL 等）を自動ロード
EnvironmentFile=-${APPS_DIR}/.local.env

# 標準出力と標準エラー出力を journald に集約
StandardOutput=journal
StandardError=journal
SyslogIdentifier=solar-station

# ファイルディスクリプタ上限設定 (安定稼働用)
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# ------------------------------------------------------------------------------
# 4. systemd ディレクトリへ配置 & daemon-reload
# ------------------------------------------------------------------------------
echo "📋 systemd ユニットファイルを配置中: ${TARGET_SERVICE_PATH}"
sudo cp "${TMP_UNIT}" "${TARGET_SERVICE_PATH}"
sudo chmod 644 "${TARGET_SERVICE_PATH}"
rm -f "${TMP_UNIT}"

echo "🔄 systemd デーモンを再読み込み中 (systemctl daemon-reload)..."
sudo systemctl daemon-reload

echo ""
echo "================================================================="
echo "✨ 太陽電波観測ステーションのセットアップが完了しました！"
echo "================================================================="
echo "以下のコマンドでサービスを管理・起動できます："
echo ""
echo "▶️  サービス有効化 & 即時起動:"
echo "   sudo systemctl enable --now ${SERVICE_NAME}"
echo ""
echo "📊 ステータス確認:"
echo "   sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "📜 リアルタイムログ確認 (journald):"
echo "   journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "⏹️  サービス停止:"
echo "   sudo systemctl stop ${SERVICE_NAME}"
echo ""
echo "🔄 サービス再起動:"
echo "   sudo systemctl restart ${SERVICE_NAME}"
echo "================================================================="
