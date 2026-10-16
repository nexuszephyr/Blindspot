import json
import logging

import httpx

from blindspot.qloo.context import hash_client_ip
from blindspot.qloo.errors import QlooAuthError
from blindspot.qloo.redact import install_log_redaction, redact
from tests.conftest import FAKE_KEY


async def test_key_never_leaks(make_client, ctx, mock_api, search_body, caplog, tmp_path):
    install_log_redaction()
    caplog.set_level(logging.DEBUG)
    # A hostile/buggy upstream that echoes the key back in an error body.
    mock_api.get("/search").mock(
        side_effect=[
            httpx.Response(400, json={"message": f"bad key {FAKE_KEY}"}),
            httpx.Response(200, json=search_body),
        ]
    )
    mock_api.get("/v2/insights").mock(return_value=httpx.Response(401, text=f"X-Api-Key: {FAKE_KEY}"))
    c = make_client(qloo_mode="record")

    r1 = await c.search_entities("x", ctx)
    r2 = await c.search_entities("y", ctx)
    r3 = await c.insights(
        {"filter.type": "urn:entity:movie", "signal.interests.tags": ["urn:tag:genre:x"]}, ctx, strict=False
    )
    assert r3.status == 401, r3.reason  # the 401 path must really run (audit F3)
    logging.getLogger("blindspot.test").warning("leak attempt %s", FAKE_KEY)

    # the key was really sent, in the header only
    req = mock_api.calls[0].request
    assert req.headers["X-Api-Key"] == FAKE_KEY and FAKE_KEY not in str(req.url)

    blobs = [r.model_dump_json() for r in (r1, r2, r3)]
    blobs.append(json.dumps(c.calls.last(100)))
    blobs.append(json.dumps(c.cache.entries()))
    blobs.append(json.dumps(c.status(ctx)))
    blobs.append(caplog.text)
    blobs += [p.read_text(encoding="utf-8") for p in (tmp_path / "raw").glob("*.json")]
    for blob in blobs:
        assert FAKE_KEY not in blob
    assert "[REDACTED]" in caplog.text
    assert any("[REDACTED]" in b for b in blobs[:3])


def test_exception_and_redact_helpers():
    from blindspot.qloo.redact import register_secret

    register_secret(FAKE_KEY)
    err = QlooAuthError(redact(f"failed with {FAKE_KEY}"))
    assert FAKE_KEY not in str(err)
    assert redact('{"x-api-key": "abc123secret"}').count("abc123secret") == 0


def test_client_ids_are_salted_hashes():
    a = hash_client_ip("203.0.113.9", "salt1")
    assert a == hash_client_ip("203.0.113.9", "salt1")
    assert a != hash_client_ip("203.0.113.9", "salt2")
    assert "203.0.113.9" not in a and a.startswith("c_")
