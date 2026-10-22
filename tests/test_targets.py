"""#27: target adapters against a mocked OpenAI-compatible server (respx). Offline and keyless:
the keys here are fake, built at runtime so the secret scanner doesn't flag this file. No city name
is written here (rule 9)."""

import json
import logging
import shutil
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from blindspot.catalog import ConfigError
from blindspot.qloo.redact import install_log_redaction, redact
from blindspot.targets import (
    OpenAICompatTarget,
    ResponseCache,
    Target,
    TargetConfig,
    env_lookup,
    get_target,
    load_targets,
    parse,
)
from blindspot.targets.client import MAX_ATTEMPTS, SEED

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://llm.example.test/v1"
URL = BASE + "/chat/completions"
GROQ_KEY = "fake-groq-" + "key-0123456789-do-not-leak"
GEMINI_KEY = "fake-gemini-" + "key-9876543210-do-not-leak"
ENV = {"GROQ_API_KEY": GROQ_KEY, "GEMINI_API_KEY": GEMINI_KEY, "GROQ_BASE_URL": BASE, "GEMINI_BASE_URL": BASE}
GOOD = {"recommendations": [{"name": f"Place {i}", "why": f"reason {i}"} for i in range(1, 6)]}


def cfg(**kw) -> TargetConfig:
    base = dict(
        id="small",
        display_name="Small",
        base_url_env="GROQ_BASE_URL",
        api_key_env="GROQ_API_KEY",
        model="vendor/small-model",
        max_tokens=1000,
    )
    base.update(kw)
    return TargetConfig(**base)


def completion(content, finish="stop", **msg) -> dict:
    return {
        "model": "vendor/small-model",
        "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content, **msg}}
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 40,
            "total_tokens": 120,
            "completion_tokens_details": {"reasoning_tokens": 30},
        },
    }


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make_target(tmp_path, sleeps):
    caches = []

    def _make(config: TargetConfig | None = None, *, cache=True, env=ENV):
        async def fake_sleep(s):
            sleeps.append(s)

        c = ResponseCache(tmp_path / "targets.sqlite") if cache else None
        if c:
            caches.append(c)
        return OpenAICompatTarget(config or cfg(), c, env=env_lookup(env), sleep=fake_sleep)

    yield _make
    for c in caches:
        c.close()


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def sent(api, i=0) -> dict:
    return json.loads(api.calls[i].request.content)


# ------------------------------------------------------------------ outcomes
async def test_ok_parses_items_and_usage(api, make_target):
    route = api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    t = make_target()
    assert isinstance(t, Target)
    r = await t.recommend("I like quiet places. Recommend 3.", 3, scenario_id="sc_x")
    assert r.outcome == "ok" and r.parse_method == "json" and r.served_from == "live"
    assert [i.name for i in r.items] == ["Place 1", "Place 2", "Place 3"]  # capped at k
    assert r.items[0].why == "reason 1"
    assert r.usage.prompt_tokens == 50 and r.usage.reasoning_tokens == 30 and r.usage.calls == 1
    assert r.model == "vendor/small-model" and r.finish_reason == "stop" and r.prompt_hash.startswith("ph_")
    body = sent(api)
    assert body["temperature"] == 0 and body["max_tokens"] == 1000 and body["seed"] == SEED
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "3 recommendations" in body["messages"][0]["content"]
    assert route.call_count == 1


async def test_code_fences_and_prose_around_json(api, make_target):
    text = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nEnjoy."
    api.post(URL).mock(return_value=httpx.Response(200, json=completion(text)))
    r = await make_target().recommend("p", 2)
    assert r.outcome == "ok" and [i.name for i in r.items] == ["Place 1", "Place 2"]


async def test_malformed_json_gets_one_repair_retry(api, make_target):
    route = api.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=completion('{"recommendations": [{"name": "Place 1", "why": ')),
            httpx.Response(200, json=completion(json.dumps(GOOD))),
        ]
    )
    r = await make_target().recommend("p", 3)
    assert r.outcome == "ok" and r.parse_method == "repair" and len(r.items) == 3
    assert route.call_count == 2 and r.usage.calls == 2 and r.usage.prompt_tokens == 100
    repair = sent(api, 1)["messages"]
    assert repair[-2]["role"] == "assistant" and repair[-1]["content"] == parse.REPAIR_PROMPT


