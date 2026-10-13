"""FastAPI app factory. Run with: uvicorn blindspot.service.app:app"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..qloo.client import QlooClient
from ..qloo.redact import install_log_redaction
from .routes import router
from .security import SECURITY_HEADERS, SlidingWindowLimiter, client_ip

log = logging.getLogger("blindspot.service")


def create_app(settings: Settings | None = None, client: QlooClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = app.state.client
        c.startup()
        log.info("blindspot started: mode=%s snapshot_entries=%d cache_entries=%d",
                 c.mode, len(c.snapshot.entries), c.cache.size())
        yield
        await c.aclose()

    app = FastAPI(title="Blindspot", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.client = client or QlooClient(settings)
    limiter = SlidingWindowLimiter(settings.http_rate_per_minute, 60.0)

    if settings.origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.origins,
                           allow_methods=["GET"], allow_headers=[], allow_credentials=False)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            ok, retry = limiter.hit(client_ip(request, settings.trusted_proxy_hops))
            if not ok:
                resp = JSONResponse({"ok": False, "served_from": "degraded",
                                     "reason": "too many requests from your address; slow down"},
                                    status_code=429, headers={"Retry-After": str(int(retry) + 1)})
                resp.headers.update(SECURITY_HEADERS)
                return resp
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            resp = JSONResponse({"ok": False, "reason": "internal error"}, status_code=500)
        resp.headers.update(SECURITY_HEADERS)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _bad_input(request: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()]
        return JSONResponse({"ok": False, "reason": "invalid input", "fields": fields},
                            status_code=422)

    app.include_router(router)
    return app


def main_app() -> FastAPI:
    """Factory for `uvicorn blindspot.service.app:main_app --factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
