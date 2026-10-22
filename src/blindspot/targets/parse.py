"""The fixed system prompt and the tolerant parser for target answers (#27).

Providers differ on JSON mode, so nothing depends on it. The parser strips code fences and takes the
first JSON object; the client then allows one repair retry, and falls back to list-like lines.
Truncated output keeps the complete items it has. Every string is cleaned (control characters
removed, whitespace collapsed, length capped): model text is untrusted data (rule 10).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import Recommendation

SYSTEM_PROMPT = (
    "You give personal recommendations. Reply with only a JSON object and no other text, in exactly "
    'this shape: {{"recommendations": [{{"name": "...", "why": "..."}}]}}. Give {k} recommendations. '
    '"name" is the usual name of one real thing, nothing else; "why" is one short sentence.'
)
REPAIR_PROMPT = (
    "That reply wasn't the JSON object I asked for. Send the same recommendations again as only the "
    'JSON object {"recommendations": [{"name": "...", "why": "..."}]}, with no other text.'
)
MAX_NAME, MAX_WHY = 200, 500
MAX_DECODE_TRIES = 50  # opening braces tried before giving up on finding a JSON object

_FENCE = re.compile(r"```[A-Za-z0-9_-]*\s*\n?(.*?)```", re.S)
# C0/C1 controls, zero-width and bidi marks, line/paragraph separators, bidi embeddings and
# isolates, BOM. Written as escapes: literal bidi characters in source are a Trojan Source risk.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")
_OBJECT = re.compile(r"\{[^{}]*\}")  # a flat object: a complete item inside truncated JSON
_LIST_LINE = re.compile(r"^\s*(?:\d{1,2}\s*[.)]|[-*•])\s+(.+?)\s*$")
_SPLIT_WHY = re.compile(r"\s+[-–—]\s+|:\s+")
_REFUSAL = re.compile(
    r"^\W*(?:i'?m sorry|sorry|i apologi[sz]e|i (?:can ?not|can't|cannot|won't|am unable|'m unable|"
    r"am not able|'m not able)|unfortunately,? i)",
    re.I,
)
_NAME_KEYS = ("name", "title")
_WHY_KEYS = ("why", "reason", "description")


def system_prompt(k: int) -> str:
    return SYSTEM_PROMPT.format(k=k)


def clean(text: object, limit: int) -> str:
    s = _CONTROL.sub(" ", str(text))
    s = " ".join(s.split())
    return s[:limit].strip()


def strip_fences(text: str) -> str:
    """The body of the first fenced block, or the text itself."""
    m = _FENCE.search(text)
    return m[1] if m else text


def first_json(text: str) -> Any | None:
    """The first JSON object (or array) that decodes, scanning opening brackets left to right."""
    decoder = json.JSONDecoder()
    body = strip_fences(text)
    tries = 0
    for m in re.finditer(r"[\[{]", body):
        tries += 1
        if tries > MAX_DECODE_TRIES:
            break
        try:
            obj, _ = decoder.raw_decode(body, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict | list):
            return obj
    return None


def _item(raw: Any) -> Recommendation | None:
    if isinstance(raw, str):
        name, why = raw, ""
    elif isinstance(raw, dict):
        name = next((raw[k] for k in _NAME_KEYS if isinstance(raw.get(k), str)), "")
        why = next((raw[k] for k in _WHY_KEYS if isinstance(raw.get(k), str)), "")
    else:
        return None
    name = clean(name, MAX_NAME)
    return Recommendation(name=name, why=clean(why, MAX_WHY)) if name else None


def items_from_json(obj: Any, k: int) -> list[Recommendation] | None:
    """Items from a decoded answer, at most k. None when the shape isn't a recommendation list;
    an empty list when it is one with nothing in it."""
    raw: Any = None
    if isinstance(obj, list):
        raw = obj
    elif isinstance(obj, dict):
        raw = obj.get("recommendations")
        if not isinstance(raw, list):  # another key holding the list, e.g. {"results": [...]}
            lists = [v for v in obj.values() if isinstance(v, list)]
            raw = lists[0] if len(lists) == 1 else None
    if not isinstance(raw, list):
        return None
    items = [it for it in map(_item, raw) if it]
    if raw and not items:
        return None  # a list, but of nothing recognizable
    return items[:k]


def items_from_partial(text: str, k: int) -> list[Recommendation]:
    """Complete items inside truncated JSON (`finish_reason: length`)."""
    out = []
    for m in _OBJECT.finditer(strip_fences(text)):
        try:
            it = _item(json.loads(m[0]))
        except ValueError:
            continue
        if it:
            out.append(it)
    return out[:k]


def items_from_lines(text: str, k: int) -> list[Recommendation]:
    """The last resort: numbered or bulleted lines, `name - why` or `name: why`."""
    out = []
    for line in text.splitlines():
        m = _LIST_LINE.match(line)
        if not m:
            continue
        body = m[1].replace("**", "").replace("__", "")
        name, *why = _SPLIT_WHY.split(body, maxsplit=1)
        it = _item({"name": name.strip(" \"'`"), "why": why[0] if why else ""})
        if it:
            out.append(it)
    return out[:k]


def looks_refused(text: str) -> bool:
    """A short answer that opens with an apology or a can't. Only asked of text that isn't JSON."""
    return bool(_REFUSAL.match(text.strip())) and len(text) < 1500


__all__ = [
    "REPAIR_PROMPT",
    "clean",
    "first_json",
    "items_from_json",
    "items_from_lines",
    "items_from_partial",
    "looks_refused",
    "strip_fences",
    "system_prompt",
]