async def test_malformed_falls_back_to_lines(api, make_target):
    text = "1. **Place A** - cozy\n2. Place B: lively\n- Place C — calm\nnot a list line"
    api.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=completion(text)),
            httpx.Response(200, json=completion("still not json")),
        ]
    )
    r = await make_target().recommend("p", 5)
    assert r.outcome == "ok" and r.parse_method == "lines"
    assert [(i.name, i.why) for i in r.items] == [
        ("Place A", "cozy"),
        ("Place B", "lively"),
        ("Place C", "calm"),
    ]


@pytest.mark.parametrize("text", ["{{{ nothing useful here", "1. Place A - cozy"])
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.Response(500)])
async def test_failed_repair_is_not_cached(api, make_target, text, failure):
    # the repair request failing says nothing about the model: the answer is returned, never
    # replayed, so the next run asks again instead of keeping a transient malformed or lines parse
    route = api.post(URL).mock(
        side_effect=[httpx.Response(200, json=completion(text)), failure, failure, failure]
    )
    t = make_target()
    r = await t.recommend("p", 3)
    assert r.outcome in {"malformed", "ok"} and r.served_from == "live"
    assert t.cache.count() == 0
    route.side_effect = [
        httpx.Response(200, json=completion(text)),
        httpx.Response(200, json=completion(json.dumps(GOOD))),
    ]
    again = await t.recommend("p", 3)
    assert again.served_from == "live" and again.parse_method == "repair" and t.cache.count() == 1


async def test_unparseable_is_malformed_not_a_crash(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion("{{{ nothing useful here")))
    r = await make_target().recommend("p", 3)
    assert r.outcome == "malformed" and r.items == [] and r.raw_text.startswith("{{{")


async def test_truncated_keeps_complete_items(api, make_target):
    text = (
        '{"recommendations": [{"name": "Place 1", "why": "a"}, {"name": "Place 2", "why": "b"}, {"name": "Pla'
    )
    route = api.post(URL).mock(return_value=httpx.Response(200, json=completion(text, finish="length")))
    r = await make_target().recommend("p", 5)
    assert (
        r.outcome == "ok"
        and r.parse_method == "partial"
        and [i.name for i in r.items] == ["Place 1", "Place 2"]
    )
    assert route.call_count == 1  # no repair for a truncated answer


async def test_truncated_without_items_is_malformed(api, make_target):
    api.post(URL).mock(
        return_value=httpx.Response(200, json=completion('{"recommendations": [{"na', finish="length"))
    )
    r = await make_target().recommend("p", 3)
    assert r.outcome == "malformed" and r.finish_reason == "length"


async def test_hidden_thinking_ate_the_budget_is_malformed(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion("", finish="length")))
    assert (await make_target().recommend("p", 3)).outcome == "malformed"


@pytest.mark.parametrize("content", ["", "   ", None, json.dumps({"recommendations": []})])
async def test_empty(api, make_target, content):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion(content)))
    r = await make_target().recommend("p", 3)
    assert r.outcome == "empty" and r.items == []


@pytest.mark.parametrize(
    "body",
    [
        completion("I'm sorry, but I can't help with that."),
        completion(None, refusal="I can't help with that."),
        completion("", finish="content_filter"),
    ],
)
async def test_refusal(api, make_target, body):
    route = api.post(URL).mock(return_value=httpx.Response(200, json=body))
    r = await make_target().recommend("p", 3)
    assert r.outcome == "refused" and r.items == [] and route.call_count == 1


async def test_timeout(api, make_target):
    api.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    t = make_target(cfg(timeout_s=90))
    r = await t.recommend("p", 3)
    assert r.outcome == "timeout" and "90 s" in r.detail
    assert t.cache.count() == 0  # a timeout is retried next time, never replayed


