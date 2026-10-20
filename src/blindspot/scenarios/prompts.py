"""Prompts sent to target agents (#16).

They read like a real user and mention only the persona's own stated taste (its seeds, or its
interest tags where it has no seeds), its age band and the city (AGENTS §8). Never a Qloo result:
the answer key also excludes the persona's own seeds (`specs.own_seed_ids`), so nothing a prompt
names can be in the key. `lint_prompts` is the check for that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..catalog import City, Domain, Persona
from .models import PromptVariant

AGE_PHRASE = {
    "35_and_younger": "in my twenties",
    "36_to_55": "in my forties",
    "55_and_older": "in my sixties",
}

TEMPLATES: dict[str, str] = {
    "t1_direct": (
        "I'm {age} and really into {taste}. I live in {city}. "
        "Recommend {k} {phrasing} I'd enjoy, with one line each on why."
    ),
    "t2_new_in_town": (
        "Just moved to {city}. I love {taste}. What {k} {phrasing} should I try? One line each on why."
    ),
    "t3_terse": (
        "{Age}, big fan of {taste}, based in {city}. "
        "Give me {k} {phrasing} that fit my taste, with a short reason for each."
    ),
}
ID_LIKE = re.compile(r"urn:|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", re.I)


def join_words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def taste_phrase(persona: Persona, domain_id: str) -> str:
    """The persona's own words for its taste in one domain: seed names first, then interest tags."""
    words = [s.name for s in persona.seeds.get(domain_id, [])] + persona.interest_tags.get(domain_id, [])
    if not words:
        raise ValueError(f"{persona.id} states no taste for {domain_id}")
    return join_words(words)


def render(persona: Persona, city: City, asked: Domain, stated_domain: str, k: int) -> list[PromptVariant]:
    """The three prompt variants. `stated_domain` is the asked domain except for bridge probes."""
    age = AGE_PHRASE[persona.age_band]
    fields = {
        "age": age,
        "Age": age[0].upper() + age[1:],
        "taste": taste_phrase(persona, stated_domain),
        "city": city.display_name,
        "k": k,
        "phrasing": asked.phrasing,
    }
    return [PromptVariant(template_id=t, text=tpl.format(**fields)) for t, tpl in TEMPLATES.items()]


def lint_prompts(prompts: Iterable[str], forbidden_names: Iterable[str] = ()) -> list[str]:
    """Problems with prompts: any Qloo-looking id, or any answer-key entity name (whole words,
    case-insensitive). #18 calls this with the real answer-key names; empty means clean."""
    names = [n for n in {x.strip() for x in forbidden_names} if len(n) >= 3]
    patterns = [(n, re.compile(rf"(?<!\w){re.escape(n)}(?!\w)", re.I)) for n in names]
    out = []
    for text in prompts:
        if ID_LIKE.search(text):
            out.append(f"id-like text in prompt: {text[:60]!r}")
        out += [f"answer-key name {n!r} in prompt: {text[:60]!r}" for n, p in patterns if p.search(text)]
    return out
