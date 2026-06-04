#!/usr/bin/env bash
# Download (resumable) the Kiwix English Wikipedia nopic ZIM and verify it.
# Usage: scripts/download.sh [YYYY-MM]   (default: 2026-03)
set -euo pipefail

MONTH="${1:-2026-03}"
NAME="wikipedia_en_all_nopic_${MONTH}.zim"
BASE="https://download.kiwix.org/zim/wikipedia"
DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
mkdir -p "$DIR"
cd "$DIR"

echo "Fetching checksum…"
curl -fsSL -O "${BASE}/${NAME}.sha256"

echo "Downloading ${NAME} (resumable)…"
aria2c -c -x16 -s16 --file-allocation=none --console-log-level=warn "${BASE}/${NAME}"

echo "Verifying sha256…"
shasum -a 256 -c "${NAME}.sha256"
echo "OK → ${DIR}/${NAME}"
