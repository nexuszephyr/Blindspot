"""Entity resolver (#29): each stage with synthetic index data, statuses, the budgeted /search
fallback, write-back and offline re-runs, and match-rate reporting.

Offline and keyless: answer keys are built with the fake Qloo server and `/search` answers come from
`FakeQloo.search_body`. Synthetic ids and names only; no city name is written here (rule 9): city
words come from the catalog."""

from __future__ import annotations

import time

import pytest

from blindspot.catalog import load_catalog, load_domains
from blindspot.groundtruth import Builder, add_search_hits, find_entities, get_answer_key
from blindspot.groundtruth.store import Store
from blindspot.idlock import load_lock
from blindspot.qloo.client import QlooClient
from blindspot.qloo.context import RequestContext
from blindspot.qloo.models import Entity
from blindspot.qloo.snapshot import Snapshot
from blindspot.resolve import Resolved, Resolver, format_rates, match_rates, script_of, token_set_ratio
from blindspot.resolve.match import FUZZY_THRESHOLD
from blindspot.resolve.resolver import CITY_RADIUS_KM, TIE_BREAK_FACTOR
from blindspot.scenarios.generator import generate
from tests.fakeqloo import FakeQloo, FakeTransport
from tests.test_catalog import ROOT

CFG = ROOT / "config"
CENTRE = (10.0, 20.0)  # synthetic coordinates for the fake's places
FAR = (40.0, 60.0)  # thousands of km away


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CFG)


@pytest.fixture(scope="module")
def picked(catalog):
    """One core scenario per domain, all in the demo subset."""
    exam = generate(catalog, load_lock(CFG)).scenarios
    out = {}
    for s in exam:
        if "demo" in s.tags and s.probe_type == "core" and s.domain_id not in out:
            out[s.domain_id] = s
    assert {"dining", "music", "film_tv"} <= set(out)
    return out


def _places_with_coordinates(fake: FakeQloo) -> None:
    plain = fake.insights_body

    def body(params):  # like insights (E10): places carry coordinates near their city
        out = plain(params)
        if params.get("filter.type") == "urn:entity:place":
            for i, e in enumerate(out["results"]["entities"]):
                e["location"] = {"lat": CENTRE[0] + i * 0.001, "lon": CENTRE[1] - i * 0.001}
        return out

    fake.insights_body = body


@pytest.fixture
def world(make_settings, tmp_path, picked, catalog):
    """A built answer-key store, a client on a fresh fake Qloo, and a resolver factory."""
    opened = []

    async def no_sleep(_s):
        return None

    def client_for(fake: FakeQloo, name: str, **kw) -> QlooClient:
        s = make_settings(
            qloo_cache_path=tmp_path / f"{name}.cache", qloo_max_live_calls=100_000,
            qloo_daily_live_cap=100_000, qloo_demo_reserve=0, qloo_max_attempts=1, **kw,
        )  # fmt: skip
        c = QlooClient(s, snapshot=Snapshot(), transport=FakeTransport(fake), sleep=no_sleep)
        opened.append(c)
        return c

    class World:
        store = Store(tmp_path / "gt.sqlite")
        fake = FakeQloo()

        def city(self, domain: str) -> str:
            return picked[domain].city_id

        def client(self, **kw) -> QlooClient:
            return client_for(self.fake, "resolve", **kw)

        def resolver(self, **kw) -> Resolver:
            client = kw.pop("client", None) or self.client(**kw.pop("settings", {}))
            return Resolver(catalog, self.store, client=client, **kw)

        def key(self, domain: str):
            return get_answer_key(picked[domain].id, self.store)

        def searches(self) -> int:
            return sum(1 for s in self.fake.seen if s.path == "/search")

        async def resolve(self, texts, domain="music", resolver=None, ctx=None, key=True) -> Resolved:
            s = picked[domain]
            return await (resolver or self.resolver()).resolve(
                texts, domain_id=domain, city_id=s.city_id, key=self.key(domain) if key else None,
                ctx=ctx or RequestContext.script("resolve-test", purpose="audit"),
            )  # fmt: skip

    w = World()
    w.catalog = catalog
    build_fake = FakeQloo()
    _places_with_coordinates(build_fake)
    words = [x for c in catalog.cities for x in (c.display_name, c.location)]
    w.build = lambda: Builder(client_for(build_fake, "build"), w.store, location_words=words).build(
        list(picked.values())
    )
    w.fake.search_body = lambda params: {"results": []}  # nothing found unless a test says so
    yield w
    for c in opened:
        c.db.close()
    w.store.close()


