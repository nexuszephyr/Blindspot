"""Deterministic scenario generator (#16).

The same config and id lock always give the same scenarios, ids and file bytes: candidates are
enumerated in a fixed order, shuffled with a seeded RNG, and picked greedily to balance cities,
domains and personas within each probe's cap (`probe_types.yaml`). Pairs (swap: one persona in two
cities; audience: two personas in one city) are picked and tagged as a unit.

Tags: `demo` (30 to 40 scenarios covering every demo city and every domain, with at least 8 swap
pairs) or `extended`; about 20% of each probe's picks are also `holdout`, kept out of the fix loop's
tuning (#43).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import combinations

from ..catalog import Catalog, City, Domain, Persona, ProbeType
from ..idlock import IdLock, dumps_lock
from .models import SCHEMA_VERSION, Scenario, content_hash
from .prompts import render
from .specs import Unresolvable, build_specs, signal_ids

DEFAULT_SEED = 20261028  # the submission date; change it and every pick changes
K = 10  # recommendations asked of the target (the answer key is `depth` deep, the pool `pool`)
PROBE_ORDER = ["swap", "core", "bridge", "audience", "freshness"]  # pairs first: they constrain most
PAIRED = {"swap", "audience"}
HOLDOUT_EVERY = 5  # every 5th pick of each probe (offset 2): about 20%
DEMO_MIN, DEMO_MAX, DEMO_SWAP_PAIRS = 30, 40, 8


@dataclass
class Unit:
    """A selection unit: one scenario, or a pair for swap and audience probes."""

    probe: str
    domain: str
    personas: tuple[str, ...]
    cities: tuple[str, ...]
    stated: str | None = None
    order: int = 0  # position after the seeded shuffle (tie-break)
    scenarios: list[Scenario] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.probe}:{self.domain}:{'+'.join(self.personas)}@{'+'.join(self.cities)}"


@dataclass
class ScenarioSet:
    version: int
    seed: int
    inputs_hash: str  # config + lock this set was built from
    scenarios: list[Scenario]
    skipped: list[str]

    def to_json(self) -> str:
        data = {
            "version": self.version,
            "seed": self.seed,
            "inputs_hash": self.inputs_hash,
            "skipped": self.skipped,
            "scenarios": [s.model_dump(mode="json") for s in self.scenarios],
        }
        return json.dumps(data, sort_keys=True, indent=1, ensure_ascii=False) + "\n"

    def output_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()


def inputs_hash(cat: Catalog, lock: IdLock) -> str:
    return content_hash({"catalog": cat.model_dump(mode="json"), "lock": dumps_lock(lock)}, "in")


# --------------------------------------------------------------------------- candidates
def _resolvable(cat: Catalog, lock: IdLock, skipped: set[str]) -> set[tuple[str, str]]:
    """(persona, domain) cells whose answer-key signal has locked ids."""
    ok = set()
    for p in cat.personas:
        for d in p.domains:
            try:
                signal_ids(p, cat.domain(d), lock)
                ok.add((p.id, d))
            except Unresolvable as exc:
                skipped.add(str(exc))
    return ok


def _candidates(cat: Catalog, probe: str, ok: set[tuple[str, str]]) -> Iterator[Unit]:
    """Every possible unit for one probe, in a fixed order."""
    pt = cat.probe(probe)
    for d in cat.domains:
        users = [p for p in cat.personas if (p.id, d.id) in ok and probe in cat.persona_probes(p, d.id)]
        if probe == "audience":
            for city in cat.cities:
                here = [p for p in users if city in cat.persona_cities(p)]
                for a, b in combinations(here, 2):
                    yield Unit(probe, d.id, (a.id, b.id), (city.id,))
            continue
        for p in users:
            cities = cat.persona_cities(p)
            if probe == "swap":
                for a, b in combinations(cities, 2):
                    yield Unit(probe, d.id, (p.id,), (a.id, b.id))
            elif probe == "bridge":
                stated = pt.pairs.get(d.id)
                if stated in p.domains:
                    yield from (Unit(probe, d.id, (p.id,), (c.id,), stated) for c in cities)
            else:  # core, freshness and any single-scenario probe
                yield from (Unit(probe, d.id, (p.id,), (c.id,)) for c in cities)


# --------------------------------------------------------------------------- selection
def _select(units: list[Unit], cap_units: int, tiers: dict[str, int], used: Counter) -> list[Unit]:
    """Greedy balanced pick: fewest picks so far for the unit's cities, domain and personas within
    this probe (and, more lightly, across probes); swap pairs prefer a rich and a focus city."""
    local: Counter = Counter()
    picked: list[Unit] = []
    pool = list(units)
    while pool and len(picked) < cap_units:

        def score(u: Unit) -> tuple[float, int]:
            keys = [("c", c) for c in u.cities] + [("d", u.domain)] + [("p", p) for p in u.personas]
            s = sum(local[k] for k in keys) + 0.25 * sum(used[k] for k in keys)
            if u.probe == "swap" and len({tiers[c] for c in u.cities}) == 1:
                s += 0.5  # same-tier pairs contrast less
            return s, u.order

        best = min(pool, key=score)
        pool.remove(best)
        picked.append(best)
        for k in [("c", c) for c in best.cities] + [("d", best.domain)] + [("p", p) for p in best.personas]:
            local[k] += 1
            used[k] += 1
    return picked


def _materialize(cat: Catalog, lock: IdLock, u: Unit, k: int) -> list[Scenario]:
    probe = cat.probe(u.probe)
    asked = cat.domain(u.domain)
    pair_id = (
        content_hash([u.probe, u.domain, sorted(u.personas), sorted(u.cities)], "pr")
        if (u.probe in PAIRED)
        else None
    )
    conditions = sorted({probe.condition[u.domain]} if u.domain in probe.condition else set())
    out = []
    for persona_id in u.personas:
        for city_id in u.cities:
            persona, city = cat.persona(persona_id), cat.city(city_id)
            out.append(_scenario(persona, city, asked, probe, u, lock, k, pair_id, conditions))
    return out


def _scenario(
    p: Persona,
    city: City,
    asked: Domain,
    probe: ProbeType,
    u: Unit,
    lock: IdLock,
    k: int,
    pair_id: str | None,
    conditions: list[str],
) -> Scenario:
    specs = build_specs(p, asked, city, probe, lock)
    return Scenario(
        persona_id=p.id,
        city_id=city.id,
        domain_id=asked.id,
        probe_type=u.probe,
        stated_domain=u.stated,
        signal=asked.query_shape.signal,
        local_spec=specs.local,
        global_spec=specs.global_,
        depth=asked.depth,
        pool=asked.membership_pool,
        k=k,
        prompt_variants=render(p, city, asked, u.stated or asked.id, k),
        pair_id=pair_id,
        conditions=conditions,
    ).with_id()


# --------------------------------------------------------------------------- tags
def _tag_holdout(picks: dict[str, list[Unit]]) -> None:
    for units in picks.values():
        for i, u in enumerate(units):
            if i % HOLDOUT_EVERY == 2:
                for s in u.scenarios:
                    s.tags.append("holdout")


def _tag_demo(cat: Catalog, picks: dict[str, list[Unit]]) -> None:
    demo_cities = {c.id for c in cat.cities if c.demo}
    covered_c: set[str] = set()
    covered_d: set[str] = set()
    cells: set[tuple[str, str]] = set()
    chosen: list[Unit] = []

    def gain(u: Unit) -> tuple[int, int]:
        cs = set(u.cities) & demo_cities
        new = len(cs - covered_c) + (u.domain not in covered_d) + len({(c, u.domain) for c in cs} - cells)
        return -new, u.order

    def take(u: Unit) -> None:
        chosen.append(u)
        covered_c.update(set(u.cities) & demo_cities)
        covered_d.add(u.domain)
        cells.update((c, u.domain) for c in u.cities if c in demo_cities)

    def size() -> int:
        return sum(len(u.scenarios) for u in chosen)

    def pick(units: list[Unit], n: int, limit: int) -> None:
        pool = [u for u in units if u not in chosen and (set(u.cities) & demo_cities)]
        for _ in range(n):
            if not pool or size() + len(pool[0].scenarios) > limit:
                return
            best = min(pool, key=gain)
            pool.remove(best)
            take(best)

    pick(picks.get("swap", []), DEMO_SWAP_PAIRS, DEMO_MAX)
    want = {(c, d.id) for c in demo_cities for d in cat.domains}
    core = picks.get("core", [])
    while core and size() < DEMO_MAX - 4 and want - cells:
        before = size()
        pick(core, 1, DEMO_MAX - 4)
        if size() == before:
            break
    for probe in ("bridge", "audience", "freshness"):  # one of each, so the demo shows every probe
        pick(picks.get(probe, []), 1, DEMO_MAX)
    while size() < DEMO_MIN and len(chosen) < sum(len(v) for v in picks.values()):
        before = size()
        pick(core, 1, DEMO_MAX)
        if size() == before:
            break
    demo = {id(u) for u in chosen}
    for units in picks.values():
        for u in units:
            for s in u.scenarios:
                s.tags.insert(0, "demo" if id(u) in demo else "extended")


# --------------------------------------------------------------------------- entry point
def generate(cat: Catalog, lock: IdLock, *, seed: int = DEFAULT_SEED, k: int = K) -> ScenarioSet:
    """Build the exam. Pure and deterministic: no network, no clock."""
    probes = {p.id: p for p in cat.probe_types}
    skipped: set[str] = set()
    ok = _resolvable(cat, lock, skipped)
    tiers = {c.id: c.tier for c in cat.cities}
    used: Counter = Counter()
    picks: dict[str, list[Unit]] = {}
    order = [p for p in PROBE_ORDER if p in probes] + sorted(set(probes) - set(PROBE_ORDER))
    for probe in order:
        units = list(_candidates(cat, probe, ok))
        rng = random.Random(f"{seed}:{probe}")  # noqa: S311  (reproducible picks, not security)
        rng.shuffle(units)
        for i, u in enumerate(units):
            u.order = i
        per_unit = 2 if probe in PAIRED else 1
        cap = probes[probe].max_scenarios // (per_unit * probes[probe].scenarios_per_cell)
        chosen = _select(units, cap, tiers, used)
        for u in chosen:
            u.scenarios = _materialize(cat, lock, u, k)
        picks[probe] = chosen
        if len(chosen) < cap:
            skipped.add(f"{probe}: only {len(chosen)} of {cap} units possible")
    _tag_holdout(picks)
    _tag_demo(cat, picks)
    scenarios = [s for probe in order for u in picks[probe] for s in u.scenarios]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate scenario ids: two picks produced identical questions")
    scenarios.sort(key=lambda s: (order.index(s.probe_type), s.domain_id, s.city_id, s.persona_id, s.id))
    return ScenarioSet(SCHEMA_VERSION, seed, inputs_hash(cat, lock), scenarios, sorted(skipped))
