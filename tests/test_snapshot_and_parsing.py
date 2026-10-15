import json

from blindspot.qloo.cache import cache_key
from blindspot.qloo.models import Entity, InsightsQuery, extract_items
from blindspot.qloo.snapshot import Snapshot, build_snapshot, minimize_body


def test_parse_search_fixture(search_body):
    items = [Entity.parse(x) for x in extract_items(search_body)]
    assert len(items) == 2
    assert items[0].entity_id.endswith("01") and items[0].name == "Example Artist One"
    assert items[0].kind == "urn:entity:artist" and items[0].popularity == 0.91
    assert items[0].raw["properties"]["short_description"] == "synthetic"


def test_parse_insights_fixture(insights_body):
    items = [Entity.parse(x) for x in extract_items(insights_body)]
    assert [e.affinity for e in items] == [0.88, 0.42]
    assert items[0].location == {"lat": 12.97, "lon": 77.59}


def test_parse_is_tolerant():
    assert extract_items({"weird": 1}) == []
    assert extract_items(None) == []
    e = Entity.parse({"id": "x", "name": "n", "types": "urn:entity:brand", "location": "nowhere", "query": 3})
    assert (
        e.entity_id == "x" and e.types == ["urn:entity:brand"] and e.location is None and e.affinity is None
    )


def test_insights_query_aliases_and_extras():
    q = InsightsQuery(
        **{
            "filter.type": "urn:entity:artist",
            "signal.interests.entities": ["a", "b"],
            "bias.trends": "high",
            "filter.geocode.country_code": "IN",
        }
    )
    p = q.to_params()
    assert p["filter.type"] == "urn:entity:artist" and p["signal.interests.entities"] == ["a", "b"]
    assert p["filter.geocode.country_code"] == "IN"


def test_minimize_drops_verbose_fields(insights_body):
    m = minimize_body(insights_body)
    e = m["results"]["entities"][0]
    assert e["entity_id"] and e["query"]["affinity"] == 0.88
    assert "akas" not in json.dumps(m)


def test_seed_empty_cache_from_snapshot(tmp_path, make_client, search_body):
    entry = {
        "key": cache_key("GET", "/search", {"query": "x"}),
        "method": "GET",
        "path": "/search",
        "params": {"query": "x"},
        "status": 200,
        "fetched_at": 5.0,
        "body": search_body,
    }
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(build_snapshot([entry], "demo")), encoding="utf-8")
    snap = Snapshot.load(p)
    assert snap.loaded
    c = make_client(snapshot=snap)
    assert c.cache.size() == 0
    assert c.startup() == 1
    assert c.cache.size() == 1 and c.cache.get(entry["key"]).source == "snapshot"
    assert c.startup() == 0  # not re-seeded when cache already has data


def test_bad_snapshot_file_is_ignored(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert not Snapshot.load(p).loaded
    assert not Snapshot.load(tmp_path / "missing.json").loaded


def test_unwritable_cache_path_falls_back(tmp_path):
    from blindspot.qloo.client import open_database

    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    db = open_database(blocker / "sub" / "cache.sqlite")  # parent is a file: cannot be created
    assert db.query("SELECT COUNT(*) n FROM cache")[0]["n"] == 0
    db.close()
