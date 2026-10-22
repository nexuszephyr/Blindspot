"""Qloo tools for the grounded agent (#42, D52): exactly two function definitions and their executor.

- `qloo_search(query, type)`: `/search` with one of the domain's `search_types`, `take` 10. Returns
  name, type and id only.
- `qloo_recommend(domain, city, interests)`: each free-text interest is resolved to an id first (a
  tag of the domain's families via `/v2/tags`, else an entity whose name matches exactly via
  `/search`). Unresolved interests are dropped and reported back; free text never goes where an id
  belongs (AGENTS §10). The query is the domain's standard shape built here (type, domain params,
  the city's location param), plus `signal.interests.tags` / `.entities`, `take` 20. Returns name,
  type, id and rank only: never scores, params or the raw response.

**Honesty rule.** The answer key is the persona's audience plus the location (D35, D38). The tools
take no audience, demographic or raw Qloo parameter from the model, never send a
`signal.demographics.*` parameter, and refuse any query whose shape (params without `take` and the
own-seed exclusion) equals one of the exam's answer-key specs. So "grounded" can't mean "copied the
answer key", on held-out scenarios too.

Model arguments are untrusted (rule 10): pydantic with `extra=forbid`, length limits and enums. An
invalid call is a tool error sent back to the model, never an exception and never a Qloo call.
Every Qloo call goes through `QlooClient` with the caller's `RequestContext` (run id, per-run budget,
deadline, cache, breaker, call log).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..catalog import Catalog, Domain
from ..idlock import EXACT, name_score, resolve_tag
from ..qloo.client import QlooClient
from ..qloo.context import RequestContext
from ..qloo.errors import InvalidQuery
from ..qloo.models import QlooResult
from ..qloo.params import check_ids, check_params, split_list
from ..targets import parse

SEARCH = "qloo_search"
RECOMMEND = "qloo_recommend"
TOOL_NAMES = (SEARCH, RECOMMEND)

SEARCH_TAKE = 10
RECOMMEND_TAKE = 20
TAG_TAKE = 10  # /v2/tags hits read per interest (a word matches tags in many families, #15)
ENTITY_TAKE = 5  # /search hits read per interest when no tag matched
MAX_INTERESTS = 5
MAX_QUERY_CHARS = 100
MAX_INTEREST_CHARS = 80
MAX_ARGUMENT_CHARS = 2000
MAX_NAME_CHARS = 200
DEFAULT_MAX_QLOO_CALLS = 24  # per scenario: 4 recommend calls of 5 interests need up to 44

# The answer key's signal family (audience, age, gender): never sent by a tool.
FORBIDDEN_PREFIX = "signal.demographics."
# Left out when comparing a tool query with an answer-key spec: `take` doesn't change the top of a
# list (E12), and the own-seed exclusion only removes the persona's own seeds.
SHAPE_IGNORED = frozenset({"take", "filter.exclude.entities"})

StopReason = Literal["budget", "deadline"]
Shape = frozenset[tuple[str, tuple[str, ...]]]


def query_shape(params: Mapping[str, Any]) -> Shape:
    """A query's comparable form: every non-empty param except `SHAPE_IGNORED`, list values sorted."""
    out = set()
    for k, v in params.items():
        vals = tuple(sorted(split_list(v)))
        if k not in SHAPE_IGNORED and vals:
            out.add((k, vals))
    return frozenset(out)


class HonestyError(InvalidQuery):
    """A tool query that could reproduce an answer key. Refused before any call."""


def assert_honest(params: Mapping[str, Any], answer_key_shapes: Iterable[Shape] = ()) -> None:
    """The honesty rule: no demographic signal, and no answer-key shape."""
    bad = sorted(k for k in params if k.startswith(FORBIDDEN_PREFIX))
    if bad:
        raise HonestyError(f"tool queries never send {bad}: that is the answer key's signal")
    if query_shape(params) in set(answer_key_shapes):
        raise HonestyError("this query has the shape of an answer-key query")


