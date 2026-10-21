"""Black-box tests for the scenario and answer-key routes (#25): the real app against a synthetic
answer-key store built with the fake Qloo server. Offline, keyless, no real data."""

from __future__ import annotations

import html

import pytest

from blindspot.catalog import load_catalog
from blindspot.groundtruth import Builder
from blindspot.groundtruth.store import Store
from blindspot.idlock import load_lock
from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot
from blindspot.scenarios.generator import generate
from tests.fakeqloo import FakeQloo, FakeTransport
from tests.test_catalog import ROOT

CFG = ROOT / "config"


@pytest.fixture(scope="module")
def exam():
    return generate(load_catalog(CFG), load_lock(CFG)).scenarios


@pytest.fixture
def demo(exam):
    return [s for s in exam if "demo" in s.tags][:6]


@pytest.fixture
async def built(make_settings, tmp_path, demo):
    """The path of a store holding synthetic answer keys for a few demo scenarios."""
    s = make_settings(
        qloo_cache_path=tmp_path / "build.cache",
        qloo_max_live_calls=100_000,
        qloo_daily_live_cap=100_000,
        qloo_demo_reserve=0,
    )

    async def no_sleep(_s):
        return None

    client = QlooClient(s, snapshot=Snapshot(), transport=FakeTransport(FakeQloo()), sleep=no_sleep)
    path = tmp_path / "gt.sqlite"
    store = Store(path)
    try:
        await Builder(client, store).build(demo)
    finally:
        client.db.close()
        store.close()
    return path


def _envelope_ok(body: dict) -> None:
    assert set(body) == {"data", "served_from", "synthetic", "degraded_reason", "run_id"}
    assert body["run_id"].startswith("run_")


# --------------------------------------------------------------------------- /api/scenarios
async def test_list_returns_envelope_and_paginates(harness, exam, tmp_path):
    h = await harness(config_dir=CFG, groundtruth_path=tmp_path / "none.sqlite")
    body = await h.json("/api/scenarios", limit=5)
    _envelope_ok(body)
    assert body["served_from"] == "disabled" and body["degraded_reason"] is None
    d = body["data"]
    assert d["total"] == len(exam) and len(d["items"]) == 5 and d["offset"] == 0
    nxt = (await h.json("/api/scenarios", limit=5, offset=5))["data"]["items"]
    assert [i["id"] for i in nxt] == [s.id for s in exam[5:10]]
    assert h.fake.count() == 0  # no Qloo call, no live budget


async def test_list_filters(harness, exam, tmp_path):
    h = await harness(config_dir=CFG, groundtruth_path=tmp_path / "none.sqlite")
    s = next(x for x in exam if "demo" in x.tags)
    d = (
        await h.json(
            "/api/scenarios", city=s.city_id, domain=s.domain_id, probe=s.probe_type, demo=True, limit=200
        )
    )["data"]
    want = [
        x for x in exam
        if (x.city_id, x.domain_id, x.probe_type) == (s.city_id, s.domain_id, s.probe_type) and "demo" in x.tags
    ]  # fmt: skip
    assert d["total"] == len(want) > 0 and {i["id"] for i in d["items"]} == {x.id for x in want}
    nodemo = (await h.json("/api/scenarios", demo="false", limit=200))["data"]
    assert nodemo["total"] == sum("demo" not in x.tags for x in exam)
    assert (await h.json("/api/scenarios", city="no-such-city"))["data"]["total"] == 0


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"demo": "maybe"}])
async def test_list_rejects_bad_input_as_json(harness, tmp_path, params):
    h = await harness(config_dir=CFG, groundtruth_path=tmp_path / "none.sqlite")
    r = await h.get("/api/scenarios", **params)
    assert r.status_code == 422 and r.json()["ok"] is False and "Traceback" not in r.text


