"""Scenario engine (#16): specs per query shape, determinism, balance, subsets, pairs, prompt lint
and the CLI. Offline: config/ and the committed id lock only. No city name is written here (rule 9)."""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from blindspot.catalog import AgeBand, Domain, Persona, ProbeType, QueryShape, Seed, load_catalog
from blindspot.idlock import IdLock, LockItem, load_lock, lock_key
from blindspot.qloo.params import check_ids, check_params
from blindspot.scenarios import cli
from blindspot.scenarios.generator import DEFAULT_SEED, generate
from blindspot.scenarios.prompts import lint_prompts, taste_phrase
from blindspot.scenarios.specs import EXCLUDE_PARAM, Unresolvable, build_specs
from tests.test_catalog import ROOT

CFG = ROOT / "config"


@pytest.fixture(scope="module")
def cat():
    return load_catalog(CFG)


@pytest.fixture(scope="module")
def lock():
    return load_lock(CFG)


@pytest.fixture(scope="module")
def ss(cat, lock):
    return generate(cat, lock)


def _by(ss, **kw):
    return [s for s in ss.scenarios if all(getattr(s, k) == v for k, v in kw.items())]


# --------------------------------------------------------------------------- specs per query shape
def test_dining_spec_is_location_filter_plus_audience(cat, ss):
    s = _by(ss, domain_id="dining", probe_type="core")[0]
    dining, city = cat.domain("dining"), cat.city(s.city_id)
    local, glob = s.local_spec.params, s.global_spec.params
    assert local["filter.type"] == "urn:entity:place"
    assert local["filter.location.query"] == city.location and "signal.location.query" not in local
    assert local["filter.tags"] == dining.params["filter.tags"]
    assert local["signal.demographics.audiences"] and all(
        a.startswith("urn:audience:") for a in local["signal.demographics.audiences"]
    )
    assert (
        local["take"] == dining.membership_pool
        and s.depth == dining.depth
        and s.pool == dining.membership_pool
    )
    assert "signal.demographics.age" not in local  # age never changes places (E5)
    assert not any("location" in k for k in glob)  # the global baseline has no location
    assert {k: v for k, v in local.items() if "location" not in k} == glob


@pytest.mark.parametrize("domain_id", ["music", "film_tv", "brands"])
def test_other_domains_use_the_location_signal(cat, ss, domain_id):
    s = _by(ss, domain_id=domain_id, probe_type="core")[0]
    assert s.local_spec.params["signal.location.query"] == cat.city(s.city_id).location
    assert s.signal == "audience"


def _synthetic(shape: QueryShape, age: AgeBand = "35_and_younger"):
    d = Domain(
        id="music", filter_type="urn:entity:artist", take=10, phrasing="artists", location_param="signal",
        query_shape=shape, probes=["core"],
    )  # fmt: skip
    p = Persona(
        id="p1", descriptor="Synthetic persona for spec tests", age_band=age, domains=["music"],
        seeds={"music": [Seed(name="Seed One", type="urn:entity:artist")]}, interest_tags={"music": ["jazz"]},
    )  # fmt: skip
    seed_id, tag_id = "00000000-0000-4000-8000-0000000000c1", "urn:tag:genre:music:jazz"
    lk = IdLock(
        items={
            lock_key("seed", "music", "Seed One"): LockItem(
                kind="seed", domain="music", query="Seed One", id=seed_id, status="resolved"
            ),
            lock_key("tag", "music", "jazz"): LockItem(
                kind="tag", domain="music", query="jazz", id=tag_id, status="resolved"
            ),
        }
    )
    probe = ProbeType(id="core", description="synthetic core probe", domains=["music"], max_scenarios=1)
    return d, p, lk, probe, seed_id, tag_id


def test_entity_tag_and_age_shapes(cat):
    city = cat.cities[0]
    d, p, lk, probe, seed_id, tag_id = _synthetic(QueryShape(signal="entity"))
    params = build_specs(p, d, city, probe, lk).local.params
    assert params["signal.interests.entities"] == [seed_id]
    d, p, lk, probe, seed_id, tag_id = _synthetic(QueryShape(signal="tag", with_age=True), "55_and_older")
    params = build_specs(p, d, city, probe, lk).local.params
    assert params["signal.interests.tags"] == [tag_id] and params["signal.demographics.age"] == "55_and_older"
    assert params[EXCLUDE_PARAM] == [seed_id]  # its own seed is never in its key


def test_unresolved_signal_raises(cat):
    d, p, lk, probe, *_ = _synthetic(QueryShape(signal="audience"))
    with pytest.raises(Unresolvable, match="no locked audience id"):
        build_specs(p, d, cat.cities[0], probe, lk)


