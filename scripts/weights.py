"""Strict loader for weights.toml — the reviewed topic-boost + hot-cache policy.

Mirrors the vault-rag loader contract: a present-but-invalid config fails
loudly (never degrades to "no weights quietly"); an absent config is the one
legitimate neutral state and is reported once on stderr by callers that care.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "weights.toml"
WEIGHTS_DB_NAME = "weights.db"
HOT_DIR_NAME = "hot"

_TOPIC_KEYS = {"titles", "weight", "cache", "aliases"}


class WeightsConfigError(ValueError):
    """weights.toml is present but invalid."""


@dataclass
class Topic:
    name: str
    titles: list[str]
    weight: float = 1.0
    cache: bool = False
    aliases: list[str] = field(default_factory=list)


def normalize(query: str) -> str:
    """Casefold + collapse punctuation/whitespace, for topic/alias/title match."""
    cleaned = "".join(c if c.isalnum() else " " for c in query.casefold())
    return " ".join(cleaned.split())


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Topic]:
    """Parse weights.toml → {topic_name: Topic}. Empty dict if the file is absent."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    unknown_top = set(raw) - {"topics"}
    if unknown_top:
        raise WeightsConfigError(
            f"{path}: unknown top-level keys {sorted(unknown_top)}"
        )
    topics_raw = raw.get("topics", {})
    if not isinstance(topics_raw, dict) or not topics_raw:
        raise WeightsConfigError(f"{path}: [topics.<name>] tables are required")

    topics: dict[str, Topic] = {}
    for name, spec in topics_raw.items():
        if not isinstance(spec, dict):
            raise WeightsConfigError(f"{path}: topics.{name} must be a table")
        unknown = set(spec) - _TOPIC_KEYS
        if unknown:
            raise WeightsConfigError(
                f"{path}: topics.{name}: unknown keys {sorted(unknown)}"
            )
        titles = spec.get("titles")
        if (
            not isinstance(titles, list)
            or not titles
            or not all(isinstance(t, str) and t.strip() for t in titles)
        ):
            raise WeightsConfigError(
                f"{path}: topics.{name}.titles must be a non-empty list of strings"
            )
        weight = spec.get("weight", 1.0)
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or weight <= 0
        ):
            raise WeightsConfigError(
                f"{path}: topics.{name}.weight must be a positive number"
            )
        cache = spec.get("cache", False)
        if not isinstance(cache, bool):
            raise WeightsConfigError(f"{path}: topics.{name}.cache must be a boolean")
        aliases = spec.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(a, str) and a.strip() for a in aliases
        ):
            raise WeightsConfigError(
                f"{path}: topics.{name}.aliases must be a list of strings"
            )
        topics[name] = Topic(
            name=name,
            titles=list(titles),
            weight=float(weight),
            cache=cache,
            aliases=list(aliases),
        )
    return topics


def alias_map(topics: dict[str, Topic]) -> dict[str, str]:
    """Normalized query string → topic name, for topic names, titles, and aliases."""
    routes: dict[str, str] = {}
    for topic in topics.values():
        for term in [topic.name, *topic.titles, *topic.aliases]:
            routes[normalize(term)] = topic.name
    return routes


def warn(msg: str) -> None:
    print(f"weights: {msg}", file=sys.stderr)
