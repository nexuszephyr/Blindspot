import httpx

from blindspot.qloo.breaker import CircuitBreaker


async def test_retry_on_429_honours_retry_after(make_client, ctx, mock_api, search_body, sleeps):
    route = mock_api.get("/search").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "3"}, json={"message": "slow down"}),
        httpx.Response(200, json=search_body),
    ])
    res = await make_client().search_entities("x", ctx)
    assert res.served_from == "live" and res.ok
    assert route.call_count == 2
    assert sleeps and sleeps[0] >= 3


async def test_retry_on_5xx_and_timeout(make_client, ctx, mock_api, search_body, sleeps):
    route = mock_api.get("/search").mock(side_effect=[
        httpx.Response(503, text="unavailable"),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json=search_body),
    ])
    res = await make_client().search_entities("x", ctx)
    assert res.ok and route.call_count == 3 and len(sleeps) == 2


async def test_gives_up_after_max_attempts(make_client, ctx, mock_api):
    route = mock_api.get("/search").mock(return_value=httpx.Response(500, json={"message": "boom"}))
    res = await make_client(qloo_max_attempts=4).search_entities("x", ctx)
    assert res.served_from == "degraded" and not res.ok
    assert route.call_count == 4
    assert "unavailable" in res.reason


async def test_no_retry_on_400(make_client, ctx, mock_api):
    route = mock_api.get("/v2/insights").mock(
        return_value=httpx.Response(400, json={"error": {"message": "at least one valid signal or filter is required"}})
    )
    res = await make_client().insights({"filter.type": "urn:entity:movie"}, ctx)
    assert route.call_count == 1
    assert res.served_from == "degraded" and res.status == 400
    assert "at least one valid signal" in res.reason


async def test_401_is_auth_error_without_retry(make_client, ctx, mock_api):
    route = mock_api.get("/search").mock(return_value=httpx.Response(401, json={"message": "No API key found"}))
    res = await make_client().search_entities("x", ctx)
    assert route.call_count == 1
    assert res.served_from == "degraded" and "API key" in res.reason


async def test_huge_retry_after_degrades_instead_of_hanging(make_client, ctx, mock_api, sleeps):
    mock_api.get("/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    res = await make_client().search_entities("x", ctx)
    assert res.served_from == "degraded" and not sleeps


async def test_breaker_opens_on_repeated_429_and_recovers(make_client, mock_api, search_body):
    from blindspot.qloo.context import RequestContext

    now = [0.0]
    c = make_client(qloo_max_attempts=1, qloo_breaker_threshold=3, qloo_breaker_cooldown_s=30)
    c.breaker = CircuitBreaker(3, 30, clock=lambda: now[0])
    route = mock_api.get("/search").mock(return_value=httpx.Response(429))
    for i in range(3):
        await c.search_entities(f"q{i}", RequestContext(client_id=f"c{i}"))
    assert c.breaker.state == "open"
    # while open, nobody hits the network, whoever they are
    before = route.call_count
    res = await c.search_entities("other", RequestContext(client_id="c_other"))
    assert res.served_from == "degraded" and "pausing" in res.reason
    assert route.call_count == before
    # after cooldown a single probe goes through and success closes the breaker
    now[0] = 31
    assert c.breaker.state == "half_open"
    route.mock(return_value=httpx.Response(200, json=search_body))
    res = await c.search_entities("probe", RequestContext(client_id="c_probe"))
    assert res.served_from == "live"
    assert c.breaker.state == "closed"


def test_breaker_half_open_allows_one_probe():
    now = [0.0]
    b = CircuitBreaker(1, 10, clock=lambda: now[0])
    b.record_429()
    assert not b.allow()
    now[0] = 11
    assert b.allow() and not b.allow()  # only one probe in flight
    b.record_429()  # probe failed -> open again
    assert b.state == "open"


async def test_observed_error_envelope_is_surfaced(make_client, ctx, mock_api):
    # Envelope shape recorded from hackathon.api.qloo.com (a request without a key).
    mock_api.get("/v2/insights").mock(return_value=httpx.Response(
        400, json={"error": {"reason": "at least one valid signal or filter is required", "message": "Bad Request", "code": 400}}))
    res = await make_client().insights({"filter.type": "urn:entity:movie", "take": 2}, ctx)
    assert res.reason == "Qloo rejected the query: Bad Request: at least one valid signal or filter is required"
