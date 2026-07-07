#!/usr/bin/env bash
# Rebuild custom wikis from wikis/ markdown: for each wiki, build its SQLite DB
# (build_wiki.py → data/<wiki>.db, for MCP/agents) AND its static site (Quartz →
# Cloudflare Pages, for browsing). Idempotent: a per-wiki git-tree-oid gate makes
# backstop runs cheap no-ops.
#
#   build_wikis.sh            # all wikis under wikis/
#   build_wikis.sh dev        # just one (the nomad dispatch path)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch --quiet origin main 2>/dev/null || true
git merge --ff-only origin/main --quiet 2>/dev/null || true   # content-only; fail-safe

wikis=("$@")
[ ${#wikis[@]} -eq 0 ] && wikis=($(ls "$ROOT/wikis" 2>/dev/null))

for w in "${wikis[@]}"; do
  [ -d "$ROOT/wikis/$w" ] || { echo "no wiki '$w' — skip"; continue; }
  tree=$(git rev-parse "HEAD:wikis/$w" 2>/dev/null || echo "")
  built=$(sqlite3 "file:$ROOT/data/$w.db?immutable=1" \
          "SELECT value FROM meta WHERE key='source_tree'" 2>/dev/null || echo "")
  if [ -n "$tree" ] && [ "$tree" = "$built" ]; then
    echo "$w: up to date ($tree) — skip"
    continue
  fi

  echo "$w: build DB → data/$w.db"
  uv run --project "$ROOT" python "$ROOT/scripts/build_wiki.py" \
    "$ROOT/wikis/$w" "$ROOT/data/$w.db.new" --wiki-name "$w" \
    --source-tree "$tree" --source-commit "$(git rev-parse HEAD 2>/dev/null || echo '')" --force
  rm -f "$ROOT/data/$w.db.new-wal" "$ROOT/data/$w.db.new-shm"
  mv -f "$ROOT/data/$w.db.new" "$ROOT/data/$w.db"

  echo "$w: build + deploy site"
  bash "$ROOT/scripts/build_site.sh" "$w" --deploy

  echo "$w: done ($tree)"
done
