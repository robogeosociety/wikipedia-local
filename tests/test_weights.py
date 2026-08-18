"""Topic weights + hot cache — strict config, boosted ranking, serve-before-FTS,
and every degradation path (no sidecar, stale sidecar, stale manifest)."""

import json
import sqlite3

import pytest

import build_weights
import mcp_server
import warm_cache
import weights
import wikidb

ZIM = "wikipedia_en_all_nopic_2026-03.zim"


def _enwiki_db(path, articles):
    con = sqlite3.connect(path)
    wikidb.build_schema(con)
    con.executemany(
        "INSERT INTO articles(title, slug, text) VALUES (?,?,?)",
        [(t, t, text) for t, text in articles],
    )
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    con.executemany(
        "INSERT INTO meta(key, value) VALUES (?,?)",
        {"source_zim": ZIM, "article_count": str(len(articles))}.items(),
    )
    con.commit()
    con.close()


TOPICS = {
    "tirzepatide": weights.Topic(
        name="tirzepatide",
        titles=["Tirzepatide"],
        weight=5.0,
        cache=True,
        aliases=["mounjaro", "zepbound"],
    )
}


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A tiny enwiki where the *unweighted* bm25 winner for 'tirzepatide drug'
    is the decoy (the term appears twice), so a boost is observable."""
    d = tmp_path / "data"
    d.mkdir()
    _enwiki_db(
        d / "wiki.db",
        [
            ("Tirzepatide", "Tirzepatide is a drug for diabetes."),
            ("Decoy", "tirzepatide tirzepatide drug drug drug mentions here."),
        ],
    )
    monkeypatch.setattr(mcp_server, "DATA_DIR", d)
    monkeypatch.setattr(mcp_server, "DB_PATH", d / "wiki.db")
    monkeypatch.setattr(mcp_server, "TOPICS", TOPICS)
    monkeypatch.setattr(mcp_server, "_ROUTES", weights.alias_map(TOPICS))
    return d


def _build_sidecar(data):
    n = build_weights.build(data / "wiki.db", data / weights.WEIGHTS_DB_NAME, TOPICS)
    assert n == 1
    return data / weights.WEIGHTS_DB_NAME


# --- config loader -----------------------------------------------------------


def test_load_config_absent_is_neutral(tmp_path):
    assert weights.load_config(tmp_path / "nope.toml") == {}


def test_load_config_parses_repo_seed():
    topics = weights.load_config()
    assert topics["tirzepatide"].cache is True
    assert "mounjaro" in topics["tirzepatide"].aliases
    assert topics["diabetes"].weight == 3.0


@pytest.mark.parametrize(
    "body",
    [
        "[topics.x]\nweight = 2.0",  # missing titles
        '[topics.x]\ntitles = ["A"]\nweight = -1',  # non-positive weight
        '[topics.x]\ntitles = ["A"]\nbogus = 1',  # unknown key
        '[nottopics.x]\ntitles = ["A"]',  # unknown top-level table
        "",  # present but empty
    ],
)
def test_load_config_rejects_invalid(tmp_path, body):
    bad = tmp_path / "weights.toml"
    bad.write_text(body)
    with pytest.raises(weights.WeightsConfigError):
        weights.load_config(bad)


# --- ranking seam ------------------------------------------------------------


def test_unweighted_decoy_outranks(data):
    hits = mcp_server.search_wikipedia("tirzepatide drug")
    assert [h["title"] for h in hits] == ["Decoy", "Tirzepatide"]


def test_sidecar_boosts_configured_topic(data):
    _build_sidecar(data)
    hits = mcp_server.search_wikipedia("tirzepatide drug")
    assert [h["title"] for h in hits] == ["Tirzepatide", "Decoy"]


def test_stale_sidecar_is_ignored(data):
    db = _build_sidecar(data)
    con = sqlite3.connect(db)
    con.execute("UPDATE meta SET value='wikipedia_en_all_nopic_2020-01.zim'")
    con.commit()
    con.close()
    hits = mcp_server.search_wikipedia("tirzepatide drug")
    assert [h["title"] for h in hits] == ["Decoy", "Tirzepatide"]  # neutral fallback


def test_build_warns_on_unresolved_title(data, capsys):
    topics = {"ghost": weights.Topic(name="ghost", titles=["No Such Page"], weight=2.0)}
    n = build_weights.build(data / "wiki.db", data / weights.WEIGHTS_DB_NAME, topics)
    assert n == 0
    assert "no titles resolved" in capsys.readouterr().err


# --- hot cache ---------------------------------------------------------------


def test_warm_then_serve_alias_without_fts(data):
    _build_sidecar(data)
    assert warm_cache.warm(data, TOPICS) == 1
    art = mcp_server.get_article("mounjaro")
    assert art["title"] == "Tirzepatide"
    assert art["served_from"] == "hot-cache"
    hits = mcp_server.search_wikipedia("Tirzepatide")
    assert hits[0]["title"] == "Tirzepatide"
    assert hits[0]["served_from"] == "hot-cache"


def test_hot_cache_respects_max_chars(data):
    warm_cache.warm(data, TOPICS)
    art = mcp_server.get_article("Tirzepatide", max_chars=10)
    assert art["truncated"] is True
    assert len(art["text"]) == 10


def test_stale_manifest_falls_back_to_fts(data):
    warm_cache.warm(data, TOPICS)
    index = data / weights.HOT_DIR_NAME / "index.json"
    manifest = json.loads(index.read_text())
    manifest["source_zim"] = "wikipedia_en_all_nopic_2020-01.zim"
    index.write_text(json.dumps(manifest))
    art = mcp_server.get_article("Tirzepatide")
    assert art["title"] == "Tirzepatide"
    assert "served_from" not in art  # FTS path, not the stale cache


def test_unrouted_query_never_touches_hot_cache(data):
    warm_cache.warm(data, TOPICS)
    hits = mcp_server.search_wikipedia("drug mentions")
    assert hits and all("served_from" not in h for h in hits)
