"""Calibration targets (#30, D51): four built-in targets with known answers, so the metrics (#31, #32)
can be checked against what they should say (AGENTS §7, architecture Section 9).

- `calib:qloo-copy` returns the scenario's local answer key, top `k` by rank: it should score high.
- `calib:global-top` returns the same list for every scenario of a domain: the entities that appear
  most often across the domain's baseline lists (`AnswerKey.global_`), ties broken by id. For dining
  the baseline is the city list without the persona (D43), so its list is built from the city
  baselines; it is still one list for every dining scenario, whatever the city. It should score low
  on taste-fit and high on famousness and sameness.
- `calib:random-real` returns `k` seeded random index entities of the domain's types, never from the
  scenario's own pool, so a hit is never luck: it should score near zero.
- `calib:invented` returns `k` plausible but fake names from neutral English words, each checked
  against the entity index first: they should all come out as phantoms.

Everything is read from stored ground truth through the `groundtruth` read API (`get_answer_key`,
`list_entities`, `find_entities`): no network, no model, no direct store reads. A response is `ok`
with `served_from: cache` (stored data, never `live`) and zero usage (`empty` if the index has
nothing to offer), or `error` with a short detail when the scenario has no built answer key. A
target never raises. Output depends only on the stored data, the seed and the scenario id.
"""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from typing import Any

from ..catalog import Catalog, Domain
from ..groundtruth import NotBuilt, find_entities, get_answer_key, list_entities
from ..groundtruth.normalize import aliases, place_forms
from ..scenarios.generator import DEFAULT_SEED
from ..scenarios.models import Scenario
from .cache import prompt_hash
from .models import Recommendation, TargetResponse

QLOO_COPY = "calib:qloo-copy"
GLOBAL_TOP = "calib:global-top"
RANDOM_REAL = "calib:random-real"
INVENTED = "calib:invented"
CALIBRATION_IDS = (QLOO_COPY, GLOBAL_TOP, RANDOM_REAL, INVENTED)

# Neutral English words only: never a real venue, artist or brand name, never a city (rule 9).
ADJECTIVES = (
    "Amber", "Quiet", "Golden", "Hollow", "Silver", "Crooked", "Gentle", "Lucky", "Restless", "Velvet",
    "Northern", "Paper", "Copper", "Wandering", "Bright", "Lonely", "Painted", "Humble", "Midnight",
    "Patient", "Rusty", "Little", "Wild", "Sleepy", "Hidden", "Merry", "Stubborn", "Tidy", "Brave",
)  # fmt: skip
NOUNS = (
    "Lantern", "Heron", "Orchard", "Kettle", "Harbor", "Meadow", "Compass", "Pebble", "Thistle",
    "Ladder", "Willow", "Ember", "Saddle", "Feather", "Candle", "Badger", "Lighthouse", "Acorn",
    "Bramble", "Teapot", "Anchor", "Sparrow", "Quarry", "Juniper", "Marble", "Basket", "Otter",
)  # fmt: skip
# The last word, by the domain's Qloo type, so a fake name reads like the domain asked for.
DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "urn:entity:place": ("Kitchen", "Canteen", "Eatery", "Table", "Dining Room", "Grill"),
    "urn:entity:artist": ("Ensemble", "Collective", "Trio", "Quartet", "Orchestra", "Band"),
    "urn:entity:movie": ("Chronicles", "Affair", "Story", "Diaries", "Legacy", "Summer"),
    "urn:entity:brand": ("Supply", "Goods", "Outfitters", "Works", "Studio", "Company"),
}
GENERIC_WORDS = ("Project", "Society", "House", "Club", "Room", "Hour")
MAX_TRIES_PER_NAME = 50  # candidates drawn per name asked for before giving up on the rest
# The longest name a Recommendation holds (models.py), read from the model so the two never drift.
MAX_NAME = next(
    m.max_length for m in Recommendation.model_fields["name"].metadata if hasattr(m, "max_length")
)


def _nameable(name: str) -> bool:
    """A stored name a target can say: the build stores \"\" for an entity Qloo sent without one."""
    return 0 < len(name) <= MAX_NAME


def _response(target_id: str, prompt: str, names: list[str]) -> TargetResponse:
    return TargetResponse(
        target_id=target_id, outcome="ok" if names else "empty",
        items=[Recommendation(name=n) for n in names], raw_text="\n".join(names), model=target_id,
        served_from="cache",
        prompt_hash=prompt_hash([{"role": "user", "content": prompt}]),
    )  # fmt: skip


def _error(target_id: str, detail: str) -> TargetResponse:
    return TargetResponse(
        target_id=target_id, outcome="error", model=target_id, served_from="cache", detail=detail
    )


