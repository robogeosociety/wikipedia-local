#!/usr/bin/env bash
# Monthly refresh: pull the newest English Wikipedia nopic ZIM, rebuild wiki.db
# atomically, snapshot it to R2, and prune the old ZIM. Idempotent — exits early
# if wiki.db is already built from the latest available dump.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"
BASE="https://download.kiwix.org/zim/wikipedia"
mkdir -p "$DATA"; cd "$DATA"

echo "[$(date '+%F %T')] discovering latest nopic ZIM…"
latest=$(curl -fsSL "$BASE/" | grep -oE 'wikipedia_en_all_nopic_[0-9]{4}-[0-9]{2}\.zim' | sort -u | tail -1)
[ -n "$latest" ] || { echo "could not determine latest nopic ZIM"; exit 1; }
echo "latest available: $latest"

marker="$DATA/.built_zim"
if [ -f "$marker" ] && [ "$(cat "$marker")" = "$latest" ]; then
  echo "wiki.db already built from $latest — nothing to do."
  exit 0
fi

echo "downloading + verifying $latest…"
curl -fsSL -O "$BASE/$latest.sha256"
aria2c -c -x16 -s16 --file-allocation=none --console-log-level=warn "$BASE/$latest"
shasum -a 256 -c "$latest.sha256"

echo "rebuilding wiki.db (atomic swap)…"
uv run --project "$ROOT" python "$ROOT/scripts/extract.py" "$DATA/$latest" "$DATA/wiki.db.new" --force
rm -f "$DATA/wiki.db.new-wal" "$DATA/wiki.db.new-shm"
mv -f "$DATA/wiki.db.new" "$DATA/wiki.db"
echo "$latest" > "$marker"

# Article ids changed with the swap: rebuild the derived ranking sidecar and
# re-render the hot set (both disposable; skipped when no policy file exists).
if [ -f "$ROOT/weights.toml" ]; then
  echo "rebuilding weights sidecar + hot cache…"
  uv run --project "$ROOT" python "$ROOT/scripts/build_weights.py"
  uv run --project "$ROOT" python "$ROOT/scripts/warm_cache.py"
fi

echo "syncing to R2…"
"$ROOT/scripts/r2_sync.sh" "$DATA/wiki.db"

echo "pruning superseded ZIMs…"
find "$DATA" -maxdepth 1 -name 'wikipedia_en_all_nopic_*.zim' ! -name "$latest" -delete 2>/dev/null || true
find "$DATA" -maxdepth 1 -name 'wikipedia_en_all_nopic_*.zim.sha256' ! -name "$latest.sha256" -delete 2>/dev/null || true

echo "[$(date '+%F %T')] refresh complete: $latest"
