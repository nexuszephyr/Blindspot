"""Entity resolver (#29): an agent's free-text picks -> Qloo entities, with a status, a method and a
confidence for each (architecture Section 8.2, decisions D47).

Order, cheapest first:
1. **Index, exact or alias.** The normalized pick (`groundtruth.normalize`) against `entity_index`
   names and aliases, limited to the domain's `search_types`.
2. **Index, fuzzy.** `match.token_set_ratio` at or above the provisional `FUZZY_THRESHOLD`.
3. **`/search`**, through `QlooClient` with the caller's `RequestContext`, so the per-run budget,
   deadline, cache and breaker apply; `max_live_searches` caps this resolver's own live searches on
   top. Only `query`, `types` and `take` are sent: there's no verified city hint, and Qloo silently
   ignores unknown params. Hits go into `entity_index`, so a re-run resolves them offline.

Choosing between several entities: `/search` gives no confidence and same-name films are common
(qloo_api_reality, #15). A tie is broken only by the answer key, then the pool, then an exact
(not alias) name, then, for a domain localized by a filter (places), distance from the city. The
city's centre is the median of the answer-key pool's coordinates, so no coordinates live in config
or code. Anything still tied is `unresolved` with reason `ambiguous`: never a guess.

A place whose only same-name entities are far from the city is `unresolved` (`elsewhere`): it exists,
so it isn't a phantom. `phantom_candidate` needs a clean search (ok or empty) with no name match and
no such existence evidence. `unresolved` is never a phantom.

Every number below is PROVISIONAL (D47): retune on the V8 name round-trip data from #21.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..catalog import Catalog, Domain
from ..groundtruth import (
    AnswerKey,
    IndexEntity,
    add_search_hits,
    entities_by_id,
    find_entities,
    list_entities,
)
from ..groundtruth.normalize import aliases, normalize_name, place_forms
from ..qloo.client import QlooClient
from ..qloo.context import RequestContext
from ..qloo.models import Entity, QlooResult
from .match import FUZZY_THRESHOLD, best_similarity, fuzzy_eligible
from .models import Method, Reason, Resolution, Resolved, ServedFrom
from .report import script_of

CONFIDENCE: dict[str, float] = {  # PROVISIONAL (D47)
    "index_exact": 1.0,
    "index_alias": 0.95,
    "index_fuzzy": 0.9,  # times the similarity
    "search_exact": 0.9,
    "search_alias": 0.85,
    "search_fuzzy": 0.8,  # times the similarity
}
TIE_BREAK_FACTOR = 0.9  # a match picked from several candidates is a little less certain
SEARCH_TAKE = 5  # hits read per search; #15 saw same-name films within the top 5
CITY_RADIUS_KM = 60.0  # PROVISIONAL (D47): a place farther than this from the city's centre is elsewhere
MIN_CENTRE_POINTS = 3  # pool entities with coordinates needed to place the city
MAX_TEXT_CHARS = 200  # agent text beyond this is cut before matching (it's untrusted)


@dataclass(frozen=True)
class _Cand:
    qloo_id: str
    name: str
    method: Method
    similarity: float = 1.0
    lat: float | None = None
    lon: float | None = None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _coord(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class _Scope:
    """What one call resolves against: the domain, the answer key's ranks, the city's centre."""

    domain: Domain
    rank: dict[str, int]  # id -> 1-based rank in the local list (key and pool)
    depth: int
    centre: tuple[float, float] | None

    def far(self, c: _Cand) -> bool:
        if self.centre is None or c.lat is None or c.lon is None or c.qloo_id in self.rank:
            return False  # anything in the local list is in the city by construction
        return _km(c.lat, c.lon, *self.centre) > CITY_RADIUS_KM


@dataclass
class _Outcome:
    pick: _Cand | None = None
    reason: Reason = ""
    tie_broken: bool = False


class Resolver:
    """Resolves picks for one audit. `store` is the answer-key store (`groundtruth.open_store()`);
    `client` may be None for an offline resolver that never searches."""

    def __init__(
        self,
        catalog: Catalog,
        store: Any,
        *,
        client: QlooClient | None = None,
        max_live_searches: int | None = None,
        search_take: int = SEARCH_TAKE,
    ):
        self.catalog, self.store, self.client = catalog, store, client
        # The same location words the answer-key build normalizes with, so forms compare equal.
        self.places = place_forms(w for c in catalog.cities for w in (c.display_name, c.location))
        self.max_live_searches = max_live_searches
        self.search_take = search_take
        self.searches = 0
        self.live_searches = 0
        self._index: dict[tuple[str, ...], list[IndexEntity]] = {}

    # ------------------------------------------------------------------ public
    async def resolve(
        self,
        texts: Iterable[str],
        *,
        domain_id: str,
        city_id: str,
        key: AnswerKey | None,
        ctx: RequestContext,
        scenario_id: str | None = None,
    ) -> Resolved:
        """Resolve an agent's recommendations, in order. One item per text."""
        self.catalog.city(city_id)  # an unknown city is a caller bug: fail loudly
        scope = self._scope(self.catalog.domain(domain_id), key)
        searches, live = self.searches, self.live_searches
        done: dict[str, Resolution] = {}  # the same name twice in one list is resolved once
        items = []
        for i, raw in enumerate(texts):
            text = (raw or "")[:MAX_TEXT_CHARS]
            norm = normalize_name(text, places=self.places)
            if norm in done:
                same = {"position": i, "text": text, "script": script_of(text)}
                items.append(done[norm].model_copy(update=same))
                continue
            r = await self._one(i, text, norm, scope, ctx)
            done[norm] = r
            items.append(r)
        return Resolved(
            domain_id=domain_id, city_id=city_id, items=items,
            scenario_id=scenario_id or (key.scenario_id if key else None),
            searches=self.searches - searches, live_searches=self.live_searches - live,
        )  # fmt: skip

    # ------------------------------------------------------------------ setup
    def _scope(self, domain: Domain, key: AnswerKey | None) -> _Scope:
        rank = {qid: i for i, qid in enumerate(key.local.pool, 1)} if key else {}
        centre = None
        if key and domain.location_param == "filter":  # results restricted to the city (places)
            pts = [
                (e.lat, e.lon)
                for e in entities_by_id(key.local.pool, store=self.store)
                if e.lat is not None and e.lon is not None
            ]
            if len(pts) >= MIN_CENTRE_POINTS:
                centre = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
        return _Scope(domain, rank, key.depth if key else 0, centre)

    def _types(self, domain: Domain) -> list[str]:
        return domain.search_types or [domain.filter_type]

    def _entities(self, types: list[str]) -> list[IndexEntity]:
        k = tuple(types)
        if k not in self._index:
            self._index[k] = list_entities(types=types, store=self.store)
        return self._index[k]

    # ------------------------------------------------------------------ one pick
    async def _one(self, pos: int, text: str, norm: str, scope: _Scope, ctx: RequestContext) -> Resolution:
        base = {"position": pos, "text": text, "norm": norm, "script": script_of(text)}
        if not norm:
            return Resolution(**base, status="unresolved", reason="empty", served_from="disabled")
        forms = aliases(text, places=self.places)
        types = self._types(scope.domain)
        pending: Reason = ""  # a reason found on the way, used if nothing better turns up

        # 1. index, exact or alias. An exact tie here is final: a search can only add same-name hits,
        #    and stopping here is what lets a re-run make no /search call.
        hits = find_entities(forms, types=types, store=self.store)
        if hits:
            out = self._pick([self._from_index(e, norm) for e in hits], scope)
            if out.pick:
                return self._matched(base, out, scope, "cache")
            if out.reason == "ambiguous":
                return Resolution(**base, status="unresolved", reason="ambiguous", served_from="cache")
            pending = out.reason  # elsewhere: try the rest before saying so

        # 2. index, fuzzy
        if fuzzy_eligible(norm):
            cands = []
            for e in self._entities(types):
                sim = best_similarity(forms, [e.norm_name, *e.aliases])
                if sim >= FUZZY_THRESHOLD:
                    cands.append(_Cand(e.qloo_id, e.name, "index_fuzzy", sim, e.lat, e.lon))
            if cands:
                out = self._pick(cands, scope)
                if out.pick:
                    return self._matched(base, out, scope, "cache")
                pending = pending or out.reason

        # 3. /search
        if self.client is None or not scope.domain.search_types:
            why = pending or "no_search"
            return Resolution(**base, status="unresolved", reason=why, served_from="disabled")
        res, why = await self._search(norm, scope.domain.search_types, ctx)
        if why:
            return Resolution(**base, status="unresolved", reason=why, served_from=res.served_from)
        cands = [c for e in res.entities if (c := self._from_hit(e, norm, forms, types))]
        if cands:
            out = self._pick(cands, scope)
            if out.pick:
                return self._matched(base, out, scope, res.served_from)
            return Resolution(**base, status="unresolved", reason=out.reason, served_from=res.served_from)
        if pending:  # a same-name place elsewhere, or a fuzzy tie: real, or not clearly absent
            return Resolution(**base, status="unresolved", reason=pending, served_from=res.served_from)
        return Resolution(**base, status="phantom_candidate", served_from=res.served_from)

    def _from_index(self, e: IndexEntity, norm: str) -> _Cand:
        method: Method = "index_exact" if e.norm_name == norm else "index_alias"
        return _Cand(e.qloo_id, e.name, method, 1.0, e.lat, e.lon)

    def _from_hit(self, e: Entity, norm: str, forms: list[str], types: list[str]) -> _Cand | None:
        if not e.entity_id or not e.name:
            return None
        if e.kind and e.kind not in types:
            return None
        hit_norm = normalize_name(e.name, places=self.places)
        hit_forms = aliases(e.name, places=self.places)
        loc = e.location or {}
        lat, lon = _coord(loc.get("lat")), _coord(loc.get("lon"))
        if hit_norm == norm:
            return _Cand(e.entity_id, e.name, "search_exact", 1.0, lat, lon)
        if set(forms) & set(hit_forms):
            return _Cand(e.entity_id, e.name, "search_alias", 1.0, lat, lon)
        if fuzzy_eligible(norm):
            sim = best_similarity(forms, hit_forms)
            if sim >= FUZZY_THRESHOLD:
                return _Cand(e.entity_id, e.name, "search_fuzzy", sim, lat, lon)
        return None

    # ------------------------------------------------------------------ choosing
    def _pick(self, cands: list[_Cand], scope: _Scope) -> _Outcome:
        best: dict[str, _Cand] = {}
        order = list(CONFIDENCE)
        for c in cands:  # one candidate per entity: its strongest method
            o = best.get(c.qloo_id)
            if o is None or (order.index(c.method), -c.similarity) < (order.index(o.method), -o.similarity):
                best[c.qloo_id] = c
        cands = sorted(best.values(), key=lambda c: c.qloo_id)
        near = [c for c in cands if not scope.far(c)]
        if not near:
            return _Outcome(reason="elsewhere")
        if len(near) == 1:
            return _Outcome(near[0])
        tests = [
            lambda c: c.qloo_id in scope.rank and scope.rank[c.qloo_id] <= scope.depth,  # in the key
            lambda c: c.qloo_id in scope.rank,  # in the pool
            lambda c: c.method in ("index_exact", "search_exact"),  # the name itself, not an alias
        ]
        if scope.centre is not None:  # a place known to be in the city
            tests.append(lambda c: c.lat is not None and c.lon is not None)
        for test in tests:
            group = [c for c in near if test(c)]
            if len(group) == 1:
                return _Outcome(group[0], tie_broken=True)
            if len(group) > 1:
                break  # several pass the same test: nothing later can fairly separate them
        return _Outcome(reason="ambiguous")

    def _matched(self, base: dict, out: _Outcome, scope: _Scope, served_from: ServedFrom) -> Resolution:
        c = out.pick
        assert c is not None
        conf = CONFIDENCE[c.method] * (c.similarity if c.method.endswith("fuzzy") else 1.0)
        if out.tie_broken:
            conf *= TIE_BREAK_FACTOR
        rank = scope.rank.get(c.qloo_id)
        status = "matched_in_key" if rank is not None and rank <= scope.depth else "matched_outside_key"
        return Resolution(
            **base, qloo_id=c.qloo_id, name=c.name, rank=rank, confidence=round(conf, 3), method=c.method,
            status=status, served_from=served_from,
        )  # fmt: skip

    # ------------------------------------------------------------------ search
    async def _search(self, norm: str, types: list[str], ctx: RequestContext) -> tuple[QlooResult, Reason]:
        """One budgeted /search. Returns the result and, when it can't be used, why."""
        assert self.client is not None
        capped = self.max_live_searches is not None and self.live_searches >= self.max_live_searches
        sctx = dataclasses.replace(ctx, allow_live=False) if capped and ctx.allow_live else ctx
        res = await self.client.search_entities(norm, sctx, types=types, take=self.search_take)
        self.searches += 1
        if res.served_from == "live" and not res.coalesced:
            self.live_searches += 1
        if res.ok or res.outcome == "empty":
            hits = [e for e in res.entities if e.entity_id and e.name]
            if hits and add_search_hits(hits, places=self.places, store=self.store):
                self._index.clear()  # the fuzzy list is stale now
            return res, ""
        return res, _why(res, capped=capped and ctx.allow_live, allow_live=ctx.allow_live)


def _why(res: QlooResult, *, capped: bool, allow_live: bool) -> Reason:
    """Why a search gave nothing usable. Read from the client's outcome and reason text."""
    if res.outcome == "disabled":
        return "disabled"
    if res.outcome == "not_live":
        text = (res.reason or "").lower()
        if capped or "budget" in text:
            return "budget"
        if "deadline" in text:
            return "deadline"
        if "replay" in text or not allow_live:
            return "offline"
    return "error"