# --------------------------------------------------------------------------- arguments
Interest = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INTEREST_CHARS)
]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    type: str = Field(max_length=64)


class RecommendArgs(_Args):
    domain: str = Field(max_length=64)
    city: str = Field(max_length=64)
    interests: list[Interest] = Field(min_length=1, max_length=MAX_INTERESTS)


def tool_definitions(catalog: Catalog, domain_id: str) -> list[dict[str, Any]]:
    """The two JSON-schema function definitions for a scenario in `domain_id`."""
    search_types = catalog.domain(domain_id).search_types
    return [
        {
            "type": "function",
            "function": {
                "name": SEARCH,
                "description": (
                    "Find real entities by name in Qloo's taste graph. Returns up to "
                    f"{SEARCH_TAKE} matches with name, type and id."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                        "type": {"type": "string", "enum": list(search_types)},
                    },
                    "required": ["query", "type"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": RECOMMEND,
                "description": (
                    "Qloo's ranked recommendations in a domain and city for a few stated interests "
                    "(genres, cuisines, styles or names of things the person likes). Returns up to "
                    f"{RECOMMEND_TAKE} entities with rank, name, type and id. Interests Qloo can't "
                    "match are reported back."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "enum": [d.id for d in catalog.domains]},
                        "city": {"type": "string", "enum": [c.id for c in catalog.cities]},
                        "interests": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1, "maxLength": MAX_INTEREST_CHARS},
                            "minItems": 1,
                            "maxItems": MAX_INTERESTS,
                        },
                    },
                    "required": ["domain", "city", "interests"],
                    "additionalProperties": False,
                },
            },
        },
    ]


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class QlooCall:
    """One client call a tool made: what Truth Receipts (#51) can show with the call log."""

    endpoint: str
    call_id: int | None
    served_from: str
    outcome: str


@dataclass
class ToolResult:
    """What one tool call did. `content` is the only part the model sees."""

    name: str
    ok: bool
    content: str
    error: str = ""
    stop: StopReason | None = None  # the per-scenario budget, the run budget or the deadline ran out
    entity_ids: list[str] = field(default_factory=list)  # entities returned to the model
    qloo_calls: list[QlooCall] = field(default_factory=list)


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _error(name: str, message: str, *, stop: StopReason | None = None, calls=None) -> ToolResult:
    return ToolResult(
        name=name,
        ok=False,
        content=_dump({"ok": False, "error": message}),
        error=message,
        stop=stop,
        qloo_calls=list(calls or []),
    )


def _validation_message(exc: ValidationError) -> str:
    """Field and problem only: the model's own value is not echoed back."""
    parts = []
    for e in exc.errors()[:5]:
        loc = ".".join(str(x) for x in e["loc"]) or "arguments"
        parts.append(f"{loc}: {e['msg']}")
    return "invalid arguments: " + "; ".join(parts)


def _stop_for(res: QlooResult) -> StopReason | None:
    """A client result that means "no more Qloo for this run": the budget or the deadline."""
    if res.ok or res.outcome != "not_live":
        return None
    reason = (res.reason or "").lower()
    if "budget" in reason:
        return "budget"
    if "deadline" in reason:
        return "deadline"
    return None


class _Stop(Exception):
    def __init__(self, stop: StopReason, message: str):
        super().__init__(message)
        self.stop = stop
        self.message = message


