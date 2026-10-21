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
from blindspot.scenarios.prompts import lint_prompts, named_seeds, taste_phrase
from blindspot.scenarios.specs import EXCLUDE_PARAM, Unresolvable, baseline_kind, build_specs
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
    # Dining's baseline is this city with no persona signal (D43): it differs from the key in that only
    assert dining.baseline == "city"
    assert glob["filter.location.query"] == city.location
    assert {k: v for k, v in local.items() if k != "signal.demographics.audiences"} == glob


def test_city_baseline_is_shared_by_personas_in_a_city_not_across_cities(cat, ss):
    dining = [s for s in ss.scenarios if s.domain_id == "dining" and s.probe_type == "core"]
    by_city: dict[str, set[str]] = {}
    for s in dining:
        by_city.setdefault(s.city_id, set()).add(s.global_spec.key)
    assert any(len({s.persona_id for s in dining if s.city_id == c}) > 1 for c in by_city)
    for city_id, keys in by_city.items():  # every persona asked about one city gets one baseline
        own = {
            s.persona_id for s in dining if s.city_id == city_id and s.local_spec.params.get(EXCLUDE_PARAM)
        }
        assert len(keys) == 1 + len(own), city_id  # only an own-seed exclusion makes a separate one
    assert len({k for keys in by_city.values() for k in keys}) >= len(by_city)


@pytest.mark.parametrize("domain_id", ["music", "film_tv", "brands"])
def test_global_baseline_is_the_same_taste_without_a_location(cat, ss, domain_id):
    assert cat.domain(domain_id).baseline == "global"
    for s in _by(ss, domain_id=domain_id):
        local, glob = s.local_spec.params, s.global_spec.params
        assert not any("location" in k for k in glob)
        assert {k: v for k, v in local.items() if "location" not in k} == glob
        assert baseline_kind(glob) == "global" and baseline_kind(local) == "city"


def test_baseline_must_be_global_or_city():
    with pytest.raises(ValueError, match="baseline"):
        Domain(
            id="x", filter_type="urn:entity:artist", take=10, phrasing="x", location_param="signal",
            baseline="nowhere",
        )  # fmt: skip


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


def test_bridge_states_taste_in_another_domain(cat, lock, ss):
    for s in _by(ss, probe_type="bridge"):
        assert s.stated_domain and s.stated_domain != s.domain_id
        assert s.stated_domain == cat.probe("bridge").pairs[s.domain_id]
        taste = taste_phrase(cat.persona(s.persona_id), s.stated_domain, lock)
        assert all(taste in v.text and cat.domain(s.domain_id).phrasing in v.text for v in s.prompt_variants)


# --------------------------------------------------------------------------- prompt lint
def test_prompts_mention_only_own_taste_age_and_city(cat, lock, ss):
    for s in ss.scenarios:
        p = cat.persona(s.persona_id)
        taste = taste_phrase(p, s.stated_domain or s.domain_id, lock)
        for v in s.prompt_variants:
            assert taste in v.text and cat.city(s.city_id).display_name in v.text
            assert p.descriptor not in v.text and p.id not in v.text
    assert lint_prompts(v.text for s in ss.scenarios for v in s.prompt_variants) == []


def test_lint_catches_ids_and_answer_key_names():
    assert lint_prompts(["I love Example Band"], ["Example Band"])
    assert lint_prompts(["see urn:tag:genre:music:jazz"])
    assert lint_prompts(["id 00000000-0000-4000-8000-000000000001"])
    assert lint_prompts(["I love Example Bandwagon"], ["Example Band"]) == []  # whole words only


def test_every_named_seed_is_locked_and_excluded_from_the_key(cat, lock, ss):
    # what a prompt names can't be in its key, so "no answer-key name in a prompt" holds by construction
    for s in ss.scenarios:
        p = cat.persona(s.persona_id)
        stated = s.stated_domain or s.domain_id
        texts = [v.text for v in s.prompt_variants]
        for seed in p.seeds.get(stated, []):
            seed_id = lock.id_for("seed", stated, seed.name)
            if any(seed.name in t for t in texts):
                assert seed_id is not None, f"{s.id} names unlocked seed {seed.name!r}"
                if not s.stated_domain:  # bridge names another domain's seeds: not in this domain's key
                    assert seed_id in s.local_spec.params.get(EXCLUDE_PARAM, [])
            else:
                assert seed_id is None  # a locked seed is always named


