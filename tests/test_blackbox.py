"""Black-box tests (#17): the real FastAPI app and QlooClient against a fake Qloo server.

Every request goes app -> QlooClient -> FakeTransport -> FakeQloo, so these tests see what a user
and Qloo would see: HTTP status, the JSON envelope, and the number of upstream requests. They are
offline and keyless (a fake key, a fake server) and guard the client's safety properties: retries,
breaker, budgets, deadline, per-request kill switch, truthful served_from, no leaks, input fuzzing
and proxy identity.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
import pytest
from pydantic import SecretStr

from blindspot.qloo.breaker import CircuitBreaker
from blindspot.qloo.cache import cache_key, normalize_params
from blindspot.qloo.client import MAX_RETRY_AFTER_S, QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.service.app import create_app
from tests.conftest import FAKE_KEY
from tests.fakeqloo import SEARCH_RESULT, FakeQloo, FakeTransport

SALT = "blackbox-salt-91x"


@dataclass
class Harness:
    fake: FakeQloo
    client: QlooClient
    http: httpx.AsyncClient
    sleeps: list[float] = field(default_factory=list)

    async def search(self, q: str, *, ip: str | None = None, **params) -> dict:
        headers = {"X-Forwarded-For": ip} if ip else {}
        r = await self.http.get("/api/qloo/search", params={"q": q, **params}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    async def status(self, *, ip: str | None = None) -> dict:
        headers = {"X-Forwarded-For": ip} if ip else {}
        r = await self.http.get("/api/qloo/status", headers=headers)
        assert r.status_code == 200
        return r.json()


@pytest.fixture
async def harness(make_settings):
    """Factory: harness(real_sleep=False, snapshot=None, **settings) -> Harness."""
    opened: list[Harness] = []

    async def make(*, real_sleep: bool = False, snapshot: Snapshot | None = None, **kw) -> Harness:
        base = dict(
            ip_hash_salt=SALT,
            http_rate_per_minute=100_000,
            qloo_breaker_threshold=1000,
            qloo_client_hourly_cap=1000,
            qloo_run_cap=1000,
            qloo_daily_live_cap=1000,
            qloo_demo_reserve=0,
        )
        s = make_settings(**{**base, **kw})
        fake, sleeps = FakeQloo(), []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        client = QlooClient(
            s,
            snapshot=snapshot or Snapshot(),
            transport=FakeTransport(fake),
            sleep=asyncio.sleep if real_sleep else fake_sleep,
        )
        app = create_app(s, client)
        client.startup()  # ASGITransport doesn't run the lifespan
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://blindspot.test")
        h = Harness(fake, client, http, sleeps)
        opened.append(h)
        return h

    yield make
    for h in opened:
        await h.http.aclose()
        await h.client.aclose()
        h.client.db.close()


# --------------------------------------------------------------------------- retries
# Policy (client._send): 5xx, timeouts and connection resets are retried up to qloo_max_attempts;
# 429 is retried honouring Retry-After up to MAX_RETRY_AFTER_S; other 4xx and unusable 200s
# (non-JSON, HTML, oversized) fail at once.
@pytest.mark.parametrize(
    "behaviour, attempts, served_from, ok",
    [
        ("ok", 1, "live", True),
        ("empty", 1, "live", True),
        ("bad_request", 1, "degraded", False),
        ("unauthorized", 1, "degraded", False),
        ("not_found", 1, "degraded", False),
        ("rate_limited", 4, "degraded", False),
        (f"rate_limited:{int(MAX_RETRY_AFTER_S) + 10}", 1, "degraded", False),
        ("server_500", 4, "degraded", False),
        ("server_502", 4, "degraded", False),
        ("server_503", 4, "degraded", False),
        ("slow", 4, "degraded", False),
        ("reset", 4, "degraded", False),
        ("malformed", 1, "degraded", False),
        ("html", 1, "degraded", False),
        ("oversized", 1, "degraded", False),
    ],
)
async def test_attempts_per_failure_type(harness, behaviour, attempts, served_from, ok):
    h = await harness(qloo_max_attempts=4, qloo_timeout_s=0.2)
    h.fake.behaviour, h.fake.slow_s = behaviour, 1.0
    r = await h.search("retry-matrix")
    assert (r["served_from"], r["ok"]) == (served_from, ok)
    assert h.fake.count() == attempts
    assert all(s.has_key_header for s in h.fake.seen)  # header auth on every attempt
    assert len(h.sleeps) == attempts - 1  # a backoff between attempts, none after the last
    if not ok:
        assert r["reason"] and "Traceback" not in r["reason"]


async def test_retry_after_is_honoured(harness):
    h = await harness()
    h.fake.queue = ["rate_limited:3", "ok"]
    r = await h.search("retry-after")
    assert r["served_from"] == "live" and h.fake.count() == 2
    assert h.sleeps and h.sleeps[0] >= 3


async def test_retry_after_beyond_the_cap_is_not_waited_for(harness):
    # deadline longer than the wait, so the cap itself (not the deadline) has to stop it
    h = await harness(request_deadline_s=120)
    h.fake.behaviour = f"rate_limited:{int(MAX_RETRY_AFTER_S) + 1}"
    r = await h.search("too-long")
    assert r["served_from"] == "degraded" and h.fake.count() == 1 and h.sleeps == []


@pytest.mark.parametrize("max_attempts", [1, 2, 3])
async def test_max_attempts_respected(harness, max_attempts):
    h = await harness(qloo_max_attempts=max_attempts)
    h.fake.behaviour = "server_503"
    await h.search("cap")
    assert h.fake.count() == max_attempts


# --------------------------------------------------------------------------- breaker
async def test_breaker_opens_serves_everyone_degraded_then_recovers(harness):
    h = await harness(qloo_max_attempts=1, trusted_proxy_hops=1)
    now = [0.0]
    h.client.breaker = CircuitBreaker(3, 30, clock=lambda: now[0])
    h.fake.behaviour = "rate_limited"
    for i in range(3):  # three rate-limited requests from three different clients
        await h.search(f"trip-{i}", ip=f"203.0.113.{i}")
    assert h.client.breaker.state == "open" and h.fake.count() == 3

    h.fake.behaviour = "ok"
    t0 = time.monotonic()
    for i in range(5):  # everyone, not just the clients that tripped it
        r = await h.search(f"while-open-{i}", ip=f"198.51.100.{i}")
        assert r["served_from"] == "degraded" and "pausing" in r["reason"]
    assert time.monotonic() - t0 < 1.0  # answered instantly
    assert h.fake.count() == 3  # nothing reached Qloo while open
    assert (await h.status())["breaker"]["state"] == "open"

    now[0] = 31  # cooldown over: one probe goes through and closes it
    r = await h.search("probe", ip="192.0.2.1")
    assert r["served_from"] == "live" and h.client.breaker.state == "closed"
    assert h.fake.count() == 4


# --------------------------------------------------------------------------- budgets
async def _used(h: Harness) -> int:
    return (await h.status())["budget"]["daily"]["used"]


async def test_daily_budget_trips_and_counts_exactly(harness):
    h = await harness(qloo_daily_live_cap=3)
    results = [await h.search(f"daily-{i}") for i in range(5)]
    assert [r["served_from"] for r in results] == ["live"] * 3 + ["degraded"] * 2
    assert "daily" in results[-1]["reason"]
    assert h.fake.count() == 3 == await _used(h)


async def test_client_hourly_budget_is_per_client(harness):
    h = await harness(qloo_client_hourly_cap=2, trusted_proxy_hops=1)
    a = [await h.search(f"a-{i}", ip="203.0.113.10") for i in range(3)]
    b = await h.search("b-0", ip="203.0.113.20")
    assert [r["served_from"] for r in a] == ["live", "live", "degraded"]
    assert "hourly" in a[-1]["reason"]
    assert b["served_from"] == "live"  # another client is unaffected
    assert h.fake.count() == 3 == await _used(h)


async def test_run_budget_counts_retries(harness):
    h = await harness(qloo_run_cap=2, qloo_max_attempts=4)
    h.fake.behaviour = "server_503"
    r = await h.search("run-cap")
    assert r["served_from"] == "degraded" and "run" in r["reason"]
    assert h.fake.count() == 2 == await _used(h)  # retries are live attempts and count


async def test_demo_reserve_is_kept_from_adhoc_traffic(harness):
    h = await harness(qloo_daily_live_cap=4, qloo_demo_reserve=2)
    results = [await h.search(f"adhoc-{i}") for i in range(4)]
    assert [r["served_from"] for r in results] == ["live", "live", "degraded", "degraded"]
    assert "demo reserve" in results[-1]["reason"]
    budget = (await h.status())["budget"]
    assert budget["daily_adhoc"]["remaining"] == 0 and budget["daily"]["remaining"] == 2
    assert h.fake.count() == 2 == budget["daily"]["used"]


# --------------------------------------------------------------------------- deadline
DEADLINE = 1.0


@pytest.mark.parametrize("behaviour", ["rate_limited:30", "rate_limited:1", "slow", "server_503"])
async def test_fifty_concurrent_requests_all_meet_the_deadline(harness, behaviour):
    h = await harness(
        real_sleep=True,
        request_deadline_s=DEADLINE,
        qloo_timeout_s=10.0,
        qloo_max_concurrency=4,
        qloo_rps=1000,
    )
    h.fake.behaviour, h.fake.slow_s = behaviour, 3.0

    async def timed(i):
        t0 = time.monotonic()
        r = await h.search(f"flood-{i}")
        return r, time.monotonic() - t0

    out = await asyncio.wait_for(asyncio.gather(*(timed(i) for i in range(50))), DEADLINE + 5)
    worst = max(t for _, t in out)
    assert worst <= DEADLINE + 0.5, f"slowest request took {worst:.2f}s"
    assert all(r["served_from"] == "degraded" for r, _ in out)


# --------------------------------------------------------------------------- kill switch
async def test_kill_switch_is_per_request(harness):
    h = await harness(trusted_proxy_hops=1)
    h.fake.behaviour, h.fake.slow_s = "slow", 0.2

    async def a(i):  # client A: Qloo off for its requests
        return await h.search(f"a-off-{i}", ip="203.0.113.1", qloo="false")

    async def b(i):  # client B: normal
        return await h.search(f"b-on-{i}", ip="203.0.113.2")

    res = await asyncio.gather(*(f(i) for i in range(5) for f in (a, b)))
    res += [await b(99)]  # B after A has switched Qloo off: still live
    a_res = [r for i, r in enumerate(res[:-1]) if i % 2 == 0]
    b_res = [r for i, r in enumerate(res[:-1]) if i % 2 == 1] + [res[-1]]
    assert all(r["served_from"] == "disabled" and r["results"] == [] for r in a_res)
    assert all(r["served_from"] == "live" for r in b_res)
    assert sum(h.fake.count(f"a-off-{i}") for i in range(5)) == 0  # zero upstream calls for A
    assert h.fake.count() == 6


# --------------------------------------------------------------------------- served_from
async def test_served_from_is_truthful(harness, tmp_path):
    norm = normalize_params({"query": "from-snapshot", "types": None, "take": 5})
    key = cache_key("GET", "/search", norm)
    entry = {"key": key, "method": "GET", "path": "/search", "params": norm, "status": 200}
    snap = Snapshot({key: {**entry, "body": SEARCH_RESULT, "fetched_at": 1.0}})
    h = await harness(snapshot=snap, qloo_max_attempts=1)

    assert (await h.search("fresh"))["served_from"] == "live"
    assert h.fake.count() == 1
    assert (await h.search("fresh"))["served_from"] == "cache"
    assert (await h.search("from-snapshot"))["served_from"] == "snapshot"
    assert (await h.search("fresh", qloo="false"))["served_from"] == "disabled"
    h.fake.behaviour = "server_503"
    assert (await h.search("failing"))["served_from"] == "degraded"
    assert h.fake.count() == 2  # only `fresh` (once) and `failing` reached Qloo


async def test_replay_mode_without_key_never_calls_qloo(harness):
    h = await harness(qloo_api_key=SecretStr(""))
    r = await h.search("anything")
    assert r["served_from"] == "degraded" and h.fake.count() == 0
    assert (await h.status())["mode"] == "replay"


# --------------------------------------------------------------------------- no leaks
async def test_status_leaks_nothing(harness, tmp_path):
    h = await harness()
    await h.search("warm")
    r = await h.http.get("/api/qloo/status")
    body = r.text
    secrets = [FAKE_KEY, FAKE_KEY[:8], FAKE_KEY[-8:], SALT]
    paths = [str(tmp_path), tmp_path.name, "cache.sqlite", "snapshot.json", "/raw"]
    hosts = ["hackathon.api.qloo.com", "localhost", "127.0.0.1", "blindspot.test"]
    env_values = [str(v) for v in h.client.s.model_dump().values() if isinstance(v, str) and len(v) > 6]
    for needle in secrets + paths + hosts + env_values:
        assert needle not in body, f"status leaks {needle!r}"
    assert "Traceback" not in body


# --------------------------------------------------------------------------- fuzzing
FUZZ = [
    "",
    "x" * 10_000,
    "مرحبا بالعالم ‮ gnp.exe",
    "nul\x00byte",
    "\x01\x02\x1b[31mctrl",
    "' OR 1=1; DROP TABLE calls; --",
    "$(rm -rf /); `id` | cat /etc/passwd",
    "../../../../etc/passwd",
    "%00%2e%2e%2f",
    "{{7*7}}<script>alert(1)</script>",
]


@pytest.mark.parametrize("q", FUZZ)
async def test_fuzzed_query_is_handled_safely(harness, q):
    h = await harness()
    r = await h.http.get("/api/qloo/search", params={"q": q})
    assert r.status_code < 500 and "Traceback" not in r.text and 'File "' not in r.text
    body = r.json()
    if r.status_code == 200:
        assert body["served_from"] in {"live", "cache", "snapshot", "degraded", "disabled"}
    assert h.fake.count() <= 1  # at most the one search; never a different endpoint
    assert all(s.path == "/search" for s in h.fake.seen)


@pytest.mark.parametrize(
    "query",
    [
        "q=a&q=b",
        "q=a&take=-1",
        "q=a&take=abc",
        "q=a&take=99999999999999999999",
        "q=a&types=urn:entity:artist&types=urn:entity:place",
        "q=a&types=" + "x" * 300,
        "q=a&qloo=maybe",
        "q=%ff%fe",
    ],
)
async def test_odd_parameters_are_handled_safely(harness, query):
    h = await harness()
    r = await h.http.get(f"/api/qloo/search?{query}")
    assert r.status_code < 500 and "Traceback" not in r.text


# --------------------------------------------------------------------------- identity
async def test_forged_forwarded_for_does_not_change_identity_without_trusted_hops(harness):
    h = await harness(trusted_proxy_hops=0, qloo_client_hourly_cap=2)
    forged = [f"10.9.8.{i}" for i in range(4)]
    results = [await h.search(f"forged-{i}", ip=ip) for i, ip in enumerate(forged)]
    assert [r["served_from"] for r in results] == ["live", "live", "degraded", "degraded"]
    ids = {(await h.status(ip=ip))["your_client_id"] for ip in forged}
    assert len(ids) == 1  # one identity, whatever the header says
    assert h.fake.count() == 2
