"""Calibration targets (#30): qloo-copy, global-top, random-real and invented, against a synthetic
answer-key store built with the fake Qloo server. Offline and keyless; synthetic ids and names only;
no city name is written here (rule 9)."""

from __future__ import annotations

import json
import shutil
import sys

import httpx
import pytest

from blindspot.catalog import load_catalog
from blindspot.groundtruth import Builder, add_search_hits, find_entities, get_answer_key, list_entities
from blindspot.groundtruth.normalize import aliases
from blindspot.groundtruth.store import Store
from blindspot.idlock import load_lock
from blindspot.qloo.client import QlooClient
from blindspot.qloo.models import Entity
from blindspot.qloo.snapshot import Snapshot
from blindspot.scenarios.generator import generate
from blindspot.targets import CALIBRATION_IDS, CalibrationTarget, Target, open_target, target_ids
from blindspot.targets.calibration import ADJECTIVES, DOMAIN_WORDS, GENERIC_WORDS, NOUNS
from blindspot.targets.config import load_targets
from tests.fakeqloo import FakeQloo, FakeTransport
from tests.test_catalog import ROOT

CFG = ROOT / "config"
DOMAINS = ("music", "dining")  # a signal-localized domain and a filter-localized one (city baseline)
HUB_ID = "00000000-0000-4000-8000-00000000hub1".upper()
HUB_NAME = "Synthetic Hub Entity"
K = 10


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CFG)


@pytest.fixture(scope="module")
def exam(catalog):
    return generate(catalog, load_lock(CFG)).scenarios


@pytest.fixture(scope="module")
def picked(exam):
    """The demo scenarios of two domains: several cities each, so several baseline lists."""
    return [s for s in exam if "demo" in s.tags and s.domain_id in DOMAINS]


def _is_baseline(params: dict[str, str]) -> bool:
    located = "filter.location.query" in params or "signal.location.query" in params
    return "signal.demographics.audiences" not in params or not located


@pytest.fixture
async def store(make_settings, tmp_path, picked, catalog):
    """A built store whose baselines all share one hub entity at rank 1, so global-top's count is
    tested, not just its tie-break."""
    fake = FakeQloo()
    plain = fake.insights_body

    def body(params):
        out = plain(params)
        if _is_baseline(params) and params.get("filter.type") == "urn:entity:artist":
            hub = {"entity_id": HUB_ID, "name": HUB_NAME, "type": "urn:entity:artist", "query": {}}
            out["results"]["entities"] = [hub, *out["results"]["entities"][:-1]]
        return out

    fake.insights_body = body

    async def no_sleep(_s):
        return None

    s = make_settings(
        qloo_cache_path=tmp_path / "build.cache", qloo_max_live_calls=100_000,
        qloo_daily_live_cap=100_000, qloo_demo_reserve=0,
    )  # fmt: skip
    client = QlooClient(s, snapshot=Snapshot(), transport=FakeTransport(fake), sleep=no_sleep)
    st = Store(tmp_path / "gt.sqlite")
    words = [x for c in catalog.cities for x in (c.display_name, c.location)]
    report = await Builder(client, st, location_words=words).build(picked)
    assert {"done"} >= set(report.statuses), report.statuses
    yield st
    client.db.close()
    st.close()


