"""Coverage probe (#24): does Qloo know enough about a location to audit it, per domain?

For any location string (not only configured cities), each domain's standard location-only query
from `config/domains.yaml` (`filter.type`, the domain's fixed `params`, its `location_param`) is
asked at the answer-key depth, and the count gets the builder's verdict (`coverage()`: rich, thin,
sparse, empty). At most three calls per domain, all through `QlooClient` (cache, budgets, breaker,
deadline, call log):

1. the standard query;
2. below depth, a broader retry without the domain's fixed narrowing params (dining's restaurant
   tag), so an empty 200 isn't called "no data" before a broader ask (AGENTS §10). The verdict still
   comes from the standard query, the one the answer key uses; the retry only explains it;
3. for `signal` domains, the same query with no location. Qloo ignores what it can't use, so a
   location it can't place may come back as the no-location list: identical lists mean `unknown`,
   never `rich`. The no-location list is the same for every location, so it's cached after one call.

A Qloo failure (timeout, budget, breaker, kill switch) is `unknown`, never `empty` (D35). Only counts
and outcomes are returned: no entity names or ids.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import unicodedata
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from ..catalog import Domain
from ..qloo.client import QlooClient
from ..qloo.context import RequestContext
from ..qloo.models import QlooResult
from .builder import coverage

MAX_LOCATION_CHARS = 100
Verdict = Literal["rich", "thin", "sparse", "empty", "unknown"]
Confidence = Literal["high", "medium", "low"]
QueryKind = Literal["standard", "broader", "no_location"]
# VERIFIED (E4), a 400 with
# `{"errors":[{"message":"filter.location.query is unable to resolve to a valid locality."}]}`
_UNRESOLVED = re.compile(r"unable to resolve to a valid locality", re.IGNORECASE)
# Weakest first: a mixed answer is labelled by its oldest source, so cached data never reads as live.
_PROVENANCE = ("snapshot", "cache", "live")


class LocationError(ValueError):
    """The location string is empty or too long once cleaned."""


def clean_location(raw: str) -> str:
    """The location as sent to Qloo: NFC, control and format characters removed, whitespace
    collapsed, at most MAX_LOCATION_CHARS. It's only ever a query-string value (never SQL or shell)."""
    kept = (
        " " if ch.isspace() else ch
        for ch in unicodedata.normalize("NFC", raw)
        if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    text = " ".join("".join(kept).split())
    if not text:
        raise LocationError("location is empty")
    if len(text) > MAX_LOCATION_CHARS:
        raise LocationError(f"location is longer than {MAX_LOCATION_CHARS} characters")
    return text


class Call(BaseModel):
    """One query behind a verdict: what was asked and how it came back (a receipt, no Qloo data)."""

    query: QueryKind
    served_from: str
    outcome: str
    count: int
    call_id: int | None = None


class DomainCoverage(BaseModel):
    domain: str
    verdict: Verdict
    confidence: Confidence
    note: str
    count: int  # results of the standard query (0 when it failed)
    depth: int
    served_from: str  # of the standard query
    calls: list[Call]


class CoverageReport(BaseModel):
    location: str
    # False: Qloo said it can't place it, or a list ignored it. True: a location filter answered.
    location_resolved: bool | None
    domains: list[DomainCoverage]

    @property
    def served_from(self) -> str:
        """`disabled` (kill switch), `degraded` (no domain got an answer), else the weakest source
        among the standard queries (the ones the verdicts count); each call keeps its own label."""
        srcs = {d.served_from for d in self.domains}
        if srcs and srcs <= {"disabled"}:
            return "disabled"
        if all(d.verdict == "unknown" and d.served_from in ("degraded", "disabled") for d in self.domains):
            return "degraded"
        return next((p for p in _PROVENANCE if p in srcs), "degraded")

    @property
    def degraded_reason(self) -> str | None:
        """Why any domain has no verdict or no Qloo answer, per domain; None when every domain has both."""
        why = [
            f"{d.domain}: {d.note}"
            for d in self.domains
            if d.verdict == "unknown" or d.served_from == "degraded"
        ]
        return "; ".join(why) or None


def _call(kind: QueryKind, res: QlooResult) -> Call:
    return Call(
        query=kind, served_from=res.served_from, outcome=res.outcome, count=res.count, call_id=res.call_id
    )


def _answered(res: QlooResult) -> bool:
    return res.ok or res.outcome == "empty"


def _unresolved(res: QlooResult) -> bool:
    return res.outcome == "qloo_error" and bool(_UNRESOLVED.search(res.reason or ""))


def _ids(res: QlooResult, depth: int) -> list[str]:
    return [e.entity_id for e in res.entities[:depth] if e.entity_id]


async def _probe_domain(
    client: QlooClient, ctx: RequestContext, d: Domain, location: str
) -> tuple[DomainCoverage, bool | None]:
    base = {"filter.type": d.filter_type, **d.params, "take": d.depth}
    loc = {f"{d.location_param}.location.query": location}
    res = await client.insights({**base, **loc}, ctx)
    calls = [_call("standard", res)]

    def out(verdict: Verdict, confidence: Confidence, note: str, resolved: bool | None = None):
        cov = DomainCoverage(
            domain=d.id, verdict=verdict, confidence=confidence, note=note, count=res.count,
            depth=d.depth, served_from=res.served_from, calls=calls,
        )  # fmt: skip
        return cov, resolved

    if _unresolved(res):
        return out("empty", "high", "Qloo could not resolve this location", False)
    if not _answered(res):  # timeout, budget, breaker, kill switch, Qloo error: no verdict (D35)
        return out("unknown", "low", res.reason or f"no answer ({res.outcome})")
    verdict: Verdict = coverage(res.count, d.depth)
    note = f"{res.count} of {d.depth} results"
    follow: list[tuple[QueryKind, Any]] = []
    broader = {k: v for k, v in base.items() if k not in d.params}
    if res.count < d.depth and d.params:
        follow.append(("broader", client.insights({**broader, **loc}, ctx)))
    if d.location_param == "signal" and res.count:
        follow.append(("no_location", client.insights(base, ctx)))
    extra = dict(zip([k for k, _ in follow], await asyncio.gather(*(c for _, c in follow)), strict=True))
    calls += [_call(k, r) for k, r in extra.items()]

    resolved: bool | None = True if d.location_param == "filter" and res.count else None
    confidence: Confidence = "high" if verdict in ("rich", "empty") else "medium"
    if (b := extra.get("broader")) is not None:
        dropped = ", ".join(sorted(d.params))
        if _answered(b):
            note += f"; {b.count} without {dropped}"
        else:
            note += f"; broader retry without {dropped} got no answer"
        if verdict == "empty":
            confidence = "medium" if _answered(b) else "low"
    elif res.count < d.depth:
        note += "; no broader query for this domain"
        confidence = "medium"
    if (g := extra.get("no_location")) is not None:
        if not _answered(g):
            note += "; not checked against the no-location list"
            confidence = "medium" if confidence == "high" else confidence
        elif _ids(g, d.depth) == _ids(res, d.depth):
            # The location changed nothing: Qloo most likely ignored it, so these are global results.
            return out(
                "unknown", "low", note + "; same list as with no location: location likely ignored", False
            )
        else:
            resolved = True
    return out(verdict, confidence, note, resolved)


def _resolved(flags: Iterable[bool | None]) -> bool | None:
    flags = list(flags)
    if False in flags:
        return False
    return True if True in flags else None


async def coverage_probe(
    location: str, domains: list[Domain], client: QlooClient, ctx: RequestContext
) -> CoverageReport:
    """Coverage verdict per domain for any location string (cleaned first; LocationError if it's
    empty or too long). Calls are logged with purpose `coverage` and spend `ctx`'s budget."""
    where = clean_location(location)
    ctx = dataclasses.replace(ctx, purpose="coverage")
    results = await asyncio.gather(*(_probe_domain(client, ctx, d, where) for d in domains))
    return CoverageReport(
        location=where,
        location_resolved=_resolved(r for _, r in results),
        domains=[c for c, _ in results],
    )
