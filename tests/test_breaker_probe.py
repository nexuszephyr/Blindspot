"""#78: a cancelled half-open probe must not lock the breaker; the probe slot is owned."""

import asyncio

import httpx
import pytest

from blindspot.qloo.breaker import CircuitBreaker
from blindspot.qloo.context import RequestContext


def C(cid):
    return RequestContext(client_id=cid)


async def test_cancelled_probe_releases_the_breaker(make_client, mock_api, search_body):
    now = [0.0]
    c = make_client(qloo_max_attempts=1, qloo_max_concurrency=1)
    c.breaker = CircuitBreaker(3, 30, clock=lambda: now[0])
    route = mock_api.get("/search").mock(return_value=httpx.Response(429))
    for i in range(3):
        await c.search_entities(f"b{i}", C(f"c{i}"))
    assert c.breaker.state == "open"

    now[0] = 31  # cool-down over: the next caller becomes the probe
    await c.sem.acquire()  # every connection slot is busy, so the probe waits for one
    used = c.budget.total_used()
    probe = asyncio.create_task(c.search_entities("probe", C("c_probe")))
    await asyncio.sleep(0.05)
    probe.cancel()  # e.g. the request deadline fence fires while it waits
    with pytest.raises(asyncio.CancelledError):
        await probe
    c.sem.release()
    assert c.budget.total_used() == used  # never reached Qloo, so not charged

    route.mock(return_value=httpx.Response(200, json=search_body))
    r = await c.search_entities("next", C("c_next"))
    assert r.served_from == "live", r.reason
    assert c.breaker.state == "closed"


def test_probe_slot_is_owned_by_its_holder():
    now = [0.0]
    b = CircuitBreaker(1, 10, clock=lambda: now[0])
    b.record_429()
    now[0] = 11
    assert b.allow() == (True, True)  # this caller holds the probe
    b.record_failure_other()  # an unrelated call's outcome must not free it
    assert b.allow() == (False, False)
    b.record_failure_other(probe=True)  # the holder releases it
    assert b.allow() == (True, True)
    b.record_429(probe=True)  # probe got 429: open again
    assert b.state == "open" and b.allow() == (False, False)
