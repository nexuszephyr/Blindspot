"""Scenario and answer-key routes (#25): the exam and its answer key over HTTP.

Both read stored data only (the scenario engine is offline; answer keys come through
`get_answer_key`), so they make no Qloo calls and spend no live budget. They still carry the
request context, run under its deadline and the per-client rate limit, and return the standard
envelope. Names are returned as stored: JSON isn't HTML, and the UI escapes when it renders (D45).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from ..catalog import load_catalog
from ..groundtruth import NotBuilt, get_answer_key, open_store
from ..idlock import load_lock
from ..qloo.context import RequestContext
from ..scenarios.generator import generate
from ..scenarios.models import Scenario
from .routes import _ctx

router = APIRouter()
ScenarioId = Annotated[str, PathParam(min_length=1, max_length=100)]


def envelope(
    ctx: RequestContext,
    data: Any,
    *,
    served_from: str,
    synthetic: bool = False,
    degraded_reason: str | None = None,
    status: int = 200,
) -> JSONResponse:
    """The response envelope (architecture Section 12)."""
    body = {
        "data": data,
        "served_from": served_from,
        "synthetic": synthetic,
        "degraded_reason": degraded_reason,
        "run_id": ctx.run_id,
    }
    return JSONResponse(body, status_code=status)


async def _within_deadline(ctx: RequestContext, fn, *args):
    """Run a blocking read in a thread, bounded by the request deadline. None means it ran out."""
    remaining = ctx.remaining()
    if remaining is not None and remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=remaining)
    except TimeoutError:
        return None


def _exam(request: Request) -> list[Scenario]:
    """The exam, generated once per process from config/ and the id lock (deterministic, offline)."""
    state = request.app.state
    if getattr(state, "exam", None) is None:
        cfg = Path(state.settings.config_dir)
        state.exam = generate(load_catalog(cfg), load_lock(cfg)).scenarios
    return state.exam


def _summary(s: Scenario) -> dict[str, Any]:
    return {
        "id": s.id,
        "persona_id": s.persona_id,
        "city_id": s.city_id,
        "domain_id": s.domain_id,
        "probe_type": s.probe_type,
        "stated_domain": s.stated_domain,
        "pair_id": s.pair_id,
        "tags": list(s.tags),
    }


def _timed_out(ctx: RequestContext) -> JSONResponse:
    return envelope(
        ctx, None, served_from="degraded", degraded_reason="request deadline exceeded", status=503
    )


def _filtered(
    exam: list[Scenario], city: str | None, domain: str | None, probe: str | None, demo: bool | None
) -> list[Scenario]:
    return [
        s
        for s in exam
        if (city is None or s.city_id == city)
        and (domain is None or s.domain_id == domain)
        and (probe is None or s.probe_type == probe)
        and (demo is None or ("demo" in s.tags) == demo)
    ]


@router.get("/api/scenarios")
async def list_scenarios(
    request: Request,
    city: Annotated[str | None, Query(max_length=60)] = None,
    domain: Annotated[str | None, Query(max_length=60)] = None,
    probe: Annotated[str | None, Query(max_length=60)] = None,
    demo: Annotated[bool | None, Query(description="only (true) or never (false) the demo subset")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The exam, filtered and paginated. Scenarios come from config, not from Qloo results."""
    ctx = _ctx(request, qloo=False)
    exam = await _within_deadline(ctx, _exam, request)
    if exam is None:
        return _timed_out(ctx)
    hits = _filtered(exam, city, domain, probe, demo)
    page = [_summary(s) for s in hits[offset : offset + limit]]
    data = {"items": page, "total": len(hits), "limit": limit, "offset": offset}
    # "disabled": no Qloo result is involved, the exam is generated offline from config
    return envelope(ctx, data, served_from="disabled")


@router.get("/api/scenarios/{scenario_id}")
async def get_scenario(request: Request, scenario_id: ScenarioId):
    """One scenario with its prompts and query specs."""
    ctx = _ctx(request, qloo=False)
    exam = await _within_deadline(ctx, _exam, request)
    if exam is None:
        return _timed_out(ctx)
    found = next((s for s in exam if s.id == scenario_id), None)
    if found is None:
        return envelope(ctx, None, served_from="degraded", degraded_reason="unknown scenario", status=404)
    return envelope(ctx, found.model_dump(mode="json"), served_from="disabled")


def _read_key(path: Path, config_dir: Path, scenario_id: str) -> dict[str, Any]:
    """The answer key as plain data. Raises NotBuilt if there is none."""
    if not path.is_absolute():  # same rule as the build CLI: relative to the repo root
        path = config_dir.resolve().parent / path
    if not path.is_file():  # opening a Store would create an empty database
        raise NotBuilt("no answer keys built; run `blindspot groundtruth build`")
    store = open_store(path)
    try:
        key = get_answer_key(scenario_id, store)
    finally:
        store.close()
    data = key.model_dump(mode="json")
    data["global"] = data.pop("global_")
    return data


@router.get("/api/groundtruth/{scenario_id}")
async def get_groundtruth(request: Request, scenario_id: ScenarioId):
    """The stored answer key for a scenario. `served_from` is `cache` or `snapshot`, never `live`;
    `baseline` says whether the famousness baseline is `global` or `city` (D43)."""
    s = request.app.state.settings
    ctx = _ctx(request, qloo=False)
    try:
        data = await _within_deadline(ctx, _read_key, s.groundtruth_path, s.config_dir, scenario_id)
    except NotBuilt:
        return envelope(ctx, None, served_from="degraded", degraded_reason="answer key not built", status=404)
    if data is None:
        return _timed_out(ctx)
    return envelope(ctx, data, served_from=data["served_from"], synthetic=s.groundtruth_synthetic)
