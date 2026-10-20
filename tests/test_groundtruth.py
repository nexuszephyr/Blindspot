"""Answer-key build (#18) against the fake Qloo server: depth, sharing, resume after an interruption,
thin handling, conditions, prompt lint, the single read path and its provenance, and normalization.
Offline and keyless; synthetic ids and names only; no city name is written here (rule 9)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from blindspot.catalog import load_catalog
from blindspot.groundtruth import Builder, NotBuilt, get_answer_key
from blindspot.groundtruth.builder import BROADEN_DROP
from blindspot.groundtruth.normalize import aliases, normalize_name
from blindspot.groundtruth.store import Store
from blindspot.idlock import load_lock
from blindspot.qloo.client import QlooClient
from blindspot.qloo.errors import QlooParamError
from blindspot.qloo.snapshot import Snapshot
from blindspot.scenarios.generator import generate
from blindspot.scenarios.models import QuerySpec
from tests.fakeqloo import FakeQloo, FakeTransport
from tests.test_catalog import ROOT

CFG = ROOT / "config"


@pytest.fixture(scope="module")
def exam():
    return generate(load_catalog(CFG), load_lock(CFG)).scenarios


@pytest.fixture
def demo(exam):
    return [s for s in exam if "demo" in s.tags]


@pytest.fixture
def make(make_settings, tmp_path):
    """make(fake=None, db="gt.sqlite", **settings) -> (builder kwargs factory) client, store, fake."""
    opened = []

    def _make(fake: FakeQloo | None = None, db: str = "gt.sqlite", **kw):
        fake = fake or FakeQloo()

        async def no_sleep(_s):
            return None

        s = make_settings(  # each store gets its own Qloo cache, so builds don't share answers
            qloo_cache_path=tmp_path / f"{db}.cache",
            qloo_max_live_calls=100_000,
            qloo_daily_live_cap=100_000,
            qloo_demo_reserve=0,
            **kw,
        )
        client = QlooClient(s, snapshot=Snapshot(), transport=FakeTransport(fake), sleep=no_sleep)
        store = Store(tmp_path / db)
        opened.append((client, store))
        return client, store, fake

    yield _make
    for client, store in opened:
        client.db.close()
        store.close()


def _specs(scenarios) -> set[str]:
    return {s.local_spec.key for s in scenarios} | {s.global_spec.key for s in scenarios}


def _same_tables(a: Store, b: Store) -> None:
    """Identical answer keys. served_from is left out: a spec fetched after an interruption may come
    from the Qloo cache instead of live, and the store says so truthfully."""
    da, db = a.dump(), b.dump()
    for t in da:
        strip = [{k: v for k, v in r.items() if k != "served_from"} for r in da[t]]
        assert strip == [{k: v for k, v in r.items() if k != "served_from"} for r in db[t]], t


# --------------------------------------------------------------------------- build
async def test_build_stores_every_key_at_depth_and_fetches_shared_specs_once(make, demo):
    client, store, fake = make()
    report = await Builder(client, store).build(demo)
    assert report.statuses == {"done": len(demo)} and not report.stopped and report.lint_problems == 0
    assert fake.count() == len(_specs(demo)) == report.fetched == report.live_calls  # once each
    for s in demo:
        key = get_answer_key(s.id, store)
        assert len(key.local.items) == s.depth and len(key.local.pool) == s.pool
        assert len(key.global_.items) == s.depth
        assert key.local.coverage == key.coverage == "rich"
        assert not any("location" in k for k in key.global_.params)
        excluded = set(s.local_spec.params.get("filter.exclude.entities", []))
        assert excluded.isdisjoint(key.local.pool)  # own seeds never in the key (D40)
    counts = store.table_counts()
    assert counts["entity_index"] > 0 and counts["ground_truth_runs"] == len(_specs(demo))


async def test_second_build_reuses_everything(make, demo):
    client, store, fake = make()
    await Builder(client, store).build(demo)
    before = fake.count()
    report = await Builder(client, store).build(demo)
    assert fake.count() == before and report.fetched == 0 and report.live_calls == 0


# --------------------------------------------------------------------------- interrupt and resume
async def test_stop_mid_build_then_resume_no_repeats_identical_tables(make, demo):
    ref_client, ref_store, _ = make(db="ref.sqlite")
    await Builder(ref_client, ref_store).build(demo)

    client, store, fake = make()
    first = await Builder(client, store, max_calls=17).build(demo)
    assert first.stopped and 0 < first.statuses.get("done", 0) < len(demo)
    assert first.statuses.get("pending", 0) > 0
    second = await Builder(client, store).build(demo)  # --resume is the default
    assert not second.stopped and second.statuses == {"done": len(demo)}
    assert fake.count() == len(_specs(demo))  # no spec was requested twice
    assert client.budget.total_used() == len(_specs(demo))
    _same_tables(store, ref_store)


async def test_killed_mid_build_then_resume_identical_tables(make, demo):
    ref_client, ref_store, _ = make(db="ref.sqlite")
    await Builder(ref_client, ref_store).build(demo)

    client, store, fake = make()
    fake.behaviour, fake.slow_s = "slow", 0.01
    task = asyncio.ensure_future(Builder(client, store, concurrency=2).build(demo))
    for _ in range(2000):  # bounded wait for the build to get going
        if fake.count() >= 12:
            break
        await asyncio.sleep(0.005)
    assert fake.count() >= 12
    task.cancel()  # like killing the process mid-request
    with pytest.raises(asyncio.CancelledError):
        await task
    assert 0 < store.status_counts().get("done", 0) < len(demo)
    fake.behaviour = "ok"
    report = await Builder(client, store).build(demo)
    assert report.statuses == {"done": len(demo)}
    # at most the requests in flight at the kill are repeated (their answers never landed)
    assert fake.count() - len(_specs(demo)) <= 2
    _same_tables(store, ref_store)


# --------------------------------------------------------------------------- thin, failed
async def test_below_threshold_gets_a_broader_retry_that_keeps_own_seeds_out(make, exam):
    with_seeds = [s for s in exam if s.local_spec.params.get("filter.exclude.entities")][:3]
    client, store, fake = make()
    fake.insights_size = lambda p: 5 if "filter.exclude.entities" in p else int(p["take"])
    seeds = {i for s in with_seeds for i in s.local_spec.params["filter.exclude.entities"]}
    plain = fake.insights_body

    def body(params):  # like Qloo: without the exclusion, the persona's own seeds rank first
        out = plain(params)
        if "filter.exclude.entities" not in params:
            top = [{"entity_id": i, "name": "Own Seed", "query": {"affinity": 1.0}} for i in sorted(seeds)]
            out["results"]["entities"] = top + out["results"]["entities"]
        return out

    fake.insights_body = body
    report = await Builder(client, store).build(with_seeds)
    assert report.broadened >= 1 and report.statuses == {"done": len(with_seeds)}
    for s in with_seeds:
        key = get_answer_key(s.id, store)
        assert key.local.broadened and "filter.exclude.entities" not in key.local.params
        assert set(s.local_spec.params["filter.exclude.entities"]).isdisjoint(key.local.pool)


async def test_thin_after_the_retry_is_recorded_not_hidden(make, demo):
    client, store, fake = make()
    fake.insights_size = lambda p: 12  # fewer than the depth (20), whatever is dropped
    report = await Builder(client, store).build(demo[:4])
    assert report.statuses == {"thin": 4}
    key = get_answer_key(demo[0].id, store)
    assert key.coverage == "thin" and len(key.local.items) == 12 and key.status == "thin"
    run = store.run(demo[0].local_spec.key)
    assert "12 of 20" in run["reason"]
    if any(k in demo[0].local_spec.params for k in BROADEN_DROP):
        assert "broader retry didn't add results" in run["reason"]


async def test_failures_are_not_stored_and_resume_retries_them(make, demo):
    client, store, fake = make(qloo_max_attempts=1)
    fake.behaviour = "server_503"
    report = await Builder(client, store).build(demo[:3])
    assert report.statuses == {"failed": 3}
    assert store.table_counts()["ground_truth_runs"] == 0
    with pytest.raises(NotBuilt, match="failed"):
        get_answer_key(demo[0].id, store)
    fake.behaviour = "ok"
    assert (await Builder(client, store).build(demo[:3])).statuses == {"done": 3}


async def test_param_audit_refuses_an_unverified_param(make, demo):
    client, store, fake = make()
    s = demo[0]
    bad = s.model_copy(update={"local_spec": QuerySpec.of({**s.local_spec.params, "filter.bogus": "1"})})
    with pytest.raises(QlooParamError):
        await Builder(client, store).build([bad])
    assert fake.count() <= 1  # the global spec may have gone; the bad one never did


# --------------------------------------------------------------------------- conditions and lint
async def test_personas_distinct_in_city_excludes_an_identical_dining_pair(make, exam):
    pair = [s for s in exam if s.conditions][:2]
    assert len(pair) == 2 and pair[0].pair_id == pair[1].pair_id
    client, store, _ = make()
    builder = Builder(client, store)
    await builder.build(pair)
    assert store.status_counts() == {"done": 2}  # distinct synthetic lists pass
    a, b = (s.local_spec.key for s in pair)
    store.execute("DELETE FROM ground_truth_items WHERE spec_key = ?", (b,))
    store.execute(
        "INSERT INTO ground_truth_items SELECT ?, rank, qloo_id, affinity, score_present "
        "FROM ground_truth_items WHERE spec_key = ?",
        (b, a),
    )
    builder.apply_conditions(pair)
    assert store.status_counts() == {"excluded": 2}
    assert "personas_distinct_in_city failed" in get_answer_key(pair[0].id, store).reason


async def test_prompt_naming_an_answer_key_entity_is_excluded(make, demo):
    s = demo[0]
    client, store, _ = make()
    builder = Builder(client, store)
    await builder.build([s])
    taste_word = re.search(r"really into (.+?)\. ", s.prompt_variants[0].text)[1].split(" and ")[0]
    top = store.items(s.local_spec.key)[0]["qloo_id"]
    store.execute("UPDATE entity_index SET name = ? WHERE qloo_id = ?", (taste_word, top))
    builder.lint([s])
    assert builder.report.lint_problems >= 1
    assert get_answer_key(s.id, store).status == "excluded"


# --------------------------------------------------------------------------- read path
async def test_provenance_is_truthful(make, demo):
    client, store, _ = make()
    await Builder(client, store).build(demo[:1])
    key = get_answer_key(demo[0].id, store)
    assert key.local.fetched_from == "live" and key.served_from == "cache"  # stored data is never "live"
    store.execute(
        "UPDATE ground_truth_runs SET served_from = 'snapshot' WHERE spec_key = ?", (demo[0].local_spec.key,)
    )
    assert get_answer_key(demo[0].id, store).served_from == "snapshot"
    with pytest.raises(NotBuilt):
        get_answer_key("sc_000000000000", store)


def test_get_answer_key_is_the_only_public_read_path():
    import blindspot.groundtruth as gt

    assert "get_answer_key" in gt.__all__ and "Store" not in gt.__all__
    outside = [
        p.relative_to(ROOT)
        for p in (ROOT / "src").rglob("*.py")
        if "groundtruth" not in p.parts and "groundtruth.store" in p.read_text(encoding="utf-8")
    ]
    assert not outside, f"read answer keys through get_answer_key, not the store: {outside}"


# --------------------------------------------------------------------------- CLI
def test_cli_dry_run_and_status(tmp_path, capsys):
    from blindspot.groundtruth.cli import main

    db = tmp_path / "gt.sqlite"
    assert main(["--config", str(CFG), "--db", str(db), "build", "--dry-run", "--tag", "demo"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"to fetch: \d+\s+would be live \(not cached\): \d+", out)
    assert main(["--config", str(CFG), "--db", str(db), "status"]) == 0
    assert "pending 0" in capsys.readouterr().out  # a dry run writes nothing


async def test_export_writes_ids_ranks_and_provenance(make, demo, tmp_path, capsys):
    from blindspot.groundtruth.cli import main

    client, store, _ = make(db="gt.sqlite")
    await Builder(client, store).build(demo)
    store.close()
    out = tmp_path / "keys.json"
    assert main(["--config", str(CFG), "--db", str(tmp_path / "gt.sqlite"), "export", "--out", str(out)]) == 0
    keys = json.loads(out.read_text(encoding="utf-8"))
    assert len(keys) == len(demo) and all(k["served_from"] == "cache" for k in keys)


# --------------------------------------------------------------------------- normalization
@pytest.mark.parametrize(
    "raw, norm",
    [
        ("Café Müller", "cafe muller"),  # accents
        ("Mötley Crüe", "motley crue"),
        ("Straße", "strasse"),  # transliteration by casefold
        ("Beyoncé", "beyonce"),
        ("  The   BEATLES!! ", "the beatles"),  # casing, punctuation, whitespace
        ("AC/DC", "ac dc"),
        ("Guns N' Roses", "guns n roses"),
        ("ラーメン 一蘭", "ラーメン 一蘭"),  # Japanese kept, voiced marks too
        ("ﾗｰﾒﾝ", "ラーメン"),  # half-width katakana folded by NFKC
        ("Ichiran (Example Ward)", "ichiran"),  # bracketed note
        ("Live in Example City", "live in example city"),  # "in" is part of names
    ],
)
def test_normalize_name(raw, norm):
    assert normalize_name(raw) == norm


def test_location_tails_come_from_config_words():
    words = ["Example City", "Other Town"]
    assert normalize_name("Noodle Bar - Example City", location_words=words) == "noodle bar"
    assert normalize_name("Noodle Bar, Other Town", location_words=words) == "noodle bar"
    assert normalize_name("Earth, Wind & Fire", location_words=words) == "earth wind fire"  # not a place
    assert normalize_name("Noodle Bar - Example City") == "noodle bar example city"  # no words, kept


def test_aliases():
    assert aliases("The Beatles") == ["beatles", "the beatles"]
    assert "simon and garfunkel" in aliases("Simon & Garfunkel")
    assert "morning glory" in aliases("(What's the Story) Morning Glory?")
    assert any("what s the story" in a for a in aliases("(What's the Story) Morning Glory?"))