class CalibrationTarget:
    """One of the four calibration targets. `scenarios` is the exam (to find a scenario's domain and
    the domain's baselines); `store` is the answer-key store (`groundtruth.open_store()`), passed
    through to the read API only."""

    def __init__(
        self,
        target_id: str,
        *,
        catalog: Catalog,
        scenarios: list[Scenario],
        store: Any = None,
        seed: int = DEFAULT_SEED,
    ):
        if target_id not in CALIBRATION_IDS:
            raise KeyError(f"unknown calibration target {target_id!r}; known: {list(CALIBRATION_IDS)}")
        self.id = target_id
        self.catalog, self.store, self.seed = catalog, store, seed
        self.scenarios = {s.id: s for s in scenarios}
        self.places = place_forms(w for c in catalog.cities for w in (c.display_name, c.location))
        self._global: dict[str, list[str]] = {}  # domain id -> its fixed list, built once

    async def recommend(self, prompt: str, k: int, *, scenario_id: str | None = None) -> TargetResponse:
        """Up to `k` names for the scenario. Never raises: an unknown or unbuilt scenario is `error`."""
        sc = self.scenarios.get(scenario_id or "")
        if sc is None:
            return _error(self.id, "calibration targets need a scenario id from the exam")
        try:
            names = await asyncio.to_thread(self._names, sc, max(k, 0))
        except NotBuilt:
            return _error(self.id, "no answer key built for this scenario")
        except Exception as exc:  # a target never raises (models.TargetResponse)
            return _error(self.id, f"calibration failed: {type(exc).__name__}")
        return _response(self.id, prompt, names)

    async def aclose(self) -> None:
        """Nothing to close: the store belongs to the caller."""

    # ------------------------------------------------------------------ the four lists
    def _names(self, sc: Scenario, k: int) -> list[str]:
        domain = self.catalog.domain(sc.domain_id)
        if self.id == QLOO_COPY:
            return [it.name for it in get_answer_key(sc.id, self.store).local.items if _nameable(it.name)][:k]
        if self.id == GLOBAL_TOP:
            return self._global_top(domain)[:k]
        key = get_answer_key(sc.id, self.store)  # NotBuilt for an unbuilt scenario, like the others
        rng = random.Random(f"{self.seed}:{sc.id}")  # noqa: S311 (a reproducible draw, not a secret)
        if self.id == RANDOM_REAL:
            return self._random_real(domain, set(key.local.pool), rng, k)
        return self._invented(domain, rng, k)

    def _global_top(self, domain: Domain) -> list[str]:
        """The domain's fixed famous list: entities counted once per distinct baseline list, most
        frequent first, ties by id. Unbuilt scenarios are skipped; none built at all is NotBuilt."""
        if domain.id in self._global:
            return self._global[domain.id]
        counts: Counter[str] = Counter()
        names: dict[str, str] = {}
        seen: set[str] = set()
        for sc in sorted(self.scenarios.values(), key=lambda s: s.id):
            if sc.domain_id != domain.id or sc.global_spec.key in seen:
                continue
            try:
                baseline = get_answer_key(sc.id, self.store).global_
            except NotBuilt:
                continue  # another scenario with the same baseline may be built
            seen.add(sc.global_spec.key)
            for it in baseline.items:
                if _nameable(it.name):
                    counts[it.qloo_id] += 1
                    names[it.qloo_id] = it.name
        if not counts:
            raise NotBuilt(f"no baseline built for domain {domain.id!r}")
        ranked = sorted(counts, key=lambda q: (-counts[q], q))
        self._global[domain.id] = [names[q] for q in ranked]
        return self._global[domain.id]

    def _random_real(self, domain: Domain, pool: set[str], rng: random.Random, k: int) -> list[str]:
        types = domain.search_types or [domain.filter_type]
        index = list_entities(types=types, store=self.store)  # sorted by id: a stable population
        # A namesake of a pool entity would resolve to the pool (the resolver's tie-break): skip it too.
        forms = {f for e in index if e.qloo_id in pool for f in (e.norm_name, *e.aliases)}
        cands = []
        for e in index:
            if (
                e.qloo_id not in pool
                and _nameable(e.name)
                and e.norm_name not in forms
                and not forms & set(e.aliases)
            ):
                forms.add(e.norm_name)
                cands.append(e.name)
        return rng.sample(cands, min(k, len(cands)))

    def _invented(self, domain: Domain, rng: random.Random, k: int) -> list[str]:
        words = DOMAIN_WORDS.get(domain.filter_type, GENERIC_WORDS)
        out: list[str] = []
        taken: set[str] = set()
        for _ in range(k * MAX_TRIES_PER_NAME):
            if len(out) >= k:
                break
            name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {rng.choice(words)}"
            forms = aliases(name, places=self.places)
            if taken & set(forms) or find_entities(forms, store=self.store):
                continue  # a repeat, or a real entity of any type: never return it
            taken.update(forms)
            out.append(name)
        return out


__all__ = ["CALIBRATION_IDS", "GLOBAL_TOP", "INVENTED", "QLOO_COPY", "RANDOM_REAL", "CalibrationTarget"]
