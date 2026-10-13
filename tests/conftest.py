import json
from pathlib import Path

import pytest
import respx

from blindspot.config import settings_for_tests
from blindspot.qloo.client import QlooClient
from blindspot.qloo.context import RequestContext
from blindspot.qloo.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://hackathon.api.qloo.com"
FAKE_KEY = "test-key-ZX81-do-not-leak-7f3a9c"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
