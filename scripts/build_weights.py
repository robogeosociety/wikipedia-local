#!/usr/bin/env python3
"""Build data/weights.db — the disposable ranking sidecar for enwiki.

weights.toml keys topics by *title* (durable across rebuilds); this script
resolves each title to the current `articles.id` in the live wiki.db and writes
`(article_id, topic, weight)` rows. Article ids change with every monthly ZIM
rebuild, so refresh.sh reruns this after the atomic swap. The sidecar is
derived and disposable by design — delete it and queries fall back to neutral
bm25 ranking.

Run:  uv run --project . python scripts/build_weights.py [wiki.db] [weights.db]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weights  # noqa: E402
from wikidb import connect_immutable  # noqa: E402

DATA_DIR = weights.REPO / "data"


def build(
    wiki_db: Path, weights_db: Path, topics: dict[str, weights.Topic] | None = None
) -> int:
    """Resolve config titles against wiki_db, write the sidecar atomically.

    Returns the number of resolved (article_id, topic) rows.
    """
    if topics is None:
        topics = weights.load_config()
    src = connect_immutable(wiki_db)
    try:
        source_zim = (
            src.execute("SELECT value FROM meta WHERE key='source_zim'").fetchone()
            or [""]
        )[0]
        rows: list[tuple[int, str, float]] = []
        for topic in topics.values():
            resolved = 0
            for title in topic.titles:
                hit = src.execute(
                    "SELECT id FROM articles WHERE title=? COLLATE NOCASE LIMIT 1",
                    (title,),
                ).fetchone()
                if hit is None:
                    weights.warn(
                        f"topics.{topic.name}: title {title!r} not found in {wiki_db}"
                    )
                    continue
                rows.append((hit["id"], topic.name, topic.weight))
                resolved += 1
            if topic.titles and not resolved:
                weights.warn(
                    f"topics.{topic.name}: no titles resolved — topic has no effect"
                )
    finally:
        src.close()

    tmp = weights_db.with_suffix(weights_db.suffix + ".new")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(
            """
            CREATE TABLE weights (
                article_id INTEGER PRIMARY KEY,
                topic      TEXT NOT NULL,
                weight     REAL NOT NULL
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        con.executemany("INSERT OR REPLACE INTO weights VALUES (?,?,?)", rows)
        con.execute("INSERT INTO meta VALUES ('source_zim', ?)", (source_zim,))
        con.commit()
    finally:
        con.close()
    tmp.replace(weights_db)
    return len(rows)


def main() -> None:
    wiki_db = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "wiki.db"
    weights_db = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else DATA_DIR / weights.WEIGHTS_DB_NAME
    )
    topics = weights.load_config()
    if not topics:
        weights.warn(
            f"no {weights.DEFAULT_CONFIG.name} — skipping sidecar build (neutral ranking)"
        )
        return
    n = build(wiki_db, weights_db, topics)
    print(f"built {weights_db}: {n} weighted articles across {len(topics)} topics")


if __name__ == "__main__":
    main()
