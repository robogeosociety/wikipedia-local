#!/usr/bin/env python3
"""FastMCP stdio server exposing local Wikipedia (SQLite/FTS5) to agents.

Tools:
  search_wikipedia(query, limit)  -> ranked title + snippet matches (FTS5 bm25)
  get_article(title_or_id, max_chars) -> full cleaned article text
  suggest(prefix, limit)          -> title prefix autocomplete

The DB is opened read-only, so any number of agents/processes can share it.
Run:  uv run --project /Volumes/dev/data/wikipedia python scripts/mcp_server.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wikidb import connect_immutable, fts_query as _fts_query  # noqa: E402

DB_PATH = Path(
    os.environ.get(
        "WIKI_DB", Path(__file__).resolve().parent.parent / "data" / "wiki.db"
    )
)

mcp = FastMCP("wikipedia-local")


def _connect() -> sqlite3.Connection:
    # Immutable: no locking, many concurrent readers, fastest reads.
    return connect_immutable(DB_PATH)


@mcp.tool()
def search_wikipedia(query: str, limit: int = 10) -> list[dict]:
    """Full-text search English Wikipedia. Returns ranked {id, title, snippet}."""
    limit = max(1, min(limit, 50))
    con = _connect()
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
def get_article(title_or_id: str, max_chars: int | None = None) -> dict:
    """Fetch a full article's plain text by exact title or numeric id.

    Falls back to the best full-text match when there is no exact title.
    """
    con = _connect()
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
            return {"error": f"no article found for {title_or_id!r}"}
        text = row["text"] or ""
        truncated = False
        if max_chars and len(text) > max_chars:
            text, truncated = text[:max_chars], True
        return {
            "id": row["id"],
            "title": row["title"],
            "slug": row["slug"],
            "text": text,
            "truncated": truncated,
        }
    finally:
        con.close()


@mcp.tool()
def suggest(prefix: str, limit: int = 10) -> list[str]:
    """Title prefix autocomplete."""
    limit = max(1, min(limit, 50))
    con = _connect()
    try:
        rows = con.execute(
            "SELECT title FROM articles WHERE title LIKE ? ORDER BY length(title) LIMIT ?",
            (prefix + "%", limit),
        ).fetchall()
        return [r["title"] for r in rows]
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
