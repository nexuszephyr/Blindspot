"""Answer-key query specs from config and the id lock (#16).

Each domain's `query_shape` says which persona signal goes into the answer key (E13, D35). The ids
come from `config/resolved_ids.lock.json` (#15): names are never sent. Every spec is checked with
the strict parameter guard and the id-format check before it leaves this module, so a spec can't
carry a parameter Qloo would silently ignore (ADR 0005) or free text where an id belongs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..catalog import AudienceName, City, Domain, Persona, ProbeType
from ..idlock import IdLock
from ..qloo.params import check_ids, check_params
from .models import QuerySpec

SIGNAL_PARAM = {
    "audience": "signal.demographics.audiences",
    "entity": "signal.interests.entities",
    "tag": "signal.interests.tags",
}
EXCLUDE_PARAM = "filter.exclude.entities"  # VERIFIED for place, artist, movie and brand (#12)


class Unresolvable(ValueError):
    """The persona's signal for this domain has no locked id, so it has no answer key."""


def audience_query(a: str | AudienceName) -> str:
    """The lock query for an audience as written in personas.yaml."""
    return a if isinstance(a, str) else f"{a.type}:{a.name}"


def signal_ids(persona: Persona, domain: Domain, lock: IdLock) -> list[str]:
    """Locked ids for the persona's signal in this domain. All of them must resolve: a partly
    resolved audience set could collide with another persona's (D38)."""
    kind = domain.query_shape.signal
    if kind == "audience":
        queries = [("audience", "*", audience_query(a)) for a in persona.audiences]
    elif kind == "entity":
        queries = [("seed", domain.id, s.name) for s in persona.seeds.get(domain.id, [])]
    else:
        queries = [("tag", domain.id, t) for t in persona.interest_tags.get(domain.id, [])]
    ids = [lock.id_for(*q) for q in queries]
    if not ids or None in ids:
        missing = [q[2] for q, i in zip(queries, ids, strict=True) if i is None] or ["(none configured)"]
        raise Unresolvable(f"{persona.id}/{domain.id}: no locked {kind} id for {missing}")
    return sorted(ids)


def own_seed_ids(persona: Persona, domain: Domain, lock: IdLock) -> list[str]:
    """The persona's own resolved seeds in the asked domain. They are excluded from its answer key:
    the prompt names them, so an agent repeating them back must not score as taste (and no prompt
    can contain an answer-key entity)."""
    ids = (lock.id_for("seed", domain.id, s.name) for s in persona.seeds.get(domain.id, []))
    return sorted(i for i in ids if i)


@dataclass(frozen=True)
class SpecPair:
    local: QuerySpec
    global_: QuerySpec


def build_specs(persona: Persona, domain: Domain, city: City, probe: ProbeType, lock: IdLock) -> SpecPair:
    """The local spec (this city: the answer key and its membership pool) and the global baseline
    (same taste, no location; shared by every scenario with the same persona, domain and probe)."""
    shape = domain.query_shape
    base: dict[str, Any] = {
        "filter.type": domain.filter_type,
        **domain.params,
        SIGNAL_PARAM[shape.signal]: signal_ids(persona, domain, lock),
        "take": domain.membership_pool,
        **probe.params,
    }
    if shape.with_age:  # the catalog already refused UNVERIFIED bands for such shapes
        base["signal.demographics.age"] = persona.age_band
    if excluded := own_seed_ids(persona, domain, lock):
        base[EXCLUDE_PARAM] = excluded
    local = {**base, **domain.location_params(city)}
    for params in (local, base):
        check_params("/v2/insights", params, strict=True)
        check_ids(params)
    return SpecPair(QuerySpec.of(local), QuerySpec.of(base))
