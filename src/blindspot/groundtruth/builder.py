"""Resumable answer-key build (#18).

Every scenario (#16) names two specs: its local list and its global baseline. Many scenarios share
a spec, so each spec is fetched once (identical specs share one in-flight request) and stored under
its content key. After both of a scenario's specs are stored, the scenario's status row is written:
that is the checkpoint. A re-run with resume skips done scenarios and stored specs; anything
interrupted mid-way is simply fetched again (the Qloo cache makes that free).

Every call goes through QlooClient (cache, budgets, breaker, call log), purpose `groundtruth`.
The answer key is Qloo, never an LLM (hard rule 8).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..qloo.cache import cache_key, normalize_params
from ..qloo.client import QlooClient
from ..qloo.context import RequestContext
from ..qloo.errors import IdFormatError, QlooParamError
from ..qloo.params import check_ids, check_params
from ..scenarios.models import QuerySpec, Scenario
from ..scenarios.prompts import lint_prompts
from .normalize import aliases, normalize_name, place_forms
from .store import Store, dumps

# Coverage of one list against the answer-key depth (provisional thresholds from #13, for #24 too).
THIN_MIN, SPARSE_MIN = 10, 1
# Optional narrowing params a broader retry may drop. Never the location, filter.type or the
# persona signal: without those the list would answer a different question.
BROADEN_DROP = ("filter.exclude.entities", "bias.trends")
PERSONAS_DISTINCT_MAX_JACCARD = 0.5  # E15: below 0.5 the audience probe is viable (D37)
# Statuses. `fetched`: both runs stored, not yet checked (lint, conditions); get_answer_key refuses it.
# Final: done | thin | empty | excluded. `failed` is retried on the next run.
STORED_STATUSES = ("fetched", "done", "thin", "empty", "excluded")  # runs stored: never refetched
READABLE_STATUSES = ("done", "thin", "excluded")


class Stop(Exception):
    """The live-call cap was reached before a lookup that would be live."""


class SpecError(ValueError):
    """Some specs would send an unverified parameter or a non-id value; nothing was sent."""


def coverage(n: int, depth: int) -> str:
    if n >= depth:
        return "rich"
    if n >= THIN_MIN:
        return "thin"
    return "sparse" if n >= SPARSE_MIN else "empty"


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def _specs(scenarios: list[Scenario]) -> dict[str, QuerySpec]:
    return {s.local_spec.key: s.local_spec for s in scenarios} | {
        s.global_spec.key: s.global_spec for s in scenarios
    }


def validate_specs(scenarios: list[Scenario]) -> None:
    """Param audit for the whole exam before any call: every param VERIFIED, ids where ids belong.
    A config bug fails here, loudly and for every bad spec at once, never mid-build."""
    bad = []
    for key, spec in sorted(_specs(scenarios).items()):
        try:
            check_params("/v2/insights", spec.params, strict=True)
            check_ids(spec.params)
        except (QlooParamError, IdFormatError) as exc:
            bad.append(f"{key}: {exc}")
    if bad:
        raise SpecError(f"{len(bad)} spec(s) can't be sent; nothing was fetched:\n" + "\n".join(bad))


@dataclass
class Report:
    scenarios: int = 0
    specs: int = 0
    fetched: int = 0  # specs fetched this run (from Qloo or its cache)
    reused: int = 0  # distinct specs already stored before this run
    live_calls: int = 0
    broadened: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    lint_problems: int = 0
    stopped: str | None = None
    planned_live: int | None = None  # dry run only


class Builder:
    def __init__(
        self,
        client: QlooClient,
        store: Store,
        *,
        concurrency: int = 4,
        max_calls: int | None = None,
        location_words: Iterable[str] = (),
        ctx: RequestContext | None = None,
    ):
        self.client, self.store = client, store
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.max_calls = max_calls
        self.places = place_forms(location_words)  # normalized once, not per entity
        self.ctx = ctx or RequestContext.script("groundtruth", purpose="groundtruth")
        self.start = self._used()
        self.reserved = 0  # requests this builder started that weren't cache hits
        self.stopping: str | None = None
        self.inflight: dict[str, asyncio.Task] = {}
        self.report = Report()

    # ------------------------------------------------------------------ helpers
    def _used(self) -> int:
        return self.client.budget.total_used()

    def _cached(self, params: dict[str, Any]) -> bool:
        key = cache_key("GET", "/v2/insights", normalize_params(params))
        return self.client.cache.get(key) is not None or self.client.snapshot.get(key) is not None

    def _guard(self, params: dict[str, Any]) -> None:
        """Reserve a live request, or stop. Called with a concurrency slot held, right before the
        request. The count is this builder's own, taken here, so slot holders can't all pass at once
        on a count the client hasn't written yet. (Client retries of a started request can add up to
        qloo_max_attempts - 1 attempts each; they're reported in live_calls.)"""
        if self._cached(params):
            return
        if self.stopping:
            raise Stop(self.stopping)
        if self.max_calls is not None and max(self.reserved, self._used() - self.start) >= self.max_calls:
            self.stopping = f"stopped at {self.reserved} live requests (--max-calls {self.max_calls})"
            raise Stop(self.stopping)
        self.reserved += 1

    # ------------------------------------------------------------------ setup
    def register(self, scenarios: list[Scenario], *, resume: bool) -> list[Scenario]:
        """Store the exam and return the scenarios still to fetch. Scenarios no longer in the exam
        (a config or prompt change gives new content-hash ids) are dropped; their runs, keyed by
        spec, stay reusable."""
        if not resume:
            for t in ("ground_truth_runs", "ground_truth_items", "scenario_status", "scenarios"):
                self.store.execute(f"DELETE FROM {t}")  # noqa: S608  (fixed table names)
        ids = {s.id for s in scenarios}
        stale = [r["id"] for r in self.store.query("SELECT id FROM scenarios") if r["id"] not in ids]
        for sid in stale:
            self.store.execute("DELETE FROM scenarios WHERE id = ?", (sid,))
            self.store.execute("DELETE FROM scenario_status WHERE scenario_id = ?", (sid,))
        for s in scenarios:
            self.store.execute(
                "INSERT OR REPLACE INTO scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s.id, s.persona_id, s.city_id, s.domain_id, s.probe_type, s.local_spec.key,
                    s.global_spec.key, s.depth, s.pool, s.pair_id, dumps(s.tags), dumps(s.conditions),
                    dumps(s.model_dump(mode="json")),
                ),
            )  # fmt: skip
            self.store.execute(
                "INSERT OR IGNORE INTO scenario_status(scenario_id, status) VALUES (?, 'pending')", (s.id,)
            )
        stored = {r["scenario_id"]: r["status"] for r in self.store.query("SELECT * FROM scenario_status")}
        return [s for s in scenarios if stored.get(s.id) not in STORED_STATUSES]

    # ------------------------------------------------------------------ fetching
    async def spec(self, spec: QuerySpec, kind: str, depth: int) -> dict[str, Any]:
        """The stored run for a spec, fetching it first if needed. Shared specs share one task."""
        row = self.store.run(spec.key)
        if row is not None and row["coverage"] != "failed":
            return dict(row)
        task = self.inflight.get(spec.key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(spec, kind, depth))
            self.inflight[spec.key] = task
        return await task

    async def _ask(self, params: dict[str, Any]):
        async with self.sem:
            self._guard(params)  # with the slot held, right before the request
            return await self.client.insights(params, self.ctx)

    async def _fetch(self, spec: QuerySpec, kind: str, depth: int) -> dict[str, Any]:
        params = dict(spec.params)
        res = await self._ask(params)
        if not res.ok and res.outcome != "empty":
            return {"spec_key": spec.key, "coverage": "failed", "reason": res.reason or res.outcome}
        sent, broadened, reason = params, 0, ""
        excluded = set(params.get("filter.exclude.entities") or [])
        if res.count < depth:
            # Below threshold: the params passed the audit above; try once without the optional
            # narrowing params before recording the list as thin (AGENTS §10: empty 200 pitfall).
            broader = {k: v for k, v in params.items() if k not in BROADEN_DROP}
            reason = f"{res.count} of {depth}"
            if broader != params:
                res2 = await self._ask(broader)
                if res2.ok and res2.count > res.count:
                    res, sent, broadened = res2, broader, 1
                    reason += (
                        f"; broader retry (without {sorted(set(params) - set(broader))}) gave {res2.count}"
                    )
                else:
                    reason += "; broader retry didn't add results"
        # A broader retry may have dropped the seed exclusion: keep the persona's own seeds out (D40).
        ents = [e for e in res.entities if e.entity_id and e.entity_id not in excluded][: int(params["take"])]
        self._store(spec.key, kind, sent, res, ents, depth, broadened, reason)
        self.report.fetched += 1
        self.report.broadened += broadened
        return dict(self.store.run(spec.key))

    def _store(self, key, kind, sent, res, ents, depth, broadened, reason) -> None:
        stmts: list[tuple[str, tuple]] = [
            ("DELETE FROM ground_truth_items WHERE spec_key = ?", (key,)),
            (
                "INSERT OR REPLACE INTO ground_truth_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    kind,
                    dumps(sent),
                    res.served_from,
                    res.outcome,
                    coverage(len(ents), depth),
                    len(ents),
                    broadened,
                    reason,
                    res.fetched_at or time.time(),
                ),
            ),
        ]
        for rank, e in enumerate(ents, 1):
            aff = e.affinity
            stmts.append(
                (
                    "INSERT INTO ground_truth_items VALUES (?,?,?,?,?)",
                    (key, rank, e.entity_id, aff, int(aff is not None)),
                )
            )
            loc, places = e.location or {}, self.places
            ext = {k: v for k, v in (e.external or {}).items() if k == "google_place"} or None  # E10
            stmts.append(
                (
                    "INSERT OR REPLACE INTO entity_index VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        e.entity_id, e.name or "", normalize_name(e.name or "", places=places),
                        e.kind, dumps(aliases(e.name or "", places=places)),
                        dumps(ext) if ext else None, loc.get("lat"), loc.get("lon"), e.popularity,
                    ),
                )
            )  # fmt: skip
        self.store.transaction(stmts)

    # ------------------------------------------------------------------ scenarios
    async def scenario(self, s: Scenario) -> None:
        local, glob = await asyncio.gather(
            self.spec(s.local_spec, "local", s.depth), self.spec(s.global_spec, "global", s.depth)
        )
        if "failed" in (local["coverage"], glob["coverage"]):
            reason = local.get("reason") or glob.get("reason") or "fetch failed"
            self._set(s.id, "failed", reason)
        else:  # the checkpoint: runs stored, never fetched again; finalize() decides the status
            self._set(s.id, "fetched", "")

    def _set(self, scenario_id: str, status: str, reason: str) -> None:
        self.store.execute(
            "UPDATE scenario_status SET status = ?, reason = ? WHERE scenario_id = ?",
            (status, reason, scenario_id),
        )

    async def build(self, scenarios: list[Scenario], *, resume: bool = True) -> Report:
        validate_specs(scenarios)  # before anything is written or sent
        stored_before = {r["spec_key"] for r in self.store.query(
            "SELECT spec_key FROM ground_truth_runs WHERE coverage != 'failed'")}  # fmt: skip
        todo = self.register(scenarios, resume=resume)
        specs = _specs(scenarios)
        self.report.scenarios, self.report.specs = len(scenarios), len(specs)
        self.report.reused = len(set(specs) & stored_before) if resume else 0
        tasks = [asyncio.ensure_future(self.scenario(s)) for s in todo]
        try:
            # A Stop lets every request already sent finish and be stored (no call is paid twice);
            # only fetches that hadn't started yet stop.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # A scenario ends as soon as one of its fetches raises Stop, but its other fetch may
            # already be paid for and in flight: let every started fetch land before going on.
            while pending := [t for t in self.inflight.values() if not t.done()]:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:  # an outside cancel (a kill): nothing more to save
            for t in [*tasks, *self.inflight.values()]:
                t.cancel()
            await asyncio.gather(*tasks, *self.inflight.values(), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException) and not isinstance(r, Stop)]
        if errors:
            raise errors[0]
        self.report.stopped = self.stopping
        self.finalize(scenarios)  # always: a stopped build still checks what it finished
        self.report.statuses = self.store.status_counts()
        self.report.live_calls = self._used() - self.start
        return self.report

    # ------------------------------------------------------------------ after the build
    def _top_ids(self, key: str, depth: int) -> list[str]:
        return [r["qloo_id"] for r in self.store.items(key)[:depth]]

    def finalize(self, scenarios: list[Scenario]) -> None:
        """Turn `fetched` scenarios into a readable status, only after their checks have run:
        the prompt lint against the real answer-key names, then `conditions`. A pair whose partner
        isn't fetched yet stays `fetched` (unreadable) until a later run fetches it."""
        status = {r["scenario_id"]: r["status"] for r in self.store.query("SELECT * FROM scenario_status")}
        partners: dict[str, list[Scenario]] = {}
        for s in scenarios:
            for c in s.conditions:
                if c != "personas_distinct_in_city":
                    raise ValueError(
                        f"unknown condition {c!r}; the builder implements personas_distinct_in_city"
                    )
                partners.setdefault(s.pair_id or s.id, []).append(s)
        for s in scenarios:
            if status.get(s.id) != "fetched":
                continue
            problems = self.lint_one(s)
            if problems:
                self.report.lint_problems += problems
                self._exclude(s.id, f"prompt names {problems} answer-key entity name(s)")
                continue
            if s.conditions:
                pair = partners[s.pair_id or s.id]
                if len(pair) != 2 or any(status.get(m.id) not in STORED_STATUSES for m in pair):
                    self._set(
                        s.id, "fetched", "waiting for its pair to be fetched (personas_distinct_in_city)"
                    )
                    continue
                a, b = pair
                j = jaccard(
                    self._top_ids(a.local_spec.key, a.depth), self._top_ids(b.local_spec.key, b.depth)
                )
                if j > PERSONAS_DISTINCT_MAX_JACCARD:
                    limit = PERSONAS_DISTINCT_MAX_JACCARD
                    self._exclude(
                        s.id, f"personas_distinct_in_city failed: top-{a.depth} Jaccard {j:.2f} > {limit}"
                    )
                    continue
            self._disposition(s)

    def _disposition(self, s: Scenario) -> None:
        local, glob = self.store.run(s.local_spec.key), self.store.run(s.global_spec.key)
        cov = local["coverage"]
        if cov == "empty":  # never scored by accident: get_answer_key refuses it
            self._set(s.id, "empty", f"local empty: {local['reason']}")
        elif cov != "rich":
            self._set(s.id, "thin", f"local {cov}: {local['reason']}")
        else:
            self._set(s.id, "done", f"global {glob['coverage']}" if glob["coverage"] != "rich" else "")

    def lint_one(self, s: Scenario) -> int:
        """No prompt may contain an answer-key entity name (#16): check against the real names."""
        names = [
            r["name"]
            for r in self.store.query(
                "SELECT e.name FROM ground_truth_items i JOIN entity_index e ON e.qloo_id = i.qloo_id "
                "WHERE i.spec_key = ? AND i.rank <= ?",
                (s.local_spec.key, s.depth),
            )
        ]
        return len(lint_prompts([v.text for v in s.prompt_variants], names))

    def _exclude(self, scenario_id: str, why: str) -> None:
        self._set(scenario_id, "excluded", why)
        self.report.excluded.append(scenario_id)

    # ------------------------------------------------------------------ dry run
    def plan(self, scenarios: list[Scenario], *, resume: bool = True) -> Report:
        """Specs still to fetch, and how many would be live (not in the Qloo cache or snapshot).
        Writes nothing and makes no calls."""
        validate_specs(scenarios)
        done_keys = set()
        if resume:
            rows = self.store.query("SELECT spec_key FROM ground_truth_runs WHERE coverage != 'failed'")
            done_keys = {r["spec_key"] for r in rows}
        specs = {s.local_spec.key: s.local_spec for s in scenarios} | {
            s.global_spec.key: s.global_spec for s in scenarios
        }
        todo = [sp for k, sp in specs.items() if k not in done_keys]
        self.report.scenarios = len(scenarios)
        self.report.specs = len(specs)
        self.report.reused = len(specs) - len(todo)
        self.report.planned_live = sum(not self._cached(dict(sp.params)) for sp in todo)
        return self.report
