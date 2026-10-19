"""scripts/experiments_p2.py (#13): helpers, decision rules, an offline end-to-end run against a
synthetic Qloo, --dry-run (no calls), --report (no client, no calls), and that nothing generated
carries an id or a name. Recordings and the doc go to tmp_path; the real files are checked unchanged."""

import hashlib
import importlib.util
import json
import re

import httpx
import pytest
import yaml

from tests.test_catalog import ROOT

_spec = importlib.util.spec_from_file_location("experiments_p2", ROOT / "scripts" / "experiments_p2.py")
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REAL = [ROOT / "docs" / "qloo_api_reality.md", ROOT / "data" / "experiments_p2" / "summary.json"]


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test writes to tmp_path only; the real doc and recordings must not change."""
    before = [_sha(p) for p in REAL]
    doc = tmp_path / "reality.md"
    doc.write_text("# Qloo API reality\n\n## Parameters VERIFIED as effective\n", encoding="utf-8")
    monkeypatch.setattr(X, "OUT", tmp_path / "rec")
    monkeypatch.setattr(X, "DOC", doc)
    yield doc
    assert [_sha(p) for p in REAL] == before


def _id(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


def _entities(ids, scored):
    return [
        {"entity_id": _id(i), "name": f"Secret Qloo Name {i}", "query": {"affinity": 0.5} if scored else {}}
        for i in ids
    ]


def _handler(request):
    """Synthetic Qloo: lists depend on the query (minus take) so overlaps are non-trivial."""
    q = dict(request.url.params)
    path = request.url.path
    if path == "/search":
        return httpx.Response(
            200, json={"results": [{"entity_id": _id(900 + len(q["query"])), "name": q["query"]}]}
        )
    if path == "/v2/tags":
        slug = re.sub(r"\W+", "_", q["filter.query"])
        return httpx.Response(
            200, json={"results": {"tags": [{"id": f"urn:tag:genre:place:{slug}", "name": "x"}]}}
        )
    take = int(q.get("take", 10))
    scored = (
        any(k.startswith("signal.") and k != "signal.location.query" for k in q)
        or "artist" in q["filter.type"]
    )
    if "filter.results.entities" in q:
        cands = q["filter.results.entities"].split(",")
        return httpx.Response(
            200, json={"results": {"entities": _entities([int(c[-12:]) for c in cands], True)}}
        )
    key = json.dumps({k: v for k, v in sorted(q.items()) if k != "take"})
    start = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 500 * 100
    return httpx.Response(200, json={"results": {"entities": _entities(range(start, start + take), scored)}})


# --- helpers and rules ---------------------------------------------------------------------------


def test_overlap_helpers():
    a, b = [_id(i) for i in range(50)], [_id(i) for i in range(40, 90)]
    assert X.jaccard(a[:10], a[:10]) == 1.0 and X.jaccard([], a) is None
    assert X.band_overlap(a, a, 20, 50) == 1.0
    assert X.band_overlap(a, b, 0, 10) == 0.0
    assert X.band_overlap(a[:15], b, 20, 50) is None  # list doesn't reach the band
    p = X.pairwise({"x": a[:10], "y": a[:10], "z": b[:10], "w": []})
    assert p["pairs"] == [1.0, 0.0, 0.0] and p["mean"] == 0.333 and p["max"] == 1.0


def test_stability_calc():
    s = X.stability([_id(1), _id(2), _id(3)], [_id(2), _id(1), _id(3)])
    assert s == {"jaccard": 1.0, "rank_shift": 0.67, "identical": False}
    assert X.stability([_id(1)], [_id(1)])["identical"]


def _cell(n, count, lat=500, in_pool=True, outcome="ok"):
    return {"n": n, "count": count, "latency_ms": lat, "all_in_pool": in_pool, "outcome": outcome}


def test_useful_max_rule():
    um = X.useful_max
    assert um([_cell(10, 10), _cell(25, 20), _cell(50, 40)]) == 50  # 0.8 is enough
    assert um([_cell(10, 10), _cell(25, 19), _cell(50, 39)]) == 10  # below 0.8
    assert um([_cell(10, 10), _cell(50, 50, in_pool=False)]) == 10  # ids outside the pool
    assert um([_cell(10, 10), _cell(50, 50, lat=4001)]) == 10  # too slow
    assert um([_cell(10, 10, lat=None)]) == 10  # cached: latency recorded earlier or unknown
    assert um([_cell(10, 0, outcome="qloo_error")]) is None


def _shape(j=1.0, scored=True, ok=True, p50=2.0, mx=4.0, persona=0.2):
    city = {"scored": scored, "stability": {"jaccard": j}, "persona_jaccard": persona}
    return {"cities": {"a": city, "b": city}, "all_ok": ok, "latency": {"p50_s": p50, "max_s": mx}}


def test_pick_shape_rule():
    shapes = {
        "entity": _shape(persona=0.1, p50=5.0),  # too slow at p50
        "tag": _shape(persona=0.1, mx=8.0),  # slowest call hits the deadline
        "audience": _shape(persona=0.3),
        "tag+audience": _shape(persona=0.3),  # tie on specificity: fewer signals wins
        "entity+tag": _shape(j=0.8, persona=0.0),  # unstable
        "entity+audience": _shape(ok=False, persona=0.0),
    }
    out = X.pick_shape(shapes)
    assert out["pick"] == "audience" and out["eligible"] == ["audience", "tag+audience"]
    assert X.pick_shape({"tag": _shape(scored=False)})["pick"] is None


def test_e14_verdicts():
    def cells(entity25, tag25):
        return [
            {"signal": "entity", "n": 10, "city": "a", **_cell(10, 10)},
            {"signal": "entity", "n": 25, "city": "a", **_cell(25, entity25)},
            {"signal": "tag", "n": 25, "city": "a", **_cell(25, tag25)},
            {"signal": "entity", "n": 25, "city": "b", **_cell(25, 25)},
            {"signal": "tag", "n": 25, "city": "b", **_cell(25, tag25)},
        ]

    v = X.e14_verdicts(cells(18, 5))
    assert v["verdicts"] == {"entity": "partial", "tag": "fails", "audience": "fails"}
    assert v["useful_max_entity"] == 10
    assert X.e14_verdicts(cells(25, 25))["verdicts"]["entity"] == "works"


# --- end to end, offline ----------------------------------------------------------------------------


def _args(*argv):
    return X.parser().parse_args(list(argv))


async def test_dry_run_makes_no_calls_and_writes_nothing(make_client, mock_api, tmp_path):
    route = mock_api.route().mock(side_effect=_handler)
    out = await X.run(_args("--dry-run"), make_client(qloo_mode="replay"))
    assert route.call_count == 0 and out["live_calls"] == 0
    assert {"E12", "E13", "E14", "E15"} <= set(out["plan"]) and sum(out["plan"].values()) > 100
    assert not (tmp_path / "rec").exists()


async def test_full_run_rerun_report_and_no_ids(make_client, mock_api, isolated):
    route = mock_api.route().mock(side_effect=_handler)
    c = make_client()
    first = await X.run(_args("--max-calls", "500"), c)
    assert first["stopped"] is None and first["live_calls"] > 100
    s = first["summary"]
    assert s["E13"]["pick"] and set(s["E15"]["domains"]) == {"dining", "music", "film_tv", "brands"}
    calls = route.call_count
    second = await X.run(_args("--max-calls", "0"), c)  # cache hits and pinned recordings only
    assert route.call_count == calls and second["live_calls"] == 0 and second["stopped"] is None
    assert X.render(second["summary"]) == X.render(s | {"date": second["summary"]["date"]})

    assert X.main(["--report"]) == 0
    assert route.call_count == calls  # --report makes no calls
    doc = isolated.read_text(encoding="utf-8")
    assert doc.count(X.BEGIN) == 1
    for section in (
        "E12. Answer-key depth",
        "E13. Dining query shape",
        "E14. Candidate scoring",
        "E15. Persona",
    ):
        assert section in doc
    assert not UUID.search(doc) and "Secret Qloo Name" not in doc
    assert "VERIFIED" in doc


async def test_budget_stop_keeps_recordings(make_client, mock_api, tmp_path):
    mock_api.route().mock(side_effect=_handler)
    out = await X.run(_args("--only", "E12", "--max-calls", "5"), make_client())
    assert out["stopped"] and out["live_calls"] == 5
    assert len(json.loads((tmp_path / "rec" / "E12.json").read_text(encoding="utf-8"))["cells"]) == 5


async def test_e15_without_an_e13_pick_stops_cleanly(make_client, mock_api):
    mock_api.route().mock(side_effect=_handler)
    out = await X.run(_args("--only", "E15"), make_client())
    assert out["stopped"] and "E13" in out["stopped"] and out["live_calls"] == 0


def test_report_needs_no_client(monkeypatch, isolated, tmp_path):
    summary = {
        "date": "2026-01-01",
        "cities": ["c1", "c2"],
        "E15": {
            "dining_shape": "audience",
            "domains": {
                "music": {
                    "signal": "audience",
                    "call": "viable",
                    "cities": {"c1": {"mean": 0.1, "max": 0.2}, "c2": {"mean": 0.2, "max": 0.3}},
                }
            },
        },
    }
    (tmp_path / "rec").mkdir()
    (tmp_path / "rec" / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(X, "QlooClient", None)  # a client would crash the report
    monkeypatch.setattr(X, "make_client", None)
    assert X.main(["--report"]) == 0
    doc = isolated.read_text(encoding="utf-8")
    assert "| music | audience | 0.1 / 0.2 | 0.2 / 0.3 | **viable** |" in doc
    assert X.main(["--report"]) == 0 and isolated.read_text(encoding="utf-8").count(X.BEGIN) == 1


# --- config ----------------------------------------------------------------------------------------

BLOCKED_PARENTS = ("communities", "political_preferences", "lifestyle_preferences_beliefs")
BLOCKED_WORDS = re.compile(
    r"religio|christian|muslim|jewish|hindu|lgbt|gay|lesbian|ethnic|race|african|asian|latin|black|white",
    re.I,
)


def test_personas_use_no_identity_audiences():
    personas = yaml.safe_load((ROOT / "config" / "experiments_p2.yaml").read_text(encoding="utf-8"))[
        "personas"
    ]
    assert len(personas) == 4 and len({p["id"] for p in personas}) == 4
    urns = [a for p in personas for a in p["audiences"]]
    assert urns and all(a.startswith("urn:audience:") for a in urns)
    assert not [a for a in urns if a.split(":")[2] in BLOCKED_PARENTS or BLOCKED_WORDS.search(a)]
    for p in personas:
        assert set(p["domains"]) == {"dining", "music", "film_tv", "brands"}
        assert all(d["seeds"] and d["tag_query"] for d in p["domains"].values())
