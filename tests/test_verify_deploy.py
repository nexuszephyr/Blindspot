"""scripts/verify_deploy.py against a mocked deployment (respx, no network)."""

import json
import runpy
from pathlib import Path

import httpx
import pytest
import respx

ROOT = Path(__file__).resolve().parents[1]
V = runpy.run_path(str(ROOT / "scripts" / "verify_deploy.py"), run_name="vd")
BASE = "https://demo.example"
CAPS = {
    "QLOO_DAILY_LIVE_CAP": 150,
    "QLOO_CLIENT_HOURLY_CAP": 30,
    "QLOO_RUN_CAP": 150,
    "QLOO_DEMO_RESERVE": 50,
}
STATUS = {
    "mode": "live",
    "key_configured": True,
    "your_client_id": "c_0123456789abcdef0123",
    "budget": {
        "daily": {"cap": 150, "used": 1},
        "client_hourly": {"cap": 30},
        "run_cap": 150,
        "demo_reserve": 50,
    },
    "breaker": {"state": "closed"},
}


def mock(
    router,
    *,
    status=STATUS,
    search=("live", "cache"),
    xff_id=None,
    unstable=False,
    unknown=None,
    oversized=None,
):
    router.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={"ok": True, "mode": "live"}))
    counter = iter(range(10_000))

    def status_resp(request):
        body = dict(status)
        if xff_id and "x-forwarded-for" in request.headers:
            body["your_client_id"] = xff_id
        if unstable:  # #80: identity followed the edge node, so it changed on every request
            body["your_client_id"] = f"c_{next(counter):020d}"
        return httpx.Response(200, json=body)

    router.get(f"{BASE}/api/qloo/status").mock(side_effect=status_resp)

    def search_resp(request):
        if len(request.url.params.get("q", "")) > 100:
            return oversized or httpx.Response(422, json={"ok": False, "reason": "invalid input"})
        s = search[min(search_resp.n, len(search) - 1)]
        search_resp.n += 1
        return httpx.Response(200, json={"served_from": s, "results": [{"name": "x"}]})

    search_resp.n = 0
    router.get(f"{BASE}/api/qloo/search").mock(side_effect=search_resp)
    router.get(f"{BASE}/no-such-route").mock(
        return_value=unknown or httpx.Response(404, json={"detail": "Not Found"})
    )


def run(caps=CAPS, **kw):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as c:
        mock(router, **kw)
        return {name: ok for name, ok, _ in V["verify"](BASE, c, "q", caps)}


def test_all_pass():
    res = run()
    assert len(res) == 8 and all(res.values())


def test_declared_caps_read_from_render_yaml():
    caps = V["declared_caps"](ROOT / "render.yaml")
    assert set(caps) == set(CAPS) and all(isinstance(v, int) for v in caps.values())


def test_cap_mismatch_fails():
    assert run(caps={**CAPS, "QLOO_DAILY_LIVE_CAP": 500})["status caps match render.yaml"] is False


def test_open_breaker_fails():
    assert (
        run(status={**STATUS, "breaker": {"state": "open"}})["status key_configured, breaker closed"] is False
    )


@pytest.mark.parametrize("leak", ["/app/data/qloo_cache.sqlite", "A" * 40, "10.0.3.7", "blindspot.internal"])
def test_status_leak_detected(leak):
    assert run(status={**STATUS, "note": leak})["status leaks nothing"] is False


def test_spoofed_xff_changing_identity_fails():
    assert (
        run(xff_id="c_ffffffffffffffffffff")["client identity stable, forged X-Forwarded-For ignored"]
        is False
    )


def test_unstable_identity_without_any_header_fails():
    """Regression for #80: before TRUSTED_PROXY_HOPS=2 the id varied per request with no header at all,
    and a single-pair check could pass by chance."""
    assert run(unstable=True)["client identity stable, forged X-Forwarded-For ignored"] is False


def test_second_search_must_be_cache():
    assert run(search=("live", "live"))["search live/cache then cache"] is False


def test_unknown_route_with_traceback_fails():
    html = httpx.Response(500, text="Traceback (most recent call last): ...")
    assert run(unknown=html)["unknown route: JSON 404, no traceback"] is False


def test_oversized_q_500_fails():
    assert run(oversized=httpx.Response(500, text="boom"))["oversized q: 4xx not 500"] is False


def test_output_has_no_values(capsys):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as c:
        mock(router)
        rc = V["report"](V["verify"](BASE, c, "q", CAPS))
    out = capsys.readouterr().out
    assert rc == 0 and "c_0123456789abcdef0123" not in out and "1.2.3.4" not in out and "5.6.7.8" not in out
    assert "x" not in out.split("detail", 1)[1].split()


def test_json_output(capsys):
    with respx.mock(assert_all_called=False) as router, httpx.Client() as c:
        mock(router, search=("live", "live"))
        rc = V["report"](V["verify"](BASE, c, "q", CAPS), as_json=True)
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["failed"] == 1 and len(doc["checks"]) == 8


def test_cold_times_first_request():
    with respx.mock() as router, httpx.Client() as c:
        router.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={"mode": "live"}))
        _name, ok, detail = V["cold"](BASE, c)
    assert ok and "s to response" in detail


def test_cold_refuses_without_confirm_idle(capsys):
    assert V["main"]([BASE, "--cold"]) == 2
    assert "--confirm-idle" in capsys.readouterr().out
