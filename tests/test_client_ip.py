"""#80: client identity must survive a spoofed X-Forwarded-For and a varying edge-proxy address."""

import pytest
from fastapi.testclient import TestClient

from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.service.app import create_app


def _tc(make_settings, **kw):
    s = make_settings(qloo_api_key="", ip_hash_salt="pepper", **kw)
    return TestClient(create_app(s, QlooClient(s, snapshot=Snapshot())))


def _id(tc, **headers):
    return tc.get("/api/qloo/status", headers=headers).json()["your_client_id"]


def test_two_hop_chain_identity_is_stable_and_unspoofable(make_settings):
    """Cloudflare -> Render -> app: the edge address (rightmost) varies per request and the
    client's own forged entry must not count either. Only the entry the trusted hops wrote counts."""
    with _tc(make_settings, trusted_proxy_hops=2) as tc:
        # Two requests from the same client through two different edge nodes: same id.
        a = _id(tc, **{"X-Forwarded-For": "203.0.113.9, 172.70.1.1"})
        b = _id(tc, **{"X-Forwarded-For": "203.0.113.9, 172.70.2.2"})
        assert a == b
        # Forging a leading entry doesn't change the identity.
        forged = _id(tc, **{"X-Forwarded-For": "1.2.3.4, 203.0.113.9, 172.70.3.3"})
        assert forged == a
        # A different real client is a different id.
        assert _id(tc, **{"X-Forwarded-For": "198.51.100.7, 172.70.1.1"}) != a


def test_too_few_hops_falls_back_to_peer(make_settings):
    """A request that didn't come through the full trusted chain can't choose its own identity."""
    with _tc(make_settings, trusted_proxy_hops=2) as tc:
        peer = _id(tc)
        assert _id(tc, **{"X-Forwarded-For": "1.2.3.4"}) == peer


@pytest.mark.parametrize("hops", [0, 1])
def test_hops_setting_is_respected(make_settings, hops):
    with _tc(make_settings, trusted_proxy_hops=hops) as tc:
        plain = _id(tc)
        one = _id(tc, **{"X-Forwarded-For": "203.0.113.9"})
        assert (one == plain) == (hops == 0)


def test_render_yaml_hops_match_the_real_chain(make_settings):
    """The hosted chain is client -> Cloudflare -> Render proxy -> app (VERIFIED: responses carry
    Server: cloudflare, CF-RAY and x-render-origin-server). With the deployed hop count, a request
    whose rightmost entry is a varying edge address must still map to one identity per client."""
    import yaml

    from tests.test_catalog import ROOT

    env = {
        e["key"]: e.get("value")
        for e in yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["envVars"]
    }
    with _tc(make_settings, trusted_proxy_hops=int(env["TRUSTED_PROXY_HOPS"])) as tc:
        a = _id(tc, **{"X-Forwarded-For": "203.0.113.9, 172.70.1.1"})
        b = _id(tc, **{"X-Forwarded-For": "203.0.113.9, 172.70.2.2"})
        assert a == b, "deployed TRUSTED_PROXY_HOPS reads the edge address, so identity varies per request"