@pytest.fixture
async def built(world):
    report = await world.build()
    assert set(report.statuses) == {"done"}
    return world


def _search_hits(*hits: dict):
    return lambda params: {"results": [dict(h) for h in hits]}


def _hit(eid: str, name: str, type_: str = "urn:entity:artist", **extra) -> dict:
    return {"entity_id": eid, "name": name, "types": [type_], "popularity": 0.5, **extra}


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


# --------------------------------------------------------------------------- stage 1: index exact/alias
async def test_exact_name_in_the_key_is_matched_in_key_offline(built):
    key = built.key("music")
    top3 = key.local.items[2]
    res = await built.resolve([top3.name])
    r = res.items[0]
    assert (r.status, r.method, r.qloo_id, r.rank) == ("matched_in_key", "index_exact", top3.qloo_id, 3)
    assert r.confidence == 1.0 and r.served_from == "cache"  # stored data is never "live"
    assert built.searches() == 0 and res.searches == 0


async def test_index_matches_carry_the_keys_snapshot_label(built):
    key = built.key("music").model_copy(update={"served_from": "snapshot"})
    s = Resolver(built.catalog, built.store)  # no client: index only
    res = await s.resolve(
        [key.local.items[0].name], domain_id="music", city_id=built.city("music"), key=key,
        ctx=RequestContext.script("resolve-test", purpose="audit"),
    )  # fmt: skip
    assert (res.items[0].status, res.items[0].served_from) == ("matched_in_key", "snapshot")


async def test_elsewhere_without_a_client_is_labelled_as_stored_data(built):
    far = _hit(_id(1), "Corner Noodle House", "urn:entity:place", location={"lat": FAR[0], "lon": FAR[1]})
    add_search_hits([Entity.parse(far)], store=built.store)
    res = await built.resolve(
        ["Corner Noodle House"], domain="dining", resolver=Resolver(built.catalog, built.store)
    )
    r = res.items[0]
    assert (r.status, r.reason, r.served_from) == ("unresolved", "elsewhere", "cache")
    assert built.searches() == 0


async def test_pool_and_index_entities_outside_the_key_are_matched_outside_key(built):
    key = built.key("music")
    in_pool = key.local.pool[key.depth + 5]  # rank depth + 6: in the pool, outside the key
    pool_name = find_name(built, in_pool)
    res = await built.resolve([pool_name])
    r = res.items[0]
    assert (r.status, r.rank) == ("matched_outside_key", key.depth + 6) and r.qloo_id == in_pool


def find_name(world, qloo_id: str) -> str:
    from blindspot.groundtruth import entities_by_id

    return entities_by_id([qloo_id], store=world.store)[0].name


async def test_aliases_and_city_tails_match(built, catalog, picked):
    item = built.key("music").local.items[0]
    city = catalog.city(picked["music"].city_id)
    texts = [f"The {item.name}", f"{item.name} - {city.display_name}", f"  {item.name.upper()}!! "]
    res = await built.resolve(texts)
    assert [r.qloo_id for r in res.items] == [item.qloo_id] * 3
    assert [r.method for r in res.items] == ["index_alias", "index_exact", "index_exact"]
    assert res.items[0].confidence < 1.0 and built.searches() == 0


async def test_index_only_matches_the_domains_types(built):
    film = built.key("film_tv").local.items[0]
    res = await built.resolve([film.name], domain="music")  # a film's name asked as an artist
    assert res.items[0].status != "matched_in_key" and res.items[0].qloo_id is None


# --------------------------------------------------------------------------- stage 2: fuzzy
def test_token_set_ratio():
    assert token_set_ratio("velvet lantern orchestra", "orchestra velvet lantern") == 1.0  # word order
    assert token_set_ratio("velvet lantren orchestra", "velvet lantern orchestra") >= FUZZY_THRESHOLD  # typo
    # a short name inside a longer one is not a match: "cafe" is not "cafe coffee day"
    assert token_set_ratio("cafe", "cafe coffee day") < FUZZY_THRESHOLD
    assert token_set_ratio("koshy s", "koshy s bakery") < FUZZY_THRESHOLD
    assert token_set_ratio("north star", "south pole") < 0.5
    assert token_set_ratio("studio 54 band", "studio 84 band") < FUZZY_THRESHOLD  # a different word
    assert token_set_ratio("", "anything") == 0.0


