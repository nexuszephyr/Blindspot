"""Request and response models.

Response field names follow Qloo's published schema (docs.qloo.com + the @devma/qloo SDK).
They are UNVERIFIED against live responses until Phase 1 recon runs with a key, so parsing is
tolerant: unknown fields are kept, and every parsed object keeps its `raw` payload.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .params import split_list

ServedFrom = Literal["live", "cache", "snapshot", "disabled", "degraded"]

# Insights filter.type: the official hackathon list (CLAUDE.md). It is `video_game`, not `videogame`.
INSIGHTS_TYPES = frozenset(
    {
        "urn:entity:artist",
        "urn:entity:book",
        "urn:entity:brand",
        "urn:entity:destination",
        "urn:entity:movie",
        "urn:entity:person",
        "urn:entity:place",
        "urn:entity:podcast",
        "urn:entity:tv_show",
        "urn:entity:video_game",
    }
)
# /search has its OWN `types` list (docs.qloo.com get-search). Spellings here are UNVERIFIED (doc)
# until recon E1 records which ones the hackathon host accepts.
SEARCH_TYPES = frozenset(
    {
        "urn:entity:artist",
        "urn:entity:book",
        "urn:entity:brand",
        "urn:entity:locality",
        "urn:entity:movie",
        "urn:entity:person",
        "urn:entity:place",
        "urn:entity:podcast",
        "urn:entity:tv_show",
        "urn:entity:videogame",
        "urn:entity:video_game",
    }
)
Outcome = Literal["ok", "empty", "qloo_error", "resolution_failed", "invalid", "disabled", "not_live"]


class Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_id: str | None = None
    name: str | None = None
    type: str | None = None
    types: list[str] = Field(default_factory=list)
    subtype: str | None = None
    popularity: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    tags: list[Any] = Field(default_factory=list)
    location: dict[str, Any] | None = None
    external: dict[str, Any] | None = None
    query: dict[str, Any] | None = None  # per-result scores (e.g. affinity) live here per docs
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("types", "tags", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> list[Any]:
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    @field_validator("properties", mode="before")
    @classmethod
    def _dictify(cls, v: Any) -> dict[str, Any]:
        return v if isinstance(v, dict) else {}

    @classmethod
    def parse(cls, d: Any) -> Entity:
        if not isinstance(d, dict):
            return cls(raw={"value": d})
        data = dict(d)
        if "entity_id" not in data and "id" in data:
            data["entity_id"] = data["id"]
        if data.get("location") is not None and not isinstance(data["location"], dict):
            data["location"] = None
        for k in ("external", "query"):
            if data.get(k) is not None and not isinstance(data[k], dict):
                data[k] = None
        try:
            return cls(**data, raw=d)
        except Exception:  # never let one odd row break a response
            return cls(
                entity_id=str(d.get("entity_id") or d.get("id") or ""), name=str(d.get("name") or ""), raw=d
            )

    @property
    def affinity(self) -> float | None:
        q = self.query or {}
        v = q.get("affinity")
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def kind(self) -> str | None:
        return self.type or self.subtype or (self.types[0] if self.types else None)


def extract_items(body: Any) -> list[Any]:
    """Find the result rows in any documented response shape.

    /search -> {"results": [...]}; /v2/insights -> {"results": {"entities": [...]}};
    tags/audiences -> {"results": {"tags"|"audiences": [...]}} (shape UNVERIFIED).
    """
    if not isinstance(body, dict):
        return []
    res = body.get("results")
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ("entities", "tags", "audiences", "results"):
            if isinstance(res.get(k), list):
                return res[k]
    for k in ("entities", "tags", "audiences"):
        if isinstance(body.get(k), list):
            return body[k]
    return []


class InsightsQuery(BaseModel):
    """GET /v2/insights parameters. Common ones are typed; any other documented dotted
    parameter can be passed through as an extra field, e.g. InsightsQuery(**{"bias.trends": "high"})."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    filter_type: str = Field(alias="filter.type")
    signal_interests_entities: list[str] | None = Field(None, alias="signal.interests.entities")
    signal_interests_tags: list[str] | None = Field(None, alias="signal.interests.tags")
    signal_location_query: str | None = Field(None, alias="signal.location.query")
    signal_demographics_age: str | None = Field(None, alias="signal.demographics.age")
    signal_demographics_gender: str | None = Field(None, alias="signal.demographics.gender")
    signal_demographics_audiences: list[str] | None = Field(None, alias="signal.demographics.audiences")
    filter_location_query: str | None = Field(None, alias="filter.location.query")
    filter_tags: list[str] | None = Field(None, alias="filter.tags")
    filter_results_entities: list[str] | None = Field(None, alias="filter.results.entities")
    filter_exclude_entities: list[str] | None = Field(None, alias="filter.exclude.entities")
    filter_popularity_min: float | None = Field(None, alias="filter.popularity.min", ge=0, le=1)
    filter_popularity_max: float | None = Field(None, alias="filter.popularity.max", ge=0, le=1)
    bias_trends: str | None = Field(None, alias="bias.trends")
    feature_explainability: bool | None = Field(None, alias="feature.explainability")
    take: int | None = Field(None, ge=1)
    page: int | None = Field(None, ge=1, le=100)

    @field_validator("filter_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in INSIGHTS_TYPES:
            hint = " (did you mean 'urn:entity:video_game'?)" if "videogame" in v else ""
            raise ValueError(f"unsupported filter.type {v!r}{hint}; valid: {sorted(INSIGHTS_TYPES)}")
        return v

    @field_validator(
        "signal_interests_entities",
        "signal_interests_tags",
        "signal_demographics_audiences",
        "filter_tags",
        "filter_results_entities",
        "filter_exclude_entities",
        mode="before",
    )
    @classmethod
    def _listify_ids(cls, v: Any) -> list[str] | None:
        # 'a,b' and ['a','b'] mean the same thing (audit F1); id *shape* is checked in params.check_ids
        return split_list(v) or None

    def to_params(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class QlooResult(BaseModel):
    """What every client call returns. It never raises to callers."""

    served_from: ServedFrom
    ok: bool
    endpoint: str
    status: int | None = None
    reason: str | None = None  # human-readable, set when degraded/disabled
    # ok | empty (200, no items) | qloo_error (Qloo said no) | resolution_failed (no id, nothing sent)
    # | invalid (rejected locally) | disabled | not_live (replay, budget, breaker, deadline)
    outcome: Outcome = "ok"
    coalesced: bool = False  # shared another request's in-flight live call (no upstream call of its own)
    unverified_params: list[str] = Field(default_factory=list)
    cache_key: str
    call_id: int | None = None
    fetched_at: float | None = None
    latency_ms: float | None = None
    body: Any = Field(default=None, repr=False)
    error_body: str | None = Field(default=None, repr=False)  # redacted, degraded only
    rate_headers: dict[str, str] = Field(default_factory=dict)  # response headers only
    entities: list[Entity] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entities)

    def summary(self, n: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "type": e.kind,
                "popularity": e.popularity,
                "affinity": e.affinity,
            }
            for e in self.entities[:n]
        ]
