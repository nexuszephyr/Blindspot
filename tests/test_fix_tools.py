"""#42: the grounded agent's Qloo tools and tool loop, offline.

A scripted OpenAI-compatible server (httpx.MockTransport) plays the model; `tests/fakeqloo.py` plays
Qloo behind the real `QlooClient`. Synthetic ids and names only; no city name is written here
(rule 9): cities come from config. The model key is fake, built at runtime.
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from blindspot.catalog import load_catalog
from blindspot.fix import Caps, QlooTools, assert_honest, query_shape, run_grounded, tool_definitions
from blindspot.fix import tools as tools_mod
from blindspot.fix.tools import RECOMMEND, SEARCH, HonestyError
from blindspot.idlock import load_lock
from blindspot.qloo.cache import normalize_params
from blindspot.qloo.client import QlooClient
from blindspot.qloo.context import RequestContext
from blindspot.qloo.snapshot import Snapshot
from blindspot.scenarios import generate
from blindspot.targets import OpenAICompatTarget, env_lookup, get_target, load_targets
from tests.fakeqloo import FakeQloo, FakeTransport

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LLM_BASE = "https://llm.example.test/v1"
LLM_KEY = "fake-groq-" + "key-tools-0123456789"
ENV = {"GROQ_API_KEY": LLM_KEY, "GROQ_BASE_URL": LLM_BASE}
UNKNOWN = "zz no such interest"
FAMILIES = ("genre:place:restaurant", "genre:music", "genre:media", "category:brand")
# Words that leak an answer key or a score if the model ever sees them.
LEAKS = ("affinity", "popularity", "filter.", "signal.", "urn:audience", "take")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CONFIG)


@pytest.fixture(scope="module")
def exam(catalog):
    return generate(catalog, load_lock(CONFIG))


@pytest.fixture(scope="module")
def agent_cfg():
    targets = load_targets(CONFIG)
    return get_target(targets, next(t.id for t in targets if t.fix_loop_agent))


def tags_body(params: dict) -> dict:
    """The same tag name in every family, so each domain finds one of its own; none for UNKNOWN or
    for "Band ..." names (those resolve as entities)."""
    q = params.get("filter.query", "")
    if q == UNKNOWN or q.startswith("Band "):
        return {"success": True, "results": {"tags": []}}
    slug = "_".join(q.lower().split())
    tags = [{"id": f"urn:tag:{fam}:synthetic_{slug}", "name": q, "popularity": 0.5} for fam in FAMILIES]
    return {"success": True, "results": {"tags": tags}}


def search_body(params: dict) -> dict:
    q = params.get("query", "")
    if q == UNKNOWN:
        return {"results": []}
    n = sum(map(ord, q)) % 1000
    return {
        "results": [
            {
                "entity_id": f"00000000-0000-4000-8000-{n:012d}",
                "name": q,
                "types": ["urn:entity:artist"],
                "popularity": 0.9,
            }
        ]
    }


@pytest.fixture
def qloo(make_settings, tmp_path):
    made = []

    def _make(**kw):
        fake = FakeQloo()
        fake.tags_body = tags_body
        fake.search_body = search_body
        s = make_settings(
            **{
                "qloo_run_cap": 1000,
                "qloo_client_hourly_cap": 1000,
                "qloo_daily_live_cap": 1000,
                "qloo_demo_reserve": 0,
                "qloo_cache_path": tmp_path / f"cache{len(made)}.sqlite",  # one cache per client
                **kw,
            }
        )

        async def no_sleep(_):
            return None

        client = QlooClient(s, snapshot=Snapshot(), transport=FakeTransport(fake), sleep=no_sleep)
        made.append(client)
        return fake, client

    yield _make
    for c in made:
        c.db.close()


def ctx(**kw) -> RequestContext:
    return RequestContext(client_id="c_fix_test", purpose="fix", **kw)


class ScriptedModel:
    """An OpenAI-compatible endpoint that answers from a script: each step is a reply dict, or a
    function of the request body that returns one. Records every request body."""

    def __init__(self, *steps, delay: float = 0.0):
        self.steps = list(steps)
        self.requests: list[dict] = []
        self.delay = delay

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {LLM_KEY}"
        body = json.loads(request.content)
        self.requests.append(body)
        if self.delay:
            await asyncio.sleep(self.delay)
        step = self.steps.pop(0) if self.steps else answer([])
        reply = step(body) if callable(step) else step
        return httpx.Response(200, json=reply)


def _completion(message: dict, finish: str) -> dict:
    return {
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 150,
            "completion_tokens_details": {"reasoning_tokens": 30},
        },
    }


def call(name: str, arguments, call_id: str = "call_1") -> dict:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return _completion(
        {
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
            ],
        },
        "tool_calls",
    )


def answer(names: list[str]) -> dict:
    recs = {"recommendations": [{"name": n, "why": "it fits"} for n in names]}
    return _completion({"content": json.dumps(recs)}, "stop")


def answer_from_tools(body: dict) -> dict:
    """Recommend the names the last tool message returned."""
    last = next(m for m in reversed(body["messages"]) if m["role"] == "tool")
    return answer([r["name"] for r in json.loads(last["content"]).get("results", [])][:3])


def make_agent(cfg, model: ScriptedModel) -> OpenAICompatTarget:
    async def no_sleep(_):
        return None

    return OpenAICompatTarget(
        cfg, None, env=env_lookup(ENV), transport=httpx.MockTransport(model), sleep=no_sleep
    )


def a_scenario(exam, domain_id: str):
    return next(s for s in exam.scenarios if s.domain_id == domain_id)


def insights_seen(fake: FakeQloo) -> list[dict]:
    return [s.params for s in fake.seen if s.path == "/v2/insights"]


# --------------------------------------------------------------------------- the loop
async def test_full_loop_to_a_final_answer(catalog, exam, agent_cfg, qloo):
    sc = a_scenario(exam, "dining")
    fake, client = qloo()
    c = ctx()
    tools = QlooTools(client, catalog, c, domain_id=sc.domain_id)
    model = ScriptedModel(
        call(
            RECOMMEND, {"domain": sc.domain_id, "city": sc.city_id, "interests": ["street food", "noodles"]}
        ),
        answer_from_tools,
    )
    resp = await run_grounded(make_agent(agent_cfg, model), tools, sc.prompt_variants[0].text, sc.k)

    assert resp.outcome == "ok" and resp.stop == "answer", resp.detail
    assert resp.turns == 2 and len(resp.items) == 3
    assert resp.usage.calls == 2 and resp.usage.reasoning_tokens == 60
    assert resp.run_id == c.run_id and resp.target_id == agent_cfg.id
    # the agent's picks all came from the tools, and the result exposes what the tools returned
    assert len(resp.returned_ids) == 20 and resp.tool_calls[0].entity_ids == resp.returned_ids
    # the request shape: tools offered, JSON mode off with tools, D49's max_tokens
    first, second = model.requests
    assert [t["function"]["name"] for t in first["tools"]] == [SEARCH, RECOMMEND]
    assert "response_format" not in first and "tool_choice" not in first
    assert first["max_tokens"] >= 1000
    assert second["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][3]["role"] == "tool" and second["messages"][3]["tool_call_id"] == "call_1"
    # Qloo: two tag lookups, one insights query with the domain's standard shape plus tag signals
    (sent,) = insights_seen(fake)
    domain = catalog.domain(sc.domain_id)
    city = catalog.city(sc.city_id)
    assert sent["filter.type"] == domain.filter_type
    assert sent[f"{domain.location_param}.location.query"] == city.location
    assert all(sent[k] == v for k, v in domain.params.items())
    assert sent["take"] == "20" and len(sent["signal.interests.tags"].split(",")) == 2
    assert not any(k.startswith("signal.demographics") for k in sent)
    # every call is logged under the run id, with the fix purpose
    rows = client.calls.for_run(c.run_id)
    assert len(rows) == resp.qloo_calls == 3 and {r["purpose"] for r in rows} == {"fix"}


async def test_a_configured_tool_calling_target_works(catalog, exam, qloo):
    """Every target with tool_calling in config/targets.yaml runs the loop (the agent and its backup)."""
    sc = a_scenario(exam, "music")
    for cfg in [t for t in load_targets(CONFIG) if t.tool_calling and t.api_key_env == "GROQ_API_KEY"]:
        _, client = qloo()
        tools = QlooTools(client, catalog, ctx(), domain_id=sc.domain_id)
        model = ScriptedModel(
            call(RECOMMEND, {"domain": "music", "city": sc.city_id, "interests": ["jazz"]}), answer_from_tools
        )
        resp = await run_grounded(make_agent(cfg, model), tools, "prompt", 3)
        assert resp.outcome == "ok" and resp.stop == "answer", (cfg.id, resp.detail)


async def test_search_tool_returns_name_type_and_id_only(catalog, qloo):
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    res = await tools.execute(SEARCH, json.dumps({"query": "Band Synthetic", "type": "urn:entity:artist"}))
    assert res.ok
    body = json.loads(res.content)
    assert body == {
        "ok": True,
        "results": [{"name": "Band Synthetic", "type": "urn:entity:artist", "id": res.entity_ids[0]}],
    }
    (seen,) = fake.seen
    assert (
        seen.path == "/search" and seen.params["take"] == "10" and seen.params["types"] == "urn:entity:artist"
    )
    assert tools.returned_ids == set(res.entity_ids)
    # a type outside the scenario domain's search_types is refused with no call
    res = await tools.execute(SEARCH, json.dumps({"query": "x", "type": "urn:entity:place"}))
    assert not res.ok and "type must be one of" in res.error and len(fake.seen) == 1


@pytest.mark.parametrize(
    "arguments, message",
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        (json.dumps({"domain": "dining", "city": "x", "interests": ["a"]}), "city must be one of"),
        (json.dumps({"domain": "nope", "city": "x", "interests": ["a"]}), "domain must be one of"),
        (json.dumps({"domain": "dining", "interests": ["a"]}), "city"),
        (json.dumps({"domain": "dining", "city": "x", "interests": []}), "interests"),
        (json.dumps({"domain": "dining", "city": "x", "interests": ["a"] * 6}), "interests"),
        (json.dumps({"domain": "dining", "city": "x", "interests": ["a" * 81]}), "interests"),
        (json.dumps({"domain": "dining", "city": "x", "interests": [3]}), "interests"),
        # no raw Qloo params and no audience can be passed: extra fields are refused
        (
            json.dumps({"domain": "dining", "city": "x", "interests": ["a"], "audience": "urn:audience:x:y"}),
            "Extra inputs",
        ),
        (
            json.dumps(
                {"domain": "dining", "city": "x", "interests": ["a"], "signal.demographics.audiences": "u"}
            ),
            "Extra inputs",
        ),
        (
            json.dumps({"domain": "dining", "city": "x", "interests": ["a"], "filter.tags": "urn:tag:a:b"}),
            "Extra inputs",
        ),
        ("{" + " " * 2100 + "}", "longer than"),
    ],
)
async def test_invalid_arguments_are_a_tool_error_and_no_qloo_call(catalog, exam, qloo, arguments, message):
    sc = a_scenario(exam, "dining")
    fake, client = qloo()
    arguments = (
        arguments.replace('"city": "x"', f'"city": "{sc.city_id}"')
        if "city must" not in message
        else arguments
    )
    tools = QlooTools(client, catalog, ctx(), domain_id="dining")
    res = await tools.execute(RECOMMEND, arguments)
    assert not res.ok and message in res.error, res.error
    assert json.loads(res.content)["ok"] is False
    assert fake.seen == [] and tools.qloo_calls == []


async def test_invalid_arguments_and_unknown_tool_in_the_loop(catalog, agent_cfg, qloo):
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="dining")
    model = ScriptedModel(
        call("run_shell", {"cmd": "rm -rf /"}, "call_a"),
        call(RECOMMEND, '{"domain": "dining", "city": ', "call_b"),
        answer(["Something"]),
    )
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3)
    assert resp.outcome == "ok" and resp.stop == "answer"
    assert [r.ok for r in resp.tool_calls] == [False, False]
    msgs = model.requests[-1]["messages"]
    tool_msgs = [json.loads(m["content"]) for m in msgs if m["role"] == "tool"]
    assert "unknown tool" in tool_msgs[0]["error"] and "not valid JSON" in tool_msgs[1]["error"]
    assert fake.seen == []  # neither made a Qloo call
    assert resp.returned_ids == []


async def test_unresolved_interest_is_reported_and_never_sent_as_text(catalog, exam, qloo):
    sc = a_scenario(exam, "dining")
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="dining")
    args = {"domain": "dining", "city": sc.city_id, "interests": [UNKNOWN]}
    res = await tools.execute(RECOMMEND, json.dumps(args))
    assert not res.ok
    body = json.loads(res.content)
    assert body["unresolved"] == [{"interest": UNKNOWN, "reason": "not found"}]
    assert insights_seen(fake) == []  # no insights call at all
    assert [s.path for s in fake.seen] == ["/v2/tags", "/search"]

    # mixed: the unknown one is dropped and reported, the rest is sent as ids only
    args["interests"] = [UNKNOWN, "street food", "Band Synthetic"]
    res = await tools.execute(RECOMMEND, json.dumps(args))
    assert res.ok
    assert json.loads(res.content)["unresolved"] == [{"interest": UNKNOWN, "reason": "not found"}]
    (sent,) = insights_seen(fake)
    assert sent["signal.interests.tags"] == "urn:tag:genre:place:restaurant:synthetic_street_food"
    assert sent["signal.interests.entities"].count("-") == 4  # one UUID: the exact-name entity
    assert UNKNOWN not in json.dumps(sent)


async def test_entity_fallback_needs_an_exact_name(catalog, exam, qloo):
    sc = a_scenario(exam, "music")
    fake, client = qloo()
    fake.search_body = lambda p: {
        "results": [{"entity_id": "00000000-0000-4000-8000-000000000001", "name": "Band Other", "types": []}]
    }
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    res = await tools.execute(
        RECOMMEND, json.dumps({"domain": "music", "city": sc.city_id, "interests": ["Band X"]})
    )
    assert not res.ok and insights_seen(fake) == []


async def test_tool_call_cap(catalog, agent_cfg, qloo):
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    search = {"query": "Band Synthetic", "type": "urn:entity:artist"}
    two = call(SEARCH, search, "c3")  # one reply asking for two calls: the cap falls between them
    two["choices"][0]["message"]["tool_calls"].append(
        {**two["choices"][0]["message"]["tool_calls"][0], "id": "c4"}
    )
    model = ScriptedModel(*[call(SEARCH, search, f"c{i}") for i in range(3)], two, answer(["Band Synthetic"]))
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3)
    assert resp.stop == "tool_cap" and resp.outcome == "ok"
    assert [r.ran for r in resp.tool_calls] == [True] * 4 + [False]
    assert len(fake.seen) == 1  # the same search four times: one live call, three from the cache
    assert resp.qloo_calls == 4
    # every call got its tool message, and the turn after the cap asks for an answer: tool_choice none
    assert [r.get("tool_choice") for r in model.requests] == [None] * 4 + ["none"]
    last = model.requests[-1]["messages"]
    assert [m["tool_call_id"] for m in last if m["role"] == "tool"] == ["c0", "c1", "c2", "c3", "c4"]
    assert "No more tool calls" in last[-1]["content"]


async def test_reaching_the_cap_exactly_forces_the_answer(catalog, agent_cfg, qloo):
    _, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    search = {"query": "Band Synthetic", "type": "urn:entity:artist"}
    model = ScriptedModel(*[call(SEARCH, search, f"c{i}") for i in range(2)], answer(["Band Synthetic"]))
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3, caps=Caps(max_tool_calls=2))
    assert resp.stop == "tool_cap" and resp.outcome == "ok" and resp.turns == 3
    assert [r.get("tool_choice") for r in model.requests] == [None, None, "none"]


async def test_turn_cap_asks_for_the_answer_on_the_last_turn(catalog, agent_cfg, qloo):
    _, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    search = {"query": "Band Synthetic", "type": "urn:entity:artist"}
    # a model that ignores tool_choice none and keeps calling: the loop still ends
    model = ScriptedModel(*[call(SEARCH, search, f"c{i}") for i in range(10)])
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3, caps=Caps(max_turns=3))
    assert resp.stop == "turn_cap" and resp.turns == 3
    assert resp.outcome == "empty" and resp.items == []
    assert model.requests[-1]["tool_choice"] == "none"


async def test_budget_exhaustion_stops_cleanly(catalog, exam, agent_cfg, qloo):
    sc = a_scenario(exam, "dining")
    fake, client = qloo(qloo_run_cap=2)  # the per-run budget: two live calls
    c = ctx()
    tools = QlooTools(client, catalog, c, domain_id="dining")
    args = {"domain": "dining", "city": sc.city_id, "interests": ["street food", "noodles", "tacos"]}
    model = ScriptedModel(call(RECOMMEND, args), answer(["Something"]))
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3)
    assert resp.stop == "budget" and resp.outcome == "ok"
    assert len(fake.seen) == 2 and insights_seen(fake) == []
    assert client.budget.remaining(run_id=c.run_id)["run"]["remaining"] == 0
    assert model.requests[-1]["tool_choice"] == "none"
    assert "budget" in json.loads(model.requests[-1]["messages"][-1]["content"])["error"]


async def test_per_scenario_qloo_call_cap(catalog, exam, qloo):
    sc = a_scenario(exam, "dining")
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="dining", max_qloo_calls=2)
    args = {"domain": "dining", "city": sc.city_id, "interests": ["street food", "noodles", "tacos"]}
    res = await tools.execute(RECOMMEND, json.dumps(args))
    assert res.stop == "budget" and not res.ok
    assert len(fake.seen) == 2 and len(res.qloo_calls) == 2


async def test_deadline_before_start_makes_no_request(catalog, agent_cfg, qloo):
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(deadline=time.monotonic() - 1), domain_id="music")
    model = ScriptedModel(answer(["x"]))
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3)
    assert resp.stop == "deadline" and resp.outcome == "timeout"
    assert model.requests == [] and fake.seen == []


async def test_deadline_during_the_loop(catalog, exam, agent_cfg, qloo):
    sc = a_scenario(exam, "music")
    fake, client = qloo()
    tools = QlooTools(client, catalog, ctx(deadline=time.monotonic() + 0.3), domain_id="music")
    args = {"domain": "music", "city": sc.city_id, "interests": ["jazz"]}
    model = ScriptedModel(call(RECOMMEND, args), answer(["x"]), delay=0.4)  # the model is slower
    resp = await run_grounded(make_agent(agent_cfg, model), tools, "prompt", 3)
    assert resp.stop == "deadline" and resp.outcome == "timeout"
    assert len(model.requests) == 1 and fake.seen == []
    assert resp.tool_calls[0].error and "time" in resp.tool_calls[0].error


async def test_the_model_never_sees_scores_or_params(catalog, exam, agent_cfg, qloo):
    sc = a_scenario(exam, "dining")
    _, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="dining")
    model = ScriptedModel(
        call(RECOMMEND, {"domain": "dining", "city": sc.city_id, "interests": ["street food", UNKNOWN]}, "a"),
        call(SEARCH, {"query": "Band Synthetic", "type": "urn:entity:place"}, "b"),
        answer_from_tools,
    )
    resp = await run_grounded(make_agent(agent_cfg, model), tools, sc.prompt_variants[0].text, sc.k)
    assert resp.outcome == "ok"
    tool_msgs = [m["content"] for m in model.requests[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    for content in tool_msgs:
        body = json.loads(content)
        assert set(body) <= {"ok", "results", "unresolved", "error"}
        for item in body.get("results", []):
            assert set(item) <= {"rank", "name", "type", "id"}
        for word in LEAKS:
            assert word not in content.replace('"rank"', ""), word
    # nor in the tool definitions (only the domain, city and interests are asked for)
    defs = json.dumps(model.requests[0]["tools"])
    assert "audience" not in defs and "signal." not in defs and "filter." not in defs


# --------------------------------------------------------------------------- the honesty rule
def answer_key_shapes(exam) -> set:
    return {query_shape(s.local_spec.params) for s in exam.scenarios} | {
        query_shape(s.global_spec.params) for s in exam.scenarios
    }


def stated_interests(catalog, sc) -> list[str]:
    """Everything the persona states (and more): seeds of every domain and interest tags."""
    p = catalog.persona(sc.persona_id)
    words = [s.name for seeds in p.seeds.values() for s in seeds]
    words += [t for tags in p.interest_tags.values() for t in tags]
    return words[:5]


def _comparable(params: dict) -> dict:
    return {k: v for k, v in normalize_params(params).items() if k not in ("take", "filter.exclude.entities")}


async def test_honesty_no_tool_query_reproduces_an_answer_key(catalog, exam, qloo):
    """Every demo scenario's tool query (fake Qloo) differs from its own local and baseline specs,
    with and without `take` and the own-seed exclusion, and never carries a demographic signal."""
    demo = [s for s in exam.scenarios if "demo" in s.tags]
    assert demo
    for sc in demo:
        fake, client = qloo()  # a fresh cache: every query reaches the fake
        tools = QlooTools(client, catalog, ctx(), domain_id=sc.domain_id)
        args = {"domain": sc.domain_id, "city": sc.city_id, "interests": stated_interests(catalog, sc)}
        res = await tools.execute(RECOMMEND, json.dumps(args))
        assert res.ok, (sc.id, res.error)
        sent = insights_seen(fake)
        assert len(sent) == 1, sc.id
        sent_params = sent[0]
        for spec in (sc.local_spec, sc.global_spec):
            assert normalize_params(sent_params) != normalize_params(spec.params), sc.id
            assert _comparable(sent_params) != _comparable(spec.params), sc.id
        assert not any(k.startswith("signal.demographics") for k in sent_params), sc.id
        assert "signal.interests.tags" in sent_params or "signal.interests.entities" in sent_params


def test_the_guard_refuses_every_answer_key_shape(exam):
    shapes = answer_key_shapes(exam)
    for sc in exam.scenarios:
        for spec in (sc.local_spec, sc.global_spec):
            with pytest.raises(HonestyError):
                assert_honest({**spec.params, "take": 20}, shapes)
            stripped = {k: v for k, v in spec.params.items() if k != "filter.exclude.entities"}
            with pytest.raises(HonestyError):
                assert_honest(stripped, shapes)


async def test_mutation_spot_check_a_tool_query_without_interests_is_refused(
    catalog, exam, qloo, monkeypatch
):
    """Mutation: if the tool's query lost its interest signal, dining's would be the city baseline
    (an answer-key spec). The guard must refuse it before any insights call."""
    sc = next(s for s in exam.scenarios if s.domain_id == "dining" and "demo" in s.tags)
    fake, client = qloo()
    original = QlooTools.build_params

    def mutated(self, domain, city_id, tags, entities):
        params = original(self, domain, city_id, tags, entities)
        params.pop("signal.interests.tags", None)
        params.pop("signal.interests.entities", None)
        return params

    monkeypatch.setattr(QlooTools, "build_params", mutated)
    tools = QlooTools(client, catalog, ctx(), domain_id="dining", answer_key_shapes=answer_key_shapes(exam))
    args = {"domain": "dining", "city": sc.city_id, "interests": ["street food"]}
    res = await tools.execute(RECOMMEND, json.dumps(args))
    assert not res.ok and "answer-key" in res.error
    assert insights_seen(fake) == []


async def test_mutation_spot_check_a_demographic_signal_is_refused(catalog, exam, qloo, monkeypatch):
    sc = a_scenario(exam, "music")
    fake, client = qloo()
    original = QlooTools.build_params

    def mutated(self, domain, city_id, tags, entities):
        return {
            **original(self, domain, city_id, tags, entities),
            "signal.demographics.audiences": ["urn:audience:a:b"],
        }

    monkeypatch.setattr(QlooTools, "build_params", mutated)
    tools = QlooTools(client, catalog, ctx(), domain_id="music")
    res = await tools.execute(
        RECOMMEND, json.dumps({"domain": "music", "city": sc.city_id, "interests": ["jazz"]})
    )
    assert not res.ok and "answer key's signal" in res.error
    assert insights_seen(fake) == []


def test_tool_definitions_are_exactly_two_and_closed(catalog):
    for d in catalog.domains:
        defs = tool_definitions(catalog, d.id)
        assert [x["function"]["name"] for x in defs] == [SEARCH, RECOMMEND]
        for x in defs:
            assert x["function"]["parameters"]["additionalProperties"] is False
        search, rec = (x["function"]["parameters"]["properties"] for x in defs)
        assert search["type"]["enum"] == d.search_types
        assert rec["domain"]["enum"] == [x.id for x in catalog.domains]
        assert rec["city"]["enum"] == [c.id for c in catalog.cities]
        assert rec["interests"]["maxItems"] == tools_mod.MAX_INTERESTS


# --------------------------------------------------------------------------- the chat client's tool calls
async def test_chat_reads_tool_calls_tolerantly(agent_cfg):
    reply = _completion(
        {
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": SEARCH, "arguments": '{"query": "x"}'}},
                {"type": "function", "function": {"name": RECOMMEND, "arguments": {"domain": "music"}}},
                {"id": "c", "type": "function"},  # no function: dropped
                "junk",
                {"id": "d", "function": {"name": SEARCH, "arguments": "x" * 5000}},
            ],
        },
        "tool_calls",
    )
    model = ScriptedModel(reply)
    got = await make_agent(agent_cfg, model).chat([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
    assert got.failure is None and got.finish_reason == "tool_calls"
    assert [(c.id, c.name) for c in got.tool_calls] == [
        ("a", SEARCH),
        ("call_local_1", RECOMMEND),
        ("d", SEARCH),
    ]
    assert json.loads(got.tool_calls[1].arguments) == {"domain": "music"}
    assert len(got.tool_calls[2].arguments) == 4000
    assert model.requests[0]["tools"] == [{"x": 1}] and "response_format" not in model.requests[0]


async def test_chat_without_a_key_makes_no_request(agent_cfg):
    model = ScriptedModel(answer(["x"]))

    async def no_sleep(_):
        return None

    agent = OpenAICompatTarget(
        agent_cfg, None, env=env_lookup({}), transport=httpx.MockTransport(model), sleep=no_sleep
    )
    got = await agent.chat([{"role": "user", "content": "hi"}], tools=[])
    assert got.failure == "error" and "not configured" in got.detail and model.requests == []


async def test_chat_redacts_a_key_echoed_back(agent_cfg):
    model = ScriptedModel(call(SEARCH, {"query": LLM_KEY}))
    got = await make_agent(agent_cfg, model).chat([{"role": "user", "content": "hi"}], tools=[{"x": 1}])
    assert LLM_KEY not in got.tool_calls[0].arguments


def test_extra_body_cannot_set_tool_choice(agent_cfg):
    with pytest.raises(ValueError, match="set by the client"):
        agent_cfg.model_validate({**agent_cfg.model_dump(), "extra_body": {"tool_choice": "required"}})


async def test_endpoint_failure_ends_the_loop_with_an_outcome(catalog, agent_cfg, qloo):
    _, client = qloo()
    tools = QlooTools(client, catalog, ctx(), domain_id="music")

    async def no_sleep(_):
        return None

    agent = OpenAICompatTarget(
        agent_cfg,
        None,
        env=env_lookup(ENV),
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
        sleep=no_sleep,
    )
    resp = await run_grounded(agent, tools, "prompt", 3)
    assert resp.stop == "error" and resp.outcome == "error" and "401" in resp.detail
