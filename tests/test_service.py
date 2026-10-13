import httpx
from fastapi.testclient import TestClient

from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.service.app import create_app
from tests.conftest import FAKE_KEY


def _app(make_settings, **kw):
    s = make_settings(**kw)
    return create_app(s, QlooClient(s, snapshot=Snapshot()))


def test_healthz_and_status_have_no_secrets(make_settings):
    with TestClient(_app(make_settings, ip_hash_salt="pepper-salt-123")) as tc:
        assert tc.get("/healthz").json()["ok"] is True
        r = tc.get("/api/qloo/status")
        assert r.status_code == 200
        body = r.text
        assert FAKE_KEY not in body and "pepper-salt-123" not in body
        st = r.json()
        assert st["mode"] == "live" and st["key_configured"] is True
        assert {"lifetime", "daily", "daily_adhoc", "client_hourly"} <= set(st["budget"])
        assert st["breaker"]["state"] == "closed"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_search_endpoint_live_then_kill_switch(make_settings, mock_api, search_body):
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    with TestClient(_app(make_settings)) as tc:
        r = tc.get("/api/qloo/search", params={"q": "abc"}).json()
        assert r["served_from"] == "live" and r["results"][0]["name"] == "Example Artist One"
        r = tc.get("/api/qloo/search", params={"q": "abc", "qloo": "false"}).json()
        assert r["served_from"] == "disabled" and r["results"] == []
        r = tc.get("/api/qloo/search", params={"q": "abc"}).json()
        assert r["served_from"] == "cache"


def test_search_validates_input(make_settings):
    with TestClient(_app(make_settings)) as tc:
        assert tc.get("/api/qloo/search").status_code == 422
        assert tc.get("/api/qloo/search", params={"q": "x" * 500}).status_code == 422
        r = tc.get("/api/qloo/search", params={"q": "x", "types": "urn:entity:evil"}).json()
        assert r["served_from"] == "degraded"


def test_no_key_runs_in_replay_and_degrades(make_settings, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with TestClient(_app(make_settings, qloo_api_key="")) as tc:
        assert tc.get("/healthz").json()["mode"] == "replay"
        r = tc.get("/api/qloo/search", params={"q": "abc"}).json()
        assert r["served_from"] == "degraded" and "hosted demo" in r["reason"]
    assert route.call_count == 0


def test_http_rate_limit_per_ip(make_settings):
    with TestClient(_app(make_settings, http_rate_per_minute=3)) as tc:
        codes = [tc.get("/api/qloo/status").status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert tc.get("/healthz").status_code == 200  # health is never rate-limited


def test_forwarded_for_trusted_only_with_proxy_hops(make_settings):
    # without trusted hops, spoofed XFF headers don't create new identities
    with TestClient(_app(make_settings, http_rate_per_minute=2)) as tc:
        codes = [tc.get("/api/qloo/status", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
                 for i in range(4)]
        assert codes[-1] == 429
    with TestClient(_app(make_settings, http_rate_per_minute=2, trusted_proxy_hops=1)) as tc:
        codes = [tc.get("/api/qloo/status", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
                 for i in range(4)]
        assert codes == [200] * 4


def test_cors_restricted(make_settings):
    with TestClient(_app(make_settings, allowed_origins="https://good.example")) as tc:
        ok = tc.get("/healthz", headers={"Origin": "https://good.example"})
        bad = tc.get("/healthz", headers={"Origin": "https://evil.example"})
        assert ok.headers.get("access-control-allow-origin") == "https://good.example"
        assert "access-control-allow-origin" not in bad.headers