def test_unlocked_seeds_are_never_named_and_unnameable_cells_are_skipped(cat, lock, ss):
    unlocked = [
        (p, d, sd.name)
        for p in cat.personas
        for d, seeds in p.seeds.items()
        for sd in seeds
        if lock.id_for("seed", d, sd.name) is None
    ]
    assert unlocked, "the committed lock has an unresolved seed; this test relies on one"
    for p, d, name in unlocked:
        for s in ss.scenarios:
            if s.persona_id == p.id and (s.stated_domain or s.domain_id) == d:
                assert all(name not in v.text for v in s.prompt_variants)
        if not named_seeds(p, d, lock) and not p.interest_tags.get(d):
            assert not [
                s for s in ss.scenarios if s.persona_id == p.id and d in (s.domain_id, s.stated_domain)
            ]
            assert any(f"{p.id}/{d}" in x for x in ss.skipped)


def test_holdout_follows_the_question_not_the_probe(ss):
    by_question: dict[str, set[bool]] = {}
    for s in ss.scenarios:
        by_question.setdefault(s.question_key(), set()).add("holdout" in s.tags)
    repeated = [k for k, v in Counter(s.question_key() for s in ss.scenarios).items() if v > 1]
    assert repeated, "no question is asked under two probes; the test needs one"
    assert all(len(v) == 1 for v in by_question.values()), "a question is both held out and not"


def test_lint_reports_are_stable():
    prompts = ["I love Alpha Band and Beta Band"]
    assert lint_prompts(prompts, ["Beta Band", "Alpha Band"]) == lint_prompts(
        prompts, ["Alpha Band", "Beta Band"]
    )


# --------------------------------------------------------------------------- CLI
def _run(capsys, *argv):
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_cli_build_twice_same_hash_and_docs(tmp_path, capsys):
    doc = tmp_path / "scenarios.md"
    doc.write_text("# Scenarios\n", encoding="utf-8")
    out = tmp_path / "s.json"
    args = ("build", "--config", str(CFG), "--out", str(out), "--docs", "--doc", str(doc))
    code, first = _run(capsys, *args)
    code2, second = _run(capsys, *args)
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


@pytest.mark.parametrize(
    "body",
    [
        f"# S\n{cli.BEGIN}\nold\n",  # END deleted
        f"# S\n{cli.END}\n{cli.BEGIN}\nold\n",  # END before BEGIN
        f"# S\n{cli.BEGIN}\n{cli.END}\n{cli.BEGIN}\n{cli.END}\n",  # duplicated
    ],
)
def test_damaged_doc_markers_fail_before_anything_is_written(tmp_path, capsys, body):
    doc, out = tmp_path / "scenarios.md", tmp_path / "s.json"
    doc.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit, match="markers are damaged"):
        cli.main(["build", "--config", str(CFG), "--out", str(out), "--docs", "--doc", str(doc)])
    assert not out.exists() and doc.read_text(encoding="utf-8") == body


def test_paths_follow_config_not_the_working_directory(tmp_path, capsys, monkeypatch):
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(CFG, repo / "config")
    (repo / "docs").mkdir()
    (repo / "docs" / "scenarios.md").write_text("# Scenarios\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg = str(repo / "config")
    assert _run(capsys, "build", "--config", cfg, "--docs")[0] == 0
    assert (repo / "data" / "scenarios" / "scenarios.json").is_file()
    assert cli.BEGIN in (repo / "docs" / "scenarios.md").read_text(encoding="utf-8")
    assert not any(elsewhere.iterdir())  # nothing written where it was run from
    code, listed = _run(capsys, "list", "--config", cfg, "--tag", "holdout")
    assert code == 0 and "holdout" in listed


def test_holdout_rule_shares_the_decision_across_probes(ss):
    # Direct test of the rule, independent of which picks happen to collide: in probe "a" the 3rd
    # pick is held out; probe "b" asks the same question as its 1st (not held out by position) and
    # pairs it with another question. Both copies and the whole pair must be held out.
    from blindspot.scenarios.generator import Unit, _tag_holdout

    def copy(s, probe):
        return s.model_copy(update={"probe_type": probe, "tags": []}, deep=True)

    s1, s2, s3, s4 = ss.scenarios[:4]
    picks = {
        "a": [Unit("a", "d", ("p",), ("c",), scenarios=[copy(s, "a")]) for s in (s1, s2, s3)],
        "b": [Unit("b", "d", ("p",), ("c", "c2"), scenarios=[copy(s3, "b"), copy(s4, "b")])],
    }
    _tag_holdout(picks)
    held = {(u.probe, s.id) for us in picks.values() for u in us for s in u.scenarios if "holdout" in s.tags}
    assert held == {("a", s3.id), ("b", s3.id), ("b", s4.id)}
