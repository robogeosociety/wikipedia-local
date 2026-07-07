#!/usr/bin/env python3
"""Build a custom wiki's SQLite DB from a directory of markdown articles.

The markdown under `wikis/<name>/` is the git-versioned authority; the DB is a
derived, queryable artifact with the same `articles` + FTS5 shape as enwiki's
`wiki.db`, plus an `article_meta` table carrying each article's frontmatter as
JSON. `text` stores the raw markdown body (wikilinks intact) — rendering and
link rewriting are the server's job, so a link-target rename never forces a
rebuild of referrers.

Content is machine-committed (by obsidian-automations lanes), so malformed
input fails the build loudly instead of degrading: missing/unparseable
frontmatter, a missing `title`, or two files mapping to one slug are errors.

Usage:
    uv run python scripts/build_wiki.py wikis/dev data/dev.db.new --wiki-name dev
    # atomicity is the caller's job: build to <db>.new, then swap (refresh.sh idiom)
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import typer
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wikidb import build_article_meta_schema, build_schema  # noqa: E402

app = typer.Typer(add_completion=False)

WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\|([^\]]+?))?\]\]")
FENCE = re.compile(r"(```.*?```)", re.DOTALL)


class BuildError(Exception):
    """A content problem that must fail the build (bad file, bad slug, …)."""


class Article(NamedTuple):
    title: str
    slug: str
    text: str
    frontmatter: dict
    path: Path


def split_note(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body). Raises on absent or unparseable frontmatter —
    every authoritative article must open with a `---` YAML block."""
    if not text.startswith("---"):
        raise BuildError("no frontmatter block")
    try:
        _, fm, body = text.split("---", 2)
        parsed = yaml.safe_load(fm)
    except (ValueError, yaml.YAMLError) as e:
        raise BuildError(f"unparseable frontmatter: {e}") from e
    if not isinstance(parsed, dict):
        raise BuildError("frontmatter is not a mapping")
    return parsed, body.lstrip("\n")


def strip_leading_h1(body: str) -> str:
    """Drop a single leading `# Title` line — layouts render the title themselves."""
    return re.sub(r"\A#\s+[^\n]+(?:\n+|\Z)", "", body, count=1)


def slugify(segment: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", segment.strip())
    return re.sub(r"-{2,}", "-", s).strip("-").lower()


def derive_slug(rel_path: PurePosixPath) -> str:
    """Repo-relative path under the wiki dir → slug: per-segment slugified,
    directory structure preserved (`carto/index.md` → `carto/index`)."""
    parts = [slugify(p) for p in rel_path.with_suffix("").parts]
    if not all(parts):
        raise BuildError(f"path segment slugifies to nothing: {rel_path}")
    return "/".join(parts)


def load_article(md_path: Path, wiki_dir: Path) -> Article:
    rel = PurePosixPath(md_path.relative_to(wiki_dir).as_posix())
    try:
        fm, body = split_note(md_path.read_text(encoding="utf-8"))
        title = fm.get("title")
        if not title or not isinstance(title, str):
            raise BuildError("missing required frontmatter field: title")
        slug = fm.get("slug") or derive_slug(rel)
    except BuildError as e:
        raise BuildError(f"{md_path}: {e}") from e
    return Article(title, str(slug), strip_leading_h1(body), fm, md_path)


def collect(wiki_dir: Path) -> list[Article]:
    """All articles under the wiki dir, sorted by path. Slug collision = error."""
    articles = [
        load_article(p, wiki_dir)
        for p in sorted(wiki_dir.rglob("*.md"))
        if not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts)
    ]
    by_slug: dict[str, Path] = {}
    for a in articles:
        if a.slug in by_slug:
            raise BuildError(
                f"slug collision on {a.slug!r}: {by_slug[a.slug]} vs {a.path}"
            )
        by_slug[a.slug] = a.path
    return articles


def check_wikilinks(articles: list[Article]) -> list[str]:
    """Warnings for [[links]] whose target resolves to no article (by title,
    slug, or slug basename). Log-only — a dangling link renders as plain text."""
    known: set[str] = set()
    for a in articles:
        known.update({a.title, a.slug, a.slug.rsplit("/", 1)[-1]})
    warnings = []
    for a in articles:
        for i, part in enumerate(FENCE.split(a.text)):
            if i % 2:  # inside a code fence
                continue
            for m in WIKILINK.finditer(part):
                target = m.group(1).split("#")[0].strip()
                if target and target not in known:
                    warnings.append(f"{a.path}: unresolved [[{m.group(1).strip()}]]")
    return warnings


def build(articles: list[Article], db_path: Path, meta: dict[str, str]) -> int:
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        build_schema(con)
        build_article_meta_schema(con)
        for a in articles:
            cur = con.execute(
                "INSERT INTO articles(title, slug, text) VALUES (?,?,?)",
                (a.title, a.slug, a.text),
            )
            con.execute(
                "INSERT INTO article_meta(article_id, frontmatter) VALUES (?,?)",
                (cur.lastrowid, json.dumps(a.frontmatter, sort_keys=True, default=str)),
            )
        con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        con.execute("INSERT INTO articles_fts(articles_fts) VALUES('optimize')")
        stamps = {
            "article_count": str(len(articles)),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        con.executemany(
            "INSERT INTO meta(key, value) VALUES (?,?)", {**meta, **stamps}.items()
        )
        con.commit()
    finally:
        con.close()
    return len(articles)


@app.command()
def main(
    wiki_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    db_path: Path = typer.Argument(...),
    wiki_name: str = typer.Option(None, help="meta.wiki; default: wiki_dir name."),
    source_tree: str = typer.Option(
        "", help="git tree oid of the wiki dir (rebuild gate)."
    ),
    source_commit: str = typer.Option("", help="git HEAD oid recorded in meta."),
    force: bool = typer.Option(False, help="Overwrite an existing DB."),
) -> None:
    if db_path.exists() and not force:
        typer.echo(f"{db_path} exists — pass --force to overwrite.")
        raise typer.Exit(1)
    if db_path.exists():
        db_path.unlink()

    t0 = time.time()
    try:
        articles = collect(wiki_dir)
    except BuildError as e:
        typer.echo(f"build failed: {e}", err=True)
        raise typer.Exit(1) from e
    if not articles:
        typer.echo(f"build failed: no articles under {wiki_dir}", err=True)
        raise typer.Exit(1)
    for warning in check_wikilinks(articles):
        typer.echo(f"warning: {warning}")

    meta = {
        "wiki": wiki_name or wiki_dir.name,
        "source_tree": source_tree,
        "source_commit": source_commit,
    }
    n = build(articles, db_path, meta)
    typer.echo(f"done → {db_path}  ({n} articles, {time.time() - t0:.1f}s)")


if __name__ == "__main__":
    app()