def test_freshness_adds_trend_bias_to_dining_only(ss):
    fresh = _by(ss, probe_type="freshness")
    assert fresh and all(s.domain_id == "dining" for s in fresh)
    assert all(s.local_spec.params["bias.trends"] == "high" for s in fresh)
    assert all("bias.trends" not in s.local_spec.params for s in ss.scenarios if s.probe_type != "freshness")


def test_every_spec_passes_the_strict_guard(ss):
    for s in ss.scenarios:
        for spec in (s.local_spec, s.global_spec):
            assert check_params("/v2/insights", spec.params, strict=True) == []
            check_ids(spec.params)


def test_specs_are_shared_where_the_question_is_the_same(ss):
    # core and bridge for one persona, domain and city ask the same answer key: one fetch in #18
    by_key = Counter(s.local_spec.key for s in ss.scenarios)
    assert len(by_key) < len(ss.scenarios)
    glob = Counter(s.global_spec.key for s in ss.scenarios)
    assert len(glob) < len(by_key)  # the global baseline is shared across cities


# --------------------------------------------------------------------------- determinism and ids
def test_same_inputs_same_bytes(cat, lock, ss):
    again = generate(load_catalog(CFG), load_lock(CFG))
    assert again.to_json() == ss.to_json() and again.output_hash() == ss.output_hash()
    other = generate(cat, lock, seed=DEFAULT_SEED + 1)
    assert other.output_hash() != ss.output_hash()  # the seed changes the picks


def test_ids_are_content_hashes(ss):
    ids = [s.id for s in ss.scenarios]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"sc_[0-9a-f]{12}", i) for i in ids)
    assert all(s.with_id().id == s.id for s in ss.scenarios)
    tagged = ss.scenarios[0].model_copy(update={"tags": ["holdout"]})
    assert tagged.with_id().id == ss.scenarios[0].id  # tags don't change identity


def test_unresolved_persona_is_skipped_not_guessed(cat, lock):
    victim = cat.personas[0]
    gone = {k for k, v in lock.items.items() if v.kind == "audience"} & {
        lock_key("audience", "*", a if isinstance(a, str) else f"{a.type}:{a.name}") for a in victim.audiences
    }
    thin = IdLock(items={k: v for k, v in lock.items.items() if k not in gone})
    out = generate(cat, thin)
    assert victim.id not in {s.persona_id for s in out.scenarios}
    assert any(victim.id in x for x in out.skipped)


# --------------------------------------------------------------------------- distribution
def test_count_and_balance(cat, ss):
    sc = ss.scenarios
    assert 80 <= len(sc) <= 120
    probes = Counter(s.probe_type for s in sc)
    assert probes["swap"] + probes["core"] >= 0.5 * len(sc)  # weighted to swap and core
    cities = Counter(s.city_id for s in sc)
    assert set(cities) == {c.id for c in cat.cities} and max(cities.values()) - min(cities.values()) <= 3
    non_fresh = Counter(s.domain_id for s in sc if s.probe_type != "freshness")
    assert (
        set(non_fresh) == {d.id for d in cat.domains}
        and max(non_fresh.values()) - min(non_fresh.values()) <= 2
    )
    assert set(probes) == {p.id for p in cat.probe_types}
    assert all(probes[p.id] <= p.max_scenarios for p in cat.probe_types)


def test_demo_subset(cat, ss):
    demo = [s for s in ss.scenarios if "demo" in s.tags]
    assert 30 <= len(demo) <= 40
    assert {s.city_id for s in demo} >= {c.id for c in cat.cities if c.demo}
    assert {s.domain_id for s in demo} == {d.id for d in cat.domains}
    swap_pairs = Counter(s.pair_id for s in demo if s.probe_type == "swap")
    assert len(swap_pairs) >= 8 and set(swap_pairs.values()) == {2}  # whole pairs only
    assert {s.probe_type for s in demo} == {p.id for p in cat.probe_types}
    assert all(("demo" in s.tags) != ("extended" in s.tags) for s in ss.scenarios)


def test_holdout_is_balanced_and_keeps_pairs_whole(ss):
    hold = [s for s in ss.scenarios if "holdout" in s.tags]
    assert 0.15 <= len(hold) / len(ss.scenarios) <= 0.25
    assert {s.probe_type for s in hold} == {s.probe_type for s in ss.scenarios}
    assert len({s.city_id for s in hold}) >= 4 and len({s.domain_id for s in hold}) == 4
    pairs = Counter(s.pair_id for s in ss.scenarios if s.pair_id)
    held = Counter(s.pair_id for s in hold if s.pair_id)
    assert all(held[p] in (0, pairs[p]) for p in pairs)