async def test_fuzzy_matches_one_clear_candidate(built):
    add_search_hits([Entity.parse(_hit(_id(1), "Velvet Lantern Orchestra"))], store=built.store)
    res = await built.resolve(["Velvet Lantren Orchestra"])
    r = res.items[0]
    assert (r.status, r.method, r.qloo_id) == ("matched_outside_key", "index_fuzzy", _id(1))
    assert 0.8 < r.confidence < 0.9 and built.searches() == 0


async def test_fuzzy_ties_are_never_guessed(built):
    add_search_hits(
        [Entity.parse(_hit(_id(1), "Velvet Lantern One")), Entity.parse(_hit(_id(2), "Velvet Lantern Ones"))],
        store=built.store,
    )
    res = await built.resolve(["Velvet Lanterns One"])  # close to both
    r = res.items[0]
    assert r.status == "unresolved" and r.reason == "ambiguous" and r.qloo_id is None


async def test_short_names_skip_fuzzy(built):
    add_search_hits([Entity.parse(_hit(_id(1), "Abcd"))], store=built.store)
    res = await built.resolve(["Abce"])  # one letter off, but too short to match loosely
    assert res.items[0].status == "phantom_candidate" and built.searches() == 1


# --------------------------------------------------------------------------- ambiguity
async def test_exact_name_tie_outside_the_key_is_ambiguous_and_never_searched(built):
    twins = [Entity.parse(_hit(_id(i), "Twin Title Remake", "urn:entity:movie")) for i in (1, 2)]
    add_search_hits(twins, store=built.store)
    res = await built.resolve(["Twin Title Remake"], domain="film_tv")
    r = res.items[0]
    assert (r.status, r.reason, r.served_from) == ("unresolved", "ambiguous", "cache")
    assert built.searches() == 0  # a search could only add same-name hits


async def test_exact_name_tie_broken_by_the_key(built):
    item = built.key("film_tv").local.items[4]
    add_search_hits([Entity.parse(_hit(_id(9), item.name, "urn:entity:movie"))], store=built.store)
    res = await built.resolve([item.name], domain="film_tv")
    r = res.items[0]
    assert (r.status, r.qloo_id) == ("matched_in_key", item.qloo_id)
    assert r.confidence == pytest.approx(1.0 * TIE_BREAK_FACTOR)


# --------------------------------------------------------------------------- stage 3: /search
async def test_search_match_is_stored_and_a_rerun_makes_no_search(built):
    built.fake.search_body = _search_hits(
        _hit(_id(5), "Unrelated Hit"),
        _hit(_id(6), "Quiet Harbour Band"),
        _hit(_id(7), "Quiet Harbour Band Live"),
    )
    first = await built.resolve(["Quiet Harbour Band"])
    r = first.items[0]
    assert (r.status, r.method, r.qloo_id, r.served_from) == (
        "matched_outside_key", "search_exact", _id(6), "live"
    )  # fmt: skip
    assert first.searches == first.live_searches == 1 and built.searches() == 1
    assert find_entities(["quiet harbour band"], store=built.store)  # written back
    again = await built.resolve(["Quiet Harbour Band"])  # a fresh resolver, as in a re-run
    assert again.items[0].method == "index_exact" and again.items[0].qloo_id == _id(6)
    assert again.searches == 0 and built.searches() == 1


async def test_search_hits_are_not_written_into_a_snapshot_built_store(built):
    # A live hit stored beside snapshot rows would later be served as `snapshot` (rules 2 and 5).
    built.fake.search_body = _search_hits(_hit(_id(6), "Quiet Harbour Band"))
    key = built.key("music").model_copy(update={"served_from": "snapshot"})
    ctx = RequestContext.script("resolve-test", purpose="audit")
    res = await built.resolver().resolve(
        ["Quiet Harbour Band"], domain_id="music", city_id=built.city("music"), key=key, ctx=ctx
    )
    r = res.items[0]
    assert (r.status, r.method, r.qloo_id, r.served_from) == (
        "matched_outside_key", "search_exact", _id(6), "live"
    )  # fmt: skip
    assert not find_entities(["quiet harbour band"], store=built.store)  # this run only


