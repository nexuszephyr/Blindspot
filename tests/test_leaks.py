"""Leak tests (audit F2/F3): no raw IP in storage, no key on stdout/stderr/logs anywhere."""

import json
import logging
import runpy
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.service.app import create_app
from tests.conftest import FAKE_KEY

IP = "203.0.113.77"
ROOT = Path(__file__).resolve().parents[1]


def _dump_db(client: QlooClient) -> str:
    out = []
    for t in ("cache", "calls", "live_attempts"):
        out += [json.dumps(dict(r)) for r in client.db.query(f"SELECT * FROM {t}")]  # noqa: S608
    return "\n".join(out)


def test_raw_ip_never_stored(make_settings, mock_api, search_body, caplog):
    caplog.set_level(logging.DEBUG)
    mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    s = make_settings(trusted_proxy_hops=1)
    client = QlooClient(s, snapshot=Snapshot())
    with TestClient(create_app(s, client)) as tc:
        h = {"X-Forwarded-For": IP}
        status = tc.get("/api/qloo/status", headers=h).text
        tc.get("/api/qloo/search", params={"q": "abc"}, headers=h)
        tc.get("/api/qloo/search", params={"q": "abc", "qloo": "false"}, headers=h)
        assert json.loads(status)["your_client_id"].startswith("c_")
    blob = _dump_db(client) + status + caplog.text
    assert IP not in blob
    assert client.db.query("SELECT COUNT(*) n FROM live_attempts")[0]["n"] == 1


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("QLOO_API_KEY", FAKE_KEY)
    monkeypatch.setenv("QLOO_CACHE_PATH", str(tmp_path / "c.sqlite"))
    monkeypatch.setenv("QLOO_SNAPSHOT_PATH", str(tmp_path / "none.json"))
    monkeypatch.setenv("QLOO_RAW_DIR", str(tmp_path / "raw"))
    from blindspot.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _echo_key(request):  # hostile upstream echoing the key into errors
    return httpx.Response(401, json={"error": {"reason": f"bad key {FAKE_KEY}", "message": FAKE_KEY}})


def test_key_never_on_stdout_stderr(isolated_env, mock_api, capfd, caplog):
    caplog.set_level(logging.DEBUG)
    mock_api.get("/search").mock(side_effect=_echo_key)
    mock_api.get("/v2/insights").mock(side_effect=httpx.ConnectError(f"boom {FAKE_KEY}"))
    from blindspot.qloo import cli

    cli.main(["search", "x"])
    cli.main(
        [
            "insights",
            "--type",
            "urn:entity:artist",
            "-p",
            "signal.interests.entities=00000000-0000-4000-8000-000000000001",
        ]
    )
    cli.main(["calls", "--last", "5"])
    cli.main(["budget"])
    # scripts/smoke.py end to end (401 path)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        with pytest.raises(SystemExit):
            runpy.run_path(str(ROOT / "scripts" / "smoke.py"), run_name="__main__")
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    # service startup + an error path
    from blindspot.service.app import main_app

    with TestClient(main_app()) as tc:
        tc.get("/api/qloo/search", params={"q": "y"})
        tc.get("/api/qloo/status")
    out = capfd.readouterr()
    assert FAKE_KEY not in out.out
    assert FAKE_KEY not in out.err
    assert FAKE_KEY not in caplog.text