def test_pairs(cat, ss):
    by_pair: dict[str, list] = {}
    for s in ss.scenarios:
        if s.pair_id:
            by_pair.setdefault(s.pair_id, []).append(s)
    assert by_pair and all(len(v) == 2 for v in by_pair.values())
    for a, b in by_pair.values():
        assert a.probe_type == b.probe_type and a.domain_id == b.domain_id
        if a.probe_type == "swap":
            assert a.persona_id == b.persona_id and a.city_id != b.city_id
            texts = [
                v.text.replace(cat.city(x.city_id).display_name, "{city}")
                for x in (a, b)
                for v in x.prompt_variants
            ]
            assert texts[:3] == texts[3:]  # only the city differs
        else:
            assert a.probe_type == "audience" and a.city_id == b.city_id and a.persona_id != b.persona_id
            cond = cat.probe("audience").condition.get(a.domain_id)
            assert a.conditions == ([cond] if cond else [])


def test_bridge_states_taste_in_another_domain(cat, ss):
    for s in _by(ss, probe_type="bridge"):
        assert s.stated_domain and s.stated_domain != s.domain_id
        assert s.stated_domain == cat.probe("bridge").pairs[s.domain_id]
        taste = taste_phrase(cat.persona(s.persona_id), s.stated_domain)
        assert all(taste in v.text and cat.domain(s.domain_id).phrasing in v.text for v in s.prompt_variants)


# --------------------------------------------------------------------------- prompt lint
def test_prompts_mention_only_own_taste_age_and_city(cat, ss):
    for s in ss.scenarios:
        p = cat.persona(s.persona_id)
        taste = taste_phrase(p, s.stated_domain or s.domain_id)
        for v in s.prompt_variants:
            assert taste in v.text and cat.city(s.city_id).display_name in v.text
            assert p.descriptor not in v.text and p.id not in v.text
    assert lint_prompts(v.text for s in ss.scenarios for v in s.prompt_variants) == []


def test_lint_catches_ids_and_answer_key_names():
    assert lint_prompts(["I love Example Band"], ["Example Band"])
    assert lint_prompts(["see urn:tag:genre:music:jazz"])
    assert lint_prompts(["id 00000000-0000-4000-8000-000000000001"])
    assert lint_prompts(["I love Example Bandwagon"], ["Example Band"]) == []  # whole words only


def test_named_seeds_are_excluded_from_the_answer_key(cat, lock, ss):
    # what a prompt names can't be in its key, so "no answer-key name in a prompt" holds by construction
    for s in ss.scenarios:
        if s.stated_domain:  # bridge names another domain's seeds, which can't be in this domain's key
            continue
        named = [
            x
            for x in (
                lock.id_for("seed", s.domain_id, sd.name)
                for sd in cat.persona(s.persona_id).seeds.get(s.domain_id, [])
            )
            if x
        ]
        assert set(named) <= set(s.local_spec.params.get(EXCLUDE_PARAM, []))


# --------------------------------------------------------------------------- CLI
def _run(capsys, *argv):
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_cli_build_twice_same_hash_and_docs(tmp_path, capsys, monkeypatch):
    doc = tmp_path / "scenarios.md"
    doc.write_text("# Scenarios\n", encoding="utf-8")
    monkeypatch.setattr(cli, "DOC", doc)
    out = tmp_path / "s.json"
    code, first = _run(capsys, "build", "--config", str(CFG), "--out", str(out), "--docs")
    code2, second = _run(capsys, "build", "--config", str(CFG), "--out", str(out), "--docs")
    h1, h2 = (re.search(r"sha256 ([0-9a-f]{64})", t)[1] for t in (first, second))
    assert code == code2 == 0 and h1 == h2
    assert "| **Total** |" in first and "demo |" in first
    text = doc.read_text(encoding="utf-8")
    assert text.count(cli.BEGIN) == 1 and text.count(cli.END) == 1  # rewritten, not appended twice
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["scenarios"]) >= 80 and data["seed"] == DEFAULT_SEED


def test_cli_list_and_show(tmp_path, capsys):
    out = tmp_path / "s.json"
    _run(capsys, "build", "--config", str(CFG), "--out", str(out))
    code, listed = _run(capsys, "list", "--file", str(out), "--probe", "swap", "--tag", "demo")
    rows = [line for line in listed.splitlines() if line.startswith("sc_")]
    assert code == 0 and rows and all(" swap " in r and "demo" in r for r in rows)
    sid = rows[0].split()[0]
    code, shown = _run(capsys, "show", sid, "--file", str(out))
    assert code == 0 and json.loads(shown)["id"] == sid
    assert _run(capsys, "show", "sc_000000000000", "--file", str(out))[0] == 1


def test_top_level_cli_dispatches(capsys):
    from blindspot.cli import main

    assert main(["nope"]) == 2