async def test_search_sends_only_query_types_and_take(built):
    await built.resolve(["Someone Unknown"], domain="film_tv")
    (seen,) = [s for s in built.fake.seen if s.path == "/search"]
    assert set(seen.params) == {"query", "types", "take"}
    assert seen.params["types"].split(",") == ["urn:entity:movie", "urn:entity:tv_show"]  # from config


async def test_search_ties_are_ambiguous_and_a_rerun_makes_no_search(built):
    built.fake.search_body = _search_hits(
        _hit(_id(1), "Same Name Film", "urn:entity:movie"), _hit(_id(2), "Same Name Film", "urn:entity:movie")
    )
    first = await built.resolve(["Same Name Film"], domain="film_tv")
    assert (first.items[0].status, first.items[0].reason) == ("unresolved", "ambiguous")
    again = await built.resolve(["Same Name Film"], domain="film_tv")
    assert again.items[0].reason == "ambiguous" and again.searches == 0 and built.searches() == 1


async def test_no_match_after_a_clean_search_is_a_phantom_candidate(built):
    built.fake.search_body = _search_hits(_hit(_id(1), "Something Else Entirely"))
    res = await built.resolve(["Invented Nonexistent Act", "Another Made Up Name"])
    assert [r.status for r in res.items] == ["phantom_candidate"] * 2
    assert all(r.qloo_id is None and r.confidence == 0 and r.served_from == "live" for r in res.items)


async def test_same_text_twice_is_resolved_once(built):
    res = await built.resolve(["Invented Act", "invented act!"])
    assert [r.position for r in res.items] == [0, 1] and res.items[1].text == "invented act!"
    assert built.searches() == 1


# --------------------------------------------------------------------------- budget, deadline, errors
async def test_resolver_cap_on_live_searches_gives_unresolved_budget(built):
    resolver = built.resolver(max_live_searches=1)
    res = await built.resolve(["Unknown One", "Unknown Two", "Unknown Three"], resolver=resolver)
    assert [r.status for r in res.items] == ["phantom_candidate", "unresolved", "unresolved"]
    assert [r.reason for r in res.items[1:]] == ["budget", "budget"]
    assert built.searches() == 1 and res.live_searches == 1


async def test_cap_still_allows_cached_searches(built):
    await built.resolve(["Unknown One"])  # cached now
    resolver = built.resolver(max_live_searches=0)
    res = await built.resolve(["Unknown One", "Unknown Two"], resolver=resolver)
    assert res.items[0].status == "phantom_candidate" and res.items[0].served_from == "cache"
    assert (res.items[1].status, res.items[1].reason) == ("unresolved", "budget")
    assert built.searches() == 1


async def test_client_per_run_budget_gives_unresolved_budget(built):
    resolver = built.resolver(settings={"qloo_run_cap": 1})
    ctx = RequestContext(client_id="c_test", purpose="audit")  # local scripts skip the per-run cap
    res = await built.resolve(["Unknown One", "Unknown Two"], resolver=resolver, ctx=ctx)
    assert res.items[0].status == "phantom_candidate"
    assert (res.items[1].status, res.items[1].reason) == ("unresolved", "budget")
    assert built.searches() == 1


async def test_deadline_passed_gives_unresolved_deadline_without_a_call(built):
    ctx = RequestContext(client_id="c_test", purpose="audit", deadline=time.monotonic() - 1)
    res = await built.resolve(["Unknown One"], ctx=ctx)
    assert (res.items[0].status, res.items[0].reason) == ("unresolved", "deadline")
    assert built.searches() == 0


async def test_errors_kill_switch_and_offline_are_unresolved_never_phantom(built):
    built.fake.behaviour = "server_503"
    res = await built.resolve(["Unknown One"])
    assert (res.items[0].status, res.items[0].reason, res.items[0].served_from) == (
        "unresolved", "error", "degraded"
    )  # fmt: skip
    built.fake.behaviour = "ok"
    off = RequestContext(client_id="c_test", qloo_enabled=False)
    assert (await built.resolve(["Unknown Two"], ctx=off)).items[0].reason == "disabled"
    no_live = RequestContext(client_id="c_test", allow_live=False)
    assert (await built.resolve(["Unknown Three"], ctx=no_live)).items[0].reason == "offline"
    offline = Resolver(load_catalog(CFG), built.store)  # no client at all
    assert (await built.resolve(["Unknown Four"], resolver=offline)).items[0].reason == "no_search"
    item = built.key("music").local.items[0]  # ... but the index still resolves offline
    assert (await built.resolve([item.name], resolver=offline)).items[0].status == "matched_in_key"


