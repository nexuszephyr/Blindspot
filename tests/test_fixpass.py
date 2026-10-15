"""Audit fix-pass tests: F1 resolution/ids, F5 enums/GET-only, params guard, deadline, outcomes."""

import asyncio
import sqlite3
import time

import httpx

from blindspot.qloo.cache import cache_key
from blindspot.qloo.context import RequestContext
from blindspot.qloo.db import Database
from blindspot.qloo.models import InsightsQuery
from blindspot.qloo.params import assert_param_effective

UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
PLACE = {"filter.type": "urn:entity:place", "filter.location.query": "Example City", "take": 5}


def C(cid="c_t", **kw):
    return RequestContext(client_id=cid, **kw)


# ---------------------------------------------------------------- F1: ids, names, resolution
def test_string_and_list_ids_are_equivalent():
    a = InsightsQuery(**{"filter.type": "urn:entity:artist", "signal.interests.entities": f"{UUID1}, {UUID2}"})
    b = InsightsQuery(**{"filter.type": "urn:entity:artist", "signal.interests.entities": [UUID1, UUID2]})
    assert a.to_params() == b.to_params()


async def test_ids_pass_through(make_client, mock_api, insights_body):
    route = mock_api.get("/v2/insights").mock(return_value=httpx.Response(200, json=insights_body))
    r = await make_client().insights({"filter.type": "urn:entity:artist", "signal.interests.entities": UUID1}, C())
    assert r.outcome == "ok" and route.calls.last.request.url.params["signal.interests.entities"] == UUID1


async def test_free_text_never_sent_as_id(make_client, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    c = make_client()
    for p in ({"signal.interests.entities": "Radiohead"}, {"filter.tags": "restaurant"},
              {"signal.demographics.audiences": "millennials"}):
        r = await c.insights({"filter.type": "urn:entity:artist", **p}, C(), strict=False)
        assert r.outcome == "resolution_failed" and r.served_from == "degraded"
    assert route.call_count == 0


async def test_names_resolve_to_ids(make_client, mock_api, search_body, insights_body):
    search_body["results"][0]["entity_id"] = UUID1
    s = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    tags = mock_api.get("/v2/tags").mock(return_value=httpx.Response(
        200, json={"results": {"tags": [{"id": "urn:tag:genre:music:example", "name": "x"}]}}))
    ins = mock_api.get("/v2/insights").mock(return_value=httpx.Response(200, json=insights_body))
    r = await make_client().insights({"filter.type": "urn:entity:artist"}, C(), strict=False, resolve={
        "signal.interests.entities": [("Example Artist", "urn:entity:artist")], "signal.interests.tags": ["example"]})
    assert r.outcome == "ok" and s.call_count == 1 and tags.call_count == 1
    sent = ins.calls.last.request.url.params
    assert sent["signal.interests.entities"] == UUID1 and sent["signal.interests.tags"] == "urn:tag:genre:music:example"


async def test_unresolvable_name_makes_zero_insights_calls(make_client, mock_api):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json={"results": []}))
    ins = mock_api.get("/v2/insights").mock(return_value=httpx.Response(200, json={}))
    c = make_client()
    r = await c.insights({"filter.type": "urn:entity:artist"}, C(run_id="run_res"),
                         resolve={"signal.interests.entities": ["Nobody At All"]})
    assert r.outcome == "resolution_failed" and ins.call_count == 0
    rows = c.calls.for_run("run_res")
    assert rows[-1]["outcome"] == "resolution_failed" and rows[0]["outcome"] == "empty"


async def test_outcomes_empty_and_qloo_error(make_client, mock_api):
    mock_api.get("/v2/insights").mock(side_effect=[
        httpx.Response(200, json={"success": True, "results": {"entities": []}}),
        httpx.Response(400, json={"error": {"message": "Bad Request", "reason": "x", "code": 400}})])
    c = make_client()
    assert (await c.insights(PLACE, C())).outcome == "empty"
    r = await c.insights({**PLACE, "take": 6}, C())
    assert r.outcome == "qloo_error" and r.status == 400


