"""Coverage probe and `GET /api/coverage` (#24): the real app and client against the fake Qloo server.
Offline and keyless; locations are invented (never a configured city, rule 9)."""

from __future__ import annotations

import json
import random
import time
import unicodedata

import pytest

from blindspot.catalog import load_domains
from blindspot.groundtruth.coverage import MAX_LOCATION_CHARS, LocationError, clean_location
from blindspot.qloo.snapshot import SYNTHETIC_SUFFIX, Snapshot, build_snapshot
from tests.test_catalog import ROOT

CFG = ROOT / "config"
DOMAINS = {d.id: d for d in load_domains(CFG)}
PLACE = "Nowhere Junction"
FILTER_DOMAINS = [d for d in DOMAINS.values() if d.location_param == "filter"]
SIGNAL_DOMAINS = [d for d in DOMAINS.values() if d.location_param == "signal"]
LOCATION_KEYS = {"filter.location.query", "signal.location.query"}


async def _coverage(h, location: str = PLACE, *, status: int = 200, **params) -> dict:
    r = await h.get("/api/coverage", location=location, **params)
    assert r.status_code == status, f"{r.status_code}: {r.text[:300]}"
    assert r.headers["content-type"].startswith("application/json") and "Traceback" not in r.text
    body = r.json()
    assert set(body) == {"data", "served_from", "synthetic", "degraded_reason", "run_id"}
    return body


def _by_domain(body: dict) -> dict[str, dict]:
    return {d["domain"]: d for d in body["data"]["domains"]}


def _sent(h, location: str) -> list[dict[str, str]]:
    return [s.params for s in h.fake.seen if location in {s.params.get(k) for k in LOCATION_KEYS}]


async def _used(h) -> int:
    return h.client.budget.total_used()


@pytest.fixture
async def cov(harness):
    async def make(**kw):
        return await harness(config_dir=CFG, **kw)

    return make


# --------------------------------------------------------------------------- verdicts
async def test_any_location_gets_a_verdict_per_domain_then_all_cache(cov):
    h = await cov()
    first = await _coverage(h)
    assert first["served_from"] == "live" and first["degraded_reason"] is None
    assert first["synthetic"] is False
    data = first["data"]
    assert data["location"] == PLACE and data["location_resolved"] is True
    doms = _by_domain(first)
    assert set(doms) == set(DOMAINS)
    for d in doms.values():
        assert d["verdict"] == "rich" and d["confidence"] == "high"
        assert d["count"] == d["depth"] == DOMAINS[d["domain"]].depth
    # one standard query per domain plus one no-location check per signal domain, nothing else
    assert h.fake.count() == len(DOMAINS) + len(SIGNAL_DOMAINS) == await _used(h)
    assert h.client.budget.by_purpose() == {"coverage": h.fake.count()}

    second = await _coverage(h)
    assert second["served_from"] == "cache"
    assert {c["served_from"] for d in second["data"]["domains"] for c in d["calls"]} == {"cache"}
    assert h.fake.count() == len(DOMAINS) + len(SIGNAL_DOMAINS)  # nothing new upstream


async def test_queries_use_each_domains_standard_shape(cov):
    h = await cov()
    await _coverage(h)
    sent = _sent(h, PLACE)
    assert len(sent) == len(DOMAINS)
    for d in DOMAINS.values():
        (p,) = [p for p in sent if p["filter.type"] == d.filter_type]
        want = {"filter.type": d.filter_type, **d.params, f"{d.location_param}.location.query": PLACE}
        assert p == {**want, "take": str(d.depth)}  # no persona signal, never a location signal on places
    no_loc = [s.params for s in h.fake.seen if not LOCATION_KEYS & set(s.params)]
    assert sorted(p["filter.type"] for p in no_loc) == sorted(d.filter_type for d in SIGNAL_DOMAINS)


async def test_no_location_check_is_shared_by_every_location(cov):
    h = await cov()
    await _coverage(h, "Place One")
    before = h.fake.count()
    await _coverage(h, "Place Two")
    assert h.fake.count() - before == len(DOMAINS)  # the no-location lists came from the cache