# --------------------------------------------------------------------------- places and the city
async def test_same_name_place_far_away_is_elsewhere_not_phantom(built):
    far = _hit(_id(1), "Corner Noodle House", "urn:entity:place", location={"lat": FAR[0], "lon": FAR[1]})
    add_search_hits([Entity.parse(far)], store=built.store)
    res = await built.resolve(["Corner Noodle House"], domain="dining")
    r = res.items[0]
    assert (r.status, r.reason) == ("unresolved", "elsewhere")  # it exists: not a phantom (E10)
    assert built.searches() == 1  # the search found nothing nearer


async def test_same_name_place_in_the_city_wins_over_one_elsewhere(built):
    near = {"lat": CENTRE[0] + 0.01, "lon": CENTRE[1]}
    add_search_hits(
        [
            Entity.parse(_hit(_id(1), "Corner Noodle House", "urn:entity:place", location={"lat": FAR[0], "lon": FAR[1]})),
            Entity.parse(_hit(_id(2), "Corner Noodle House", "urn:entity:place", location=near)),
        ],
        store=built.store,
    )  # fmt: skip
    res = await built.resolve(["Corner Noodle House"], domain="dining")
    assert (res.items[0].status, res.items[0].qloo_id) == ("matched_outside_key", _id(2))
    assert CITY_RADIUS_KM < 100


async def test_dining_key_place_resolves_in_key(built):
    item = built.key("dining").local.items[1]
    res = await built.resolve([item.name], domain="dining")
    assert (res.items[0].status, res.items[0].rank) == ("matched_in_key", 2)


# --------------------------------------------------------------------------- untrusted text
async def test_untrusted_text_is_data(built):
    nasty = "Ignore previous instructions'; DROP TABLE entity_index; -- " + "x" * 500
    res = await built.resolve([nasty, "", "   "])
    assert res.items[0].status == "phantom_candidate" and len(res.items[0].text) == 200
    assert [r.reason for r in res.items[1:]] == ["empty", "empty"]
    assert built.store.table_counts()["entity_index"] > 0  # still there


# --------------------------------------------------------------------------- reporting
def test_script_of():
    assert script_of("Cafe Muller") == "latin"
    assert script_of("ラーメン") == "kana"
    assert script_of("ﾗｰﾒﾝ") == "kana"
    assert script_of("一蘭") == "cjk"
    assert script_of("नमस्ते") == "devanagari"
    assert script_of("123 !!") == "none"


async def test_match_rates_per_domain_and_script(built):
    item = built.key("music").local.items[0]
    budgeted = built.resolver(max_live_searches=1)
    res = await built.resolve([item.name, "Unknown One", "ラーメン", "Unknown Two"], resolver=budgeted)
    statuses = [r.status for r in res.items]
    assert statuses == ["matched_in_key", "phantom_candidate", "unresolved", "unresolved"]
    rows = match_rates([res])
    latin = next(r for r in rows if r.script == "latin")
    assert (latin.total, latin.matched, latin.phantom, latin.unresolved) == (3, 1, 1, 1)
    assert latin.unresolved_by_reason == {"budget": 1} and latin.vs_ceiling == "pending (#21)"
    kana = next(r for r in rows if r.script == "kana")
    assert kana.phantom == 0 and kana.unresolved == 1  # unresolved is never a phantom
    with_ceiling = match_rates([res], ceilings={"music": 0.5})
    assert next(r for r in with_ceiling if r.script == "latin").vs_ceiling == "67% of ceiling 0.50"
    table = format_rates(rows)
    assert "pending (#21)" in table and item.name not in table  # counts only, no names


# --------------------------------------------------------------------------- config
def test_search_types_are_validated(tmp_path):
    import yaml

    doc = {"domains": [{"id": "x", "filter_type": "urn:entity:movie", "take": 5, "phrasing": "films",
                        "location_param": "signal", "search_types": ["urn:entity:film"]}]}  # fmt: skip
    (tmp_path / "domains.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="search types"):
        load_domains(tmp_path)


def test_every_configured_domain_has_search_types(catalog):
    assert all(d.search_types for d in catalog.domains)
