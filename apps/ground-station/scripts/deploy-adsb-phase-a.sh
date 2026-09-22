#!/usr/bin/env bash
# ==============================================================================
# 案A: ADS-B 成功優先 — エッジ (SSH) へ設定同期 & 健全性チェック
# ------------------------------------------------------------------------------
# 使い方 (開発マシン / LAN 到達可能なシェルから):
#   ./apps/ground-station/scripts/deploy-adsb-phase-a.sh tozastation@192.168.68.66
#
# 前提:
#   - ローカルで config.toml が案A（衛星オフ / ADS-B オン）になっていること
#   - docker-compose.yaml に ultrafeeder (8090:80) が定義されていること
#   - SSH 鍵認証済みであること
# ==============================================================================

set -euo pipefail

REMOTE="${1:?使い方: $0 user@host}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_REPO="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
LOCAL_CONFIG="${LOCAL_REPO}/apps/ground-station/config.toml"
LOCAL_COMPOSE="${LOCAL_REPO}/docker-compose.yaml"
ADSB_HOST_PORT=18090  # 8080/8081=他アプリ, 8090=PocketBase のため回避

SSH_OPTS=(-F /dev/null -o BatchMode=yes -o ConnectTimeout=15 -o IdentitiesOnly=yes)
if [[ -f "${HOME}/.ssh/id_rsa" ]]; then
  SSH_OPTS+=(-i "${HOME}/.ssh/id_rsa")
elif [[ -f "${HOME}/.ssh/id_ed25519" ]]; then
  SSH_OPTS+=(-i "${HOME}/.ssh/id_ed25519")
fi

ssh_r() { ssh "${SSH_OPTS[@]}" "${REMOTE}" "$@"; }
scp_r() { scp "${SSH_OPTS[@]}" "$@"; }

echo "==> 1. SSH 接続確認: ${REMOTE}"
ssh_r 'echo "connected as $(whoami)@$(hostname)"'

echo "==> 2. リモート上の radio-astronomy を探索"
REMOTE_REPO="$(ssh_r 'find ~ -maxdepth 6 -type d -name radio-astronomy 2>/dev/null | head -1')"
if [[ -z "${REMOTE_REPO}" ]]; then
  echo "エラー: リモートに radio-astronomy ディレクトリが見つかりません" >&2
  exit 1
fi
echo "    REMOTE_REPO=${REMOTE_REPO}"

echo "==> 3. config.toml / docker-compose.yaml を同期"
scp_r "${LOCAL_CONFIG}" "${REMOTE}:${REMOTE_REPO}/apps/ground-station/config.toml"
scp_r "${LOCAL_COMPOSE}" "${REMOTE}:${REMOTE_REPO}/docker-compose.yaml"

echo "==> 4. ground-station を停止して SDR を解放 (存在すれば)"
ssh_r '
  if systemctl list-unit-files ground-station.service >/dev/null 2>&1; then
    sudo systemctl stop ground-station.service || true
    echo "ground-station: $(systemctl is-active ground-station.service 2>/dev/null || echo unknown)"
  else
    pkill -f "[g]round-station" 2>/dev/null || true
    echo "systemd ユニットなし。プロセス停止を試行済み"
  fi
  echo "--- SDR 関連プロセス ---"
  pgrep -af "rtl_|readsb|ultrafeeder|dump1090" || echo "(なし)"
'

echo "==> 5. RTL-SDR USB 認識"
ssh_r 'lsusb | grep -iE "Realtek|RTL|2838|SDR" || echo "WARNING: RTL-SDR が見つかりません (usbipd 要確認)"'

echo "==> 6. docker compose で ultrafeeder を起動 (ホスト :${ADSB_HOST_PORT})"
ssh_r "
  set -e
  cd '${REMOTE_REPO}'
  # 旧 docker run 製コンテナがポート不一致のまま残っている場合は compose 管理へ載せ替える
  if docker ps -a --format '{{.Names}}' | grep -qx ultrafeeder; then
    PORTS=\$(docker port ultrafeeder 80/tcp 2>/dev/null || true)
    if ! echo \"\${PORTS}\" | grep -q ':${ADSB_HOST_PORT}'; then
      echo \"旧 ultrafeeder (\${PORTS:-no-port}) を削除して compose 定義に合わせます\"
      docker compose stop ultrafeeder 2>/dev/null || true
      docker rm -f ultrafeeder 2>/dev/null || true
    fi
  fi
  docker compose up -d ultrafeeder
  docker compose ps ultrafeeder
"

echo "==> 7. aircraft.json 機影チェック (最大 30 秒待機, :${ADSB_HOST_PORT})"
ssh_r "
  ADSB_HOST_PORT=${ADSB_HOST_PORT}
  for i in \$(seq 1 15); do
    if curl -fsS --max-time 2 http://127.0.0.1:\${ADSB_HOST_PORT}/data/aircraft.json >/tmp/aircraft.json 2>/dev/null; then
      COUNT=\$(python3 -c 'import json; d=json.load(open(\"/tmp/aircraft.json\")); print(len(d.get(\"aircraft\",[])))' 2>/dev/null || echo '?')
      echo \"attempt \${i}: aircraft count = \${COUNT}\"
      if [[ \"\${COUNT}\" != '0' && \"\${COUNT}\" != '?' ]]; then
        python3 -c '
import json
d=json.load(open(\"/tmp/aircraft.json\"))
for a in d.get(\"aircraft\",[])[:5]:
    print(f\"  hex={a.get(\"hex\")} flight={str(a.get(\"flight\",\"?\")).strip()} alt={a.get(\"alt_baro\",\"?\")} lat={a.get(\"lat\",\"?\")} lon={a.get(\"lon\",\"?\")}\")
'
        echo 'SUCCESS: 機影を確認できました'
        exit 0
      fi
    else
      echo \"attempt \${i}: aircraft.json 未応答\"
    fi
    sleep 2
  done
  echo 'WARNING: まだ機影 0 件です。アンテナ(1090向け短縮・垂直)・USBパススルー・ログを確認してください'
  cd '${REMOTE_REPO}' && docker compose logs --tail 40 ultrafeeder || true
  exit 2
"

echo ""
echo "完了。手元ブラウザ確認:"
echo "  ssh -L ${ADSB_HOST_PORT}:localhost:${ADSB_HOST_PORT} ${REMOTE}"
echo "  → http://localhost:${ADSB_HOST_PORT}"
echo ""
echo "エッジ上での常用コマンド:"
echo "  cd ${REMOTE_REPO}"
echo "  docker compose up -d ultrafeeder   # ADS-B 起動"
echo "  docker compose up -d voicevox      # ずんだもん"
echo "  docker compose logs -f ultrafeeder"
echo ""
echo "その後 ground-station を ADS-B 監視付きで再起動する場合:"
echo "  ssh ${REMOTE} 'sudo systemctl start ground-station'"
