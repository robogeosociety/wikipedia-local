#!/usr/bin/env python3
"""Extract a Kiwix ZIM into a single SQLite DB with an FTS5 full-text index.

The ZIM is the build-time source (clean, pre-rendered article HTML); SQLite is
the queryable serving artifact. Extraction is CPU-bound (HTML→text), so it is
sharded across worker processes: each worker parses a contiguous slice of the
ZIM entry-id space into its own SQLite shard, then the shards are merged into the
final DB and a single external-content FTS5 index is built (text stored once).

Usage:
    uv run python scripts/extract.py data/wikipedia_en_all_nopic_2026-03.zim data/wiki.db
    uv run python scripts/extract.py <zim> <db> --workers 6
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from multiprocessing import Process
from pathlib import Path

import typer
from libzim.reader import Archive
from selectolax.parser import HTMLParser

app = typer.Typer(add_completion=False)

BATCH = 5000
# Tags whose text is chrome, not article prose.
_STRIP = (
    "script, style, head, sup.reference, .mw-editsection, .navbox, "
    ".noprint, .sidebar, .vertical-navbox, table.infobox"
)


def html_to_text(html: str) -> str:
    """Reduce a ZIM article's HTML to readable plain text."""
    tree = HTMLParser(html)
    for node in tree.css(_STRIP):
        node.decompose()
    body = tree.body or tree
    text = body.text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def is_article(entry) -> bool:
    """True for real content articles (HTML, non-redirect)."""
    if entry.is_redirect:
        return False
    try:
        item = entry.get_item()
    except RuntimeError:
        return False
    return item.mimetype.startswith("text/html")


def worker(zim_path: str, shard_db: str, start: int, end: int, wid: int) -> None:
    """Parse entry ids [start, end) into a flat (title, slug, text) shard DB."""
    archive = Archive(zim_path)
    con = sqlite3.connect(shard_db)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE a (title TEXT, slug TEXT, text TEXT)")
    rows: list[tuple] = []
    kept = 0
    for i in range(start, end):
        try:
            entry = archive._get_entry_by_id(i)
        except RuntimeError:
            continue
        if not is_article(entry):
            continue
        try:
            html = bytes(entry.get_item().content).decode("utf-8", "replace")
        except RuntimeError:
            continue
        text = html_to_text(html)
        if not text:
            continue
        rows.append((entry.title or entry.path, entry.path, text))
        kept += 1
        if len(rows) >= BATCH:
            con.executemany("INSERT INTO a VALUES (?,?,?)", rows)
            con.commit()
            rows.clear()
            print(f"[w{wid}] {i - start:,}/{end - start:,} scanned, {kept:,} kept", flush=True)
    if rows:
        con.executemany("INSERT INTO a VALUES (?,?,?)", rows)
        con.commit()
    con.close()
    print(f"[w{wid}] DONE ids {start:,}-{end:,}: {kept:,} articles", flush=True)


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


@app.command()
def main(
    zim_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    db_path: Path = typer.Argument(...),
    workers: int = typer.Option(0, help="Worker processes (default: CPU count - 2)."),
    force: bool = typer.Option(False, help="Overwrite an existing DB."),
) -> None:
    if db_path.exists() and not force:
        typer.echo(f"{db_path} exists — pass --force to overwrite.")
        raise typer.Exit(1)
    if db_path.exists():
        db_path.unlink()

    archive = Archive(str(zim_path))
    n = archive.all_entry_count
    nw = workers or max(1, (os.cpu_count() or 2) - 2)
    typer.echo(f"ZIM: {zim_path.name}  entries={n:,}  workers={nw}")

    # Contiguous shards preserve ZIM cluster locality (sequential reads).
    bounds = [(n * k // nw, n * (k + 1) // nw) for k in range(nw)]
    tmpdir = Path(tempfile.mkdtemp(dir=str(db_path.parent), prefix=".shards_"))
    shards = [str(tmpdir / f"shard{k}.db") for k in range(nw)]

    t0 = time.time()
    procs = [
        Process(target=worker, args=(str(zim_path), shards[k], bounds[k][0], bounds[k][1], k))
        for k in range(nw)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    if any(p.exitcode != 0 for p in procs):
        raise RuntimeError(f"a worker failed: exitcodes={[p.exitcode for p in procs]}")
    typer.echo(f"parse done in {time.time() - t0:.0f}s — merging {nw} shards…")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA cache_size=-400000")  # ~400 MB
    build_schema(con)
    for s in shards:
        con.execute("ATTACH ? AS shard", (s,))
        con.execute("INSERT INTO articles(title, slug, text) SELECT title, slug, text FROM shard.a")
        con.commit()
        con.execute("DETACH shard")
    kept = con.execute("SELECT count(*) FROM articles").fetchone()[0]

    typer.echo(f"merged {kept:,} articles — building FTS index…")
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    con.execute("INSERT INTO meta(key,value) VALUES ('source_zim',?)", (zim_path.name,))
    con.execute("INSERT INTO meta(key,value) VALUES ('article_count',?)", (str(kept),))
    con.commit()
    typer.echo("optimizing…")
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES('optimize')")
    con.commit()
    # No VACUUM: a fresh build has nothing to reclaim, and VACUUM writes a full
    # ~60 GB temp copy to the system temp dir (small boot disk) → "disk full".
    con.close()
    shutil.rmtree(tmpdir, ignore_errors=True)
    typer.echo(f"done → {db_path}  ({kept:,} articles, {time.time() - t0:.0f}s total)")


if __name__ == "__main__":
    app()
