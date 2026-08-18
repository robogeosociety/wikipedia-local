#!/usr/bin/env python3
"""Pre-render the hot set: every `cache = true` topic in weights.toml becomes
data/hot/<topic>.json (its ranked search hits + full article payloads), plus a
data/hot/index.json manifest carrying `source_zim` and the route map.

The server serves these files for exact topic/title/alias queries without
touching FTS; a manifest whose source_zim no longer matches the live wiki.db
is ignored (stale cache degrades to the FTS path, never to a wrong answer).
refresh.sh reruns this after build_weights.py on every monthly swap.

Run:  uv run --project . python scripts/warm_cache.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_server  # noqa: E402
import weights  # noqa: E402
from wikidb import connect_immutable  # noqa: E402


def warm(data_dir: Path, topics: dict[str, weights.Topic] | None = None) -> int:
    """Render every cached topic into data_dir/hot/. Returns topics written."""
    if topics is None:
        topics = weights.load_config()
    cached = {name: t for name, t in topics.items() if t.cache}
    hot = data_dir / weights.HOT_DIR_NAME
    hot.mkdir(parents=True, exist_ok=True)

    wiki_db = mcp_server.DB_PATH
    con = connect_immutable(wiki_db)
    try:
        source_zim = (
            con.execute("SELECT value FROM meta WHERE key='source_zim'").fetchone()
            or [""]
        )[0]
    finally:
        con.close()

    # Render through the live (weighted) FTS path, never through the hot cache.
    was_enabled, mcp_server.HOT_ENABLED = mcp_server.HOT_ENABLED, False
    manifest_topics: dict[str, dict] = {}
    try:
        for topic in cached.values():
            articles = {}
            for title in topic.titles:
                art = mcp_server.get_article(title)
                if "error" in art:
                    weights.warn(
                        f"topics.{topic.name}: skipping unrendered title {title!r}"
                    )
                    continue
                articles[weights.normalize(title)] = art
            payload = {
                "topic": topic.name,
                "search": mcp_server.search_wikipedia(topic.name),
                "articles": articles,
                "primary": weights.normalize(topic.titles[0]),
            }
            (hot / f"{topic.name}.json").write_text(json.dumps(payload))
            manifest_topics[topic.name] = {
                "file": f"{topic.name}.json",
                "routes": sorted(
                    {
                        weights.normalize(t)
                        for t in [topic.name, *topic.titles, *topic.aliases]
                    }
                ),
            }
    finally:
        mcp_server.HOT_ENABLED = was_enabled

    manifest = {
        "source_zim": source_zim,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topics": manifest_topics,
    }
    (hot / "index.json").write_text(json.dumps(manifest, indent=2))
    return len(manifest_topics)


def main() -> None:
    topics = weights.load_config()
    if not any(t.cache for t in topics.values()):
        weights.warn("no cache=true topics in weights.toml — nothing to warm")
        return
    n = warm(mcp_server.DATA_DIR, topics)
    print(f"warmed {n} topics into {mcp_server.DATA_DIR / weights.HOT_DIR_NAME}")


if __name__ == "__main__":
    main()
