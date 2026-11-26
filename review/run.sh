#!/usr/bin/env bash
# Claude review of one target repo checkout (pipeline stage 4). Read-only on the target.
#
# Usage: review/run.sh <project-id> <target-dir> <out-dir>
#
# Env:
#   CLAUDE_CODE_OAUTH_TOKEN  required; Jenkins credential "claude-token" (Max subscription)
#   RUN_AT                   ISO 8601 +09:00 run time; defaults to now
#   CNY_LEDGER_DIR           where ledgers live; default /var/jenkins_home/code-nyang-e/ledger
#   CLAUDE_REVIEW_TIMEOUT    default 25m
#   CLAUDE_REVIEW_MAX_TURNS  default 60
#
# Output: <out-dir>/findings.json  {"reviewedCommit", "scope", "reviewedFiles", "findings": [...]}
#         plus raw.json / scope.txt for debugging (internal, never published).
# Exit code is non-zero when the review failed; the pipeline then publishes claude = null.
#
# Scope: the first run reviews every source file; later runs review only source files
# changed since the ledger's last reviewed commit (saves Max usage). Nothing changed:
# Claude is not called and the open findings are carried over.
#
# Never add --bare: it ignores CLAUDE_CODE_OAUTH_TOKEN.

set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <project-id> <target-dir> <out-dir>" >&2
  exit 2
fi

here="$(cd "$(dirname "$0")" && pwd)"
project_id="$1"
target="$(cd "$2" && pwd)"
mkdir -p "$3"
out="$(cd "$3" && pwd)"

case "$project_id" in
  *[!a-z0-9-]*|"") echo "bad project id: $project_id" >&2; exit 2 ;;
esac

: "${CLAUDE_CODE_OAUTH_TOKEN:?CLAUDE_CODE_OAUTH_TOKEN is not set (Jenkins credential claude-token)}"
# These outrank the subscription token if present.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

ledger_dir="${CNY_LEDGER_DIR:-/var/jenkins_home/code-nyang-e/ledger}"
ledger="$ledger_dir/$project_id.json"
run_at="${RUN_AT:-$(TZ=Asia/Seoul date +%Y-%m-%dT%H:%M:%S+09:00)}"

cd "$target"
head="$(git rev-parse HEAD)"

# Source files only. Git pathspec wildcards match across directories.
list_sources() { grep -E '\.(java|kt)$' || true; }

base="$(python3 "$here/ledger.py" last-commit "$ledger")"
if [ -n "$base" ] && git cat-file -e "${base}^{commit}" 2>/dev/null; then
  scope_kind=changed
  git diff --name-only --diff-filter=AMR "$base" "$head" -- 'src/main/*' | list_sources > "$out/scope.txt"
else
  scope_kind=full
  git ls-files -- 'src/main/*' | list_sources > "$out/scope.txt"
fi
if [ ! -s "$out/scope.txt" ]; then
  scope_kind=none
fi
echo "review scope: $scope_kind, $(wc -l < "$out/scope.txt") file(s), head $head, last reviewed ${base:-none}"

structured_arg=()
if [ "$scope_kind" != none ]; then
  prompt="$(cat "$here/prompt.md")

## 이번 검토 범위

$(sed 's/^/- /' "$out/scope.txt")"

  set +e
  timeout "${CLAUDE_REVIEW_TIMEOUT:-25m}" claude -p "$prompt" \
    --output-format json \
    --json-schema "$(jq -c 'del(."$schema")' "$here/findings.schema.json")" \
    --tools "Read,Grep,Glob" \
    --allowedTools "Read,Grep,Glob" \
    --permission-mode dontAsk \
    --setting-sources user \
    --settings '{"disableAllHooks": true}' \
    --no-session-persistence \
    --max-turns "${CLAUDE_REVIEW_MAX_TURNS:-60}" \
    > "$out/raw.json" 2> "$out/claude.stderr"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "claude -p failed (exit $rc)" >&2
    tail -n 20 "$out/claude.stderr" >&2 || true
    exit 1
  fi
  if ! jq -e '.structured_output' "$out/raw.json" > "$out/structured.json"; then
    echo "claude -p returned no structured_output" >&2
    jq -c '{subtype, is_error, num_turns}' "$out/raw.json" >&2 || true
    exit 1
  fi
  structured_arg=(--structured "$out/structured.json")
fi

python3 "$here/ledger.py" merge "$ledger" \
  --repo-dir "$target" \
  --scope "$out/scope.txt" \
  --scope-kind "$scope_kind" \
  --commit "$head" \
  --run-at "$run_at" \
  --out "$out/findings.json" \
  "${structured_arg[@]}"
