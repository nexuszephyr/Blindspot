"""Opt-in live test: QLOO_LIVE_TESTS=1 pytest -m live  (uses 1 live call, needs .env)."""

import os

import pytest

from blindspot.config import Settings
from blindspot.qloo.client import QlooClient
from blindspot.qloo.context import RequestContext

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("QLOO_LIVE_TESTS") != "1", reason="set QLOO_LIVE_TESTS=1 to run")
async def test_live_search(tmp_path):
    s = Settings(qloo_cache_path=tmp_path / "c.sqlite", qloo_snapshot_path=tmp_path / "none.json")
    assert s.has_key, "QLOO_API_KEY missing from environment/.env"
    c = QlooClient(s)
    try:
        res = await c.search_entities("Radiohead", RequestContext.script("live-test"), take=2)
        assert res.served_from == "live", res.reason
        assert res.ok and res.entities
    finally:
        await c.aclose()
