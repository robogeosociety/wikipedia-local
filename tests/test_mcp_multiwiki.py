"""Multi-wiki MCP routing — enwiki default + custom wikis via wiki= (no network)."""

import sqlite3

import pytest

import build_wiki
import mcp_server
import wikidb


def _enwiki_db(path):
    """A minimal enwiki-shaped DB (ZIM-built shape: no article_meta)."""
    con = sqlite3.connect(path)
    wikidb.build_schema(con)
    con.execute(
        "INSERT INTO articles(title, slug, text) VALUES (?,?,?)",
        ("Photosynthesis", "Photosynthesis", "Plants convert light in chloroplasts."),
    )
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    con.executemany(
        "INSERT INTO meta(key, value) VALUES (?,?)",
        {
            "source_zim": "wikipedia_en_all_nopic_2026-03.zim",
            "article_count": "1",
        }.items(),
    )
    con.commit()
    con.close()


def _dev_db(path):
    """A custom wiki DB built the real way (build_wiki → article_meta present)."""
    articles = [
        build_wiki.Article(
            "carto",
            "carto",
            "Carto renders vector tiles.",
            {"title": "carto", "objectives": [{"id": "ga", "status": "active"}]},
            path,
        )
    ]
    build_wiki.build(articles, path, {"wiki": "dev", "source_commit": "abc1234"})


@pytest.fixture
def wikis(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _enwiki_db(data / "wiki.db")
    _dev_db(data / "dev.db")
    monkeypatch.setattr(mcp_server, "DATA_DIR", data)
    monkeypatch.setattr(mcp_server, "DB_PATH", data / "wiki.db")
    return data


def test_default_wiki_is_enwiki(wikis):
    hits = mcp_server.search_wikipedia("chloroplasts")
    assert [h["title"] for h in hits] == ["Photosynthesis"]


def test_wiki_param_routes_to_custom(wikis):
    hits = mcp_server.search_wikipedia("vector tiles", wiki="dev")
    assert [h["title"] for h in hits] == ["carto"]
    # and enwiki does not know the custom article
    assert mcp_server.search_wikipedia("vector tiles") == []


def test_unknown_wiki_errors_with_known_list(wikis):
    (res,) = mcp_server.search_wikipedia("x", wiki="nope")
    assert "unknown wiki 'nope'" in res["error"]
    assert "enwiki" in res["error"] and "dev" in res["error"]


def test_get_article_includes_frontmatter_for_custom(wikis):
    art = mcp_server.get_article("carto", wiki="dev")
    assert art["title"] == "carto"
    assert art["frontmatter"]["objectives"][0]["id"] == "ga"


def test_get_article_enwiki_has_no_frontmatter(wikis):
    art = mcp_server.get_article("Photosynthesis")
    assert "frontmatter" not in art


def test_suggest_scoped_per_wiki(wikis):
    assert mcp_server.suggest("carto", wiki="dev") == ["carto"]
    assert mcp_server.suggest("carto") == []  # not in enwiki


def test_list_wikis(wikis):
    listed = {w["wiki"]: w for w in mcp_server.list_wikis()}
    assert set(listed) == {"enwiki", "dev"}
    assert listed["enwiki"]["source"].endswith(".zim")
    assert listed["dev"]["source"] == "abc1234"
    assert listed["dev"]["article_count"] == 1
