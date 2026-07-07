# CLAUDE.md — Local Wikipedia for agents

Offline, full-text-searchable English Wikipedia. The whole encyclopedia (~6.9M
articles, text only) lives in one SQLite file that agents query directly or via
MCP — no network, no API limits.

## The artifact

`data/wiki.db` — a single SQLite database built from the Kiwix **nopic** ZIM
(`wikipedia_en_all_nopic_2026-03.zim`, a fixed 2026-03 snapshot).

Schema:
- `articles(id, title, slug, text)` — one row per article; `text` is cleaned plain text.
- `articles_fts` — FTS5 **external-content** index over `(title, text)`, tokenizer
  `porter unicode61`. Its `rowid` equals `articles.id`.
- `meta(key, value)` — `source_zim`, `article_count`.

It's opened **read-only/immutable**, so any number of agents or sidecar containers
can query the same file concurrently.

## Query it — MCP (preferred for agents)

MCP server `wikipedia-local` is registered at **user scope** (`~/.claude.json`), so it's
available in every Claude Code session. It exposes:
- `search_wikipedia(query, limit=10, wiki="enwiki")` → ranked `[{id, title, snippet}]` (FTS5 bm25).
- `get_article(title_or_id, max_chars=None, wiki="enwiki")` → full article `{id, title, slug, text}`
  (custom wikis also return `frontmatter`).
- `suggest(prefix, limit=10, wiki="enwiki")` → title autocomplete.
- `list_wikis()` → the wikis this server can query (name, `article_count`, provenance).

## Custom wikis — multi-wiki host

Beyond enwiki, this repo hosts Tommy's own **authoritative wikis**: git-versioned markdown
under `wikis/<name>/` (e.g. `wikis/dev/`, `wikis/atlas/`) compiled by `scripts/build_wiki.py`
into `data/<name>.db` with the **same `articles` + FTS5 schema as `wiki.db`**, plus one
`article_meta(article_id, frontmatter)` side table. Every MCP tool takes a `wiki=` argument
(default `enwiki`) so agents search/read them the same way. The content pipeline (triggers,
templates, research) lives in the sibling `obsidian-automations` repo, which PRs markdown into
`wikis/` and dispatches a rebuild — see `robogeosociety/obsidian-automations` PR #245.

- **Local `<name>.db` is derived from git.** Unlike enwiki, custom wikis get **no R2 snapshot** —
  the `wikis/` markdown in git *is* the authority and the DR copy; `build_wiki.py` rebuilds the
  DB from a checkout in seconds. `r2_sync.sh`/`r2_pull.sh`/`refresh.sh` stay **enwiki-only**.

Re-register on another machine with:
```sh
claude mcp add --scope user wikipedia-local -- \
  uv run --project /Volumes/dev/data/wikipedia python /Volumes/dev/data/wikipedia/scripts/mcp_server.py
```

## Query it — direct SQL

```sh
# keyword search, best matches first
sqlite3 data/wiki.db "
  SELECT a.title, snippet(articles_fts,1,'[',']',' … ',10)
  FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid
  WHERE articles_fts MATCH 'photosynthesis chloroplast'
  ORDER BY bm25(articles_fts) LIMIT 5;"

# full text of one article
sqlite3 data/wiki.db "SELECT text FROM articles WHERE title='Photosynthesis';"
```
FTS5 query syntax: `'term1 term2'` (AND), `'term1 OR term2'`, `'"exact phrase"'`,
`'prefi*'`, `'title:foo'`.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/download.sh [YYYY-MM]` | resumable ZIM download + checksum verify |
| `scripts/extract.py <zim> <db>` | build `wiki.db` (articles + FTS5) from a ZIM |
| `scripts/mcp_server.py` | the FastMCP stdio server |
| `scripts/r2_sync.sh [db] [bucket] [date]` | sync content UP to R2: content-only, resumable, bw-capped; updates `latest` |
| `scripts/r2_pull.sh [latest\|date] [out] [bucket]` | pull DOWN from R2 (default: latest), reassemble + rebuild FTS |
| `scripts/refresh.sh` | monthly: newest ZIM → rebuild → R2 sync → prune |

## Operations

- **Local `wiki.db` is authoritative.** R2 is a monthly off-site mirror, **out of the read
  path** (DR / seeding only). Because the FTS index is *derived*, R2 stores a **content-only**
  snapshot (`articles` + `meta`, no `articles_fts`) — roughly half the size; `r2_pull.sh`
  rebuilds the index locally on restore. `r2_sync.sh` zstd-compresses + splits the content DB
  into ~290 MiB parts (wrangler's single-object cap) under `snapshots/<YYYY-MM-DD>/` with a
  manifest, plus a `latest.txt` pointer. The upload is **resumable** (rerun the same date to
  continue) and **bandwidth-capped** (`R2_BWLIMIT`, default `10m`, via `pv`). Sync status →
  Grafana **Backups** dashboard (`backup,target=wikipedia`); the write-only `INFLUX_OPS_TOKEN`
  is sourced from `observability/influxdb/.env` at runtime.
- **Monthly refresh:** `nomad/wikipedia-refresh.nomad` runs `refresh.sh` on the 5th.
  It rebuilds atomically (build to `wiki.db.new`, then swap) so live readers aren't
  disrupted. Idempotent — no-ops if already built from the latest dump.
- **Future:** semantic/vector search is intentionally deferred; add a vector sidecar
  + `semantic_search` MCP tool only if keyword search proves insufficient.
