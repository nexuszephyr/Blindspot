"""Scenario schema (#16): one exam question and the Qloo queries that form its answer key."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
Tag = Literal["demo", "extended", "holdout"]


def content_hash(data: Any, prefix: str, n: int = 12) -> str:
    """`<prefix>_` + the first n hex of sha256 over canonical JSON (sorted keys, no spaces)."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}_{hashlib.sha256(canonical.encode()).hexdigest()[:n]}"


class QuerySpec(BaseModel):
    """GET /v2/insights parameters, ids only (resolved through the lock). `key` identifies equal
    specs, so the answer-key build (#18) fetches a shared spec once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    params: dict[str, str | int | list[str]]
    key: str = ""

    @classmethod
    def of(cls, params: dict[str, Any]) -> QuerySpec:
        clean = {k: (sorted(v) if isinstance(v, list) else v) for k, v in sorted(params.items())}
        return cls(params=clean, key=content_hash(clean, "qs"))


class PromptVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: str
    text: str


class Scenario(BaseModel):
    """One exam question. The id is a content hash of everything except `id` and `tags`, so the
    same config and lock always give the same ids; tags only say which subsets it belongs to."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    version: int = SCHEMA_VERSION
    persona_id: str
    city_id: str
    domain_id: str  # the domain asked for
    probe_type: str
    stated_domain: str | None = None  # bridge: the domain the taste is stated in
    signal: str  # the persona signal in the answer key (query_shape.signal)
    local_spec: QuerySpec  # audience/taste + this city: the answer key and the membership pool
    global_spec: QuerySpec  # the baseline: same taste without a location, or (a domain with
    # `baseline: city`) this city without the persona signal (D43)
    depth: int  # answer-key depth (E12, D34): the top `depth` of local_spec
    pool: int  # membership pool (E14, D36): local_spec's take
    k: int  # how many recommendations the target is asked for
    prompt_variants: list[PromptVariant] = Field(min_length=1)
    pair_id: str | None = None  # swap: same persona, two cities; audience: same city, two personas
    conditions: list[str] = Field(default_factory=list)  # checks the answer-key build (#18) must pass
    # before this scenario is scored; they need answer keys, so the generator only records them
    tags: list[Tag] = Field(default_factory=list)

    def content(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"id", "tags"})

    def question_key(self) -> str:
        """What the target is asked and graded against, whichever probe asked it: the same question
        under two probes has one key (holdout follows it, #43)."""
        q = [self.persona_id, self.city_id, self.domain_id, self.stated_domain, self.local_spec.key]
        return content_hash([*q, [v.text for v in self.prompt_variants]], "q")

    def with_id(self) -> Scenario:
        return self.model_copy(update={"id": content_hash(self.content(), "sc")})