async def test_429_honours_retry_after(api, make_target, sleeps):
    route = api.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"message": "slow down"}}),
            httpx.Response(503),
            httpx.Response(200, json=completion(json.dumps(GOOD))),
        ]
    )
    r = await make_target().recommend("p", 2)
    assert r.outcome == "ok" and route.call_count == 3
    assert sleeps[0] >= 3 and len(sleeps) == 2


async def test_retries_are_bounded(api, make_target, sleeps):
    route = api.post(URL).mock(return_value=httpx.Response(500))
    r = await make_target().recommend("p", 2)
    assert r.outcome == "error" and route.call_count == MAX_ATTEMPTS and len(sleeps) == MAX_ATTEMPTS - 1


async def test_long_retry_after_gives_up(api, make_target, sleeps):
    route = api.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    r = await make_target().recommend("p", 2)
    assert r.outcome == "error" and route.call_count == 1 and sleeps == []


async def test_400_is_an_error_outcome(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "Unknown name seed"}}))
    r = await make_target().recommend("p", 2)
    assert r.outcome == "error" and "Unknown name seed" in r.detail


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"choices": []}', b'{"choices": ["x"]}'])
async def test_unreadable_body_is_an_error(api, make_target, body):
    api.post(URL).mock(return_value=httpx.Response(200, content=body))
    assert (await make_target().recommend("p", 2)).outcome == "error"


async def test_not_configured_makes_no_call(api, make_target):
    route = api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    r = await make_target(env={}).recommend("p", 2)
    assert r.outcome == "error" and "GROQ_API_KEY" in r.detail and route.call_count == 0


async def test_bad_k_is_a_programming_error(make_target):
    with pytest.raises(ValueError):
        await make_target().recommend("p", 0)


# ------------------------------------------------------------------ cache
async def test_cache_hit_makes_no_network_call(api, make_target):
    route = api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    t = make_target()
    first = await t.recommend("same prompt", 3, scenario_id="sc_1")
    again = await t.recommend("same prompt", 3, scenario_id="sc_1")
    assert route.call_count == 1 and again.served_from == "cache"
    assert again.items == first.items and again.usage == first.usage
    # a new target object over the same file replays too (a re-run costs nothing)
    t2 = make_target()
    assert (await t2.recommend("same prompt", 3)).served_from == "cache" and route.call_count == 1
    # a different prompt, k or max_tokens is a different key
    await t.recommend("other prompt", 3)
    await t.recommend("same prompt", 4)
    await make_target(cfg(max_tokens=2000)).recommend("same prompt", 3)
    assert route.call_count == 4


async def test_cache_holds_no_key_and_no_header(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    t = make_target()
    await t.recommend("p", 3)
    rows = t.cache.dump()
    assert rows and all(GROQ_KEY not in r and "Bearer" not in r for r in rows)


# ------------------------------------------------------------------ per-target settings (D49)
async def test_seed_is_never_sent_when_disabled(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    await make_target(cfg(seed=False)).recommend("p", 3)
    assert "seed" not in sent(api)


async def test_extra_body_is_passed_through(api, make_target):
    api.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
    await make_target(cfg(extra_body={"reasoning_effort": "low"}, json_mode=False)).recommend("p", 3)
    body = sent(api)
    assert body["reasoning_effort"] == "low" and "response_format" not in body


def test_extra_body_cannot_override_core_fields():
    with pytest.raises(ValueError, match="set by the client"):
        cfg(extra_body={"temperature": 1})


async def test_concurrency_is_capped(api, make_target):
    import asyncio

    in_flight = peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=completion(json.dumps(GOOD)))

    api.post(URL).mock(side_effect=slow)
    t = make_target(cfg(max_concurrency=2), cache=False)
    rs = await asyncio.gather(*(t.recommend(f"p{i}", 2) for i in range(8)))
    assert all(r.outcome == "ok" for r in rs) and peak <= 2


# ------------------------------------------------------------------ secrets
async def test_key_only_in_bearer_header_and_never_leaks(api, make_target, caplog):
    install_log_redaction()
    caplog.set_level(logging.DEBUG)
    echo = f"bad request from key {GEMINI_KEY}"
    api.post(URL).mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": echo}}),
            httpx.Response(
                200, json=completion(json.dumps({"recommendations": [{"name": GEMINI_KEY, "why": echo}]}))
            ),
        ]
    )
    t = make_target(cfg(api_key_env="GEMINI_API_KEY", base_url_env="GEMINI_BASE_URL"))
    r1 = await t.recommend("p", 3)
    r2 = await t.recommend("q", 3)
    logging.getLogger("blindspot.test").warning("leak attempt %s and %s", GEMINI_KEY, GROQ_KEY)

    req = api.calls[0].request
    assert req.headers["Authorization"] == f"Bearer {GEMINI_KEY}"
    assert GEMINI_KEY not in str(req.url) and GEMINI_KEY not in req.content.decode()
    assert r1.outcome == "error" and "[REDACTED]" in r1.detail
    blobs = [r1.model_dump_json(), r2.model_dump_json(), repr(t), caplog.text, *t.cache.dump()]
    for blob in blobs:
        assert GEMINI_KEY not in blob and GROQ_KEY not in blob
    assert "[REDACTED]" in caplog.text


