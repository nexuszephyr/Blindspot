"""The one read path for answer keys (#18): `get_answer_key(scenario_id)`. The entity index has a
small read and write API here too, for the resolver (#29): `find_entities`, `list_entities`,
`entities_by_id` and `add_search_hits`.

Later phases never query Qloo for ground truth themselves, and never read the store's tables
directly (a test checks nothing outside this package imports `groundtruth.store`).

Provenance (hard rule 5): an answer key read from the store is stored data, so it is served as
`cache`, or as `snapshot` when the snapshot supplied it; never `live`, even if it was fetched live.
`fetched_from` and `fetched_at` say how and when it was originally fetched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..config import Settings
from ..qloo.models import Entity
from .builder import READABLE_STATUSES
from .normalize import Places, aliases, normalize_name
from .store import Store, dumps

ServedFrom = Literal["cache", "snapshot"]


class KeyItem(BaseModel):
    rank: int
    qloo_id: str
    name: str
    norm_name: str
    type: str | None = None
    affinity: float | None = None


class RankedList(BaseModel):
    kind: Literal["local", "global", "city"]
    items: list[KeyItem]  # the answer key: the top `depth`
    pool: list[str]  # ids of the whole membership pool (top `pool`), for picks outside the key
    served_from: ServedFrom
    fetched_from: str  # how the builder got it: live | cache | snapshot
    fetched_at: float | None
    coverage: str  # rich | thin | sparse | empty
    broadened: bool
    params: dict  # what was sent (ids only)


class AnswerKey(BaseModel):
    scenario_id: str
    status: str  # done | thin | excluded. pending, fetched (unchecked), failed and empty can't be read
    reason: str
    depth: int
    local: RankedList
    global_: RankedList  # the baseline: `global_.kind` is "global" or "city" (D43)
    served_from: ServedFrom  # the local list's
    baseline: Literal["global", "city"]  # what the famousness baseline is (D43)
    coverage: str  # the local list's


class NotBuilt(KeyError):
    """The scenario is unknown, or its answer key hasn't been built (or failed)."""


def _served(fetched_from: str) -> ServedFrom:
    return "snapshot" if fetched_from == "snapshot" else "cache"


def _ranked(store: Store, key: str, depth: int) -> RankedList:
    run = store.run(key)
    rows = store.query(
        "SELECT i.rank, i.qloo_id, i.affinity, e.name, e.norm_name, e.type FROM ground_truth_items i "
        "LEFT JOIN entity_index e ON e.qloo_id = i.qloo_id WHERE i.spec_key = ? ORDER BY i.rank",
        (key,),
    )
    items = [
        KeyItem(
            rank=r["rank"], qloo_id=r["qloo_id"], name=r["name"] or "", norm_name=r["norm_name"] or "",
            type=r["type"], affinity=r["affinity"],
        )
        for r in rows[:depth]
    ]  # fmt: skip
    return RankedList(
        kind=run["kind"],
        items=items,
        pool=[r["qloo_id"] for r in rows],
        served_from=_served(run["served_from"]),
        fetched_from=run["served_from"],
        fetched_at=run["fetched_at"],
        coverage=run["coverage"],
        broadened=bool(run["broadened"]),
        params=json.loads(run["params_json"]),
    )


def get_answer_key(scenario_id: str, store: Store | None = None) -> AnswerKey:
    """The stored answer key for a scenario. Raises NotBuilt if there isn't one."""
    own = store is None
    store = store or open_store()
    try:
        rows = store.query(
            "SELECT s.local_key, s.global_key, s.depth, t.status, t.reason FROM scenarios s "
            "JOIN scenario_status t ON t.scenario_id = s.id WHERE s.id = ?",
            (scenario_id,),
        )
        if not rows:
            raise NotBuilt(f"unknown scenario {scenario_id!r}; run `blindspot groundtruth build`")
        r = rows[0]
        if r["status"] not in READABLE_STATUSES or store.run(r["local_key"]) is None:
            raise NotBuilt(f"{scenario_id}: answer key not built ({r['status']}: {r['reason'] or 'pending'})")
        local = _ranked(store, r["local_key"], r["depth"])
        glob = _ranked(store, r["global_key"], r["depth"])
        return AnswerKey(
            scenario_id=scenario_id, status=r["status"], reason=r["reason"], depth=r["depth"], local=local,
            global_=glob, served_from=local.served_from, coverage=local.coverage, baseline=glob.kind,
        )  # fmt: skip
    finally:
        if own:
            store.close()


# --------------------------------------------------------------------------- entity index (#29)
class IndexEntity(BaseModel):
    """One `entity_index` row, for the resolver. Stored data: served as `cache`, never `live`."""

    qloo_id: str
    name: str
    norm_name: str
    type: str | None = None
    aliases: list[str]
    external: dict | None = None
    lat: float | None = None
    lon: float | None = None


