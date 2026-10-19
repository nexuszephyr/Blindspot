"""Parameter guard. Qloo silently ignores unknown/unsupported params (VERIFIED, audit L3), so the
client checks params *before* sending.

VERIFIED holds only parameters observed to work in recorded live responses; everything else is
UNKNOWN. Promote a parameter only with evidence: scripts/promote_param.py measures it, writes a row to
docs/param_evidence.md and, with --apply, adds it to PROMOTED below.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import IdFormatError, QlooParamError

# page/offset VERIFIED on place (E9: page 2 == offset 5, no overlap with page 1).
_COMMON_INSIGHTS = {"filter.type", "take", "page", "offset"}
_ENTITY_SEED = {"signal.interests.entities"}  # E3: returns scored results for every type below
# Each set holds params observed to CHANGE results (assert_param_effective) in recorded live responses,
# recon run 2026-10-15 (docs/qloo_api_reality.md E3-E11). Accepted-but-ineffective params are left out
# on purpose: for places, signal.demographics.age/gender leave the result set unchanged (Jaccard 1.0),
# and bias.trends / filter.popularity.min=0.9 did not change artist results.
_TASTE = {
    "signal.interests.entities",
    "signal.interests.tags",
    "signal.location.query",
    "signal.demographics.age",
    "signal.demographics.audiences",
}
VERIFIED_INSIGHTS: dict[str, set[str]] = {
    "urn:entity:artist": _COMMON_INSIGHTS
    | _TASTE
    | {
        "signal.demographics.gender",
        "filter.popularity.max",
        "filter.results.entities",
        "feature.explainability",
    },
    "urn:entity:movie": _COMMON_INSIGHTS | _TASTE,
    "urn:entity:brand": _COMMON_INSIGHTS | _TASTE,
    "urn:entity:place": _COMMON_INSIGHTS
    | {
        "filter.location.query",
        "filter.tags",
        # DISCOURAGED for place: allowed, but it timed out in 4 of 6 cities (E4). Use filter.location.query.
        "signal.location.query",
        "signal.interests.entities",
        "signal.interests.tags",
        "signal.demographics.audiences",
        "bias.trends",
        "feature.explainability",
    },
    **{
        t: _COMMON_INSIGHTS | _ENTITY_SEED
        for t in (
            "urn:entity:tv_show",
            "urn:entity:person",
            "urn:entity:book",
            "urn:entity:podcast",
            "urn:entity:destination",
            "urn:entity:videogame",
        )
    },
}
# Promoted with scripts/promote_param.py; each entry names its row in docs/param_evidence.md.
PROMOTED: list[tuple[str, str, str]] = [
    ("urn:entity:place", "filter.exclude.entities", "ev_a1647925"),  # docs/param_evidence.md ev_a1647925
    ("urn:entity:artist", "filter.exclude.entities", "ev_15455188"),  # docs/param_evidence.md ev_15455188
    ("urn:entity:movie", "filter.exclude.entities", "ev_ef4684c9"),  # docs/param_evidence.md ev_ef4684c9
    ("urn:entity:brand", "filter.exclude.entities", "ev_3b8677bd"),  # docs/param_evidence.md ev_3b8677bd
    ("urn:entity:place", "filter.popularity.max", "ev_a76125b5"),  # docs/param_evidence.md ev_a76125b5
    ("urn:entity:movie", "filter.popularity.max", "ev_7106748a"),  # docs/param_evidence.md ev_7106748a
    ("urn:entity:brand", "filter.popularity.max", "ev_db9f8c70"),  # docs/param_evidence.md ev_db9f8c70
    ("urn:entity:place", "filter.results.entities", "ev_0d51fb5f"),  # docs/param_evidence.md ev_0d51fb5f
    ("urn:entity:movie", "filter.results.entities", "ev_cb6ddf4d"),  # docs/param_evidence.md ev_cb6ddf4d
    ("urn:entity:brand", "filter.results.entities", "ev_b38f3365"),  # docs/param_evidence.md ev_b38f3365
    # end PROMOTED (promote_param.py --apply inserts above this line)
]
for _type, _param, _evidence in PROMOTED:
    VERIFIED_INSIGHTS.setdefault(_type, set(_COMMON_INSIGHTS)).add(_param)

# Do not retry: accepted by Qloo but no measured change (INEFFECTIVE). Evidence in docs/param_evidence.md.
#   urn:entity:place signal.demographics.age=35_and_younger (Phase 1 E5, E11)
#   urn:entity:place signal.demographics.gender=female (Phase 1 E5)
#   urn:entity:artist bias.trends=high (Phase 1 E6, coverage freshness probe)
#   urn:entity:movie bias.trends=high (docs/param_evidence.md ev_2e9b0769)
#   urn:entity:brand bias.trends=high (docs/param_evidence.md ev_7ab34baf)
# end do-not-retry

VERIFIED_OTHER: dict[str, set[str]] = {
    "/search": {"query", "types", "take"},
}

# ID families (VERIFIED shapes from recorded responses): entity ids are UUIDs, tag ids urn:tag:...
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ID_PARAMS: dict[str, tuple[str, re.Pattern[str]]] = {
    "signal.interests.entities": ("entity", _UUID),
    "filter.results.entities": ("entity", _UUID),
    "filter.exclude.entities": ("entity", _UUID),
    "signal.interests.tags": ("tag", re.compile(r"^urn:tag:[\w:.\-]+$")),
    "filter.tags": ("tag", re.compile(r"^urn:tag:[\w:.\-]+$")),
    "filter.exclude.tags": ("tag", re.compile(r"^urn:tag:[\w:.\-]+$")),
    "signal.demographics.audiences": ("audience", re.compile(r"^urn:audience:[\w:.\-]+$")),
}


def split_list(v: Any) -> list[str]:
    """'a, b' or ['a', 'b'] -> ['a', 'b'] (same rule as cache.normalize_params)."""
    if v is None:
        return []
    items = v if isinstance(v, (list, tuple, set)) else str(v).split(",")
    return [str(x).strip() for x in items if str(x).strip()]


def check_ids(params: Mapping[str, Any]) -> None:
    """Free text must never be sent where Qloo expects an id (the guide says it fails)."""
    for name, (family, pat) in ID_PARAMS.items():
        bad = [x for x in split_list(params.get(name)) if not pat.match(x)]
        if bad:
            raise IdFormatError(
                f"{name} needs {family} ids, got non-id value(s): {bad[:3]}", param=name, values=bad
            )


def unverified(path: str, params: Mapping[str, Any]) -> list[str]:
    """Names of params that are not VERIFIED for this endpoint / filter.type."""
    if path == "/v2/insights":
        allowed = VERIFIED_INSIGHTS.get(str(params.get("filter.type")), _COMMON_INSIGHTS)
    else:
        allowed = VERIFIED_OTHER.get(path)
        if allowed is None:
            return []  # lookup endpoints (tags, audiences, entities) are not guarded yet
    return sorted(k for k, v in params.items() if v not in (None, "", []) and k not in allowed)


def check_params(path: str, params: Mapping[str, Any], *, strict: bool) -> list[str]:
    """Raise QlooParamError in strict mode; otherwise return the unverified names for logging."""
    extra = unverified(path, params)
    if extra and strict:
        allowed = sorted(
            VERIFIED_INSIGHTS.get(str(params.get("filter.type")), _COMMON_INSIGHTS)
            if path == "/v2/insights"
            else VERIFIED_OTHER.get(path, set())
        )
        raise QlooParamError(
            f"parameter(s) {extra} not verified for {params.get('filter.type') or path}; verified: {allowed}",
            params=extra,
        )
    return extra


def assert_param_effective(baseline: Iterable[Any], variant: Iterable[Any]) -> dict[str, Any]:
    """Did adding a parameter change the results? Takes entity lists (or ids) from two runs."""

    def ids(xs):
        return [getattr(x, "entity_id", x) for x in xs]

    a, b = ids(baseline), ids(variant)
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / len(sa | sb) if sa | sb else 1.0
    return {
        "changed": a != b,
        "set_changed": sa != sb,
        "order_only": a != b and sa == sb,
        "jaccard": round(jac, 3),
        "n_baseline": len(a),
        "n_variant": len(b),
    }
