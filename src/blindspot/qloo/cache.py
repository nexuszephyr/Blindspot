"""Response cache keyed on a normalized request, so equivalent queries share one entry."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .db import Database
from .models import extract_items

EMPTY_TTL_S = 3600.0

Params = Mapping[str, Any]


def _norm_value(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple, set)):
        items = [_norm_value(x) for x in v]
        items = [x for x in items if x]
        return ",".join(sorted(items) if isinstance(v, set) else items) or None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if "," in s:
        s = ",".join(p.strip() for p in s.split(",") if p.strip())
    return s or None


def normalize_params(params: Params | None) -> dict[str, str]:
    """Drop empty values, stringify consistently, sort keys.

    Lists and comma-separated strings normalize to the same form. Order inside a list is
    preserved because Qloo may treat it as meaningful.
    """
    out: dict[str, str] = {}
    for k, v in (params or {}).items():
        nv = _norm_value(v)
        if nv is not None:
            out[str(k).strip()] = nv
    return dict(sorted(out.items()))


def cache_key(method: str, path: str, params: Params | None) -> str:
    norm = normalize_params(params)
    raw = json.dumps([method.upper(), "/" + path.strip("/"), norm], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class CacheEntry:
    key: str
    status: int
    body: Any
    fetched_at: float
    source: str  # "live" or "snapshot" (seeded from the snapshot at startup)


class ResponseCache:
    def __init__(self, db: Database, ttl_days: float = 7.0):
        self.db = db
        self.ttl_s = ttl_days * 86400

    def get(self, key: str, *, now: float | None = None, ignore_ttl: bool = False) -> CacheEntry | None:
        rows = self.db.query("SELECT key,status,body,fetched_at,source FROM cache WHERE key=?", (key,))
        if not rows:
            return None
        r = rows[0]
        now = time.time() if now is None else now
        body = json.loads(r["body"])
        # Snapshot-seeded rows don't expire: they are the offline demo data. Empty 200s get a short
        # TTL so a bad-param empty result is not pinned for days (audit F9).
        ttl = self.ttl_s if extract_items(body) else min(self.ttl_s, EMPTY_TTL_S)
        if r["source"] != "snapshot" and not ignore_ttl and now - r["fetched_at"] > ttl:
            return None
        return CacheEntry(r["key"], r["status"], body, r["fetched_at"], r["source"])

    def put(
        self,
        key: str,
        method: str,
        path: str,
        params: Params | None,
        status: int,
        body: Any,
        *,
        fetched_at: float | None = None,
        source: str = "live",
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cache(key,method,path,params_json,status,body,fetched_at,source)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                key,
                method.upper(),
                "/" + path.strip("/"),
                json.dumps(normalize_params(params)),
                status,
                json.dumps(body),
                time.time() if fetched_at is None else fetched_at,
                source,
            ),
        )

    def size(self) -> int:
        return int(self.db.query("SELECT COUNT(*) AS n FROM cache")[0]["n"])

    def entries(self, keys: list[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT key,method,path,params_json,status,body,fetched_at,source FROM cache"
        rows = self.db.query(sql)
        out = []
        for r in rows:
            if keys is not None and r["key"] not in keys:
                continue
            out.append(
                {
                    "key": r["key"],
                    "method": r["method"],
                    "path": r["path"],
                    "params": json.loads(r["params_json"]),
                    "status": r["status"],
                    "body": json.loads(r["body"]),
                    "fetched_at": r["fetched_at"],
                    "source": r["source"],
                }
            )
        return out
