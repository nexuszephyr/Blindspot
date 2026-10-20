"""Name normalization for matching an agent's free-text picks to Qloo entities (#18, shared with the
resolver in #29).

`normalize_name` folds what doesn't change identity: Unicode form (NFKC), case (casefold, so "ß"
becomes "ss"), accents on Latin letters ("Café Müller" becomes "cafe muller"), punctuation and
symbols, a bracketed note ("Noodle Bar (Example Ward)"), a trailing location given by the caller
("Noodle Bar - Example City", "Noodle Bar, Example City") and extra whitespace. Non-Latin
text is kept as written: Japanese "ラーメン" keeps its voiced marks, because stripping combining
marks there would change the word.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_BRACKETS = re.compile(r"\s*[\(\[\{（【][^\)\]\}）】]*[\)\]\}）】]\s*")
_TAIL_SEP = re.compile(r"\s+[-–—|/@]\s+|\s*,\s*")  # " - ", " | ", ", " (words like "in" are names too)


def _is_latin(ch: str) -> bool:
    try:
        return "LATIN" in unicodedata.name(ch)
    except ValueError:
        return False


def _strip_latin_accents(s: str) -> str:
    """Drop combining marks that sit on Latin letters only (é -> e, ü -> u); marks on any other
    script (Japanese dakuten, Devanagari vowel signs) stay."""
    out: list[str] = []
    base_latin = False
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.combining(ch):
            if not base_latin:
                out.append(ch)
            continue
        base_latin = _is_latin(ch)
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _punct_to_space(s: str) -> str:
    """Letters, digits and marks of every script are kept; punctuation and symbols become spaces."""
    return "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in s)


def normalize_name(name: str, *, location_words: Iterable[str] = ()) -> str:
    """Comparable form of a name. `location_words` (city names from config/, never hard-coded) are
    removed when they trail the name after a separator ("X - Example City", "X, Example City")."""
    s = unicodedata.normalize("NFKC", name or "").casefold()
    s = _BRACKETS.sub(" ", s)
    places = [p for p in {normalize_name(w) for w in location_words if w} if p]
    if places:
        parts = _TAIL_SEP.split(s)
        while len(parts) > 1 and any(p in _plain(parts[-1]) for p in places):
            parts.pop()
        s = " ".join(parts)
    s = _strip_latin_accents(s)
    return " ".join(_punct_to_space(s).split())


def _plain(s: str) -> str:
    return " ".join(_punct_to_space(_strip_latin_accents(s)).split())


def aliases(name: str, *, location_words: Iterable[str] = ()) -> list[str]:
    """Extra comparable forms: the full name with any bracketed part kept, without a leading article,
    and with "&" read as "and"."""
    norm = normalize_name(name, location_words=location_words)
    out = {norm, _plain(unicodedata.normalize("NFKC", name or "").casefold())}  # brackets kept, too
    for article in ("the ", "a ", "la ", "le ", "el ", "les ", "los ", "die ", "der ", "das "):
        if norm.startswith(article):
            out.add(norm[len(article) :])
    if "&" in name:
        out.add(normalize_name(name.replace("&", " and "), location_words=location_words))
    return sorted(x for x in out if x)
