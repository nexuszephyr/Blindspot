"""What the resolver returns (#29): one `Resolution` per recommendation, grouped in `Resolved`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Status = Literal["matched_in_key", "matched_outside_key", "phantom_candidate", "unresolved"]
Method = Literal[
    "index_exact", "index_alias", "index_fuzzy", "search_exact", "search_alias", "search_fuzzy", "none"
]  # fmt: skip
# Why a pick is unresolved (empty otherwise). Never counted as a phantom (AGENTS §5, Phase 3).
# ambiguous: several entities fit and the key, pool or city can't pick one; elsewhere: the only
# entities with this name are places outside the city; budget, deadline, offline (replay or live off),
# disabled (kill switch), error (Qloo error or breaker), no_search (the domain has no search types or
# the resolver has no client), empty (no name in the text).
Reason = Literal[
    "", "ambiguous", "elsewhere", "budget", "deadline", "offline", "disabled", "error", "no_search", "empty"
]  # fmt: skip
ServedFrom = Literal["live", "cache", "snapshot", "disabled", "degraded"]
MATCHED: tuple[Status, ...] = ("matched_in_key", "matched_outside_key")


class Resolution(BaseModel):
    position: int  # 0-based position in the agent's list
    text: str  # the agent's text as given (untrusted data: escape it wherever it's rendered)
    norm: str  # its normalized form
    script: str  # the writing system of the text (`latin`, `cjk`, `kana`, `devanagari`, ...)
    qloo_id: str | None = None
    name: str | None = None  # Qloo's name for the match
    rank: int | None = None  # 1-based rank in the local list (key or pool) when it's there
    confidence: float = 0.0  # 0 to 1; 0 when there's no match
    method: Method = "none"
    status: Status
    reason: Reason = ""
    # Where the evidence came from: `cache` for the stored index (never `live`, like answer keys),
    # the `/search` result's own label after a search, `disabled` or `degraded` when nothing was asked.
    served_from: ServedFrom

    @property
    def matched(self) -> bool:
        return self.status in MATCHED


class Resolved(BaseModel):
    domain_id: str
    city_id: str
    scenario_id: str | None = None
    items: list[Resolution]
    searches: int = 0  # `/search` requests made through the client (cache hits included)
    live_searches: int = 0  # of those, how many reached Qloo