class Guarded:
    """A store that only the groundtruth package may touch: any other caller fails the test."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        assert caller.startswith("blindspot.groundtruth"), f"{caller} touched the store ({name})"
        return getattr(self._inner, name)


def target(tid, catalog, exam, store, **kw) -> CalibrationTarget:
    return CalibrationTarget(tid, catalog=catalog, scenarios=exam, store=Guarded(store), **kw)


async def ask(t, sc, k=K):
    return await t.recommend(sc.prompt_variants[0].text, k, scenario_id=sc.id)


def names(r) -> list[str]:
    return [it.name for it in r.items]


def _check_envelope(r, tid):
    assert r.outcome == "ok" and r.served_from == "cache" and r.model == tid == r.target_id
    assert r.usage.total_tokens == 0 and r.usage.calls == 0 and r.detail == ""


# --------------------------------------------------------------------------- lookup
def test_every_calibration_target_is_selectable_by_id(catalog, exam):
    configs = load_targets(CFG)
    assert set(CALIBRATION_IDS) <= set(target_ids(configs))
    for tid in CALIBRATION_IDS:
        t = open_target(tid, configs, catalog=catalog, scenarios=exam)
        assert isinstance(t, Target) and isinstance(t, CalibrationTarget) and t.id == tid
    with pytest.raises(KeyError, match="calib:qloo-copy"):
        open_target("calib:nope", configs, catalog=catalog, scenarios=exam)


# --------------------------------------------------------------------------- the four targets
async def test_qloo_copy_returns_the_key_top_k_in_order(catalog, exam, store, picked):
    t = target("calib:qloo-copy", catalog, exam, store)
    for sc in picked:
        r = await ask(t, sc)
        _check_envelope(r, "calib:qloo-copy")
        assert names(r) == [it.name for it in get_answer_key(sc.id, store).local.items[:K]]
        assert len(r.items) == K


async def test_global_top_is_one_list_per_domain_from_the_baselines(catalog, exam, store, picked):
    t = target("calib:global-top", catalog, exam, store)
    per_domain: dict[str, list[list[str]]] = {}
    for sc in picked:
        r = await ask(t, sc)
        _check_envelope(r, "calib:global-top")
        per_domain.setdefault(sc.domain_id, []).append(names(r))
    for d, lists in per_domain.items():
        assert len(lists) > 1 and all(x == lists[0] for x in lists), d  # identical across scenarios
        assert len({sc.city_id for sc in picked if sc.domain_id == d}) > 1  # whatever the city
    assert per_domain["music"][0][0] == HUB_NAME  # the entity in every baseline comes first
    assert per_domain["music"] != per_domain["dining"]
    # Every name comes from a baseline list, never a local one or a hand-written list.
    baseline = {it.name for sc in picked for it in get_answer_key(sc.id, store).global_.items}
    assert {n for lists in per_domain.values() for n in lists[0]} <= baseline
    dining = [sc for sc in picked if sc.domain_id == "dining"]
    assert {get_answer_key(sc.id, store).baseline for sc in dining} == {"city"}  # D43


async def test_global_top_breaks_ties_by_id(catalog, exam, store, picked):
    sc = next(s for s in picked if s.domain_id == "dining")
    r = await ask(target("calib:global-top", catalog, exam, store), sc, k=50)
    counts: dict[str, int] = {}
    seen, by_name = set(), {}
    for s in sorted(picked, key=lambda s: s.id):
        if s.domain_id != "dining" or s.global_spec.key in seen:
            continue
        seen.add(s.global_spec.key)
        for it in get_answer_key(s.id, store).global_.items:
            counts[it.qloo_id] = counts.get(it.qloo_id, 0) + 1
            by_name[it.qloo_id] = it.name
    expected = [by_name[q] for q in sorted(counts, key=lambda q: (-counts[q], q))][:50]
    assert names(r) == expected


async def test_random_real_is_real_outside_the_pool_and_seeded(catalog, exam, store, picked):
    for sc in picked:
        domain = catalog.domain(sc.domain_id)
        index = {e.name for e in list_entities(types=domain.search_types, store=store)}
        pool = set(get_answer_key(sc.id, store).local.pool)
        pool_names = {e.name for e in list_entities(store=store) if e.qloo_id in pool}
        r = await ask(target("calib:random-real", catalog, exam, store, seed=1), sc)
        _check_envelope(r, "calib:random-real")
        assert len(r.items) == K and len(set(names(r))) == K
        assert set(names(r)) <= index and not set(names(r)) & pool_names
        again = await ask(target("calib:random-real", catalog, exam, store, seed=1), sc)
        other = await ask(target("calib:random-real", catalog, exam, store, seed=2), sc)
        assert names(again) == names(r) and names(other) != names(r)


async def test_invented_names_are_never_in_the_index(catalog, exam, store, picked):
    for sc in picked:
        r = await ask(target("calib:invented", catalog, exam, store, seed=7), sc)
        _check_envelope(r, "calib:invented")
        assert len(r.items) == K and len(set(names(r))) == K
        for n in names(r):
            assert not find_entities(aliases(n), store=store), n
        again = await ask(target("calib:invented", catalog, exam, store, seed=7), sc)
        other = await ask(target("calib:invented", catalog, exam, store, seed=8), sc)
        assert names(again) == names(r) and names(other) != names(r)


async def test_invented_skips_a_candidate_the_index_happens_to_hold(catalog, exam, store, picked):
    sc = picked[0]
    first = names(await ask(target("calib:invented", catalog, exam, store, seed=7), sc))
    clash = first[0]
    hit = Entity(entity_id="00000000-0000-4000-8000-0000000c1a54", name=clash, types=["urn:entity:brand"])
    assert add_search_hits([hit], store=store) == 1  # a real entity of another type, same name
    r = await ask(target("calib:invented", catalog, exam, store, seed=7), sc)
    assert clash not in names(r) and len(r.items) == K
    assert names(r)[: K - 1] == first[1:]  # the rest of the draw is unchanged
    assert all(not find_entities(aliases(n), store=store) for n in names(r))


def test_word_lists_name_no_configured_city(catalog):
    words = {w.casefold() for w in (*ADJECTIVES, *NOUNS, *GENERIC_WORDS)}
    words |= {w.casefold() for ws in DOMAIN_WORDS.values() for w in ws}
    for c in catalog.cities:
        for place in (c.display_name, c.location):
            assert not {p.casefold() for p in place.replace(",", " ").split()} & words, c.id


# --------------------------------------------------------------------------- failure and network
@pytest.mark.parametrize("tid", CALIBRATION_IDS)
async def test_no_built_key_is_an_error_not_a_raise(tid, catalog, exam, store, picked):
    unbuilt = next(s for s in exam if s.domain_id not in DOMAINS)  # in the exam, never built
    t = target(tid, catalog, exam, store)
    for sid in (unbuilt.id, "sc_000000000000", None):
        r = await t.recommend("Recommend some things.", K, scenario_id=sid)
        assert r.outcome == "error" and r.detail and not r.items and r.served_from == "cache", (tid, sid)


async def test_every_target_makes_zero_network_calls(catalog, exam, store, picked, monkeypatch):
    calls = []

    async def no_network(self, request, *a, **kw):
        calls.append(str(request.url))
        raise AssertionError("calibration targets must not reach the network")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    for tid in CALIBRATION_IDS:
        t = target(tid, catalog, exam, store)
        for sc in picked:
            r = await ask(t, sc)
            assert r.outcome == "ok", (tid, r.detail)
    assert calls == []


# --------------------------------------------------------------------------- CLI
def test_cli_ask_runs_a_calibration_target_offline(store, picked, tmp_path, monkeypatch, capsys):
    from blindspot.config import get_settings
    from blindspot.targets.cli import main

    cfgdir = tmp_path / "repo" / "config"  # a repo root with no .env
    shutil.copytree(CFG, cfgdir)
    sc = next(s for s in picked if s.domain_id == "music")
    f = tmp_path / "scenarios.json"
    f.write_text(json.dumps({"scenarios": [s.model_dump(mode="json") for s in picked]}), encoding="utf-8")
    monkeypatch.setenv("GROUNDTRUTH_PATH", str(tmp_path / "gt.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_PATH", str(tmp_path / "targets.sqlite"))

    async def no_network(self, request, *a, **kw):
        raise AssertionError("no network")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    argv = ["--config", str(cfgdir), "ask", "--target", "calib:qloo-copy", "--scenario", sc.id]
    get_settings.cache_clear()
    try:
        code = main([*argv, "--file", str(f)])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    first = get_answer_key(sc.id, store).local.items[0].name
    assert code == 0 and "calib:qloo-copy (calib:qloo-copy): ok" in out and "served_from cache" in out
    assert f" 1. {first}" in out and "total 0" in out
    assert not (tmp_path / "targets.sqlite").exists()  # a calibration answer is never cached

    assert main(["--config", str(cfgdir), "list"]) == 0
    listed = capsys.readouterr().out
    assert all(tid in listed for tid in CALIBRATION_IDS)
