"""Snapshot: a minimal, named export of cached responses used to serve demo queries
without the network, and to re-seed an empty cache on hosts with ephemeral disks.

Qloo's hackathon guide says not to store Qloo response data in a public repository, so a
real snapshot lives only in data/snapshot/ (gitignored) or in the host's secret storage.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .cache import ResponseCache

log = logging.getLogger("blindspot.snapshot")
FORMAT = "blindspot-snapshot/1"
NOTICE = (
    "Contains data derived from the Qloo API. Do not commit to a public repository "
    "(Qloo hackathon developer guide: 'Don't store Qloo response data in a public repository')."
)

# Fields kept per entity. Everything else (akas, long descriptions, images...) is dropped.
_ENTITY_KEYS = ("entity_id", "id", "name", "type", "types", "subtype", "popularity", "query")
_PROPERTY_KEYS = ("geocode", "address", "short_description", "business_rating", "price_level")
_TAG_KEYS = ("id", "tag_id", "name", "type")


def _minimize_entity(e: Any) -> Any:
    if not isinstance(e, dict):
        return e
    out = {k: e[k] for k in _ENTITY_KEYS if k in e}
    if isinstance(e.get("location"), dict):
        out["location"] = {k: e["location"][k] for k in ("lat", "lon") if k in e["location"]}
    props = e.get("properties")
    if isinstance(props, dict):
        kept = {k: props[k] for k in _PROPERTY_KEYS if k in props}
        if kept:
            out["properties"] = kept
    if isinstance(e.get("tags"), list):
        out["tags"] = [
            {k: t[k] for k in _TAG_KEYS if k in t} if isinstance(t, dict) else t for t in e["tags"][:5]
        ]
    return out


def minimize_body(body: Any) -> Any:
    """Keep ids, names, types and scores; drop verbose fields."""
    if not isinstance(body, dict):
        return body
    out: dict[str, Any] = {k: body[k] for k in ("success",) if k in body}
    res = body.get("results")
    if isinstance(res, list):
        out["results"] = [_minimize_entity(e) for e in res]
    elif isinstance(res, dict):
        out["results"] = {
            k: [_minimize_entity(e) for e in v] if isinstance(v, list) else v
            for k, v in res.items()
            if k in ("entities", "tags", "audiences", "results") or not isinstance(v, (dict, list))
        }
    if "query" in body:
        out["query"] = body["query"]
    return out


class Snapshot:
    def __init__(self, entries: dict[str, dict[str, Any]] | None = None, path: Path | None = None):
        self.entries = entries or {}
        self.path = path

    @property
    def loaded(self) -> bool:
        return bool(self.entries)

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    @classmethod
    def load(cls, path: Path | str | None) -> Snapshot:
        if not path:
            return cls()
        p = Path(path)
        if not p.is_file():
            return cls(path=p)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if doc.get("format") != FORMAT:
                log.warning("snapshot %s has unknown format; ignoring", p)
                return cls(path=p)
            entries = {e["key"]: e for e in doc.get("entries", []) if "key" in e and "body" in e}
            return cls(entries, path=p)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("could not read snapshot %s: %s", p, type(exc).__name__)
            return cls(path=p)

    def seed_cache(self, cache: ResponseCache, *, only_if_empty: bool = True) -> int:
        if not self.entries or (only_if_empty and cache.size() > 0):
            return 0
        for e in self.entries.values():
            cache.put(
                e["key"],
                e.get("method", "GET"),
                e.get("path", "/"),
                e.get("params", {}),
                int(e.get("status", 200)),
                e["body"],
                fetched_at=float(e.get("fetched_at", time.time())),
                source="snapshot",
            )
        return len(self.entries)


def build_snapshot(entries: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "name": name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notice": NOTICE,
        "entries": [
            {
                "key": e["key"],
                "label": e.get("label"),
                "method": e["method"],
                "path": e["path"],
                "params": e["params"],
                "status": e["status"],
                "fetched_at": e["fetched_at"],
                "body": minimize_body(e["body"]),
            }
            for e in entries
        ],
    }
