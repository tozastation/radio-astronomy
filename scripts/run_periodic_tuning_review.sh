#!/usr/bin/env bash
set -euo pipefail

# 定期チューニングレビュー & PR 自動作成スクリプト
# cron / systemd timer からの非対話実行を前提とする。

export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${LOG_DIR:-$HOME/.local/state/radio-astronomy-review}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-7}"
AGY_MODEL="${AGY_MODEL:-gemini-4-neon}"
AGY_EFFORT="${AGY_EFFORT:-medium}"
AGY_TIMEOUT="${AGY_TIMEOUT:-15m}"
DRY_RUN="${DRY_RUN:-false}"

mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
DATE_TAG="$(date +%Y%m%d)"
LOG_FILE="$LOG_DIR/review_${STAMP}.log"
SUMMARY_FILE="$LOG_DIR/summary_${STAMP}.md"
RESULT_JSON="$LOG_DIR/agy_result_${STAMP}.json"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date -Is)] Starting periodic tuning review (days=${LOOKBACK_DAYS}, model=${AGY_MODEL}, effort=${AGY_EFFORT})"

cd "$REPO_DIR"

# 1. 事前チェック: 作業ツリーがクリーンであること
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[error] Working tree is dirty. Aborting to protect local changes."
  exit 1
fi

# 2. 既にオープン中のチューニングレビュー PR があれば重複作成を防ぐ
OPEN_PRS="$(gh pr list --state open --search "head:chore/tuning-review-" --json number --jq 'length' 2>/dev/null || echo "0")"
if [[ "$OPEN_PRS" -gt 0 ]]; then
  echo "[info] Open tuning-review PR already exists (${OPEN_PRS}). Skipping."
  exit 0
fi

# 3. 最新の main に同期
git fetch origin main
git checkout main
git pull --ff-only origin main

# 4. 直近観測データの集計 (analytics が構築済みならセマンティック/オントロジー診断を使用)
ANALYTICS_VENV="$REPO_DIR/analytics/.venv"
if [[ -x "$ANALYTICS_VENV/bin/python" ]] && [[ -x "$ANALYTICS_VENV/bin/dbt" ]]; then
  echo "[info] Generating ontology/semantic review context via analytics/.venv"
  if PATH="$ANALYTICS_VENV/bin:$PATH" "$ANALYTICS_VENV/bin/python" "$REPO_DIR/analytics/scripts/sync_s3_to_duckdb.py" \
    && PATH="$ANALYTICS_VENV/bin:$PATH" "$ANALYTICS_VENV/bin/python" "$REPO_DIR/analytics/scripts/generate_review_context.py" --run-dbt --days "$LOOKBACK_DAYS" > "$SUMMARY_FILE"; then
    echo "[info] Ontology review summary saved to $SUMMARY_FILE"
  else
    echo "[warn] Analytics pipeline failed; falling back to summarize_recent_observations.py"
    python3 scripts/summarize_recent_observations.py --days "$LOOKBACK_DAYS" > "$SUMMARY_FILE"
  fi
else
  python3 scripts/summarize_recent_observations.py --days "$LOOKBACK_DAYS" > "$SUMMARY_FILE"
fi
echo "[info] Observation summary saved to $SUMMARY_FILE"

# 5. 作業用ブランチを作成 (変更がなければ後で削除する)
BRANCH="chore/tuning-review-${DATE_TAG}"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  BRANCH="chore/tuning-review-${STAMP}"
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
    "should_open_pr": { "type": "boolean" },
    "commit_message": { "type": "string" },
    "pr_title": { "type": "string" },
    "pr_body_markdown": { "type": "string" },
    "review_summary": { "type": "string" }
  },
  "required": ["should_open_pr", "commit_message", "pr_title", "pr_body_markdown", "review_summary"]
}'

PROMPT="$(cat <<EOF
あなたは radio-astronomy リポジトリの定期チューニングレビュアーです。
以下は直近 ${LOOKBACK_DAYS} 日間の Garage S3 実測データ集計結果です。

$(cat "$SUMMARY_FILE")

この集計結果と現在のリポジトリ内のコードおよび設定（apps/satellite-tracker-rs, apps/satellite-analyzer, infrastructure/apps 等）を調査し、以下を実行してください。

1. 受信成功率、デコード状況、天の川銀河 21cm ドリフトスキャンの SNR 傾向、未処理データ滞留などを確認し、コードや設定に明確な改善・チューニングの余地があるか判定してください。
2. 改善の根拠が明確にある場合のみ、関連するファイル（設定値、閾値、デコーダや DSP のパラメータ・ロジック等）を編集し、変更箇所に対応するテスト（pytest や cargo test 等）を実行してパスすることを確認してください。
3. 根拠が不十分な場合や現状維持が妥当な場合は、ファイルを一切編集せず should_open_pr を false にしてください。
4. commit_message は日本語・絵文字禁止・Conventional Commits 形式（例: fix: ..., feat: ..., perf: ...）で簡潔に記述してください。
5. pr_body_markdown には、可視化されたデータ根拠（実測メトリクスの Markdown 表、原因分類・前回 PR 前後比較表、必要に応じて Mermaid チャート `xychart-beta` 等、および対象期間・サンプル数）を必ず掲載し、変更内容と実行したテスト結果を Markdown でまとめてください。
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

# 7. 出力 JSON の解析 (agy --output-format json の応答構造に対応)
PARSED="$(python3 - "$RESULT_JSON" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    raw = json.load(f)
# agy json output may wrap the structured payload under "result" or "response" or return directly
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

SHOULD_PR="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("true" if d.get("should_open_pr") else "false")' "$PARSED")"
REVIEW_SUMMARY="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("review_summary",""))' "$PARSED")"

echo "[info] Review summary: $REVIEW_SUMMARY"
echo "[info] should_open_pr=${SHOULD_PR}"

if [[ "$SHOULD_PR" != "true" ]] || [[ -z "$(git status --porcelain)" ]]; then
  echo "[info] No changes to propose. Cleaning up temporary branch."
  cleanup_branch
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[info] DRY_RUN=true; diff preview:"
  git diff
  cleanup_branch
fi

COMMIT_MSG="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("commit_message","chore: 観測データに基づくパラメータチューニング"))' "$PARSED")"
PR_TITLE="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("pr_title","chore: 観測データに基づく定期チューニング提案"))' "$PARSED")"
PR_BODY="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("pr_body_markdown",""))' "$PARSED")"

git add -A
git commit -m "$COMMIT_MSG"
git push -u origin "$BRANCH"
gh pr create --base main --head "$BRANCH" --title "$PR_TITLE" --body "$PR_BODY"

git checkout main
echo "[$(date -Is)] Completed periodic tuning review and opened PR."