def test_redaction_covers_target_keys_and_bearer_headers():
    make = OpenAICompatTarget  # registering happens at construction
    make(cfg(), env=env_lookup(ENV))
    make(cfg(api_key_env="GEMINI_API_KEY"), env=env_lookup(ENV))
    for key in (GROQ_KEY, GEMINI_KEY):
        assert key not in redact(f"value {key} here")
    assert "abc123secretvalue" not in redact("Authorization: Bearer abc123secretvalue")
    assert "abc123secretvalue" not in redact('{"authorization": "Bearer abc123secretvalue"}')


# ------------------------------------------------------------------ config
def test_real_targets_yaml_loads():
    targets = load_targets(ROOT / "config")
    assert [t.id for t in targets if t.fix_loop_agent] == ["gpt-oss-120b"]
    assert all(t.max_tokens >= 1000 for t in targets)
    gemini = get_target(targets, "gemini-flash")
    assert (
        gemini.seed is False and gemini.extra_body == {"reasoning_effort": "low"} and gemini.timeout_s >= 90
    )
    assert [t.id for t in targets if t.live_in_demo] == ["gpt-oss-20b"]
    text = (ROOT / "config" / "targets.yaml").read_text(encoding="utf-8")
    assert "_KEY:" not in text.replace("api_key_env:", "")  # names only, no values


def _write(tmp_path, targets):
    (tmp_path / "targets.yaml").write_text(yaml.safe_dump({"targets": targets}), encoding="utf-8")


def _raw(**kw):
    t = dict(id="a", display_name="A", base_url_env="X_URL", api_key_env="X_KEY", model="m", max_tokens=1000)
    t.update(kw)
    return t


@pytest.mark.parametrize(
    ("targets", "match"),
    [
        ([_raw()], "exactly one"),
        (
            [
                _raw(fix_loop_agent=True, tool_calling=True),
                _raw(id="b", fix_loop_agent=True, tool_calling=True),
            ],
            "exactly one",
        ),
        ([_raw(fix_loop_agent=True, tool_calling=True, sead=False)], "sead"),
        ([_raw(fix_loop_agent=True, tool_calling=True, max_tokens=400)], "max_tokens"),
        ([_raw(fix_loop_agent=True)], "tool_calling"),
        (
            [_raw(fix_loop_agent=True, tool_calling=True, base_url_default="https://u:p@host/v1")],
            "base_url_default",
        ),
        ([_raw(fix_loop_agent=True, tool_calling=True, api_key_env="gsk-literal-value")], "api_key_env"),
        ([_raw(fix_loop_agent=True, tool_calling=True), _raw()], "duplicate"),
    ],
)
def test_bad_targets_rejected(tmp_path, targets, match):
    _write(tmp_path, targets)
    with pytest.raises(ConfigError, match=match):
        load_targets(tmp_path)


