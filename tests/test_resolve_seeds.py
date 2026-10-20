"""scripts/resolve_seeds.py (#15) against a synthetic Qloo (respx): the lock, a cache-hit re-run,
--dry-run, --max-calls, the distinct-audience stop and no Qloo names in committed output. Every file
goes to tmp_path; the real lock, review doc and detail file are checked unchanged."""

import hashlib
import importlib.util
import json
import re
import shutil
import sys

import httpx
import pytest

from tests.test_catalog import ROOT

_spec = importlib.util.spec_from_file_location("resolve_seeds", ROOT / "scripts" / "resolve_seeds.py")
R = importlib.util.module_from_spec(_spec)
sys.modules["resolve_seeds"] = R  # dataclasses look their module up while loading
_spec.loader.exec_module(R)

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REAL = [
    ROOT / "config" / "resolved_ids.lock.json",
    ROOT / "docs" / "seed_review.md",
    ROOT / "data" / "resolved_ids.detail.json",
]


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    before = [_sha(p) for p in REAL]
    config = tmp_path / "config"
    shutil.copytree(ROOT / "tests" / "fixtures" / "config", config)
    (config / "resolved_ids.lock.json").unlink()  # start without a lock
    review = tmp_path / "seed_review.md"
    review.write_text("# Seed review\n", encoding="utf-8")
    monkeypatch.setattr(R, "CONFIG", config)
    monkeypatch.setattr(R, "REVIEW", review)
    monkeypatch.setattr(R, "DETAIL", tmp_path / "data" / "detail.json")
    yield config
    assert [_sha(p) for p in REAL] == before


def _uuid(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}".upper()


def _handler(request):
    q = dict(request.url.params)
    path = request.url.path
    if path == "/search":
        name = q["query"]
        return httpx.Response(200, json={"results": [
            {"entity_id": _uuid(name), "name": name.upper(), "types": [q["types"]], "popularity": 0.9},
            {"entity_id": _uuid("x" + name), "name": "Secret Qloo Name", "types": [q["types"]], "popularity": 0.1},
        ]})  # fmt: skip
    if path == "/v2/tags":
        slug = q["filter.query"].replace(" ", "_")
        return httpx.Response(200, json={"results": {"tags": [
            {"id": f"urn:tag:keyword:qloo:{slug}", "name": "Secret Qloo Tag"},
            {"id": f"urn:tag:genre:place:{slug}", "name": q["filter.query"].title()},
        ]}})  # fmt: skip
    if path == "/v2/audiences/types":
        types = [{"type": f"urn:audience:{t}"} for t in ("hobbies_and_interests", "leisure", "communities")]
        return httpx.Response(200, json={"results": {"audience_types": types}})
    if path == "/v2/audiences":
        group = q["filter.parents.types"].removeprefix("urn:audience:")
        items = {
            "hobbies_and_interests": [("test_snacks", "Test Snacks")],
            "leisure": [("test_listening", "Test Listening"), ("foodie", "Foodie")],
        }[group]
        return httpx.Response(200, json={"results": {"audiences": [
            {"id": f"urn:audience:{group}:{i}", "entity_id": _uuid(i), "name": n} for i, n in items
        ]}})  # fmt: skip
    raise AssertionError(f"unexpected request {path}")


def _args(*argv):
    return R.parser().parse_args(list(argv))


async def test_run_locks_everything_and_reruns_byte_identical(make_client, mock_api, cfg):
    route = mock_api.route().mock(side_effect=_handler)
    c = make_client()
    out = await R.run(_args(), c)
    assert out["stopped"] is None and out["collapse"] is None
    assert out["counts"] == {
        "audience": {"resolved": 2, "ambiguous": 0, "unresolved": 0},
        "seed": {"resolved": 2, "ambiguous": 0, "unresolved": 0},
        "tag": {"resolved": 1, "ambiguous": 0, "unresolved": 0},
    }
    lock_path = cfg / "resolved_ids.lock.json"
    first, calls = lock_path.read_bytes(), route.call_count
    lock = R.load_lock(cfg)
    assert lock.id_for("audience", "*", "leisure:test listening") == "urn:audience:leisure:test_listening"
    assert lock.id_for("tag", "dining", "test snacks") == "urn:tag:genre:place:test_snacks"  # family filter
    assert lock.id_for("seed", "music", "Test Artist One") == _uuid("Test Artist One")

    again = await R.run(_args(), c)
    assert route.call_count == calls and again["live_calls"] == 0  # all cache hits
    assert lock_path.read_bytes() == first  # byte-identical

    text = first.decode("utf-8")
    assert "Secret Qloo" not in text and "TEST ARTIST" not in text and '"name"' not in text
    assert "popularity" not in text
    detail = json.loads(R.DETAIL.read_text(encoding="utf-8"))
    assert detail["seed:music:test artist one"]["candidates"][0]["name"] == "TEST ARTIST ONE"  # review only
    review = R.REVIEW.read_text(encoding="utf-8")
    assert R.BEGIN in review and "Nothing to review." in review and not UUID.search(review)


