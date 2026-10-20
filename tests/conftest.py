import asyncio
import json
from pathlib import Path

import pytest
import respx

from blindspot.config import Settings, settings_for_tests
from blindspot.qloo.client import QlooClient
from blindspot.qloo.context import RequestContext
from blindspot.qloo.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://hackathon.api.qloo.com"
FAKE_KEY = "test-key-ZX81-do-not-leak-7f3a9c"
HARNESS_SALT = "blackbox-salt-91x"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # CI sets QLOO_MODE=replay / QLOO_ENABLED=false globally; tests must not inherit any setting.
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def search_body():
    return load_fixture("search_synthetic.json")


@pytest.fixture
def insights_body():
    return load_fixture("insights_synthetic.json")


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make_settings(tmp_path):
    def _make(**kw):
        base = dict(
            qloo_api_key=FAKE_KEY,
            qloo_cache_path=tmp_path / "cache.sqlite",
            qloo_snapshot_path=tmp_path / "snapshot.json",
            qloo_raw_dir=tmp_path / "raw",
            qloo_rps=1000,
            qloo_max_concurrency=8,
        )
        base.update(kw)
        return settings_for_tests(**base)

    return _make


@pytest.fixture
def make_client(make_settings, sleeps):
    clients = []

    def _make(snapshot: Snapshot | None = None, **kw):
        async def fake_sleep(s):
            sleeps.append(s)

        c = QlooClient(make_settings(**kw), snapshot=snapshot or Snapshot(), sleep=fake_sleep)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.db.close()


@pytest.fixture
def ctx():
    return RequestContext(client_id="c_test_a")


@pytest.fixture
def mock_api():
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def harness(make_settings, sleeps):
    """The real app and QlooClient wired to a fake Qloo server (tests/fakeqloo.py), for black-box
    tests of anything on the request path. Call `await harness(**settings)`; options:
    `real_sleep=True` (default: backoff sleeps are recorded in `sleeps`, not slept) and `snapshot=`.
    Runs the app's real lifespan, cleans up even if a test fails, and fails the test if the fake
    met anything unexpected (a misspelled behaviour can't pass silently)."""
    from contextlib import AsyncExitStack

    import httpx

    from blindspot.service.app import create_app
    from tests.fakeqloo import FakeQloo, FakeTransport, Harness

    async with AsyncExitStack() as stack:
        fakes: list[FakeQloo] = []

        async def make(*, real_sleep: bool = False, snapshot: Snapshot | None = None, **kw) -> Harness:
            base = dict(
                ip_hash_salt=HARNESS_SALT,
                http_rate_per_minute=100_000,
                qloo_breaker_threshold=1000,
                qloo_client_hourly_cap=1000,
                qloo_run_cap=1000,
                qloo_daily_live_cap=1000,
                qloo_demo_reserve=0,
            )
            s = make_settings(**{**base, **kw})
            fake = FakeQloo()
            fakes.append(fake)

            async def fake_sleep(seconds: float) -> None:
                sleeps.append(seconds)

            client = QlooClient(
                s,
                snapshot=snapshot or Snapshot(),
                transport=FakeTransport(fake),
                sleep=asyncio.sleep if real_sleep else fake_sleep,
            )
            stack.callback(client.db.close)  # registered first, so it runs last
            app = create_app(s, client)
            await stack.enter_async_context(app.router.lifespan_context(app))  # startup + aclose
            http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://blindspot.test")
            stack.push_async_callback(http.aclose)
            return Harness(fake, client, http, sleeps)

        yield make
        unexpected = [e for f in fakes for e in f.errors]
        assert not unexpected, f"the fake Qloo server met something unexpected: {unexpected}"
