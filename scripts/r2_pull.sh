#!/usr/bin/env bash
# Pull a local wiki.db DOWN from an R2 CONTENT snapshot (DR / seeding a machine).
# R2 stores content only (articles + meta), so this reassembles + verifies that, then
# REBUILDS the FTS index locally to produce a fully queryable wiki.db. Atomic install.
#
# Usage: scripts/r2_pull.sh [latest|YYYY-MM-DD] [out_db] [bucket]
set -euo pipefail

SEL="${1:-latest}"
OUT="${2:-$(cd "$(dirname "$0")/.." && pwd)/data/wiki.db}"
BUCKET="${3:-wikipedia-snapshots}"
WORK="$(dirname "$OUT")/.r2_pull"

command -v zstd >/dev/null    || { echo "zstd required: brew install zstd"; exit 1; }
command -v sqlite3 >/dev/null || { echo "sqlite3 required"; exit 1; }
wr() { npx --yes wrangler@latest "$@"; }

rm -rf "$WORK"; mkdir -p "$WORK"

if [ "$SEL" = "latest" ]; then
  echo "resolving latest…"
  wr r2 object get "${BUCKET}/latest.txt" --file="$WORK/latest.txt" --remote >/dev/null
  DATE="$(tr -d '[:space:]' < "$WORK/latest.txt")"
else
  DATE="$SEL"
fi
PREFIX="snapshots/${DATE}"
echo "pulling content snapshot ${DATE} from r2://${BUCKET}/${PREFIX}/"

wr r2 object get "${BUCKET}/${PREFIX}/manifest.txt" --file="$WORK/manifest.txt" --remote >/dev/null
parts=$(grep -E '^  wiki.db.zst.part-' "$WORK/manifest.txt" | awk '{print $1}')
for name in $parts; do
  echo "  ← $name"
  wr r2 object get "${BUCKET}/${PREFIX}/${name}" --file="$WORK/$name" --remote >/dev/null
done

echo "reassembling content db…"
cat "$WORK"/wiki.db.zst.part-* | zstd -d -o "$WORK/wiki.db"
want=$(grep '^content_sha256=' "$WORK/manifest.txt" | cut -d= -f2)
got=$(shasum -a 256 "$WORK/wiki.db" | awk '{print $1}')
[ "$want" = "$got" ] || { echo "sha256 MISMATCH want=$want got=$got"; exit 1; }
echo "content sha256 OK"

echo "rebuilding FTS index locally (a few minutes)…"
sqlite3 "$WORK/wiki.db" "
  PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
  CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, text, content='articles', content_rowid='id', tokenize='porter unicode61');
  INSERT INTO articles_fts(articles_fts) VALUES('rebuild');
  INSERT INTO articles_fts(articles_fts) VALUES('optimize');"

mv -f "$WORK/wiki.db" "$OUT"
rm -rf "$WORK"
echo "Pulled ${DATE} + rebuilt FTS → $OUT"
