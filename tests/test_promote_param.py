"""scripts/promote_param.py (#12): verdict logic, thresholds, idempotent evidence rows, --apply,
--dry-run, and that no Qloo names or result ids reach the evidence file. Offline (respx)."""

import importlib
import importlib.util
import shutil
import sys
from types import SimpleNamespace as NS

import httpx
import pytest

from blindspot.qloo.errors import QlooParamError
from tests.test_catalog import ROOT

# A real module (not runpy's globals copy), so monkeypatching EVIDENCE/PARAMS_PY reaches the functions.
_spec = importlib.util.spec_from_file_location("promote_param", ROOT / "scripts" / "promote_param.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def _id(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


def _body(ids, scored=False):
    return {
        "success": True,
        "results": {
            "entities": [
                {
                    "name": f"Secret Qloo Name {i}",
                    "entity_id": _id(i),
                    "type": "urn:entity",
                    "query": {"affinity": 0.5} if scored else {},
                }
                for i in ids
            ]
        },
    }


def _handler(request):
    q = dict(request.url.params)
    if "filter.popularity.max" in q:
        return httpx.Response(200, json=_body(range(5, 15), scored=True))
    if "filter.exclude.entities" in q:
        gone = set(q["filter.exclude.entities"].split(","))
        return httpx.Response(200, json=_body([i for i in range(20) if _id(i) not in gone][:10]))
    if "bias.trends" in q:
        return httpx.Response(200, json=_body(range(10)))  # silently ignored
    return httpx.Response(200, json=_body(range(10)))


@pytest.fixture
def files(tmp_path, monkeypatch):
    ev = tmp_path / "param_evidence.md"
    doc = (ROOT / "docs" / "param_evidence.md").read_text(encoding="utf-8").splitlines()
    ev.write_text("\n".join(x for x in doc if not x.startswith("| ev_")) + "\n", encoding="utf-8")  # no rows
    pkg = tmp_path / "fakeqloo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in ("params.py", "errors.py"):
        shutil.copy(ROOT / "src" / "blindspot" / "qloo" / name, pkg / name)
    monkeypatch.setattr(P, "EVIDENCE", ev)
    monkeypatch.setattr(P, "PARAMS_PY", pkg / "params.py")
    return ev, pkg


def _rows(ev):
    return [line for line in ev.read_text(encoding="utf-8").splitlines() if line.startswith("| ev_")]


async def _run(client, *argv):
    return await P.run(P.parser().parse_args(list(argv)), client)


def _res(ids, outcome="ok", unverified=(), scored=False):
    ents = [NS(entity_id=_id(i), affinity=0.5 if scored else None) for i in ids]
    return NS(
        entities=ents,
        count=len(ents),
        served_from="cache",
        outcome=outcome,
        reason="timed out" if outcome != "ok" else None,
        unverified_params=list(unverified),
        latency_ms=1.0,
    )


# --- thresholds and verdicts -------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant, effect",
    [
        (list(range(10)), "identical"),
        (list(range(9)), "changed"),  # Jaccard 0.9: exactly on the threshold
        ([1, 0, *range(2, 10)], "small"),  # adjacent swap: rank shift 0.2
        ([5, 1, 2, 3, 4, 0, 6, 7, 8, 9], "changed"),  # rank shift exactly 1.0
        ([], "changed"),  # empty variant is a measured change
    ],
)
def test_measure_threshold_edges(variant, effect):
    cell = P.measure("bias.trends", _res(range(10)), _res(variant, "ok" if variant else "empty"), None)
    assert cell["effect"] == effect


def test_measure_errors_and_guards():
    m = P.measure
    assert m("x", _res([], "qloo_error"), _res(range(10)), None)["effect"] == "error"
    assert m("x", _res(range(10)), _res([], "qloo_error"), None)["effect"] == "error"
    other = m("x", _res(range(10)), _res(range(5), unverified=["x", "filter.bogus"]), None)
    assert other["effect"] == "error" and "filter.bogus" in other["reason"]
    leak = m("filter.exclude.entities", _res(range(10)), _res(range(1, 11)), [_id(1)])
    assert leak["effect"] == "small" and leak["hits"] == "1/1"
    assert P.rank_shift([_id(1)], [_id(2)]) is None


@pytest.mark.parametrize(
    "effects, tiers, verdict",
    [
        (["changed", "identical"], [1, 2], "VERIFIED"),
        (["changed", "error"], [1, 2], "INCONCLUSIVE"),
        (["identical", "identical"], [1, 2], "INEFFECTIVE"),
        (["identical", "identical"], [1, 1], "INCONCLUSIVE"),  # needs a tier-1 and a tier-2 city
        (["identical"], [1], "INCONCLUSIVE"),
        (["small", "identical"], [1, 2], "INCONCLUSIVE"),
    ],
)
def test_verdict_rules(effects, tiers, verdict):
    cells = [{"effect": e, "reason": "r" if e in ("error", "small") else ""} for e in effects]
    assert P.verdict(cells, tiers)[0] == verdict


# --- end to end with a mocked Qloo -------------------------------------------------------------


