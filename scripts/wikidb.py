"""Shared SQLite schema + connection helpers for every wiki DB in this repo.

One schema serves them all: enwiki (`data/wiki.db`, built by `extract.py` from a
ZIM) and the custom authoritative wikis (`data/<wiki>.db`, built by
`build_wiki.py` from `wikis/<wiki>/` markdown). Custom-wiki DBs add exactly one
side table, `article_meta`, so the core `articles`/`articles_fts`/`meta` shapes
stay byte-identical across all DBs and every existing query keeps working.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def build_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE articles (
            id    INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            slug  TEXT,
            text  TEXT
        );
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title, text,
            content='articles',
            content_rowid='id',
            tokenize='porter unicode61'
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )


def build_article_meta_schema(con: sqlite3.Connection) -> None:
    """Custom-wiki side table: the article's YAML frontmatter as one JSON blob.

    Kept out of `build_schema` so enwiki's DB never grows it — downstream
    consumers (objectives sync, the server) read structured fields with
    `SELECT a.slug, m.frontmatter FROM article_meta m JOIN articles a ...`.
    """
    con.executescript(
        """
        CREATE TABLE article_meta (
            article_id  INTEGER PRIMARY KEY REFERENCES articles(id),
            frontmatter TEXT NOT NULL
        );
        """
    )


def connect_immutable(db_path: Path | str) -> sqlite3.Connection:
    """Read-only, no-locking connection — any number of concurrent readers."""
    uri = f"file:{db_path}?immutable=1"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def fts_query(raw: str) -> str:
    """Make arbitrary user text safe as an FTS5 MATCH by quoting each token."""
    tokens = [t for t in raw.replace('"', " ").split() if t]
    return " ".join(f'"{t}"' for t in tokens) if tokens else '""'