@pytest.mark.parametrize(("n", "verdict"), [(12, "thin"), (4, "sparse"), (0, "empty")])
async def test_short_lists_get_the_builder_verdicts_after_a_broader_retry(cov, n, verdict):
    h = await cov()
    h.fake.insights_size = lambda p: n if "filter.tags" in p else int(p["take"])
    body = await _coverage(h)
    for d in FILTER_DOMAINS:
        got = _by_domain(body)[d.id]
        assert got["verdict"] == verdict and got["count"] == n
        assert [c["query"] for c in got["calls"]] == ["standard", "broader"]
        assert got["confidence"] == "medium"
        assert f"{n} of {d.depth}" in got["note"] and f"{d.depth} without" in got["note"]
    broader = [p for p in _sent(h, PLACE) if p["filter.type"] in {d.filter_type for d in FILTER_DOMAINS}]
    assert any("filter.tags" not in p for p in broader)  # the retry dropped only the fixed params
    assert all(p["filter.location.query"] == PLACE for p in broader)


async def test_signal_domain_below_depth_says_there_is_no_broader_query(cov):
    h = await cov()
    signal_types = {d.filter_type for d in SIGNAL_DOMAINS}
    h.fake.insights_size = lambda p: 15 if p["filter.type"] in signal_types else int(p["take"])
    for d in SIGNAL_DOMAINS:
        got = _by_domain(await _coverage(h))[d.id]
        assert got["verdict"] == "thin" and "no broader query" in got["note"]


async def test_unresolvable_location_is_empty_or_unknown_never_rich(cov):
    h = await cov()
    h.fake.unresolvable.add(PLACE)
    body = await _coverage(h)
    assert body["data"]["location_resolved"] is False
    doms = _by_domain(body)
    for d in FILTER_DOMAINS:
        assert doms[d.id]["verdict"] == "empty" and doms[d.id]["confidence"] == "high"
        assert "could not resolve" in doms[d.id]["note"]
    for d in SIGNAL_DOMAINS:  # the location was ignored: these are global results, not local data
        assert doms[d.id]["verdict"] == "unknown" and "location likely ignored" in doms[d.id]["note"]
    assert "location likely ignored" in body["degraded_reason"]


async def test_location_rejected_by_every_domain_is_an_answer_not_a_503(cov):
    # If a location signal also gets the 400 (UNVERIFIED), every domain has an answer: empty
    h = await cov()
    h.fake.unresolvable.add(PLACE)
    h.fake.unresolvable_signal_400 = True
    body = await _coverage(h)
    assert body["data"]["location_resolved"] is False
    assert {d["verdict"] for d in body["data"]["domains"]} == {"empty"}
    assert body["served_from"] == "degraded" and "could not resolve" in body["degraded_reason"]
    assert h.fake.count() == len(DOMAINS)  # no broader or no-location calls after a 400


# --------------------------------------------------------------------------- failures and budgets
async def test_failures_are_unknown_and_explained(cov):
    h = await cov(qloo_max_attempts=1)
    h.fake.behaviour = "server_503"
    body = await _coverage(h, status=503)
    assert body["served_from"] == "degraded"
    assert {d["verdict"] for d in body["data"]["domains"]} == {"unknown"}  # never "empty" (D35)
    for d in DOMAINS:
        assert f"{d}: " in body["degraded_reason"]


async def test_budget_is_per_client_and_degraded_says_why(cov):
    per_probe = len(DOMAINS) + len(SIGNAL_DOMAINS)
    h = await cov(qloo_client_hourly_cap=per_probe, trusted_proxy_hops=1)
    a = await _coverage(h, "Place One", ip="203.0.113.10")
    assert a["served_from"] == "live"
    spent = await _coverage(h, "Place Two", ip="203.0.113.10", status=503)
    assert spent["served_from"] == "degraded" and "hourly" in spent["degraded_reason"]
    assert {d["verdict"] for d in spent["data"]["domains"]} == {"unknown"}
    other = await _coverage(h, "Place Two", ip="203.0.113.20")  # another client is unaffected
    assert other["served_from"] == "live"
    assert h.fake.count() == per_probe + len(DOMAINS) == await _used(h)  # no-location lists were cached


async def test_kill_switch_makes_no_calls(cov):
    h = await cov()
    body = await _coverage(h, qloo="false")
    assert body["served_from"] == "disabled" and "off for this request" in body["degraded_reason"]
    assert {d["verdict"] for d in body["data"]["domains"]} == {"unknown"}
    assert h.fake.count() == 0


async def test_slow_qloo_is_bounded_by_the_request_deadline(cov):
    h = await cov(real_sleep=True, request_deadline_s=0.5, qloo_timeout_s=5)
    h.fake.behaviour, h.fake.slow_s = "slow", 3.0
    t0 = time.monotonic()
    body = await _coverage(h, status=503)
    assert time.monotonic() - t0 < 2.5
    assert {d["verdict"] for d in body["data"]["domains"]} == {"unknown"}
    assert all(f"{d}: " in body["degraded_reason"] for d in DOMAINS)  # a timeout is unknown, not empty


