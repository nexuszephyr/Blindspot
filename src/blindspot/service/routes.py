"""HTTP routes. All output is JSON; external text is returned as data, never interpreted."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Query, Request

from ..qloo.context import RequestContext, hash_client_ip
from ..qloo.models import SEARCH_TYPES
from .security import client_ip

router = APIRouter()


def _ctx(request: Request, *, qloo: bool) -> RequestContext:
    s = request.app.state.settings
    ip = client_ip(request, s.trusted_proxy_hops)
    # every web request gets an overall deadline: judges never wait on Qloo retries
    return RequestContext(
        client_id=hash_client_ip(ip, s.ip_hash_salt.get_secret_value()),
        qloo_enabled=qloo,
        deadline=time.monotonic() + s.request_deadline_s,
    )


# Static on purpose: no external text and nothing dynamic. The Phase 6 web UI (#47) replaces it.
ROOT_INFO = {
    "ok": True,
    "name": "Blindspot",
    "about": "Audits AI agents for cultural blindness using Qloo's taste graph as the answer key.",
    "phase": "2: answer key (the web UI arrives in Phase 6)",
    "links": {
        "health": "/healthz",
        "status": "/api/qloo/status",
        "search": "/api/qloo/search?q=",
    },
}


@router.api_route("/", methods=["GET", "HEAD"])
async def root():
    """What a judge sees at the bare hosted URL (#83); `/docs` stays disabled (D19)."""
    return ROOT_INFO


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz(request: Request):
    return {"ok": True, "mode": request.app.state.client.mode}


@router.get("/api/qloo/status")
async def status(request: Request):
    return request.app.state.client.status(_ctx(request, qloo=True))


@router.get("/api/qloo/search")
async def search(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    types: Annotated[str | None, Query(max_length=200)] = None,
    take: Annotated[int, Query(ge=1, le=10)] = 5,
    qloo: Annotated[bool, Query(description="per-request kill switch")] = True,
):
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    if type_list and any(t not in SEARCH_TYPES for t in type_list):
        return {"served_from": "degraded", "ok": False, "reason": "unsupported type", "results": []}
    ctx = _ctx(request, qloo=qloo)
    res = await request.app.state.client.search_entities(q, ctx, types=type_list, take=take)
    return {
        "served_from": res.served_from,
        "ok": res.ok,
        "reason": res.reason,
        "run_id": ctx.run_id,
        "call_id": res.call_id,
        "fetched_at": res.fetched_at,
        "results": res.summary(take),
    }
