#!/usr/bin/env bash
# Build (and optionally deploy) one custom wiki's static site with the shared
# parameterized Quartz shell in `site/`. Generalises the atlas approach (Quartz →
# Cloudflare Pages) to every wiki: stage wikis/<name> → site/content, render with
# per-wiki title/baseUrl, emit to site/public, then optionally `wrangler pages
# deploy` to that wiki's Pages project.
#
#   scripts/build_site.sh dev            # build only (site/public)
#   scripts/build_site.sh dev --deploy   # build + Cloudflare Pages deploy
#
# Deploy needs a Pages-Write token in $CF_PAGES_TOKEN or ~/.config/cf-pages-token
# (the tfvend wiki_pages_deploy token). Absent it, deploy is skipped loudly.
set -euo pipefail

WIKI="${1:?usage: build_site.sh <wiki> [--deploy]}"
DEPLOY="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="$ROOT/site"
SRC="$ROOT/wikis/$WIKI"
[ -d "$SRC" ] || { echo "no wiki at $SRC"; exit 1; }

# Per-wiki presentation + Pages project. atlas keeps its existing `travel-wiki`
# project + domain untouched; dev/campsites are the new projects.
case "$WIKI" in
  dev)       TITLE="Dev Wiki";   BASEURL="dev.robogeosociety.xyz";        PROJECT="dev-wiki" ;;
  atlas)     TITLE="Atlas";      BASEURL="atlas.robogeosociety.xyz";      PROJECT="travel-wiki" ;;
  campsites) TITLE="Campsites";  BASEURL="campsites.robogeosociety.xyz";  PROJECT="campsites-wiki" ;;
  *) echo "unknown wiki '$WIKI' (dev|atlas|campsites)"; exit 1 ;;
esac

# Quartz deps on first run. --ignore-scripts is REQUIRED (sharp's install script
# source-builds and fails; skipping it lets the prebuilt @img/sharp-* land).
if [ ! -d "$SITE/node_modules" ]; then
  echo "$(date '+%F %T') site: npm install (first run)"
  (cd "$SITE" && npm install --no-audit --no-fund --ignore-scripts)
fi

# Build straight from the tracked wikis/<name> tree. (Do NOT copy into
# site/content: Quartz respects .gitignore during content discovery, and the
# build artifacts there are ignored — so a staged copy renders as ZERO pages.
# Pointing -d at the tracked source also gives Quartz real git dates.)
echo "$(date '+%F %T') $WIKI: quartz build ($BASEURL)"
(cd "$SITE" && WIKI_TITLE="$TITLE" WIKI_BASEURL="$BASEURL" npx quartz build -d "$SRC")
echo "$(date '+%F %T') $WIKI: built → $SITE/public"

[ "$DEPLOY" = "--deploy" ] || { echo "$WIKI: build-only (pass --deploy to publish)"; exit 0; }

TOKEN="${CF_PAGES_TOKEN:-}"
[ -n "$TOKEN" ] || { f="$HOME/.config/cf-pages-token"; [ -s "$f" ] && TOKEN="$(cat "$f")"; }
if [ -z "$TOKEN" ]; then
  echo "$(date '+%F %T') $WIKI: no Pages token — skipping deploy (site left stale)"
  exit 0
fi
echo "$(date '+%F %T') $WIKI: wrangler pages deploy → https://$BASEURL"
CLOUDFLARE_API_TOKEN="$TOKEN" npx --yes wrangler pages deploy "$SITE/public" \
  --project-name "$PROJECT" --branch main --commit-dirty=true
echo "$(date '+%F %T') $WIKI: Pages deploy OK"