async def test_show_one_and_unknown_id(harness, exam, tmp_path):
    h = await harness(config_dir=CFG, groundtruth_path=tmp_path / "none.sqlite")
    s = exam[0]
    body = await h.json(f"/api/scenarios/{s.id}")
    _envelope_ok(body)
    assert body["data"]["id"] == s.id and body["data"]["prompt_variants"]
    r = await h.get("/api/scenarios/sc_doesnotexist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    b = r.json()
    _envelope_ok(b)
    assert b["data"] is None and b["served_from"] == "degraded" and "Traceback" not in r.text


# --------------------------------------------------------------------------- /api/groundtruth
async def test_groundtruth_returns_key_with_truthful_provenance(harness, demo, built):
    h = await harness(config_dir=CFG, groundtruth_path=built)
    for s in demo:
        body = await h.json(f"/api/groundtruth/{s.id}")
        _envelope_ok(body)
        d = body["data"]
        assert body["served_from"] == d["served_from"] != "live"  # stored keys are never live
        assert body["served_from"] in {"cache", "snapshot"}
        assert d["baseline"] == ("city" if s.domain_id == "dining" else "global")
        assert len(d["local"]["items"]) == s.depth and len(d["global"]["items"]) == s.depth
        assert body["synthetic"] is False
    assert h.fake.count() == 0


async def test_groundtruth_synthetic_label(harness, demo, built):
    h = await harness(config_dir=CFG, groundtruth_path=built, groundtruth_synthetic=True)
    assert (await h.json(f"/api/groundtruth/{demo[0].id}"))["synthetic"] is True


async def test_groundtruth_unbuilt_or_unknown_is_404_json(harness, exam, demo, built):
    h = await harness(config_dir=CFG, groundtruth_path=built)
    unbuilt = next(s for s in exam if s.id not in {d.id for d in demo})
    for sid in (unbuilt.id, "sc_doesnotexist"):
        r = await h.get(f"/api/groundtruth/{sid}")
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
        b = r.json()
        _envelope_ok(b)
        assert b["data"] is None and b["served_from"] == "degraded" and "Traceback" not in r.text


async def test_groundtruth_without_a_store_is_404_and_creates_nothing(harness, demo, tmp_path):
    missing = tmp_path / "sub" / "absent.sqlite"
    h = await harness(config_dir=CFG, groundtruth_path=missing)
    r = await h.get(f"/api/groundtruth/{demo[0].id}")
    assert r.status_code == 404 and not missing.exists() and not missing.parent.exists()


async def test_groundtruth_names_are_escaped(harness, demo, built):
    store = Store(built)
    try:
        qid = store.query("SELECT qloo_id FROM entity_index LIMIT 1")[0]["qloo_id"]
        store.execute("UPDATE entity_index SET name = ? WHERE qloo_id = ?", ("<script>x</script> & co", qid))
    finally:
        store.close()
    h = await harness(config_dir=CFG, groundtruth_path=built)
    texts = []
    for s in demo:
        d = (await h.json(f"/api/groundtruth/{s.id}"))["data"]
        texts += [i["name"] for k in ("local", "global") for i in d[k]["items"]]
    assert any(t == html.escape("<script>x</script> & co") for t in texts)
    assert not any("<" in t for t in texts)


async def test_routes_obey_the_request_deadline(harness, demo, built):
    h = await harness(config_dir=CFG, groundtruth_path=built, request_deadline_s=-1)
    for path in ("/api/scenarios", f"/api/scenarios/{demo[0].id}", f"/api/groundtruth/{demo[0].id}"):
        r = await h.get(path)
        assert r.status_code == 503 and r.json()["served_from"] == "degraded", path
        assert r.json()["degraded_reason"] == "request deadline exceeded"


async def test_50_concurrent_requests(harness, demo, built):
    import asyncio

    h = await harness(config_dir=CFG, groundtruth_path=built)
    paths = ["/api/scenarios", f"/api/groundtruth/{demo[0].id}"] * 25
    rs = await asyncio.gather(*(h.get(p) for p in paths))
    assert [r.status_code for r in rs] == [200] * 50


def test_param_cli_group_dispatches(monkeypatch, capsys):
    import sys

    import blindspot.cli as cli

    assert cli.main(["param"]) == 2 and cli.main(["param", "nope"]) == 2
    seen = []
    monkeypatch.setitem(sys.modules, "promote_param", type(sys)("promote_param"))
    sys.modules["promote_param"].main = lambda a: seen.append(a) or 0
    assert cli.main(["param", "promote", "place", "x", "y", "--dry-run"]) == 0
    assert seen == [["place", "x", "y", "--dry-run"]]
