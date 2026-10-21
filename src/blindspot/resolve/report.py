"""Match-rate reporting (#29): per domain and script, compared with the V8 ceiling.

V8 (`/search` finds an entity by its own name, architecture Section 7.4) sets the realistic ceiling
for the match rate. Its data comes from #21; until then `v8_ceiling` is None and the comparison says
`pending`. Reports hold counts only, never names or ids.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping

from pydantic import BaseModel

from .models import Resolved

# Unicode script words folded together: Japanese kana are one script for reporting.
_SCRIPT_ALIASES = {"hiragana": "kana", "katakana": "kana", "halfwidth": "kana", "fullwidth": "latin"}


def script_of(text: str) -> str:
    """The main writing system of a text, from Unicode character names: `latin`, `cjk`, `kana`,
    `devanagari`, `hangul`, `arabic`, ... The most frequent one wins (ties: alphabetical); `none`
    for text without letters."""
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            word = unicodedata.name(ch).split()[0].lower()
        except ValueError:
            continue
        counts[_SCRIPT_ALIASES.get(word, word)] += 1
    if not counts:
        return "none"
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


class MatchRate(BaseModel):
    domain: str
    script: str
    total: int
    matched: int  # matched_in_key + matched_outside_key
    in_key: int
    outside_key: int
    phantom: int
    unresolved: int
    unresolved_by_reason: dict[str, int]
    rate: float  # matched / total
    v8_ceiling: float | None = None
    vs_ceiling: str  # "pending (#21)", or the rate as a share of the ceiling

    @property
    def row(self) -> str:
        return (
            f"{self.domain:<10} {self.script:<11} {self.total:>5} {self.rate:>6.2f} "
            f"{self.in_key:>6} {self.outside_key:>7} {self.phantom:>7} {self.unresolved:>10}  "
            f"{self.vs_ceiling}"
        )


HEADER = (
    f"{'domain':<10} {'script':<11} {'items':>5} {'rate':>6} {'in_key':>6} {'outside':>7} {'phantom':>7} "
    f"{'unresolved':>10}  vs V8"
)


def match_rates(
    results: Iterable[Resolved], *, ceilings: Mapping[str, float] | None = None
) -> list[MatchRate]:
    """One row per (domain, script), sorted. `ceilings` maps a domain, or `domain:script`, to its V8
    round-trip rate; a missing one reads `pending (#21)`."""
    groups: dict[tuple[str, str], list] = {}
    for r in results:
        for item in r.items:
            groups.setdefault((r.domain_id, item.script), []).append(item)
    rows = []
    for (domain, script), items in sorted(groups.items()):
        st = Counter(i.status for i in items)
        matched = st["matched_in_key"] + st["matched_outside_key"]
        reasons = Counter(i.reason for i in items if i.status == "unresolved")
        rate = matched / len(items)
        ceiling = None
        if ceilings:
            ceiling = ceilings.get(f"{domain}:{script}", ceilings.get(domain))
        if ceiling is None:
            vs = "pending (#21)"
        elif ceiling <= 0:
            vs = "ceiling 0"
        else:
            vs = f"{rate / ceiling:.0%} of ceiling {ceiling:.2f}"
        rows.append(
            MatchRate(
                domain=domain, script=script, total=len(items), matched=matched,
                in_key=st["matched_in_key"], outside_key=st["matched_outside_key"],
                phantom=st["phantom_candidate"], unresolved=st["unresolved"],
                unresolved_by_reason=dict(sorted(reasons.items())),
                rate=round(rate, 4), v8_ceiling=ceiling, vs_ceiling=vs,
            )
        )  # fmt: skip
    return rows


def format_rates(rows: list[MatchRate]) -> str:
    return "\n".join([HEADER, *(r.row for r in rows)])