async def test_rerun_is_all_cache_hits_and_adds_no_row(make_client, mock_api, files):
    ev, _ = files
    route = mock_api.get("/v2/insights").mock(side_effect=_handler)
    c = make_client()
    first = await _run(c, "movie", "filter.popularity.max", "0.5")
    assert first["verdict"] == "VERIFIED" and first["live_calls"] == 4 and len(_rows(ev)) == 1
    calls = route.call_count
    second = await _run(c, "movie", "filter.popularity.max", "0.5")
    assert route.call_count == calls and second["live_calls"] == 0
    assert second["row"] == "unchanged" and len(_rows(ev)) == 1
    assert all(cell["served_from"] == "cache/cache" for cell in second["cells"])


async def test_ineffective_and_exclude_rows(make_client, mock_api, files):
    ev, _ = files
    mock_api.get("/v2/insights").mock(side_effect=_handler)
    c = make_client()
    assert (await _run(c, "movie", "bias.trends", "high"))["verdict"] == "INEFFECTIVE"
    out = await _run(c, "artist", "filter.exclude.entities", "@std:3")
    assert out["verdict"] == "VERIFIED" and all(cell["hits"] == "0/3" for cell in out["cells"])
    assert len(_rows(ev)) == 2


async def test_no_names_or_result_ids_in_evidence(make_client, mock_api, files):
    ev, _ = files
    mock_api.get("/v2/insights").mock(side_effect=_handler)
    c = make_client()
    await _run(c, "brand", "filter.exclude.entities", "@std:3")
    await _run(c, "brand", "filter.results.entities", "@std:5")
    text = ev.read_text(encoding="utf-8")
    assert "Secret Qloo Name" not in text and "00000000-0000-4000-8000" not in text
    assert "@std:3" in text


async def test_dry_run_makes_no_calls_and_writes_nothing(make_client, mock_api, files):
    ev, _ = files
    route = mock_api.get("/v2/insights").mock(side_effect=_handler)
    before = ev.read_text(encoding="utf-8")
    out = await _run(
        make_client(qloo_mode="replay"), "place", "filter.exclude.entities", "@std:3", "--dry-run"
    )
    assert route.call_count == 0 and out["live_calls"] == 0
    assert out["plan"] and all("LIVE" in line for line in out["plan"])
    assert ev.read_text(encoding="utf-8") == before


async def test_baseline_is_strict_before_any_call(make_client, mock_api, files):
    route = mock_api.get("/v2/insights").mock(side_effect=_handler)
    with pytest.raises(QlooParamError):
        await _run(make_client(), "movie", "bias.trends", "high", "--baseline", "filter.bogus=1")
    assert route.call_count == 0


# --- --apply -----------------------------------------------------------------------------------


def _load(pkg):
    sys.modules.pop("fakeqloo.params", None)
    sys.modules.pop("fakeqloo", None)
    sys.path.insert(0, str(pkg.parent))
    try:
        return importlib.import_module("fakeqloo.params")
    finally:
        sys.path.remove(str(pkg.parent))


def test_apply_promotes_once_and_strict_still_guards(files):
    _, pkg = files
    apply = P.apply
    assert apply("urn:entity:book", "filter.popularity.max", "0.5", "VERIFIED", "ev_00000001") == "applied"
    assert apply("urn:entity:book", "filter.popularity.max", "0.5", "VERIFIED", "ev_00000001") == (
        "already verified"
    )
    src = (pkg / "params.py").read_text(encoding="utf-8")
    assert src.count('"filter.popularity.max", "ev_00000001"') == 1
    mod = _load(pkg)
    assert "filter.popularity.max" in mod.VERIFIED_INSIGHTS["urn:entity:book"]
    q = {"filter.type": "urn:entity:book", "filter.popularity.max": 0.5}
    assert mod.check_params("/v2/insights", q, strict=True) == []
    with pytest.raises(mod.QlooParamError):
        mod.check_params("/v2/insights", {**q, "filter.popularity.min": 0.1}, strict=True)
    with pytest.raises(mod.QlooParamError):  # promotion is per type
        mod.check_params("/v2/insights", {**q, "filter.type": "urn:entity:podcast"}, strict=True)


def test_apply_ineffective_lists_but_never_removes(files):
    _, pkg = files
    apply = P.apply
    assert apply("urn:entity:book", "bias.trends", "high", "INEFFECTIVE", "ev_00000002") == "applied"
    assert apply("urn:entity:book", "bias.trends", "high", "INEFFECTIVE", "ev_00000002") == "already listed"
    kept = apply("urn:entity:place", "bias.trends", "high", "INEFFECTIVE", "ev_00000003")
    assert "not removed" in kept
    assert apply("urn:entity:movie", "x", "1", "INCONCLUSIVE", "ev_00000004") == "nothing to apply"
    src = (pkg / "params.py").read_text(encoding="utf-8")
    assert src.count("#   urn:entity:book bias.trends=high (docs/param_evidence.md ev_00000002)") == 1
    mod = _load(pkg)
    assert "bias.trends" in mod.VERIFIED_INSIGHTS["urn:entity:place"]
    assert "bias.trends" not in mod.VERIFIED_INSIGHTS["urn:entity:book"]


def test_doc_states_the_script_thresholds():
    doc = (ROOT / "docs" / "param_evidence.md").read_text(encoding="utf-8")
    assert f"≤ **{P.JACCARD_CHANGED}**" in doc and f"≥ **{P.RANK_SHIFT_CHANGED}**" in doc
