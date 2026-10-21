"""`GET /api/coverage?location=` (#24): does Qloo have enough data to audit a location?

Any location string works, not only configured cities. The string is untrusted: length-limited,
cleaned of control characters, and only ever sent as one query-string value through `QlooClient`,
which applies the cache, the caller's budgets, the breaker and this request's deadline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request

from ..catalog import Domain, load_domains
from ..groundtruth.coverage import LocationError, coverage_probe
from .routes import _ctx
from .scenario_routes import envelope

router = APIRouter()
# Generous before cleaning (combining marks, stray whitespace); clean_location caps the result.
RAW_LOCATION_CHARS = 200


def _domains(request: Request) -> list[Domain]:
    state = request.app.state
    if getattr(state, "domains", None) is None:
        state.domains = load_domains(Path(state.settings.config_dir))
    return state.domains


@router.get("/api/coverage")
async def get_coverage(
    request: Request,
    location: Annotated[str, Query(min_length=1, max_length=RAW_LOCATION_CHARS)],
    qloo: Annotated[bool, Query(description="per-request kill switch")] = True,
):
    """Verdict per domain (`rich | thin | sparse | empty | unknown`) with a confidence and a note.
    `unknown` means Qloo gave no usable answer (timeout, budget, ignored location), never "no data"."""
    ctx = _ctx(request, qloo=qloo)
    client = request.app.state.client
    try:
        report = await coverage_probe(location, _domains(request), client, ctx)
    except LocationError as exc:
        return envelope(ctx, None, served_from="degraded", degraded_reason=str(exc), status=400)
    served = report.served_from
    synthetic = client.snapshot.synthetic and any(
        c.served_from == "snapshot" for d in report.domains for c in d.calls
    )
    return envelope(
        ctx,
        report.model_dump(mode="json"),
        served_from=served,
        synthetic=synthetic,
        degraded_reason=report.degraded_reason,
        # 503 only when no domain got an answer: "Qloo can't place this" (a 400) is an answer
        status=503 if served == "degraded" and all(d.verdict == "unknown" for d in report.domains) else 200,
    )
