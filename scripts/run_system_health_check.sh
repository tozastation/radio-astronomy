#!/usr/bin/env bash
set -euo pipefail

# 定期システム健全性・メトリクス異常チェック & PR 自動作成スクリプト
# cron / systemd timer からの非対話実行を前提とする。

export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${LOG_DIR:-$HOME/.local/state/radio-astronomy-health}"
AGY_MODEL="${AGY_MODEL:-gemini-4-hydrogen}"
AGY_EFFORT="${AGY_EFFORT:-high}"
AGY_TIMEOUT="${AGY_TIMEOUT:-15m}"
DRY_RUN="${DRY_RUN:-false}"

mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_TAG="$(date +%Y%m%d)"
LOG_FILE="$LOG_DIR/health_${STAMP}.log"
HEALTH_FILE="$LOG_DIR/health_${STAMP}.md"
RESULT_JSON="$LOG_DIR/agy_result_${STAMP}.json"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date -Is)] Starting periodic system health check (model=${AGY_MODEL}, effort=${AGY_EFFORT})"

cd "$REPO_DIR"

# 1. 事前チェック: 作業ツリーがクリーンであること
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[error] Working tree is dirty. Aborting to protect local changes."
  exit 1
fi

# 2. 既にオープン中のシステム健全性 PR があれば重複作成を防ぐ
OPEN_PRS="$(gh pr list --state open --search "head:chore/system-health-" --json number --jq 'length' 2>/dev/null || echo "0")"
if [[ "$OPEN_PRS" -gt 0 ]]; then
  echo "[info] Open system-health PR already exists (${OPEN_PRS}). Skipping."
  exit 0
fi

# 3. 最新の main に同期
git fetch origin main
git checkout main
git pull --ff-only origin main

# 4. システムメトリクス・Kubernetes 状態の収集
python3 scripts/collect_system_health.py > "$HEALTH_FILE"
echo "[info] Health summary saved to $HEALTH_FILE"

# 5. 作業用ブランチを作成 (変更がなければ後で削除する)
BRANCH="chore/system-health-${DATE_TAG}"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  BRANCH="chore/system-health-${STAMP}"
fi
git checkout -b "$BRANCH"

cleanup_branch() {
  local exit_code=$?
  cd "$REPO_DIR"
  local current_branch
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" == "$BRANCH" ]]; then
    git reset --hard HEAD >/dev/null 2>&1 || true
    git clean -fd >/dev/null 2>&1 || true
    git checkout main >/dev/null 2>&1 || true
    git branch -D "$BRANCH" >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}

SCHEMA_JSON='{
  "type": "object",
  "properties": {
    "has_anomaly": { "type": "boolean" },
    "should_open_pr": { "type": "boolean" },
    "commit_message": { "type": "string" },
    "pr_title": { "type": "string" },
    "pr_body_markdown": { "type": "string" },
    "health_summary": { "type": "string" }
  },
  "required": ["has_anomaly", "should_open_pr", "commit_message", "pr_title", "pr_body_markdown", "health_summary"]
}'

PROMPT="$(cat <<EOF
あなたは radio-astronomy クラスタの SRE 監視レビュアーです。
以下は現在の Prometheus アラート・ターゲット・Kubernetes ノード・Pod・ホストリソースの収集結果です。

$(cat "$HEALTH_FILE")

この収集結果とリポジトリ内のマニフェストおよびコード（infrastructure/, apps/ 等）を調査し、以下を実行してください。

1. 異常や設定不備（例: Pod の NotReady / 異常終了、ディスク逼迫、Prometheus アラート、スクレイプ失敗など）があるか判定してください。なお、過去 38 日間の累積 restartCount のみや通常の Watchdog アラートは緊急異常とみなさないでください。
2. リポジトリ内の Kubernetes マニフェスト、Prometheus 設定、リソース制限（CPU/Memory limits）、アプリケーションコードの変更によって解消できる明確な問題がある場合のみ、該当ファイルを修正し、関連テストや設定検証を実行してください。
3. 異常がない場合、またはコード/マニフェストの変更では解消できない一過性・ハードウェア起因の事象である場合は、ファイルを一切編集せず should_open_pr を false にしてください。
4. commit_message は日本語・絵文字禁止・Conventional Commits 形式（例: fix: ..., chore: ...）で簡潔に記述してください。
5. pr_body_markdown には、検知したメトリクス/アラートの根拠、修正内容、検証結果を Markdown でまとめてください。
EOF
)"

# 6. agy ヘッドレス実行
if ! agy \
  --model "$AGY_MODEL" \
  --effort "$AGY_EFFORT" \
  --dangerously-skip-permissions \
  --print-timeout "$AGY_TIMEOUT" \
  --output-format json \
  --json-schema "$SCHEMA_JSON" \
  -p "$PROMPT" > "$RESULT_JSON"; then
  echo "[error] agy execution failed."
  cleanup_branch
fi

# 7. 出力 JSON の解析
PARSED="$(python3 - "$RESULT_JSON" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    raw = json.load(f)
payload = raw
for key in ("structured_output", "result", "response"):
    if isinstance(raw, dict) and key in raw:
        val = raw[key]
        if isinstance(val, str):
            try:
                payload = json.loads(val)
                break
            except json.JSONDecodeError:
                pass
        elif isinstance(val, dict):
            payload = val
            break
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)"

HAS_ANOMALY="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("true" if d.get("has_anomaly") else "false")' "$PARSED")"
SHOULD_PR="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("true" if d.get("should_open_pr") else "false")' "$PARSED")"
HEALTH_SUMMARY="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("health_summary",""))' "$PARSED")"

echo "[info] Health summary: $HEALTH_SUMMARY"
echo "[info] has_anomaly=${HAS_ANOMALY}, should_open_pr=${SHOULD_PR}"

if [[ "$SHOULD_PR" != "true" ]] || [[ -z "$(git status --porcelain)" ]]; then
  echo "[info] No PR changes proposed. Cleaning up temporary branch."
  cleanup_branch
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[info] DRY_RUN=true; diff preview:"
  git diff
  cleanup_branch
fi

COMMIT_MSG="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("commit_message","fix: システムメトリクス異常に基づく設定修正"))' "$PARSED")"
PR_TITLE="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("pr_title","fix: システム健全性チェックによる設定修正"))' "$PARSED")"
PR_BODY="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("pr_body_markdown",""))' "$PARSED")"

git add -A
git commit -m "$COMMIT_MSG"
git push -u origin "$BRANCH"
gh pr create --base main --head "$BRANCH" --title "$PR_TITLE" --body "$PR_BODY"

git checkout main
echo "[$(date -Is)] Completed periodic system health check and opened PR."
