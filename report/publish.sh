#!/usr/bin/env bash
# Publish one project's data to the dashboard repo (pipeline stage 7, CNY-ISS-7).
#
# Usage: report/publish.sh <project-id> <site-dir> [--dry-run]
#
# Writes only the files the internal side owns (data contract v1, folder ownership):
#   projects.json
#   projects/<id>/latest.json
#   projects/<id>/history.json
#   projects/<id>/index.html     copy of the dashboard's own project-template.html
# Any other change in the dashboard checkout aborts the publish.
#
# Order: fetch -> rebase onto origin -> copy -> allowlist + schema check -> commit
#        "data(<id>): <short sha>" -> push; a rejected push is rebased and retried once.
#
# Env:
#   GH_TOKEN          Jenkins credential "github-token" (fine-grained PAT,
#                     dasu-archive/dashboard, Contents read/write). Not needed for --dry-run.
#   DASHBOARD_REPO    default dasu-archive/dashboard
#   DASHBOARD_BRANCH  default main
#   CNY_PUBLISH_DIR   dashboard checkout to reuse; default /var/jenkins_home/code-nyang-e/dashboard

set -euo pipefail

if [ $# -lt 2 ] || [ $# -gt 3 ] || { [ $# -eq 3 ] && [ "$3" != "--dry-run" ]; }; then
  echo "usage: $0 <project-id> <site-dir> [--dry-run]" >&2
  exit 2
fi

here="$(cd "$(dirname "$0")" && pwd)"
id="$1"
site="$(cd "$2" && pwd)"
dry_run="${3:-}"

case "$id" in
  *[!a-z0-9-]*|"") echo "bad project id: $id" >&2; exit 2 ;;
esac

repo="${DASHBOARD_REPO:-dasu-archive/dashboard}"
branch="${DASHBOARD_BRANCH:-main}"
work="${CNY_PUBLISH_DIR:-/var/jenkins_home/code-nyang-e/dashboard}"
url="https://github.com/${repo}.git"

for f in projects.json "projects/$id/latest.json" "projects/$id/history.json"; do
  [ -f "$site/$f" ] || { echo "missing $site/$f" >&2; exit 1; }
done

# The token is handed to git only through this helper: never in the URL, never echoed.
auth=(-c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=${GH_TOKEN}"; }; f')

git_() { git -C "$work" "$@"; }

# 1. Up-to-date checkout of the dashboard.
if [ -d "$work/.git" ]; then
  git_ remote set-url origin "$url"
  git_ fetch -q origin "$branch"
  git_ checkout -q -B "$branch" "origin/$branch"
  git_ reset -q --hard "origin/$branch"
  git_ clean -qfd
else
  mkdir -p "$(dirname "$work")"
  git clone -q --branch "$branch" "$url" "$work"
fi

# 2. Copy only our files.
install -D -m 0644 "$site/projects.json" "$work/projects.json"
for f in latest.json history.json; do
  install -D -m 0644 "$site/projects/$id/$f" "$work/projects/$id/$f"
done
if [ -f "$work/project-template.html" ]; then
  install -D -m 0644 "$work/project-template.html" "$work/projects/$id/index.html"
else
  echo "warning: dashboard has no project-template.html yet; projects/$id/index.html not written" >&2
fi

# 3. Nothing outside our files may change.
allowed="^(projects\.json|projects/${id}/(latest|history)\.json|projects/${id}/index\.html)$"
changed="$(git_ status --porcelain --untracked-files=all | cut -c4-)"
if [ -z "$changed" ]; then
  echo "publish: nothing changed for $id; no commit"
  exit 0
fi
bad="$(printf '%s\n' "$changed" | grep -Ev "$allowed" || true)"
if [ -n "$bad" ]; then
  echo "publish: refusing, files outside the internal ownership changed:" >&2
  printf '  %s\n' $bad >&2
  exit 1
fi

# 4. Last check on exactly what will be pushed (the copied files plus everything they sit next to).
check="$(mktemp -d)"
trap 'rm -rf -- "$check"' EXIT
install -D -m 0644 "$work/projects.json" "$check/projects.json"
for d in "$work"/projects/*/; do
  [ -f "$d/latest.json" ] || continue
  p="$(basename "$d")"
  install -D -m 0644 "$d/latest.json" "$check/projects/$p/latest.json"
  [ -f "$d/history.json" ] && install -D -m 0644 "$d/history.json" "$check/projects/$p/history.json"
done
python3 "$here/allowlist.py" "$check"
python3 "$here/../schema/validate.py" "$check" > /dev/null

# 5. Commit.
commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["commit"])' "$site/projects/$id/latest.json")"
build="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["run"]; print(d["build"], d["at"])' "$site/projects/$id/latest.json")"
git_ add -- projects.json "projects/$id"
git_ -c user.name="code-nyang-e" -c user.email="code-nyang-e@noreply.invalid" \
  commit -q -m "data($id): ${commit:0:7}" -m "code-nyang-e build ${build}"

echo "publish: commit for $id"
git_ show --stat --format='  %h %s' HEAD | sed 's/^/  /'

if [ "$dry_run" = "--dry-run" ]; then
  echo "publish: dry run, not pushed"
  exit 0
fi

: "${GH_TOKEN:?GH_TOKEN is not set (Jenkins credential github-token)}"

# 6. Push; on rejection rebase once and retry.
if ! git_ "${auth[@]}" push -q origin "HEAD:$branch"; then
  echo "publish: push rejected, rebasing on origin/$branch and retrying once" >&2
  git_ "${auth[@]}" pull -q --rebase origin "$branch"
  git_ "${auth[@]}" push -q origin "HEAD:$branch"
fi
echo "publish: pushed $(git_ rev-parse HEAD) to $repo $branch"