async def test_synthetic_snapshot_is_labelled(cov, tmp_path):
    h = await cov()
    await _coverage(h)
    path = tmp_path / "synthetic.json"
    snap = build_snapshot(h.client.cache.entries(), "test" + SYNTHETIC_SUFFIX)
    path.write_text(json.dumps(snap), encoding="utf-8")
    loaded = Snapshot.load(path)
    assert loaded.synthetic is True

    h2 = await cov(qloo_cache_path=tmp_path / "fresh.sqlite", snapshot=loaded)
    body = await _coverage(h2)
    assert body["served_from"] == "snapshot" and body["synthetic"] is True
    assert h2.fake.count() == 0


# --------------------------------------------------------------------------- untrusted input
@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("  Nowhere   Junction ", "Nowhere Junction"),
        ("Nowhere\x00Junction", "NowhereJunction"),
        ("Nowhere\r\nJunction\t", "Nowhere Junction"),
        ("No​where‮", "Nowhere"),  # zero-width and bidi override are format characters
        ("Café Town", "Café Town"),  # NFC
    ],
)
def test_clean_location(raw, clean):
    assert clean_location(raw) == clean


@pytest.mark.parametrize("raw", ["", "   ", "\x00\x01\x7f", "x" * (MAX_LOCATION_CHARS + 1)])
def test_clean_location_rejects(raw):
    with pytest.raises(LocationError):
        clean_location(raw)


@pytest.mark.parametrize("location", [" ", "\x00", "​​", "y" * (MAX_LOCATION_CHARS + 5)])
async def test_route_rejects_empty_or_long_locations_as_envelope(cov, location):
    h = await cov()
    body = await _coverage(h, location, status=400)
    assert body["data"] is None and body["served_from"] == "degraded" and body["degraded_reason"]
    assert h.fake.count() == 0


@pytest.mark.parametrize("params", [{}, {"location": ""}, {"location": "z" * 201}, {"qloo": "maybe"}])
async def test_route_rejects_bad_params_as_json(cov, params):
    h = await cov()
    r = await h.get("/api/coverage", **params)
    assert r.status_code == 422 and r.json()["ok"] is False and "Traceback" not in r.text
    assert h.fake.count() == 0


NASTY = [
    "'; DROP TABLE cache; --",
    '" OR 1=1 --',
    "$(rm -rf /)",
    "`id`",
    "a && b | c ; d",
    "<script>alert(1)</script>",
    "%00%0d%0aSet-Cookie: x=1",
    "../../etc/passwd",
    "filter.location.query=elsewhere&take=50",
    "{{7*7}}",
    "東京都 渋谷区",
    "Vila Ação-ish",
    "\U0001f30d\U0001f5fa",
    "\x1b[31mred",
]


def _fuzz_strings(n: int = 40) -> list[str]:
    rng = random.Random(24)  # seeded: the same strings on every run and platform
    pool = [chr(c) for c in (*range(0, 0x80), *range(0x80, 0x250), 0x200B, 0x202E, 0xFEFF, 0x3042, 0x1F600)]
    return ["".join(rng.choice(pool) for _ in range(rng.randint(1, 150))) for _ in range(n)]


@pytest.mark.parametrize("location", NASTY + _fuzz_strings())
async def test_fuzzed_locations_never_crash_and_reach_qloo_only_as_a_clean_value(cov, location):
    h = await cov()
    r = await h.get("/api/coverage", location=location)
    assert r.status_code in (200, 400, 422), r.text[:300]
    assert r.headers["content-type"].startswith("application/json") and "Traceback" not in r.text
    for s in h.fake.seen:
        assert s.path == "/v2/insights"
        sent = [s.params[k] for k in LOCATION_KEYS if k in s.params]
        assert set(s.params) - LOCATION_KEYS <= {"filter.type", "filter.tags", "take"}
        for value in sent:  # exactly what clean_location made of the input, nothing else
            assert value == clean_location(location)
            assert len(value) <= MAX_LOCATION_CHARS
            assert not any(unicodedata.category(ch).startswith("C") for ch in value)
    if r.status_code == 200:
        assert r.json()["data"]["location"] == clean_location(location)


async def test_concurrent_probes_share_calls_and_respect_caps(cov):
    import asyncio

    per_probe = len(DOMAINS) + len(SIGNAL_DOMAINS)
    h = await cov()
    rs = await asyncio.gather(*(h.get("/api/coverage", location=PLACE) for _ in range(50)))
    assert {r.status_code for r in rs} == {200}
    assert h.fake.count() == per_probe == await _used(h)  # identical in-flight queries coalesce