# --------------------------------------------------------------------------- executor
class QlooTools:
    """Runs the model's tool calls for one scenario's conversation.

    `answer_key_shapes`: `query_shape()` of every answer-key spec in the exam (local and baseline);
    a tool query with one of those shapes is refused. `max_qloo_calls` caps the client calls the
    tools make for this conversation (cached ones included), on top of the client's own budgets."""

    def __init__(
        self,
        client: QlooClient,
        catalog: Catalog,
        ctx: RequestContext,
        *,
        domain_id: str,
        answer_key_shapes: Iterable[Shape] = (),
        max_qloo_calls: int = DEFAULT_MAX_QLOO_CALLS,
    ):
        self.client = client
        self.catalog = catalog
        self.ctx = ctx
        self.domain = catalog.domain(domain_id)
        self.answer_key_shapes = frozenset(answer_key_shapes)
        self.max_qloo_calls = max_qloo_calls
        self.qloo_calls: list[QlooCall] = []
        self.returned_ids: set[str] = set()  # every entity id a tool showed the model
        self._search_types = sorted({t for d in catalog.domains for t in d.search_types})

    def definitions(self) -> list[dict[str, Any]]:
        return tool_definitions(self.catalog, self.domain.id)

    async def execute(self, name: str, arguments: str) -> ToolResult:
        """Run one tool call. Never raises: every problem is a tool error for the model."""
        if name not in TOOL_NAMES:
            return _error(parse.clean(name, 64) or "unknown", f"unknown tool; use one of {list(TOOL_NAMES)}")
        if len(arguments) > MAX_ARGUMENT_CHARS:
            return _error(name, f"arguments are longer than {MAX_ARGUMENT_CHARS} characters")
        try:
            raw = json.loads(arguments or "{}")
        except ValueError:
            return _error(name, "arguments are not valid JSON")
        if not isinstance(raw, dict):
            return _error(name, "arguments must be a JSON object")
        before = len(self.qloo_calls)
        try:
            if name == SEARCH:
                result = await self._search(SearchArgs.model_validate(raw))
            else:
                result = await self._recommend(RecommendArgs.model_validate(raw))
        except ValidationError as exc:
            return _error(name, _validation_message(exc))
        except _Stop as stop:
            return _error(name, stop.message, stop=stop.stop, calls=self.qloo_calls[before:])
        result.qloo_calls = self.qloo_calls[before:]
        self.returned_ids.update(result.entity_ids)
        return result

    # ------------------------------------------------------------------ one client call
    async def _call(self, coro_fn) -> QlooResult:
        """Run one client call within the per-scenario cap and the deadline; record it."""
        if len(self.qloo_calls) >= self.max_qloo_calls:
            raise _Stop("budget", f"the Qloo call limit for this question ({self.max_qloo_calls}) is used up")
        rem = self.ctx.remaining()
        if rem is not None and rem <= 0:
            raise _Stop("deadline", "the time for this question is up")
        res: QlooResult = await coro_fn()
        self.qloo_calls.append(QlooCall(res.endpoint, res.call_id, res.served_from, res.outcome))
        if stop := _stop_for(res):
            raise _Stop(stop, f"Qloo can't be asked again for this question ({stop})")
        return res

    @staticmethod
    def _unavailable(res: QlooResult) -> str:
        return "Qloo is unavailable right now" if res.outcome != "invalid" else "Qloo refused the query"

    # ------------------------------------------------------------------ qloo_search
    async def _search(self, args: SearchArgs) -> ToolResult:
        query = parse.clean(args.query, MAX_QUERY_CHARS)
        if not query:
            return _error(SEARCH, "query is empty")
        if args.type not in self.domain.search_types:
            return _error(SEARCH, f"type must be one of {self.domain.search_types}")
        res = await self._call(
            lambda: self.client.search_entities(query, self.ctx, types=[args.type], take=SEARCH_TAKE)
        )
        if not res.ok:
            return _error(SEARCH, self._unavailable(res))
        items = []
        for e in res.entities[:SEARCH_TAKE]:
            if e.entity_id and e.name:
                items.append({"name": parse.clean(e.name, MAX_NAME_CHARS), "type": e.kind, "id": e.entity_id})
        return ToolResult(
            name=SEARCH,
            ok=True,
            content=_dump({"ok": True, "results": items}),
            entity_ids=[i["id"] for i in items],
        )

    # ------------------------------------------------------------------ qloo_recommend
    async def _resolve(self, interest: str, domain_id: str) -> tuple[str, str] | str:
        """(`tag` | `entity`, id), or why it didn't resolve. A tag must be in the asked domain's
        families (`idlock.resolve_tag`); an entity, of any audited type, must match the name
        exactly (a cross-domain taste, such as an artist for restaurants, is a valid signal)."""
        res = await self._call(lambda: self.client.search_tags(self.ctx, query=interest, take=TAG_TAKE))
        if res.ok:
            decision = resolve_tag(interest, domain_id, [e.raw for e in res.entities])
            if decision.id:
                return "tag", decision.id
        elif res.outcome != "empty":
            return "lookup failed"
        res = await self._call(
            lambda: self.client.search_entities(
                interest, self.ctx, types=self._search_types, take=ENTITY_TAKE
            )
        )
        if not res.ok:
            return "not found" if res.outcome == "empty" else "lookup failed"
        for e in res.entities[:ENTITY_TAKE]:
            if e.entity_id and name_score(interest, e.name or "") == EXACT:
                return "entity", e.entity_id
        return "not found"

    def build_params(
        self, domain: Domain, city_id: str, tags: list[str], entities: list[str]
    ) -> dict[str, Any]:
        """The domain's standard query shape plus the resolved interest ids (D52)."""
        params: dict[str, Any] = {
            "filter.type": domain.filter_type,
            **domain.params,
            **domain.location_params(self.catalog.city(city_id)),
            "take": RECOMMEND_TAKE,
        }
        if tags:
            params["signal.interests.tags"] = sorted(set(tags))
        if entities:
            params["signal.interests.entities"] = sorted(set(entities))
        return params

    async def _recommend(self, args: RecommendArgs) -> ToolResult:
        domain_ids = [d.id for d in self.catalog.domains]
        city_ids = [c.id for c in self.catalog.cities]
        if args.domain not in domain_ids:
            return _error(RECOMMEND, f"domain must be one of {domain_ids}")
        if args.city not in city_ids:
            return _error(RECOMMEND, f"city must be one of {city_ids}")
        domain = self.catalog.domain(args.domain)
        tags: list[str] = []
        entities: list[str] = []
        unresolved: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in args.interests:
            interest = parse.clean(raw, MAX_INTEREST_CHARS)
            if not interest or interest.casefold() in seen:
                continue
            seen.add(interest.casefold())
            got = await self._resolve(interest, domain.id)
            if isinstance(got, str):
                unresolved.append({"interest": interest, "reason": got})
            elif got[0] == "tag":
                tags.append(got[1])
            else:
                entities.append(got[1])
        if not tags and not entities:
            return ToolResult(
                name=RECOMMEND,
                ok=False,
                content=_dump(
                    {"ok": False, "error": "no interest matched anything in Qloo", "unresolved": unresolved}
                ),
                error="no interest resolved",
            )
        params = self.build_params(domain, args.city, tags, entities)
        try:
            check_params("/v2/insights", params, strict=True)
            check_ids(params)
            assert_honest(params, self.answer_key_shapes)
        except InvalidQuery as exc:
            return _error(RECOMMEND, f"query refused: {exc.reason}")
        res = await self._call(lambda: self.client.insights(params, self.ctx))
        if not res.ok and res.outcome != "empty":
            return _error(RECOMMEND, self._unavailable(res))
        items = []
        for e in res.entities:
            if e.entity_id and e.name:
                items.append(
                    {
                        "rank": len(items) + 1,
                        "name": parse.clean(e.name, MAX_NAME_CHARS),
                        "type": e.kind,
                        "id": e.entity_id,
                    }
                )
            if len(items) == RECOMMEND_TAKE:
                break
        body: dict[str, Any] = {"ok": True, "results": items}
        if unresolved:
            body["unresolved"] = unresolved
        return ToolResult(name=RECOMMEND, ok=True, content=_dump(body), entity_ids=[i["id"] for i in items])


__all__ = [
    "DEFAULT_MAX_QLOO_CALLS",
    "RECOMMEND",
    "RECOMMEND_TAKE",
    "SEARCH",
    "SEARCH_TAKE",
    "TOOL_NAMES",
    "HonestyError",
    "QlooCall",
    "QlooTools",
    "RecommendArgs",
    "SearchArgs",
    "ToolResult",
    "assert_honest",
    "query_shape",
    "tool_definitions",
]
