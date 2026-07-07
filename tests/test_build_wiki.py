"""Hermetic tests for the custom-wiki build layer (no ZIM, no network)."""

import json
import sqlite3
from pathlib import PurePosixPath

import pytest
from typer.testing import CliRunner

import build_wiki
import wikidb
from build_wiki import (
    Article,
    BuildError,
    app,
    check_wikilinks,
    collect,
    derive_slug,
    load_article,
)

runner = CliRunner()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def wiki(tmp_path):
    d = tmp_path / "wikis" / "dev"
    write(d / "index.md", "---\ntitle: Dev Wiki\n---\n\nThe home page.\n")
    write(
        d / "obsidian-automations.md",
        "---\n"
        "title: obsidian-automations\n"
        "kind: project\n"
        "objectives:\n"
        "  - id: wiki-decoupling\n"
        "    title: Decouple the wikis\n"
        "    status: active\n"
        "---\n\n"
        "Automation code, with a [[Dev Wiki]] link and a photosynthesis mention.\n",
    )
    return d


def build_db(wiki_dir, db_path, **meta):
    articles = collect(wiki_dir)
    build_wiki.build(
        articles,
        db_path,
        {"wiki": "dev", "source_tree": "abc123", "source_commit": "def456", **meta},
    )
    return sqlite3.connect(db_path)


# --- loading / slugs -----------------------------------------------------


def test_title_required(tmp_path):
    write(tmp_path / "a.md", "---\nkind: project\n---\nbody\n")
    with pytest.raises(BuildError, match="title"):
        load_article(tmp_path / "a.md", tmp_path)


def test_frontmatter_required_and_parseable(tmp_path):
    write(tmp_path / "a.md", "no frontmatter here\n")
    with pytest.raises(BuildError, match="frontmatter"):
        load_article(tmp_path / "a.md", tmp_path)
    write(tmp_path / "b.md", "---\ntitle: [unclosed\n---\nbody\n")
    with pytest.raises(BuildError, match="frontmatter"):
        load_article(tmp_path / "b.md", tmp_path)


def test_slug_derivation_and_override(tmp_path):
    assert derive_slug(PurePosixPath("carto/index.md")) == "carto/index"
    assert derive_slug(PurePosixPath("Los Angeles/Griffith Park.md")) == (
        "los-angeles/griffith-park"
    )
    write(tmp_path / "a.md", "---\ntitle: A\nslug: custom/slug\n---\nbody\n")
    assert load_article(tmp_path / "a.md", tmp_path).slug == "custom/slug"


def test_slug_collision_fails(tmp_path):
    write(tmp_path / "a.md", "---\ntitle: A\nslug: same\n---\nbody\n")
    write(tmp_path / "b.md", "---\ntitle: B\nslug: same\n---\nbody\n")
    with pytest.raises(BuildError, match="collision"):
        collect(tmp_path)


def test_leading_h1_stripped(tmp_path):
    write(tmp_path / "a.md", "---\ntitle: A\n---\n# A\n\nThe body.\n")
    assert load_article(tmp_path / "a.md", tmp_path).text == "The body.\n"


# --- wikilink report ------------------------------------------------------


def test_wikilink_report_flags_unresolved(wiki):
    articles = collect(wiki)
    assert check_wikilinks(articles) == []  # [[Dev Wiki]] resolves by title
    write(wiki / "c.md", "---\ntitle: C\n---\nSee [[No Such Page]].\n")
    warnings = check_wikilinks(collect(wiki))
    assert len(warnings) == 1 and "No Such Page" in warnings[0]


def test_wikilinks_in_code_fences_ignored(tmp_path):
    a = Article("A", "a", "```\n[[Not A Link]]\n```\n", {}, tmp_path / "a.md")
    assert check_wikilinks([a]) == []


# --- the built DB ---------------------------------------------------------


def test_schema_matches_enwiki(wiki, tmp_path):
    con = build_db(wiki, tmp_path / "dev.db")
    ref = sqlite3.connect(":memory:")
    wikidb.build_schema(ref)
    core = (
        "SELECT name, sql FROM sqlite_master "
        "WHERE name IN ('articles','articles_fts','meta') ORDER BY name"
    )
    assert con.execute(core).fetchall() == ref.execute(core).fetchall()


def test_article_meta_roundtrips_objectives(wiki, tmp_path):
    con = build_db(wiki, tmp_path / "dev.db")
    fm = json.loads(
        con.execute(
            "SELECT m.frontmatter FROM article_meta m JOIN articles a "
            "ON a.id = m.article_id WHERE a.slug = 'obsidian-automations'"
        ).fetchone()[0]
    )
    assert fm["objectives"][0] == {
        "id": "wiki-decoupling",
        "title": "Decouple the wikis",
        "status": "active",
    }


def test_fts_search_hits_body(wiki, tmp_path):
    con = build_db(wiki, tmp_path / "dev.db")
    rows = con.execute(
        "SELECT a.title FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid "
        "WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts)",
        (wikidb.fts_query("photosynthesis"),),
    ).fetchall()
    assert [r[0] for r in rows] == ["obsidian-automations"]


def test_meta_keys(wiki, tmp_path):
    con = build_db(wiki, tmp_path / "dev.db")
    meta = dict(con.execute("SELECT key, value FROM meta"))
    assert meta["wiki"] == "dev"
    assert meta["source_tree"] == "abc123"
    assert meta["article_count"] == "2"
    assert meta["built_at"]


# --- CLI ------------------------------------------------------------------


def test_cli_end_to_end(wiki, tmp_path):
    db = tmp_path / "dev.db"
    result = runner.invoke(app, [str(wiki), str(db), "--wiki-name", "dev"])
    assert result.exit_code == 0, result.output
    assert db.exists()
    # refuses to overwrite without --force
    result = runner.invoke(app, [str(wiki), str(db)])
    assert result.exit_code == 1


def test_cli_fails_on_empty_wiki(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, [str(empty), str(tmp_path / "x.db")])
    assert result.exit_code == 1


# --- the checked-in seeds build for real ----------------------------------


def test_seed_wikis_build(tmp_path):
    repo = build_wiki.Path(__file__).resolve().parent.parent
    for name in ("dev", "atlas"):
        articles = collect(repo / "wikis" / name)
        assert articles, f"wikis/{name} has no articles"
        n = build_wiki.build(articles, tmp_path / f"{name}.db", {"wiki": name})
        assert n == len(articles)
