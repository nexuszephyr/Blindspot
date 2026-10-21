"""The one read path for answer keys (#18): `get_answer_key(scenario_id)`.

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
from .builder import READABLE_STATUSES
from .store import Store

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
