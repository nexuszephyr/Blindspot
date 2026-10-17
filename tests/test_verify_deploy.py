"""scripts/verify_deploy.py against a mocked deployment (respx, no network)."""

import runpy
from pathlib import Path

import httpx
import pytest
import respx

V = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "verify_deploy.py"), run_name="vd")
BASE = "https://demo.example"
STATUS = {
    "mode": "live",
    "key_configured": True,
    "your_client_id": "c_0123456789abcdef0123",
    "budget": {"daily": {"cap": 500, "used": 1}},
    "breaker": {"state": "closed"},
}


def mock(router, *, status=STATUS, search=("live", "cache"), xff_id=None):
    router.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={"ok": True, "mode": "live"}))

    def status_resp(request):
        body = dict(status)
        if xff_id and "x-forwarded-for" in request.headers:
            body["your_client_id"] = xff_id
        return httpx.Response(200, json=body)

    router.get(f"{BASE}/api/qloo/status").mock(side_effect=status_resp)
    router.get(f"{BASE}/api/qloo/search").mock(
        side_effect=[httpx.Response(200, json={"served_from": s, "results": [{"name": "x"}]}) for s in search]
    )


def run(**kw):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as c:
        mock(router, **kw)
        return {name: ok for name, ok, _ in V["verify"](BASE, c, "q")}


def test_all_pass():
    assert all(run().values())


@pytest.mark.parametrize("leak", ["/app/data/qloo_cache.sqlite", "A" * 40, "10.0.3.7"])
def test_status_leak_detected(leak):
    assert run(status={**STATUS, "cache_path": leak})["status leaks nothing"] is False


def test_spoofed_xff_changing_identity_fails():
    assert run(xff_id="c_ffffffffffffffffffff")["forged X-Forwarded-For ignored"] is False


def test_second_search_must_be_cache():
    assert run(search=("live", "live"))["search live/cache then cache"] is False


def test_output_has_no_values(capsys):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as c:
        mock(router)
        rc = V["report"](V["verify"](BASE, c, "q"))
    out = capsys.readouterr().out
    assert rc == 0 and "c_0123456789abcdef0123" not in out and "x" not in out.split("detail", 1)[1].split()


def test_cold_times_first_request():
    with respx.mock() as router, httpx.Client() as c:
        router.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={"mode": "live"}))
        _name, ok, detail = V["cold"](BASE, c, 1200, sleep=lambda s: None)
    assert ok and "s to response" in detail