async def test_dry_run_makes_no_calls_and_writes_nothing(make_client, mock_api, cfg, tmp_path):
    route = mock_api.route().mock(side_effect=_handler)
    out = await R.run(_args("--dry-run"), make_client(qloo_mode="replay"))
    assert route.call_count == 0 and out["live_calls"] == 0
    assert out["plan"] == {"seeds": 2, "tags": 1, "audiences": 3}  # types + 2 listings
    assert not (cfg / "resolved_ids.lock.json").exists() and not R.DETAIL.exists()
    assert R.REVIEW.read_text(encoding="utf-8") == "# Seed review\n"


async def test_max_calls_stops_cleanly_and_keeps_partial_output(make_client, mock_api, cfg):
    mock_api.route().mock(side_effect=_handler)
    out = await R.run(_args("--max-calls", "1"), make_client())
    assert out["stopped"] and out["live_calls"] == 1
    lock = R.load_lock(cfg)
    assert len(lock.items) == 1 and next(iter(lock.items.values())).kind == "seed"


async def test_only_keeps_other_kinds(make_client, mock_api, cfg):
    mock_api.route().mock(side_effect=_handler)
    c = make_client()
    await R.run(_args(), c)
    before = R.load_lock(cfg)
    await R.run(_args("--only", "tags"), c)
    assert R.load_lock(cfg) == before


async def test_audience_collapse_stops_without_writing_the_lock(make_client, mock_api, cfg):
    """Two personas whose audiences differ as written but resolve to the same URN."""
    personas = (cfg / "personas.yaml").read_text(encoding="utf-8")
    personas = personas.replace(
        "audiences: [urn:audience:hobbies_and_interests:test_snacks]",
        "audiences: [urn:audience:leisure:foodie]",
    ).replace(
        "audiences: [{name: test listening, type: leisure}]", "audiences: [{name: foodies, type: leisure}]"
    )
    (cfg / "personas.yaml").write_text(personas, encoding="utf-8")
    mock_api.route().mock(side_effect=_handler)
    out = await R.run(_args(), make_client())
    assert out["collapse"] and "same set" in out["collapse"]
    assert not (cfg / "resolved_ids.lock.json").exists()


def _paged(page2_has_new_ids: bool):
    """leisure fills a page (take patched to 2); page 2 either has new ids or repeats page 1."""

    def handler(request):
        q = dict(request.url.params)
        if request.url.path == "/v2/audiences" and q["filter.parents.types"] == "urn:audience:leisure":
            items = [("a_one", "A One"), ("a_two", "A Two")]
            if q.get("page") == "2" and page2_has_new_ids:
                items = [("test_listening", "Test Listening")]
            body = [{"id": f"urn:audience:leisure:{i}", "entity_id": _uuid(i), "name": n} for i, n in items]
            return httpx.Response(200, json={"results": {"audiences": body}})
        return _handler(request)

    return handler


async def test_full_page_fetches_page_two_and_merges_new_ids(make_client, mock_api, cfg, monkeypatch):
    monkeypatch.setattr(R, "AUDIENCE_TAKE", 2)
    route = mock_api.route().mock(side_effect=_paged(True))
    out = await R.run(_args(), make_client())
    assert out["notes"]["pagination"]["leisure"]["page2"] == "VERIFIED"
    assert out["notes"]["pagination"]["hobbies_and_interests"]["page2"] is None  # didn't fill a page
    assert (
        R.load_lock(cfg).id_for("audience", "*", "leisure:test listening")
        == "urn:audience:leisure:test_listening"
    )
    assert sum(c.request.url.params.get("page") == "2" for c in route.calls) == 1


async def test_ignored_page_param_marks_names_possibly_truncated(make_client, mock_api, cfg, monkeypatch):
    monkeypatch.setattr(R, "AUDIENCE_TAKE", 2)
    mock_api.route().mock(side_effect=_paged(False))
    out = await R.run(_args(), make_client())
    assert out["notes"]["pagination"]["leisure"]["page2"].startswith("FAILED")
    item = R.load_lock(cfg).items["audience:*:leisure:test listening"]
    assert item.status == "unresolved" and item.reason == "listing may be truncated (2 of take 2)"


async def test_failed_lookups_keep_the_locked_ids(make_client, mock_api, cfg, tmp_path):
    route = mock_api.route().mock(side_effect=_handler)
    await R.run(_args(), make_client())
    before = (cfg / "resolved_ids.lock.json").read_bytes()
    route.mock(return_value=httpx.Response(429, json={"error": "rate limited"}))
    out = await R.run(_args(), make_client(qloo_cache_path=tmp_path / "fresh.sqlite"))  # cache expired
    assert out["failed"] == 5  # 2 seeds, 1 tag, 2 audiences (the types listing is not an item)
    assert (cfg / "resolved_ids.lock.json").read_bytes() == before