_INDEX_COLS = "e.qloo_id, e.name, e.norm_name, e.type, e.aliases_json, e.external_json, e.lat, e.lon"


def _entity(r) -> IndexEntity:
    return IndexEntity(
        qloo_id=r["qloo_id"], name=r["name"], norm_name=r["norm_name"], type=r["type"],
        aliases=json.loads(r["aliases_json"] or "[]"),
        external=json.loads(r["external_json"]) if r["external_json"] else None, lat=r["lat"], lon=r["lon"],
    )  # fmt: skip


def _type_clause(types: list[str] | None) -> tuple[str, list[str]]:
    if not types:
        return "", []
    return f" AND e.type IN ({','.join('?' * len(types))})", list(types)


def _with_store(store: Store | None, fn):
    own = store is None
    store = store or open_store()
    try:
        return fn(store)
    finally:
        if own:
            store.close()


def find_entities(
    forms: list[str], *, types: list[str] | None = None, store: Store | None = None
) -> list[IndexEntity]:
    """Index entries whose normalized name or one of whose aliases is one of `forms`, optionally only
    of these Qloo types. Sorted by id, so the order never depends on insert order."""
    forms = sorted({f for f in forms if f})
    if not forms:
        return []
    marks = ",".join("?" * len(forms))
    tsql, targs = _type_clause(types)
    sql = (
        f"SELECT DISTINCT {_INDEX_COLS} FROM entity_index e LEFT JOIN json_each(e.aliases_json) a "  # noqa: S608
        f"WHERE (e.norm_name IN ({marks}) OR a.value IN ({marks})){tsql} ORDER BY e.qloo_id"
    )
    return _with_store(store, lambda s: [_entity(r) for r in s.query(sql, [*forms, *forms, *targs])])


def list_entities(*, types: list[str] | None = None, store: Store | None = None) -> list[IndexEntity]:
    """Every index entry (of these types), for fuzzy matching."""
    tsql, targs = _type_clause(types)
    sql = f"SELECT {_INDEX_COLS} FROM entity_index e WHERE 1=1{tsql} ORDER BY e.qloo_id"  # noqa: S608
    return _with_store(store, lambda s: [_entity(r) for r in s.query(sql, targs)])


def entities_by_id(ids: list[str], *, store: Store | None = None) -> list[IndexEntity]:
    """Index entries for these ids (unknown ids are skipped)."""
    ids = sorted(set(ids))
    if not ids:
        return []
    sql = (
        f"SELECT {_INDEX_COLS} FROM entity_index e WHERE e.qloo_id IN ({','.join('?' * len(ids))}) "  # noqa: S608
        "ORDER BY e.qloo_id"
    )
    return _with_store(store, lambda s: [_entity(r) for r in s.query(sql, ids)])


def add_search_hits(entities: list[Entity], *, places: Places = (), store: Store | None = None) -> int:
    """Add `/search` hits to `entity_index`, so later runs resolve them offline. An entity already in
    the index keeps its row: the answer-key build's insights row carries scores, coordinates and
    external ids that a search hit may lack. Returns how many rows were added."""
    stmts: list[tuple[str, tuple]] = []
    for e in entities:
        if not e.entity_id or not e.name:
            continue
        loc = e.location or {}
        ext = {k: v for k, v in (e.external or {}).items() if k == "google_place"} or None  # E10
        stmts.append(
            (
                "INSERT OR IGNORE INTO entity_index VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    e.entity_id, e.name, normalize_name(e.name, places=places), e.kind,
                    dumps(aliases(e.name, places=places)), dumps(ext) if ext else None,
                    _float(loc.get("lat")), _float(loc.get("lon")), e.popularity,
                ),
            )
        )  # fmt: skip

    def write(s: Store) -> int:
        before = s.table_counts()["entity_index"]
        s.transaction(stmts)
        return s.table_counts()["entity_index"] - before

    return _with_store(store, write) if stmts else 0


def _float(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def default_store_path(config_dir: Path | str | None = None) -> Path:
    """Where the answer keys live. A relative GROUNDTRUTH_PATH is taken from the repo root (the parent
    of the config dir), the same for the build CLI and for every reader, whatever the working
    directory."""
    s = Settings()
    if s.groundtruth_path.is_absolute():
        return s.groundtruth_path
    return Path(config_dir or s.config_dir).resolve().parent / s.groundtruth_path


def open_store(path: Path | str | None = None, *, config_dir: Path | str | None = None) -> Store:
    return Store(path or default_store_path(config_dir))
