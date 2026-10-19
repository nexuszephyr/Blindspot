"""#80: client identity must survive spoofed X-Forwarded-For entries and varying proxy addresses.

The hosted chain is client -> Cloudflare -> Render edge -> Render internal proxy -> app (VERIFIED
2026-10-19 with the proxy-headers probe: 3 entries [client, edge, internal]; a forged value becomes a
4th entry on the LEFT; entry [-3] is identical across every request and equals cf-connecting-ip).
"""

import logging

import pytest
from fastapi.testclient import TestClient

from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.service.app import create_app

CHAIN_A = "203.0.113.9, 172.70.1.1, 10.0.0.5"  # client, Cloudflare edge, Render internal
CHAIN_B = "203.0.113.9, 172.70.2.2, 10.0.0.7"  # same client, different edge and internal hops


def _tc(make_settings, **kw):
    s = make_settings(qloo_api_key="", ip_hash_salt="pepper", **kw)
    return TestClient(create_app(s, QlooClient(s, snapshot=Snapshot())))


def _id(tc, **headers):
    return tc.get("/api/qloo/status", headers=headers).json()["your_client_id"]


def test_three_hop_chain_identity_is_stable_and_unspoofable(make_settings):
    with _tc(make_settings, trusted_proxy_hops=3) as tc:
        a = _id(tc, **{"X-Forwarded-For": CHAIN_A})
        assert a == _id(tc, **{"X-Forwarded-For": CHAIN_B})  # both proxy hops vary: same id
        # Forged entries are appended to, so they sit left of the client and never count.
        assert _id(tc, **{"X-Forwarded-For": "1.2.3.4, " + CHAIN_A}) == a
        assert _id(tc, **{"X-Forwarded-For": "1.2.3.4, 9.9.9.9, " + CHAIN_B}) == a
        assert _id(tc, **{"X-Forwarded-For": "198.51.100.7, 172.70.1.1, 10.0.0.5"}) != a


def test_too_few_hops_falls_back_to_peer(make_settings):
    """A request that didn't come through the full trusted chain can't choose its own identity."""
    with _tc(make_settings, trusted_proxy_hops=3) as tc:
        peer = _id(tc)
        assert _id(tc, **{"X-Forwarded-For": "1.2.3.4"}) == peer
        assert _id(tc, **{"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}) == peer


@pytest.mark.parametrize("hops", [0, 1])
def test_hops_setting_is_respected(make_settings, hops):
    with _tc(make_settings, trusted_proxy_hops=hops) as tc:
        plain = _id(tc)
        one = _id(tc, **{"X-Forwarded-For": "203.0.113.9"})
        assert (one == plain) == (hops == 0)


def test_render_yaml_hops_match_the_real_chain(make_settings):
    """With the deployed hop count, varying edge and internal addresses must still give one identity."""
    import yaml

    from tests.test_catalog import ROOT

    env = {
        e["key"]: e.get("value")
        for e in yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["envVars"]
    }
    with _tc(make_settings, trusted_proxy_hops=int(env["TRUSTED_PROXY_HOPS"])) as tc:
        a = _id(tc, **{"X-Forwarded-For": CHAIN_A})
        b = _id(tc, **{"X-Forwarded-For": CHAIN_B})
        assert a == b, "deployed TRUSTED_PROXY_HOPS reads a proxy address, so identity varies per request"


def test_chosen_entry_is_checked_against_cf_connecting_ip(make_settings, caplog):
    """Sanity check: when Cloudflare's cf-connecting-ip is present it must equal the entry we chose.
    A mismatch means the hop count is wrong; it is logged (no IPs) and the chosen entry still wins."""
    caplog.set_level(logging.WARNING, logger="blindspot.service")
    with _tc(make_settings, trusted_proxy_hops=3) as tc:
        _id(tc, **{"X-Forwarded-For": CHAIN_A, "CF-Connecting-IP": "203.0.113.9"})
        assert "cf-connecting-ip" not in caplog.text
        with _tc(make_settings, trusted_proxy_hops=2) as wrong:
            _id(wrong, **{"X-Forwarded-For": CHAIN_A, "CF-Connecting-IP": "203.0.113.9"})
    assert "cf-connecting-ip" in caplog.text and "203.0.113.9" not in caplog.text
