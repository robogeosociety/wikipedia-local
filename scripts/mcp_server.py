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
from wikidb import connect_immutable, fts_query as _fts_query  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("WIKI_DATA_DIR", _REPO / "data"))
# enwiki keeps its own env for backward compat with the user-scope registration.
DB_PATH = Path(os.environ.get("WIKI_DB", DATA_DIR / "wiki.db"))
ENWIKI = "enwiki"

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
    con = _connect(wiki)
    if con is None:
        return [_unknown(wiki)]
    try:
        rows = con.execute(
            """
            SELECT a.id AS id, a.title AS title,
                   snippet(articles_fts, 1, '«', '»', ' … ', 12) AS snippet
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            WHERE articles_fts MATCH ?
            ORDER BY bm25(articles_fts)
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
            hit = con.execute(
                """
                SELECT a.id FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid
                WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts) LIMIT 1
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
