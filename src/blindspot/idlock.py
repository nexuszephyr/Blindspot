"""Locked Qloo ids for the text in config/ (#15). Pure: no network.

`scripts/resolve_seeds.py` looks every persona seed, interest tag and audience up once and writes
`config/resolved_ids.lock.json`; queries read ids from the lock (`load_lock(...).id_for(...)`), so
scenario generation is reproducible offline and names are never sent where Qloo wants an id.

The lock holds only our own query text and Qloo ids: no Qloo names, descriptions or popularity (D5).
The rules that pick an id are here so they can be tested without HTTP; docs/seed_review.md states
them for reviewers.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .catalog import AUDIENCE_URN, IDENTITY_AUDIENCE_TYPES, ConfigError, identity_terms

LOCK_FILE = "resolved_ids.lock.json"
Kind = Literal["seed", "tag", "audience"]

EXACT, PARTIAL, WEAK = 1.0, 0.7, 0.3

# Tag families (`urn:tag:<family>:<namespace>:...`, written "<family>:<namespace>") that count as a
# match per domain. From E2 and the #13 lookups: a dining tag must be about places or food, a music
# tag a music genre, a film tag a media genre. "street food" alone returned 10 tags across families
# (keyword, cuisine, place genre...), so the family filters before the name does. A domain missing
# here has no match: its tags stay unresolved.
TAG_FAMILIES: dict[str, frozenset[str]] = {
    "dining": frozenset(
        {"genre:place", "category:place", "cuisine:qloo", "dining_option:qloo", "amenity:place"}
    ),
    "music": frozenset({"genre:music"}),
    "film_tv": frozenset({"genre:media"}),
    "brands": frozenset({"category:brand", "genre:brand", "keyword:qloo"}),
}


# --------------------------------------------------------------------------- keys and names
def lock_key(kind: str, domain: str, query: str) -> str:
    """`<kind>:<domain>:<query lowercased, whitespace collapsed>`; audiences use domain `*`."""
    return f"{kind}:{domain}:{' '.join(query.lower().split())}"


def normalize(name: str) -> str:
    """For comparing names: NFKC, casefold, `&` -> and, punctuation dropped, spaces collapsed,
    a leading "the " dropped. Accents are kept (NFKC only unifies how they're encoded)."""
    s = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    s = " ".join(re.sub(r"[\W_]+", " ", s).split())
    return s.removeprefix("the ")


def name_score(ours: str, theirs: str) -> float:
    """1.0 for the same normalized name, 0.7 when one starts with or contains the other, else 0."""
    a, b = normalize(ours), normalize(theirs or "")
    if not a or not b:
        return 0.0
    if a == b:
        return EXACT
    return PARTIAL if a in b or b in a else 0.0


# --------------------------------------------------------------------------- the lock
class LockItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    domain: str  # a domain id, or "*" for audiences
    query: str  # our text from config/, never Qloo's
    search_type: str | None = None  # seeds only: the /search type asked for
    id: str | None = None  # Qloo entity UUID or URN; None when unresolved
    status: Literal["resolved", "unresolved"]
    ambiguous: bool = False
    confidence: float | None = None
    reason: str = ""

    @property
    def key(self) -> str:
        return lock_key(self.kind, self.domain, self.query)


class IdLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: dict[str, LockItem] = {}

    def id_for(self, kind: str, domain: str, query: str) -> str | None:
        """The locked id, or None if the item is missing or unresolved."""
        item = self.items.get(lock_key(kind, domain, query))
        return item.id if item and item.status == "resolved" else None

    def unresolved(self) -> list[LockItem]:
        return [i for _, i in sorted(self.items.items()) if i.status == "unresolved"]

    def ambiguous(self) -> list[LockItem]:
        return [i for _, i in sorted(self.items.items()) if i.status == "resolved" and i.ambiguous]


def dumps_lock(lock: IdLock) -> str:
    """Deterministic text: sorted keys, no timestamps, so an unchanged run is byte-identical."""
    items = {k: {f: v for f, v in i.model_dump().items() if not (f == "search_type" and v is None)}
             for k, i in lock.items.items()}  # fmt: skip
    return (
        json.dumps({"version": lock.version, "items": items}, sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )


def write_lock(lock: IdLock, path: Path | str) -> None:
    Path(path).write_text(dumps_lock(lock), encoding="utf-8", newline="\n")


def load_lock(config_dir: Path | str = "config") -> IdLock:
    path = Path(config_dir) / LOCK_FILE
    if not path.is_file():
        raise ConfigError(f"{path}: not found; run scripts/resolve_seeds.py")
    try:
        lock = IdLock.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ConfigError(f"{path}: not a valid lock file: {exc}") from None
    for key, item in lock.items.items():
        if key != item.key:
            raise ConfigError(f"{path}: item {key!r} doesn't match its own fields (expected {item.key!r})")
    return lock


# --------------------------------------------------------------------------- rules
class Decision(BaseModel):
    """What a rule picked. `chosen` is the index into the hits (for the detail file), or None."""

    id: str | None
    confidence: float | None
    ambiguous: bool
    reason: str
    chosen: int | None = None

    @property
    def status(self) -> str:
        return "resolved" if self.id else "unresolved"


def _unresolved(reason: str) -> Decision:
    return Decision(id=None, confidence=None, ambiguous=False, reason=reason)


def _popularity(hit: dict[str, Any]) -> float:
    p = hit.get("popularity")
    return float(p) if isinstance(p, (int, float)) else 0.0


def _type_ok(hit: dict[str, Any], search_type: str) -> bool:
    return search_type in (hit.get("types") or []) or search_type in (hit.get("type"), hit.get("subtype"))


def resolve_seed(query: str, search_type: str, hits: list[dict[str, Any]]) -> Decision:
    """Rank /search hits (top 5) for one seed name and type.

    - 1.0: the top hit has our normalized name and the requested type.
    - 0.7: the top hit's name starts with or contains ours (or the reverse), and the type matches.
    - 0.3: anything else.
    Ambiguous when confidence < 1.0, when 2+ hits in the top 5 are exact name and type matches (the
    most popular is taken), or when the top hit's type doesn't match. No hits: unresolved."""
    hits = hits[:5]
    if not hits:
        return _unresolved("no search hits")
    exact = [
        i
        for i, h in enumerate(hits)
        if _type_ok(h, search_type) and name_score(query, h.get("name")) == EXACT
    ]
    if len(exact) >= 2:
        best = max(exact, key=lambda i: (_popularity(hits[i]), -i))
        return Decision(
            id=hits[best].get("entity_id"), confidence=EXACT, ambiguous=True, chosen=best,
            reason=f"{len(exact)} exact name and type matches in the top 5; took the most popular",
        )  # fmt: skip
    top = hits[0]
    if not _type_ok(top, search_type):
        return Decision(
            id=top.get("entity_id"),
            confidence=WEAK,
            ambiguous=True,
            chosen=0,
            reason="top hit has another type",
        )
    score = name_score(query, top.get("name"))
    if score == EXACT:
        return Decision(id=top.get("entity_id"), confidence=EXACT, ambiguous=False, chosen=0, reason="")
    if exact:
        why = f"top hit is a partial name match; an exact match is at rank {exact[0] + 1}"
    else:
        why = "top hit is a partial name match" if score == PARTIAL else "top hit's name doesn't match"
    return Decision(id=top.get("entity_id"), confidence=score or WEAK, ambiguous=True, chosen=0, reason=why)


def tag_family(tag_id: str) -> str:
    """`urn:tag:genre:place:restaurant` -> `genre:place`."""
    parts = tag_id.split(":")
    return f"{parts[2]}:{parts[3]}" if len(parts) >= 4 and parts[:2] == ["urn", "tag"] else ""


def resolve_tag(query: str, domain: str, hits: list[dict[str, Any]]) -> Decision:
    """Pick a tag from /v2/tags hits: only tags in the domain's allowed families count; exact
    normalized name 1.0, contains 0.7. Ambiguous when 2+ allowed tags match exactly or the best
    is only 0.7 (ties: the most popular, then Qloo's order). Nothing allowed: unresolved."""
    allowed = TAG_FAMILIES.get(domain)
    if allowed is None:
        return _unresolved(f"no tag families are defined for domain {domain}")
    families = sorted({tag_family(str(h.get("id", ""))) for h in hits} - {""})
    scored = [
        (name_score(query, h.get("name")), i)
        for i, h in enumerate(hits)
        if tag_family(str(h.get("id", ""))) in allowed
    ]
    scored = [(s, i) for s, i in scored if s > 0]
    if not scored:
        returned = ", ".join(families) or "none"
        return _unresolved(f"no tag in an allowed {domain} family (families returned: {returned})")
    best = max(s for s, _ in scored)
    tops = [i for s, i in scored if s == best]
    pick = max(tops, key=lambda i: (_popularity(hits[i]), -i))
    reasons = []
    if len(tops) > 1:
        reasons.append(f"{len(tops)} allowed tags match {'exactly' if best == EXACT else 'partly'}")
    if best < EXACT:
        reasons.append("best match is partial")
    return Decision(
        id=str(hits[pick]["id"]),
        confidence=best,
        ambiguous=bool(reasons),
        chosen=pick,
        reason="; ".join(reasons),
    )


def listing_ids(listing: list[dict[str, Any]]) -> list[str]:
    """Audience URNs from a GET /v2/audiences listing. Each item has `id` (the URN to send) and
    `entity_id` (a UUID Qloo does NOT accept as an audience, E5): always read `id`."""
    return [str(a["id"]) for a in listing if AUDIENCE_URN.match(str(a.get("id", "")))]


def resolve_audience_urn(urn: str, listing: list[dict[str, Any]]) -> Decision:
    if urn in listing_ids(listing):
        return _identity_guard(
            Decision(
                id=urn, confidence=EXACT, ambiguous=False, reason="", chosen=listing_ids(listing).index(urn)
            )
        )
    return _unresolved("not in listing")


def resolve_audience_name(name: str, listing: list[dict[str, Any]]) -> Decision:
    """Match our audience name against the names in its parent type's listing."""
    scored = [
        (name_score(name, a.get("name")), i)
        for i, a in enumerate(listing)
        if AUDIENCE_URN.match(str(a.get("id", "")))
    ]
    scored = [(s, i) for s, i in scored if s > 0]
    if not scored:
        return _unresolved("no audience in that type has this name")
    best = max(s for s, _ in scored)
    tops = [i for s, i in scored if s == best]
    reasons = (
        [f"{len(tops)} audiences match {'exactly' if best == EXACT else 'partly'}"] if len(tops) > 1 else []
    )
    if best < EXACT:
        reasons.append("best match is partial")
    d = Decision(
        id=str(listing[tops[0]]["id"]),
        confidence=best,
        ambiguous=bool(reasons),
        reason="; ".join(reasons),
        chosen=tops[0],
    )
    return _identity_guard(d)


def _identity_guard(d: Decision) -> Decision:
    """A name can resolve to an identity audience; those stay out unless opted in (AGENTS §13)."""
    m = AUDIENCE_URN.match(d.id or "")
    if m and (m[1] in IDENTITY_AUDIENCE_TYPES or identity_terms(d.id)):
        return _unresolved("identity-based (AGENTS §13)")
    return d