# ---------------------------------------------------------------- F5: enums, GET only
async def test_videogame_rejected_with_hint(make_client, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    r = await make_client().insights({"filter.type": "urn:entity:videogame", "take": 2}, C())
    assert r.outcome == "invalid" and "urn:entity:video_game" in r.reason and route.call_count == 0
    assert InsightsQuery(**{"filter.type": "urn:entity:video_game"})


async def test_only_get_and_no_legacy_endpoints(make_client, mock_api, insights_body):
    route = mock_api.route().mock(return_value=httpx.Response(200, json=insights_body))
    c = make_client()
    await c.insights(PLACE, C())
    for legacy in ("/recommendations", "/recs", "/v2/insights/search"):
        assert (await c.request(legacy, {"take": 2}, C())).outcome == "invalid"
    assert route.call_count == 1 and all(call.request.method == "GET" for call in route.calls)
    req = route.calls.last.request
    assert req.url.path == "/v2/insights" and req.content == b"" and "filter.type" in req.url.params


# ---------------------------------------------------------------- params guard
async def test_bogus_param_raises_in_strict_mode(make_client, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    c = make_client()
    r = await c.insights({**PLACE, "filter.bogus": "x"}, C())
    assert r.outcome == "invalid" and "filter.bogus" in r.reason and "filter.location.query" in r.reason
    r = await c.request("/search", {"query": "x", "bogus": 1}, C())
    assert r.outcome == "invalid" and route.call_count == 0


async def test_non_strict_logs_unverified_params(make_client, mock_api, insights_body):
    mock_api.get("/v2/insights").mock(return_value=httpx.Response(200, json=insights_body))
    c = make_client()
    r = await c.insights({**PLACE, "bias.trends": "high"}, C(run_id="run_u"), strict=False)
    assert r.ok and r.unverified_params == ["bias.trends"]
    assert c.calls.for_run("run_u")[0]["unverified_params"] == "bias.trends"


def test_assert_param_effective():
    assert assert_param_effective(["a", "b"], ["a", "b"])["changed"] is False
    eff = assert_param_effective(["a", "b"], ["b", "a"])
    assert eff["changed"] and eff["order_only"]
    assert assert_param_effective(["a"], ["c"])["jaccard"] == 0.0


# ---------------------------------------------------------------- deadline
def _web(deadline_s, cid="c_web"):
    return RequestContext(client_id=cid, deadline=time.monotonic() + deadline_s)


async def test_deadline_slow_server(make_settings, mock_api, search_body):
    from blindspot.qloo.client import QlooClient
    from blindspot.qloo.snapshot import Snapshot

    async def slow(request):
        await asyncio.sleep(20)
        return httpx.Response(200, json=search_body)

    mock_api.get("/search").mock(side_effect=slow)
    c = QlooClient(make_settings(), snapshot=Snapshot())  # real sleep
    t = time.monotonic()
    r = await c.search_entities("slow", _web(1.0))
    assert time.monotonic() - t < 1.5 and r.served_from == "degraded" and r.outcome == "not_live"
    used = c.budget.total_used()
    await asyncio.sleep(0.3)
    assert c.budget.total_used() == used  # nothing keeps retrying in the background


async def test_retry_after_beyond_deadline_degrades_now(make_settings, mock_api):
    from blindspot.qloo.client import QlooClient
    from blindspot.qloo.snapshot import Snapshot

    route = mock_api.get("/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "5"}))
    c = QlooClient(make_settings(), snapshot=Snapshot())
    t = time.monotonic()
    r = await c.search_entities("ra", _web(2.0))
    assert time.monotonic() - t < 1.0 and r.outcome == "qloo_error" and route.call_count == 1


async def test_fifty_concurrent_429_none_hang(make_settings, mock_api):
    from blindspot.qloo.client import QlooClient
    from blindspot.qloo.snapshot import Snapshot

    mock_api.get("/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
    c = QlooClient(make_settings(qloo_client_hourly_cap=1000), snapshot=Snapshot())

    async def one(i):
        t = time.monotonic()
        r = await c.search_entities(f"q{i}", _web(8.0, f"c{i}"))
        return time.monotonic() - t, r

    out = await asyncio.wait_for(asyncio.gather(*[one(i) for i in range(50)]), timeout=15)
    times = sorted(t for t, _ in out)
    assert times[int(len(times) * 0.95) - 1] < 8.5 and all(r.served_from == "degraded" for _, r in out)


async def test_follower_survives_leader_cancellation(make_settings, mock_api, search_body):
    from blindspot.qloo.client import QlooClient
    from blindspot.qloo.snapshot import Snapshot

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=search_body)

    mock_api.get("/search").mock(side_effect=slow)
    c = QlooClient(make_settings(), snapshot=Snapshot())
    leader = asyncio.create_task(c.search_entities("same", C("c_lead")))
    await asyncio.sleep(0.1)
    follower = asyncio.create_task(c.search_entities("same", _web(3.0, "c_follow")))
    await asyncio.sleep(0.1)
    leader.cancel()
    r = await asyncio.wait_for(follower, 4)
    assert r.served_from == "degraded" and r.coalesced


async def test_breaker_open_answers_instantly(make_client, mock_api):
    mock_api.get("/search").mock(return_value=httpx.Response(429))
    c = make_client(qloo_max_attempts=1)
    for i in range(3):
        await c.search_entities(f"b{i}", C(f"c{i}"))
    t = time.monotonic()
    r = await c.search_entities("x", _web(8.0))
    assert time.monotonic() - t < 0.2 and r.outcome == "not_live"


# ---------------------------------------------------------------- F6, F9, F10, F11, F17, purpose
async def test_coalesced_rows_are_marked(make_client, mock_api, search_body):
    async def slow(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=search_body)

    mock_api.get("/search").mock(side_effect=slow)
    c = make_client()
    a, b = await asyncio.gather(c.search_entities("s", C("c1")), c.search_entities("s", C("c2")))
    assert [a.coalesced, b.coalesced] == [False, True]
    rows = c.calls.last(2)
    assert sorted(r["coalesced"] for r in rows) == [0, 1] and c.budget.total_used() == 1


def test_empty_results_expire_after_an_hour(tmp_path):
    from blindspot.qloo.cache import ResponseCache

    cache = ResponseCache(Database(tmp_path / "c.sqlite"), ttl_days=7)
    cache.put("k", "GET", "/v2/insights", {}, 200, {"results": {"entities": []}}, fetched_at=0.0)
    assert cache.get("k", now=1800) is not None and cache.get("k", now=3700) is None


async def test_huge_body_rejected(make_client, ctx, mock_api):
    mock_api.get("/search").mock(return_value=httpx.Response(200, content=b'{"results":[]}' + b" " * (5 << 20)))
    r = await make_client().search_entities("big", ctx)
    assert r.outcome == "qloo_error" and r.served_from == "degraded"


async def test_one_rate_limited_request_does_not_trip_breaker(make_client, ctx, mock_api, sleeps):
    mock_api.get("/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    c = make_client(qloo_max_attempts=4)
    await c.search_entities("one", ctx)
    assert c.breaker.state == "closed"


async def test_replay_serves_stale_cache(make_client, ctx, search_body):
    c = make_client(qloo_mode="replay", qloo_cache_ttl_days=1)
    k = cache_key("GET", "/search", {"query": "old", "take": "5"})
    c.cache.put(k, "GET", "/search", {"query": "old", "take": "5"}, 200, search_body, fetched_at=0.0)
    r = await c.search_entities("old", ctx)
    assert r.served_from == "cache" and r.fetched_at == 0.0


async def test_live_calls_by_purpose(make_client, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client()
    await c.search_entities("a", C(purpose="recon"))
    await c.search_entities("b", C(purpose="coverage"))
    await c.search_entities("b", C(purpose="coverage"))  # cache: not a live call
    assert c.budget.by_purpose() == {"recon": 1, "coverage": 1}
    assert c.status()["live_calls_by_purpose"] == {"recon": 1, "coverage": 1}


def test_migration_adds_columns_to_old_db(tmp_path):
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE calls (id INTEGER PRIMARY KEY, run_id TEXT, client_id TEXT, ts REAL NOT NULL,"
                      " endpoint TEXT NOT NULL, params_json TEXT NOT NULL, served_from TEXT NOT NULL, status INTEGER,"
                      " latency_ms REAL, result_count INTEGER, error TEXT, cache_key TEXT);"
                      "CREATE TABLE live_attempts (id INTEGER PRIMARY KEY, ts REAL NOT NULL, client_id TEXT NOT NULL,"
                      " run_id TEXT NOT NULL, priority TEXT NOT NULL);"
                      "INSERT INTO live_attempts(ts,client_id,run_id,priority) VALUES (1e12,'c','r','demo');")
    con.commit()
    con.close()
    db = Database(p)
    cols = {r[1] for r in db.query("PRAGMA table_info(calls)")}
    assert {"outcome", "purpose", "coalesced", "unverified_params"} <= cols
    assert db.query("SELECT purpose FROM live_attempts")[0][0] == "app"
