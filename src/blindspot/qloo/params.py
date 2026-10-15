"""Parameter guard. Qloo silently ignores unknown/unsupported params (VERIFIED, audit L3), so the
client checks params *before* sending.

VERIFIED holds only parameters observed to work in recorded live responses; everything else is
UNKNOWN. Promote a parameter here only with evidence (see docs/qloo_api_reality.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import IdFormatError, QlooParamError

_COMMON_INSIGHTS = {"filter.type", "take", "page"}

# Per filter.type. Filled from recon evidence; each entry cites its proof in qloo_api_reality.md.
VERIFIED_INSIGHTS: dict[str, set[str]] = {
    "urn:entity:artist": _COMMON_INSIGHTS | {"signal.interests.entities"},
    "urn:entity:place": _COMMON_INSIGHTS | {"filter.location.query"},
}
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
            raise IdFormatError(f"{name} needs {family} ids, got non-id value(s): {bad[:3]}", param=name,
                                values=bad)


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
        allowed = sorted(VERIFIED_INSIGHTS.get(str(params.get("filter.type")), _COMMON_INSIGHTS)
                         if path == "/v2/insights" else VERIFIED_OTHER.get(path, set()))
        raise QlooParamError(f"parameter(s) {extra} not verified for {params.get('filter.type') or path}; "
                             f"verified: {allowed}", params=extra)
    return extra


def assert_param_effective(baseline: Iterable[Any], variant: Iterable[Any]) -> dict[str, Any]:
    """Did adding a parameter change the results? Takes entity lists (or ids) from two runs."""
    def ids(xs):
        return [getattr(x, "entity_id", x) for x in xs]
    a, b = ids(baseline), ids(variant)
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / len(sa | sb) if sa | sb else 1.0
    return {"changed": a != b, "set_changed": sa != sb, "order_only": a != b and sa == sb,
            "jaccard": round(jac, 3), "n_baseline": len(a), "n_variant": len(b)}
