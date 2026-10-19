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


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz(request: Request):
    return {"ok": True, "mode": request.app.state.client.mode}


@router.get("/api/qloo/status")
async def status(request: Request):
    out = request.app.state.client.status(_ctx(request, qloo=True))
    if (
        request.app.state.settings.debug_proxy_headers
    ):  # names, counts, shapes and salted hashes; never values
        h = request.headers
        xff = [x.strip() for x in h.get("x-forwarded-for", "").split(",") if x.strip()]
        salt = request.app.state.settings.ip_hash_salt.get_secret_value()

        def hashed(value: str) -> str:  # same HMAC and salt as client ids, 8 chars: enough to see what varies
            return hash_client_ip(value, salt)[2:10]

        out["proxy_headers"] = {
            "x_forwarded_for_entries": len(xff),
            "x_forwarded_for_private": [x.startswith(("10.", "172.", "192.168.", "127.", "fd")) for x in xff],
            "x_forwarded_for_hashes": [hashed(x) for x in xff],
            "peer_hash": hashed(client_ip(request, 0)),
            **{
                n.replace("-", "_") + "_hash": hashed(h[n])
                for n in ("cf-connecting-ip", "true-client-ip")
                if n in h
            },
            "present": sorted(
                n
                for n in (
                    "cf-connecting-ip",
                    "true-client-ip",
                    "x-real-ip",
                    "forwarded",
                    "x-forwarded-proto",
                    "x-forwarded-host",
                    "cdn-loop",
                    "via",
                    "rndr-id",
                    "cf-ray",
                    "x-render-origin-server",
                )
                if n in h
            ),
            "peer_private": client_ip(request, 0).startswith(("10.", "172.", "192.168.", "127.")),
        }
    return out


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
