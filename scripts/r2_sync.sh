#!/usr/bin/env bash
# Sync wiki.db UP to R2 as an immutable, date-stamped CONTENT snapshot + `latest`.
#
# R2 is OUT of the read path (disaster-recovery / seeding only), so we do NOT ship the
# FTS index — it's derived data. We back up a content-only copy (articles + meta);
# r2_pull.sh rebuilds the FTS index locally on restore. That roughly halves the upload.
#
# wrangler `r2 object put` is single-shot (~300 MiB), so the content DB is zstd-compressed
# and split into ~290 MiB parts under snapshots/<DATE>/ with a manifest. The upload is:
#   • resumable — parts already sent are skipped (rerun the same DATE to continue)
#   • bandwidth-capped — set R2_BWLIMIT (e.g. 10m) to pace it via pv and not saturate the link
#
# Emits backup,target=wikipedia to InfluxDB `ops` (token sourced from the observability .env).
#
# Usage: scripts/r2_sync.sh [db_path] [bucket] [YYYY-MM-DD]
# Env:   R2_BWLIMIT=10m   (pv -L rate; empty = uncapped)
set -euo pipefail

DB="${1:-$(cd "$(dirname "$0")/.." && pwd)/data/wiki.db}"
BUCKET="${2:-wikipedia-snapshots}"
DATE="${3:-$(date +%F)}"
PART_SIZE="290m"
BWLIMIT="${R2_BWLIMIT:-10m}"
PREFIX="snapshots/${DATE}"
DATADIR="$(dirname "$DB")"
WORK="$DATADIR/.r2_parts_${DATE}"
CONTENT="$DATADIR/.wiki-content_${DATE}.db"

command -v zstd >/dev/null   || { echo "zstd required: brew install zstd"; exit 1; }
command -v sqlite3 >/dev/null || { echo "sqlite3 required"; exit 1; }
[ -f "$DB" ] || { echo "missing $DB"; exit 1; }

wr() { npx --yes wrangler@latest "$@"; }

# Shared write-only ops token lives in the observability .env on this host; source at
# runtime if not already set (keeps the secret out of this repo).
OPS_ENV="${INFLUX_OPS_ENV:-/Volumes/dev/observability/influxdb/.env}"
if [ -z "${INFLUX_OPS_TOKEN:-}" ] && [ -f "$OPS_ENV" ]; then
  INFLUX_OPS_TOKEN="$(grep -E '^INFLUX_OPS_TOKEN=' "$OPS_ENV" | head -1 | cut -d= -f2- | tr -d '"')"
fi

# Dashboard metric (skipped cleanly if INFLUX_OPS_TOKEN unset); runs on every exit.
START=$(date +%s); OK=0; METRIC_BYTES=0
emit_metric() {
  local dur=$(( $(date +%s) - START ))
  [ -n "${INFLUX_OPS_TOKEN:-}" ] || { echo "INFLUX_OPS_TOKEN unset — skipping dashboard metric"; return; }
  local url="${INFLUX_URL:-http://localhost:8086}/api/v2/write?org=${INFLUX_ORG:-home}&bucket=${INFLUX_OPS_BUCKET:-ops}&precision=s"
  curl -fsS -XPOST "$url" -H "Authorization: Token ${INFLUX_OPS_TOKEN}" \
    --data-binary "backup,target=wikipedia success=${OK}i,bytes=${METRIC_BYTES}i,duration_s=${dur}i" \
    >/dev/null && echo "dashboard metric emitted (success=${OK})" || echo "metric POST failed"
}
trap emit_metric EXIT

echo "DB: $DB    Snapshot: $PREFIX    bwlimit: ${BWLIMIT:-none}"

# Build the parts once; resume reuses a completed split (same DATE).
if [ -f "$WORK/.split_done" ] && [ -f "$WORK/manifest.txt" ]; then
  echo "resuming — reusing existing parts in $WORK"
else
  rm -rf "$WORK"; mkdir -p "$WORK"
  echo "building content-only copy (articles + meta, no FTS index)…"
  rm -f "$CONTENT"
  sqlite3 "$CONTENT" "
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT NOT NULL, slug TEXT, text TEXT);
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    ATTACH '$DB' AS src;
    INSERT INTO articles SELECT id, title, slug, text FROM src.articles;
    INSERT INTO meta SELECT key, value FROM src.meta;
    DETACH src;"
  CONTENT_SHA="$(shasum -a 256 "$CONTENT" | awk '{print $1}')"
  echo "compressing + splitting content db ($(du -h "$CONTENT" | cut -f1)) …"
  zstd -T0 -12 -c "$CONTENT" | split -b "$PART_SIZE" -d -a 4 - "$WORK/wiki.db.zst.part-"
  PARTS=("$WORK"/wiki.db.zst.part-*)
  {
    echo "schema=content-only"
    echo "rebuild_fts=true"
    echo "content_file=wiki-content.db"
    echo "content_sha256=${CONTENT_SHA}"
    echo "content_bytes=$(stat -f%z "$CONTENT")"
    echo "compression=zstd"
    echo "part_count=${#PARTS[@]}"
    echo "reassemble=cat wiki.db.zst.part-* | zstd -d -o wiki-content.db  # then rebuild FTS"
    echo "parts:"
    for p in "${PARTS[@]}"; do echo "  $(basename "$p") $(shasum -a 256 "$p" | awk '{print $1}')"; done
  } > "$WORK/manifest.txt"
  rm -f "$CONTENT"
  touch "$WORK/.split_done"
fi

PARTS=("$WORK"/wiki.db.zst.part-*)
METRIC_BYTES=0; for p in "${PARTS[@]}"; do METRIC_BYTES=$((METRIC_BYTES + $(stat -f%z "$p"))); done

echo "uploading manifest + ${#PARTS[@]} parts (skip already-sent)…"
wr r2 object put "${BUCKET}/${PREFIX}/manifest.txt" --file="$WORK/manifest.txt" --remote -y >/dev/null
touch "$WORK/.uploaded"

put_part() {  # paced read → wrangler stdin; pv -L caps effective throughput
  if [ -n "$BWLIMIT" ] && command -v pv >/dev/null; then
    pv -q -L "$BWLIMIT" < "$1" | wr r2 object put "$2" --pipe --remote -y >/dev/null
  else
    wr r2 object put "$2" --file="$1" --remote -y >/dev/null
  fi
}

for p in "${PARTS[@]}"; do
  name="$(basename "$p")"
  if grep -qx "$name" "$WORK/.uploaded" 2>/dev/null; then echo "  ✓ $name (already up)"; continue; fi
  echo "  → $name"
  put_part "$p" "${BUCKET}/${PREFIX}/${name}"
  echo "$name" >> "$WORK/.uploaded"
done

printf '%s\n' "$DATE" > "$WORK/latest.txt"
wr r2 object put "${BUCKET}/latest.txt" --file="$WORK/latest.txt" --remote -y >/dev/null

rm -rf "$WORK"
OK=1
echo "Synced content snapshot → r2://${BUCKET}/${PREFIX}/ (${#PARTS[@]} parts); latest → ${DATE}."
