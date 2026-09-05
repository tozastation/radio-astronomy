#!/usr/bin/env bash
# ==============================================================================
# 🛰️ Ground Station systemd サービス自動インストール・セットアップスクリプト
# ------------------------------------------------------------------------------
# 【機能概要】
# 1. ground-station の release バイナリをビルド (cargo build --release)
# 2. 実行ユーザーとリポジトリパスを自動検出し、systemd ユニットファイルを生成
# 3. /etc/systemd/system/ground-station.service に配置し、systemd を再読み込み
# 4. 有効化・起動コマンドと journald ログ確認コマンドの案内
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/../../.." && pwd))"
APPS_DIR="${REPO_ROOT}/apps/ground-station"
SERVICE_NAME="ground-station.service"
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
echo "🛰️  Ground Station systemd インストーラ"
echo "================================================================="
echo "・実行ユーザー: ${RUN_USER}:${RUN_GROUP} (UID: ${RUN_UID})"
echo "・リポジトリ  : ${REPO_ROOT}"
echo "・作業ディレクトリ: ${APPS_DIR}"
echo "================================================================="

# ------------------------------------------------------------------------------
# 1. Release バイナリのビルド確認
# ------------------------------------------------------------------------------
echo "🔨 release バイナリをビルドしています..."
if [ -n "${SUDO_USER:-}" ]; then
    # sudo 実行時は一般ユーザー権限でビルド (パーミッション汚染防止)
    su - "${RUN_USER}" -c "cd '${APPS_DIR}' && cargo build --release"
else
    cd "${APPS_DIR}" && cargo build --release
fi

BINARY_PATH="${REPO_ROOT}/target/release/ground-station"
if [ ! -f "${BINARY_PATH}" ]; then
    echo "❌ エラー: release バイナリの生成に失敗しました: ${BINARY_PATH}"
    exit 1
fi
echo "✅ release バイナリの確認完了: ${BINARY_PATH}"

# ------------------------------------------------------------------------------
# 2. ユニットファイルの動的生成
# ------------------------------------------------------------------------------
TMP_UNIT="$(mktemp)"
cat <<EOF > "${TMP_UNIT}"
[Unit]
Description=Ground Station Satellite Observation Daemon (Meteor-M, CubeSat, ISS)
Documentation=https://github.com/tozastation/radio-astronomy
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_ROOT}
ExecStart=${BINARY_PATH} --config apps/ground-station/config.toml daemon
Restart=always
RestartSec=10s
TimeoutStopSec=30s
KillMode=mixed
KillSignal=SIGTERM

# オーディオデバイス(/dev/snd/*)およびPulseAudio/PipeWireソケット接続設定
SupplementaryGroups=audio
Environment="XDG_RUNTIME_DIR=/run/user/${RUN_UID}"
Environment="PULSE_SERVER=unix:/run/user/${RUN_UID}/pulse/native"

# 環境変数の設定 (RUST_LOGでログレベル制御)
Environment="RUST_LOG=info"
# .local.env が存在する場合は環境変数（DISCORD_WEBHOOK_URL 等）を自動ロード
EnvironmentFile=-${APPS_DIR}/.local.env

# 標準出力と標準エラー出力を journald に集約
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ground-station

# ファイルディスクリプタ上限設定 (安定稼働用)
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# ------------------------------------------------------------------------------
# 3. systemd ディレクトリへ配置 & daemon-reload
# ------------------------------------------------------------------------------
echo "📋 systemd ユニットファイルを配置中: ${TARGET_SERVICE_PATH}"
sudo cp "${TMP_UNIT}" "${TARGET_SERVICE_PATH}"
sudo chmod 644 "${TARGET_SERVICE_PATH}"
rm -f "${TMP_UNIT}"

echo "🔄 systemd デーモンを再読み込み中 (systemctl daemon-reload)..."
sudo systemctl daemon-reload

echo ""
echo "================================================================="
echo "✨ インストールが正常に完了しました！"
echo "================================================================="
echo "以下のコマンドでサービスを管理できます："
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
