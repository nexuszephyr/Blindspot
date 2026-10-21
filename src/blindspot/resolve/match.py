"""Fuzzy name similarity for the resolver (#29), standard library only.

A token-set ratio in the style of the usual fuzzy-matching libraries: both names are split into word
sets, and the shared words are compared with each side's full sorted form, so word order and a
repeated word don't matter. Two changes make it stricter:
- When one name's words are all inside the other's ("cafe" and "cafe coffee day"), the usual ratio
  scores 1.0. Here that shortcut counts only if the shared words cover at least `SUBSET_MIN_COVER`
  of the longer name; otherwise the two sorted forms are compared as a whole.
- When each name has a word the other lacks, those words must be spellings of one another (a typo,
  not a different word: `WORD_VARIANT_MIN`), or only the shared part counts.

Loose matches create false taste-fit (#29 risk), so an unresolved pick is preferred to a wrong one.

The constants are PROVISIONAL (D47): retune them on the V8 name round-trip data from #21.
"""

from __future__ import annotations

from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9  # PROVISIONAL (D47): retune on V8 data from #21
SUBSET_MIN_COVER = 0.6  # PROVISIONAL (D47): share of the longer name a subset must cover
MIN_FUZZY_CHARS = 4  # names shorter than this (spaces left out) are matched exactly or not at all
WORD_VARIANT_MIN = 0.75  # PROVISIONAL (D47): two words this similar are spellings of one word


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def token_set_ratio(a: str, b: str) -> float:
    """Similarity of two normalized names, 0 to 1."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    shared = " ".join(sorted(ta & tb))
    ca = f"{shared} {' '.join(sorted(ta - tb))}".strip()
    cb = f"{shared} {' '.join(sorted(tb - ta))}".strip()
    whole = _ratio(ca, cb)
    if not shared:
        return whole
    if ta <= tb or tb <= ta:
        if len(shared) < SUBSET_MIN_COVER * max(len(ca), len(cb)):
            return whole  # a short name inside a longer one: no subset shortcut
        return max(whole, _ratio(shared, ca), _ratio(shared, cb))
    if not _spelling_variants(ta - tb, tb - ta):
        # Each side has a word the other lacks, and they aren't spellings of one word: different
        # names that share words ("Studio 54 Band", "Studio 84 Band"). Score the shared part only.
        return min(whole, len(shared) / max(len(ca), len(cb)))
    return max(whole, _ratio(shared, ca), _ratio(shared, cb))


def _spelling_variants(da: set[str], db: set[str]) -> bool:
    """Every unshared word has a close spelling on the other side ("lantren", "lantern")."""
    return all(
        max(_ratio(x, y) for y in other) >= WORD_VARIANT_MIN
        for side, other in ((da, db), (db, da))
        for x in side
    )


def could_match(a: str, b: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    """A cheap upper bound: False means `token_set_ratio(a, b)` can't reach `threshold`."""
    if set(a.split()) & set(b.split()):
        return True
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    m = SequenceMatcher(None, sa, sb, autojunk=False)
    return m.real_quick_ratio() >= threshold and m.quick_ratio() >= threshold


def fuzzy_eligible(norm: str) -> bool:
    return len(norm.replace(" ", "")) >= MIN_FUZZY_CHARS


def best_similarity(forms: list[str], names: list[str]) -> float:
    """The best similarity between any of an agent pick's forms and any of an entity's names."""
    best = 0.0
    for a in forms:
        for b in names:
            if could_match(a, b):
                best = max(best, token_set_ratio(a, b))
    return best
