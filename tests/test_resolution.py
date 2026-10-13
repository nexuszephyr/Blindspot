import asyncio
import json

import httpx

from blindspot.qloo.cache import cache_key, normalize_params
from blindspot.qloo.context import RequestContext
from blindspot.qloo.snapshot import Snapshot, build_snapshot


def _snapshot_for(params, body, path="/search"):
    norm = normalize_params(params)
    entry = {"key": cache_key("GET", path, norm), "method": "GET", "path": path, "params": norm,
             "status": 200, "fetched_at": 1.0, "body": body}
    return Snapshot({entry["key"]: build_snapshot([entry], "t")["entries"][0]})


async def test_order_cache_snapshot_live_degraded(make_client, ctx, mock_api, search_body):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    snap = _snapshot_for({"query": "snap", "take": 5}, search_body)
    c = make_client(snapshot=snap)

    assert (await c.search_entities("snap", ctx)).served_from == "snapshot"
    assert route.call_count == 0
    assert (await c.search_entities("fresh", ctx)).served_from == "live"
    assert (await c.search_entities("fresh", ctx)).served_from == "cache"
    route.mock(return_value=httpx.Response(500))
    res = await c.search_entities("down", ctx)
    assert res.served_from == "degraded" and res.reason and not res.entities


async def test_replay_mode_never_touches_network(make_client, ctx, mock_api, search_body):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_mode="replay", snapshot=_snapshot_for({"query": "snap", "take": 5}, search_body))
    assert (await c.search_entities("snap", ctx)).served_from == "snapshot"
    res = await c.search_entities("unknown", ctx)
    assert res.served_from == "degraded" and "hosted demo" in res.reason
    assert route.call_count == 0


async def test_missing_key_falls_back_to_replay(make_client, ctx, mock_api):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json={}))
    c = make_client(qloo_api_key="")
    assert c.mode == "replay"
    assert (await c.search_entities("x", ctx)).served_from == "degraded"
    assert route.call_count == 0


async def test_allow_live_false_is_cache_only(make_client, mock_api):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json={}))
    res = await make_client().search_entities("x", RequestContext(client_id="c", allow_live=False))
    assert res.served_from == "degraded" and route.call_count == 0


async def test_per_request_kill_switch_is_isolated(make_client, mock_api, search_body):
    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=search_body)

    route = mock_api.get("/search").mock(side_effect=slow)
    c = make_client()
    off = RequestContext(client_id="c_off", qloo_enabled=False)
    on = RequestContext(client_id="c_on")
    r_off, r_on, r_off2 = await asyncio.gather(
        c.search_entities("x", off), c.search_entities("x", on), c.search_entities("y", off))
    assert r_off.served_from == "disabled" and not r_off.entities
    assert r_off2.served_from == "disabled"
    assert r_on.served_from == "live" and r_on.entities
    assert route.call_count == 1
    # global state untouched: a later normal request still works
    assert (await c.search_entities("x", RequestContext(client_id="c_off"))).served_from == "cache"


async def test_ops_master_switch(make_client, ctx, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_enabled=False)
    assert (await c.search_entities("x", ctx)).served_from == "disabled"


async def test_identical_concurrent_requests_share_one_call(make_client, mock_api, search_body):
    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=search_body)

    route = mock_api.get("/search").mock(side_effect=slow)
    c = make_client()
    results = await asyncio.gather(*[
        c.search_entities("same", RequestContext(client_id=f"c{i}")) for i in range(10)])
    assert route.call_count == 1
    assert all(r.served_from == "live" and r.ok for r in results)


async def test_invalid_queries_rejected_locally(make_client, ctx, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    c = make_client()
    assert (await c.request("/v2/admin", {}, ctx)).served_from == "degraded"
    assert (await c.search_entities("x" * 5000, ctx)).served_from == "degraded"
    assert (await c.insights({"filter.type": "urn:entity:nope"}, ctx)).served_from == "degraded"
    assert (await c.search_entities("x", ctx, types=["urn:entity:bogus"])).served_from == "degraded"
    assert route.call_count == 0


async def test_take_is_clamped(make_client, ctx, mock_api, search_body):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    await make_client(qloo_max_take=50).search_entities("x", ctx, take=500)
    assert route.calls.last.request.url.params["take"] == "50"


async def test_record_mode_saves_raw_bodies_without_headers(make_client, ctx, mock_api, search_body, tmp_path):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_mode="record")
    await c.search_entities("x", ctx)
    files = list((tmp_path / "raw").glob("*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["body"] == search_body and "headers" not in doc


async def test_call_log_queryable_by_run(make_client, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client()
    run = RequestContext(client_id="c1", run_id="run_abc")
    await c.search_entities("x", run)
    await c.search_entities("x", run)
    await c.search_entities("x", RequestContext(client_id="c1", run_id="run_abc", qloo_enabled=False))
    rows = c.calls.for_run("run_abc")
    assert [r["served_from"] for r in rows] == ["live", "cache", "disabled"]
    assert rows[0]["params"]["query"] == "x" and rows[0]["result_count"] == 2
    assert rows[0]["latency_ms"] is not None and rows[0]["client_id"] == "c1"
