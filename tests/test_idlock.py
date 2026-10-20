"""The id lock and the rules that pick an id (#15): pure, no HTTP. Synthetic names and ids only."""

import json

import pytest

from blindspot.catalog import ConfigError
from blindspot.idlock import (
    EXACT,
    PARTIAL,
    WEAK,
    IdLock,
    LockItem,
    dumps_lock,
    load_lock,
    lock_key,
    normalize,
    resolve_audience_name,
    resolve_audience_urn,
    resolve_seed,
    resolve_tag,
    tag_family,
    write_lock,
)
from tests.test_catalog import ROOT

FIXTURE_CONFIG = ROOT / "tests" / "fixtures" / "config"
ARTIST = "urn:entity:artist"


def _uuid(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


def _hit(i, name, types=(ARTIST,), popularity=0.5):
    return {"entity_id": _uuid(i), "name": name, "types": list(types), "popularity": popularity}


def test_lock_key_normalizes_case_and_space():
    assert lock_key("seed", "music", "  Test   Artist\tOne ") == "seed:music:test artist one"
    assert lock_key("audience", "*", "leisure:Fine  Dining") == "audience:*:leisure:fine dining"
    urn = "urn:audience:leisure:Music_Festivals"
    assert lock_key("audience", "*", urn) == "audience:*:urn:audience:leisure:music_festivals"


def test_name_normalization():
    assert normalize("Pride & Prejudice") == normalize("pride and prejudice") == "pride and prejudice"
    assert normalize("The Test Band") == "test band" and normalize("Theory") == "theory"
    assert normalize("Beyoncé") == normalize("Beyoncé")  # composed and decomposed accents agree
    assert normalize("Beyoncé") != normalize("Beyonce")  # accents are kept
    assert normalize("Dr.  Test-Boots!") == "dr test boots"


def test_seed_rules():
    rs = resolve_seed
    exact = rs("Test Artist", ARTIST, [_hit(1, "TEST ARTIST"), _hit(2, "Other")])
    assert (exact.id, exact.confidence, exact.ambiguous) == (_uuid(1), EXACT, False)
    part = rs("Test Artist", ARTIST, [_hit(1, "Test Artist Live"), _hit(2, "x")])
    assert (part.confidence, part.ambiguous) == (PARTIAL, True)
    wrong = rs("Test Artist", ARTIST, [_hit(1, "Test Artist", types=("urn:entity:movie",))])
    assert (wrong.id, wrong.confidence, wrong.ambiguous) == (_uuid(1), WEAK, True)
    assert "another type" in wrong.reason
    twins = rs("Test Film", "urn:entity:movie", [
        _hit(1, "Test Film", ("urn:entity:movie",), 0.2),
        _hit(2, "The Test Film", ("urn:entity:movie",), 0.9),
    ])  # fmt: skip
    assert (twins.id, twins.confidence, twins.ambiguous) == (_uuid(2), EXACT, True)  # remake: most popular
    none = rs("Test Artist", ARTIST, [])
    assert (none.id, none.status, none.reason) == (None, "unresolved", "no search hits")
    later = rs("Test Artist", ARTIST, [_hit(1, "Test Artist Tribute"), _hit(2, "Test Artist")])
    assert later.id == _uuid(1) and "rank 2" in later.reason  # top-hit rule, flagged for review


def _tag(urn, name, popularity=0.5):
    return {"id": urn, "name": name, "popularity": popularity}


def test_tag_family_filtering_per_domain():
    hits = [
        _tag("urn:tag:keyword:qloo:test_snacks", "Test Snacks"),
        _tag("urn:tag:genre:place:restaurant:test_snacks", "Test Snacks", 0.1),
        _tag("urn:tag:genre:music:test_snacks", "Test Snacks"),
    ]
    assert tag_family(hits[1]["id"]) == "genre:place"
    dining = resolve_tag("test snacks", "dining", hits)
    assert (dining.id, dining.confidence, dining.ambiguous) == (hits[1]["id"], EXACT, False)
    assert resolve_tag("test snacks", "music", hits).id == hits[2]["id"]
    film = resolve_tag("test snacks", "film_tv", hits)
    assert (
        film.status == "unresolved"
        and "families returned: genre:music, genre:place, keyword:qloo" in film.reason
    )
    unknown = resolve_tag("test snacks", "test_domain", hits)
    assert unknown.status == "unresolved" and "no tag families" in unknown.reason
    two = resolve_tag(
        "test snacks", "dining", [*hits, _tag("urn:tag:cuisine:qloo:test_snacks", "Test Snacks", 0.9)]
    )
    assert two.ambiguous and two.id == "urn:tag:cuisine:qloo:test_snacks"  # most popular exact
    partial = resolve_tag("snacks", "dining", hits)
    assert (partial.confidence, partial.ambiguous) == (PARTIAL, True)


def test_audiences_use_the_urn_not_entity_id():
    listing = [
        {"id": "urn:audience:leisure:test_listening", "entity_id": _uuid(7), "name": "Test Listening"},
        {"id": "urn:audience:leisure:other", "entity_id": _uuid(8), "name": "Other"},
    ]
    d = resolve_audience_name("test listening", listing)
    assert d.id == "urn:audience:leisure:test_listening" and d.confidence == EXACT and not d.ambiguous
    assert resolve_audience_urn("urn:audience:leisure:other", listing).id == "urn:audience:leisure:other"
    assert resolve_audience_urn("urn:audience:leisure:gone", listing).reason == "not in listing"
    assert resolve_audience_name("nothing like it", listing).status == "unresolved"
    both = resolve_audience_name("test", [*listing, {"id": "urn:audience:leisure:tests", "name": "Tests"}])
    assert both.ambiguous and both.confidence == PARTIAL


def test_identity_audiences_are_forced_unresolved():
    listing = [
        {"id": "urn:audience:leisure:political_junkie", "entity_id": _uuid(1), "name": "Political Junkie"},
        {"id": "urn:audience:communities:test_group", "entity_id": _uuid(2), "name": "Test Group"},
    ]
    d = resolve_audience_name("political junkie", listing)
    assert d.status == "unresolved" and d.reason == "identity-based (AGENTS §13)"
    assert (
        resolve_audience_urn("urn:audience:communities:test_group", listing).reason
        == "identity-based (AGENTS §13)"
    )


def _lock():
    return IdLock(
        items={
            lock_key("tag", "dining", "Test Snacks"): LockItem(
                kind="tag", domain="dining", query="Test Snacks", status="unresolved", reason="x"
            ),
            lock_key("seed", "music", "Test Artist"): LockItem(
                kind="seed", domain="music", query="Test Artist", search_type=ARTIST, id=_uuid(1),
                status="resolved", confidence=1.0,
            ),
        }
    )  # fmt: skip


def test_lock_is_deterministic_sorted_and_name_free(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_lock(_lock(), a)
    write_lock(IdLock.model_validate(json.loads(a.read_text(encoding="utf-8"))), b)
    assert (
        a.read_bytes() == b.read_bytes() and a.read_bytes().endswith(b"}\n") and b"\r" not in a.read_bytes()
    )
    data = json.loads(a.read_text(encoding="utf-8"))
    assert list(data["items"]) == sorted(data["items"])
    assert list(data["items"]["seed:music:test artist"]) == sorted(data["items"]["seed:music:test artist"])
    text = dumps_lock(_lock())
    assert '"name"' not in text and "popularity" not in text
    assert "search_type" not in json.dumps(data["items"]["tag:dining:test snacks"])  # seeds only


def test_load_lock_and_id_for():
    lock = load_lock(FIXTURE_CONFIG)
    assert lock.id_for("audience", "*", "leisure:Test  Listening") == "urn:audience:leisure:test_listening"
    assert lock.id_for("seed", "music", "Test Artist One") == "00000000-0000-4000-8000-0000000000b1"
    assert lock.id_for("tag", "dining", "test snacks") is None  # unresolved
    assert lock.id_for("seed", "music", "Not In Lock") is None
    assert [i.query for i in lock.unresolved()] == ["test snacks"]
    assert [i.query for i in lock.ambiguous()] == ["Test Artist One"]
    assert (FIXTURE_CONFIG / "resolved_ids.lock.json").read_text(encoding="utf-8") == dumps_lock(lock)


def test_load_lock_rejects_bad_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_lock(tmp_path)
    item = _lock().items["seed:music:test artist"].model_dump()
    (tmp_path / "resolved_ids.lock.json").write_text(
        json.dumps({"version": 1, "items": {"seed:music:wrong key": item}}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="doesn't match"):
        load_lock(tmp_path)
