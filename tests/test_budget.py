import asyncio

import httpx

from blindspot.qloo.context import RequestContext


async def test_client_cap_blocks_one_client_not_another(make_client, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_client_hourly_cap=2)
    a = [await c.search_entities(f"a{i}", RequestContext(client_id="c_a")) for i in range(3)]
    assert [r.served_from for r in a] == ["live", "live", "degraded"]
    assert "hourly" in a[2].reason
    b = await c.search_entities("b0", RequestContext(client_id="c_b"))
    assert b.served_from == "live"
    # client A can still read what is cached
    assert (await c.search_entities("a0", RequestContext(client_id="c_a"))).served_from == "cache"


async def test_run_cap(make_client, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_run_cap=2)
    ctx = RequestContext(client_id="c_r", run_id="run_1")
    out = [(await c.search_entities(f"q{i}", ctx)).served_from for i in range(3)]
    assert out == ["live", "live", "degraded"]


async def test_global_cap_degrades_gracefully(make_client, mock_api, search_body):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_max_live_calls=3)
    out = [await c.search_entities(f"q{i}", RequestContext(client_id=f"c{i}")) for i in range(6)]
    assert [r.served_from for r in out] == ["live"] * 3 + ["degraded"] * 3
    assert all("budget" in r.reason for r in out[3:])
    assert route.call_count == 3


async def test_demo_reserve_survives_adhoc_flood(make_client, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client(qloo_daily_live_cap=10, qloo_demo_reserve=4, qloo_client_hourly_cap=100)
    flood = await asyncio.gather(
        *[c.search_entities(f"flood{i}", RequestContext(client_id=f"c{i % 5}")) for i in range(30)]
    )
    assert sum(r.served_from == "live" for r in flood) == 6  # 10 - 4 reserved
    demo = [
        await c.search_entities(f"demo{i}", RequestContext(client_id="c_demo", priority="demo"))
        for i in range(5)
    ]
    assert [r.served_from for r in demo] == ["live"] * 4 + ["degraded"]


async def test_retries_count_against_budget(make_client, mock_api):
    route = mock_api.get("/search").mock(return_value=httpx.Response(503))
    c = make_client(qloo_max_live_calls=2, qloo_max_attempts=4)
    res = await c.search_entities("x", RequestContext(client_id="c"))
    assert res.served_from == "degraded" and route.call_count == 2


async def test_fifty_concurrent_requests_respect_cap(make_client, mock_api, search_body):
    async def slow(request):
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=search_body)

    route = mock_api.get("/search").mock(side_effect=slow)
    c = make_client(qloo_max_live_calls=20, qloo_client_hourly_cap=1000, qloo_max_concurrency=4)
    tasks = [c.search_entities(f"q{i % 35}", RequestContext(client_id=f"c{i % 7}")) for i in range(50)]
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    assert len(results) == 50
    assert route.call_count <= 20
    assert c.budget.total_used() <= 20
    assert all(r.served_from in {"live", "cache", "degraded"} for r in results)
    assert sum(r.served_from == "live" for r in results) >= 20  # every live slot was used
    assert sum(r.served_from == "degraded" for r in results) >= 1  # 35 distinct queries > cap 20


def test_remaining_report(make_client):
    c = make_client(qloo_daily_live_cap=10, qloo_demo_reserve=4)
    rep = c.budget.remaining("c_x", "run_x")
    assert rep["daily_adhoc"]["cap"] == 6 and rep["demo_reserve"] == 4
    assert rep["client_hourly"]["remaining"] == 60 and rep["run"]["remaining"] == 150