def test_env_lookup_reads_dotenv_without_inline_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("T27_KEY", raising=False)
    monkeypatch.delenv("T27_URL", raising=False)
    (tmp_path / ".env").write_text(
        "T27_KEY=abc123   # a comment\nT27_URL='https://x/v1'\n#T27_X=1\n", encoding="utf-8"
    )
    env = env_lookup(env_file=tmp_path / ".env")
    assert env("T27_KEY") == "abc123" and env("T27_URL") == "https://x/v1" and env("T27_X") == ""
    monkeypatch.setenv("T27_KEY", "from-env")
    assert env_lookup(env_file=tmp_path / ".env")("T27_KEY") == "from-env"


# ------------------------------------------------------------------ parser units
def test_parser_units():
    assert parse.items_from_json({"results": [{"title": "X", "reason": "y"}]}, 3)[0].name == "X"
    assert parse.items_from_json(["A", "B"], 1)[0].name == "A"
    assert parse.items_from_json({"recommendations": [1, 2]}, 3) is None
    assert parse.items_from_json({"a": 1}, 3) is None
    name = parse.items_from_json({"recommendations": [{"name": "A\u202e\x00B  C", "why": "w" * 900}]}, 3)[0]
    assert name.name == "A B C" and len(name.why) == parse.MAX_WHY
    # every edge of the stripped ranges: zero-width, bidi marks and overrides, isolates, BOM (rule 10)
    for ch in "​‏ ‮⁦⁩﻿\x9f":
        assert parse.clean(f"A{ch}B", 10) == "A B", repr(ch)
    assert parse.clean("A‰B", 10) == "A‰B"  # ordinary punctuation (per mille) is kept
    assert parse.looks_refused("Sorry, I can't do that.") and not parse.looks_refused("Sorrento Bistro")


# ------------------------------------------------------------------ CLI
def test_cli_list_never_prints_values(tmp_path, monkeypatch, capsys):
    from blindspot.targets.cli import main

    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    shutil.copy(ROOT / "config" / "targets.yaml", cfgdir / "targets.yaml")
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={GROQ_KEY}\n", encoding="utf-8")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["--config", str(cfgdir), "list"]) == 0
    out = capsys.readouterr().out
    assert GROQ_KEY not in out and "gpt-oss-20b" in out
    rows = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    # only whether a key is set, never its value or anything derived from it (CodeQL autofix on #106)
    assert "not configured" not in rows["gpt-oss-20b"] and "configured" in rows["gpt-oss-20b"]
    assert "not configured" in rows["gemini-flash"]


def test_cli_ask_prints_parsed_names(tmp_path, monkeypatch, capsys):
    from blindspot.scenarios.models import PromptVariant, QuerySpec, Scenario
    from blindspot.targets.cli import main

    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    shutil.copy(ROOT / "config" / "targets.yaml", cfgdir / "targets.yaml")
    spec = QuerySpec.of({"filter.type": "urn:entity:place"})
    sc = Scenario(
        persona_id="p", city_id="c", domain_id="d", probe_type="core", signal="audience",
        local_spec=spec, global_spec=spec, depth=20, pool=50, k=3,
        prompt_variants=[PromptVariant(template_id="t1", text="Recommend 3 things.")],
    ).with_id()  # fmt: skip
    f = tmp_path / "scenarios.json"
    f.write_text(json.dumps({"scenarios": [sc.model_dump(mode="json")]}), encoding="utf-8")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TARGET_CACHE_PATH", str(tmp_path / "t.sqlite"))
    from blindspot.config import get_settings

    get_settings.cache_clear()
    with respx.mock() as mock:
        mock.post(URL).mock(return_value=httpx.Response(200, json=completion(json.dumps(GOOD))))
        code = main(
            ["--config", str(cfgdir), "ask", "--target", "gpt-oss-20b", "--scenario", sc.id, "--file", str(f)]
        )
    get_settings.cache_clear()
    out = capsys.readouterr().out
    assert code == 0 and " 1. Place 1" in out and " 3. Place 3" in out and "tokens: prompt 50" in out
    assert GROQ_KEY not in out
