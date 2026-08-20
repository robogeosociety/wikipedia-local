#!/usr/bin/env python3
"""FastMCP stdio server exposing local wikis (SQLite/FTS5) to agents.

Serves enwiki (`data/wiki.db`, ZIM-built) AND the custom authoritative wikis
(`data/<wiki>.db`, built from `wikis/<wiki>/` markdown) through one interface —
every DB shares the same `articles` + FTS5 schema, so the same tools query them
all. Pick a wiki with the `wiki=` argument (default `enwiki`).

Tools:
  search_wikipedia(query, limit, wiki)      -> ranked {id, title, snippet}
  get_article(title_or_id, max_chars, wiki) -> full text (+ frontmatter, custom wikis)
  suggest(prefix, limit, wiki)              -> title prefix autocomplete
  list_wikis()                              -> the wikis this server can query

Each DB is opened read-only/immutable, so any number of agents share them.
Run:  uv run --project /Volumes/dev/data/wikipedia python scripts/mcp_server.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weights  # noqa: E402
from wikidb import connect_immutable, fts_query as _fts_query  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("WIKI_DATA_DIR", _REPO / "data"))
# enwiki keeps its own env for backward compat with the user-scope registration.
DB_PATH = Path(os.environ.get("WIKI_DB", DATA_DIR / "wiki.db"))
ENWIKI = "enwiki"

# Reviewed topic policy (weights.toml). Invalid config fails loudly here, at
# startup; an absent config is the one legitimate neutral state.
TOPICS = weights.load_config()
if not TOPICS:
    weights.warn("no weights.toml — running with neutral ranking, no hot cache")
_ROUTES = weights.alias_map(TOPICS)
# warm_cache.py flips this off so it renders through the live FTS path.
HOT_ENABLED = True
COUNTER_URL = os.environ.get("WIKI_COUNTER_URL", "redis://127.0.0.1:6379")
COUNTER_TTL = 7 * 86400

mcp = FastMCP("wikipedia-local")


def _db_for(wiki: str) -> Path:
    """Resolve a wiki name to its DB file. `enwiki` (default) → the ZIM-built DB;
    any other name → `data/<wiki>.db`."""
    return DB_PATH if wiki == ENWIKI else DATA_DIR / f"{wiki}.db"


def _known_wikis() -> list[str]:
    names = [ENWIKI] if DB_PATH.exists() else []
    names += sorted(
        p.stem
        for p in DATA_DIR.glob("*.db")
        if p.resolve() != DB_PATH.resolve() and not p.name.endswith(".db.new")
    )
    return names


def _connect(wiki: str) -> sqlite3.Connection | None:
    db = _db_for(wiki)
    return connect_immutable(db) if db.exists() else None


def _unknown(wiki: str) -> dict:
    return {
        "error": f"unknown wiki {wiki!r}; known: {', '.join(_known_wikis()) or 'none'}"
    }


def _source_zim(con: sqlite3.Connection, table: str = "meta") -> str:
    row = con.execute(f"SELECT value FROM {table} WHERE key='source_zim'").fetchone()
    return row[0] if row else ""


def _attach_weights(con: sqlite3.Connection) -> bool:
    """ATTACH data/weights.db as `w` if present AND built from the live ZIM.

    A stale or missing sidecar degrades to neutral bm25 ranking, never to a
    wrong boost (article ids are not stable across monthly rebuilds).
    """
    db = DATA_DIR / weights.WEIGHTS_DB_NAME
    if not db.exists():
        return False
    try:
        con.execute("ATTACH DATABASE ? AS w", (f"file:{db}?immutable=1",))
        if _source_zim(con, "w.meta") != _source_zim(con):
            con.execute("DETACH DATABASE w")
            return False
        return True
    except sqlite3.Error:
        return False


def _count_topic(topic: str) -> None:
    """Best-effort windowed hit counter (`wiki:topic:<name>`) on the local
    Valkey — the discobots bus degradability contract: no redis-py or no bus
    means a silent no-op, and every key carries a TTL (the bus runs noeviction).
    Only topics already named in the reviewed weights.toml are ever counted;
    raw query strings never leave the process.
    """
    try:
        import redis  # noqa: PLC0415 — optional, deliberately not a dependency

        r = redis.Redis.from_url(
            COUNTER_URL, socket_timeout=0.1, socket_connect_timeout=0.1
        )
        key = f"wiki:topic:{topic}"
        with r.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, COUNTER_TTL)
            pipe.execute()
    except Exception:
        pass


def _hot_payload(query: str) -> dict | None:
    """The pre-rendered payload for an exact topic/title/alias query, or None.

    Trusted only when the manifest's source_zim matches the live wiki.db —
    stale cache degrades to the FTS path, never to a stale answer.
    """
    if not HOT_ENABLED:
        return None
    index = DATA_DIR / weights.HOT_DIR_NAME / "index.json"
    if not index.exists():
        return None
    try:
        manifest = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    con = _connect(ENWIKI)
    if con is None:
        return None
    try:
        if manifest.get("source_zim") != _source_zim(con):
            return None
    finally:
        con.close()
    norm = weights.normalize(query)
    for entry in manifest.get("topics", {}).values():
        if norm in entry.get("routes", []):
            try:
                return json.loads((index.parent / entry["file"]).read_text())
            except (OSError, json.JSONDecodeError, KeyError):
                return None
    return None


def _route(query: str) -> str | None:
    """The configured topic this query names exactly, if any (for counting)."""
    return _ROUTES.get(weights.normalize(query))


def _has_article_meta(con: sqlite3.Connection) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_meta'"
        ).fetchone()
        is not None
    )


@mcp.tool()
def search_wikipedia(query: str, limit: int = 10, wiki: str = ENWIKI) -> list[dict]:
    """Full-text search a wiki (default enwiki). Returns ranked {id, title, snippet}.

    Set `wiki` to query a custom wiki instead (e.g. "dev", "atlas") — see list_wikis().
    """
    limit = max(1, min(limit, 50))
    if wiki == ENWIKI:
        if (topic := _route(query)) is not None:
            _count_topic(topic)
        if (hot := _hot_payload(query)) is not None:
            return [dict(h, served_from="hot-cache") for h in hot["search"][:limit]]
    con = _connect(wiki)
    if con is None:
        return [_unknown(wiki)]
    try:
        weighted = wiki == ENWIKI and _attach_weights(con)
        weight_join, weight_rank = (
            (
                "LEFT JOIN w.weights wt ON wt.article_id = a.id",
                " * COALESCE(wt.weight, 1.0)",
            )
            if weighted
            else ("", "")
        )
        rows = con.execute(
            f"""
            SELECT a.id AS id, a.title AS title,
                   snippet(articles_fts, 1, '«', '»', ' … ', 12) AS snippet
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            {weight_join}
            WHERE articles_fts MATCH ?
            ORDER BY bm25(articles_fts){weight_rank}
            LIMIT ?
            """,
            (_fts_query(query), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


@mcp.tool()
def get_article(
    title_or_id: str, max_chars: int | None = None, wiki: str = ENWIKI
) -> dict:
    """Fetch a full article's text by exact title or numeric id from a wiki
    (default enwiki). Falls back to the best full-text match when there is no
    exact title. For custom wikis the article's `frontmatter` is included.
    """
    if wiki == ENWIKI and not title_or_id.isdigit():
        if (topic := _route(title_or_id)) is not None:
            _count_topic(topic)
        if (hot := _hot_payload(title_or_id)) is not None:
            norm = weights.normalize(title_or_id)
            art = hot["articles"].get(norm) or hot["articles"].get(hot["primary"])
            if art is not None:
                text = art["text"]
                truncated = bool(max_chars and len(text) > max_chars)
                if truncated:
                    text = text[:max_chars]
                return dict(
                    art, text=text, truncated=truncated, served_from="hot-cache"
                )
    con = _connect(wiki)
    if con is None:
        return _unknown(wiki)
    try:
        row = None
        if title_or_id.isdigit():
            row = con.execute(
                "SELECT id, title, slug, text FROM articles WHERE id=?",
                (int(title_or_id),),
            ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT id, title, slug, text FROM articles WHERE title=? COLLATE NOCASE LIMIT 1",
                (title_or_id,),
            ).fetchone()
        if row is None:
            weighted = wiki == ENWIKI and _attach_weights(con)
            weight_join, weight_rank = (
                (
                    "LEFT JOIN w.weights wt ON wt.article_id = a.id",
                    " * COALESCE(wt.weight, 1.0)",
                )
                if weighted
                else ("", "")
            )
            hit = con.execute(
                f"""
                SELECT a.id FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid
                {weight_join}
                WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts){weight_rank} LIMIT 1
                """,
                (_fts_query(title_or_id),),
            ).fetchone()
            if hit:
                row = con.execute(
                    "SELECT id, title, slug, text FROM articles WHERE id=?",
                    (hit["id"],),
                ).fetchone()
        if row is None:
            return {"error": f"no article found for {title_or_id!r} in wiki {wiki!r}"}
        text = row["text"] or ""
        truncated = False
        if max_chars and len(text) > max_chars:
            text, truncated = text[:max_chars], True
        result = {
            "id": row["id"],
            "title": row["title"],
            "slug": row["slug"],
            "text": text,
            "truncated": truncated,
        }
        if _has_article_meta(con):
            fm = con.execute(
                "SELECT frontmatter FROM article_meta WHERE article_id=?", (row["id"],)
            ).fetchone()
            if fm:
                result["frontmatter"] = json.loads(fm["frontmatter"])
        return result
    finally:
        con.close()


@mcp.tool()
def suggest(prefix: str, limit: int = 10, wiki: str = ENWIKI) -> list[str]:
    """Title prefix autocomplete for a wiki (default enwiki)."""
    limit = max(1, min(limit, 50))
    con = _connect(wiki)
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT title FROM articles WHERE title LIKE ? ORDER BY length(title) LIMIT ?",
            (prefix + "%", limit),
        ).fetchall()
        return [r["title"] for r in rows]
    finally:
        con.close()


@mcp.tool()
def list_wikis() -> list[dict]:
    """The wikis this server can query: name + article_count + provenance
    (source ZIM for enwiki, git source_commit for custom wikis)."""
    out = []
    for name in _known_wikis():
        con = _connect(name)
        if con is None:
            continue
        try:
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            out.append(
                {
                    "wiki": name,
                    "article_count": int(meta.get("article_count", 0)),
                    "source": meta.get("source_zim") or meta.get("source_commit", ""),
                }
            )
        finally:
            con.close()
    return out


if __name__ == "__main__":
    mcp.run()
