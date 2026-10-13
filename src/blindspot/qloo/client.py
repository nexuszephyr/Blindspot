"""Judge-safe async Qloo client.

Resolution order for every request: cache -> snapshot -> live (if allowed, under budget,
breaker closed) -> a structured `degraded` result with a human-readable reason.
Public methods never raise; they return a QlooResult labelled with `served_from`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from .breaker import CircuitBreaker
from .budget import BudgetManager, Caps
from .cache import ResponseCache, cache_key, normalize_params
from .calllog import CallLog
from .context import RequestContext
from .db import Database
from .errors import (
    BreakerOpen,
    BudgetExceeded,
    InvalidQuery,
    QlooAuthError,
    QlooBadRequest,
    QlooError,
    QlooRateLimited,
    QlooServerError,
)
from .models import SEARCH_TYPES, Entity, InsightsQuery, QlooResult, extract_items
from .ratelimit import TokenBucket
from .redact import redact, register_secret
from .snapshot import Snapshot

log = logging.getLogger("blindspot.qloo")

# Only documented read endpoints may be called; anything else is rejected locally.
ALLOWED_PATHS = {
    "/search", "/entities", "/v2/insights", "/v2/tags", "/v2/tags/types",
    "/v2/audiences", "/v2/audiences/types",
}
MAX_PARAMS = 40
MAX_RETRY_AFTER_S = 20.0  # longer waits are not worth holding a user request for
# Response headers worth recording (never request headers, which carry the key).
RATE_HEADER_RE = re.compile(r"(?i)ratelimit|rate-limit|retry-after|x-request-id|quota")


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _error_message(resp: httpx.Response) -> str:
    try:
        b = resp.json()
        if isinstance(b, dict):
            for k in ("message", "error", "reason", "detail"):
                if isinstance(b.get(k), str):
                    return b[k][:300]
                if isinstance(b.get(k), dict) and isinstance(b[k].get("message"), str):
                    return b[k]["message"][:300]
    except ValueError:
        pass
    return resp.text[:300]


class QlooClient:
    def __init__(
        self,
        settings: Settings,
        *,
        db: Database | None = None,
        snapshot: Snapshot | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        clock=time.time,
    ):
        self.s = settings
        register_secret(settings.qloo_api_key.get_secret_value())
        self.db = db or Database(settings.qloo_cache_path)
        self.cache = ResponseCache(self.db, settings.qloo_cache_ttl_days)
        self.calls = CallLog(self.db)
        self.budget = BudgetManager(self.db, Caps.from_settings(settings), clock=clock)
        self.breaker = CircuitBreaker(settings.qloo_breaker_threshold, settings.qloo_breaker_cooldown_s)
        self.bucket = TokenBucket(settings.qloo_rps)
        self.sem = asyncio.Semaphore(max(1, settings.qloo_max_concurrency))
        self.snapshot = snapshot if snapshot is not None else Snapshot.load(settings.qloo_snapshot_path)
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._sleep = sleep
        self._inflight: dict[str, asyncio.Future] = {}
        self.last_rate_headers: dict[str, str] = {}
        self.seeded = 0

    # ------------------------------------------------------------------ lifecycle
    def startup(self) -> int:
        """Seed an empty cache from the snapshot (free hosts often have ephemeral disks)."""
        self.seeded = self.snapshot.seed_cache(self.cache, only_if_empty=True)
        if self.seeded:
            log.info("seeded cache with %d snapshot entries", self.seeded)
        return self.seeded

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.s.qloo_base_url,
                headers={"X-Api-Key": self.s.qloo_api_key.get_secret_value(),
                         "Accept": "application/json",
                         "User-Agent": "blindspot/0.1 (+qloo-hackathon)"},
                timeout=httpx.Timeout(self.s.qloo_timeout_s, connect=5.0),
                transport=self._transport,
                follow_redirects=False,
            )
        return self._http

    @property
    def mode(self) -> str:
        return self.s.effective_mode

    # ------------------------------------------------------------------ validation
    def _validate(self, path: str, params: Mapping[str, Any]) -> dict[str, str]:
        path = "/" + path.strip("/")
        if path not in ALLOWED_PATHS:
            raise InvalidQuery(f"endpoint {path} is not allowed")
        norm = normalize_params(params)
        if len(norm) > MAX_PARAMS:
            raise InvalidQuery("too many parameters")
        total = 0
        for k, v in norm.items():
            if len(k) > 80 or len(v) > self.s.qloo_max_param_chars:
                raise InvalidQuery(f"parameter {k[:40]} is too long")
            total += len(k) + len(v)
        if total > self.s.qloo_max_param_chars * 2:
            raise InvalidQuery("query is too large")
        if "take" in norm:
            try:
                norm["take"] = str(min(max(int(norm["take"]), 1), self.s.qloo_max_take))
            except ValueError as exc:
                raise InvalidQuery("take must be an integer") from exc
        return norm

    # ------------------------------------------------------------------ core
    async def request(
        self,
        path: str,
        params: Mapping[str, Any] | None,
        ctx: RequestContext,
        *,
        bypass_cache: bool = False,
    ) -> QlooResult:
        endpoint = "/" + path.strip("/")
        params = dict(params or {})
        key = cache_key("GET", endpoint, params)

        def finish(res: QlooResult, *, error: str | None = None, log_params=None) -> QlooResult:
            res.call_id = self.calls.record(
                ctx, endpoint, log_params if log_params is not None else params, res.served_from,
                status=res.status, latency_ms=res.latency_ms, result_count=res.count,
                error=error if error is not None else (res.reason if not res.ok else None),
                cache_key=key,
            )
            return res

        def degraded(reason: str, *, status: int | None = None) -> QlooResult:
            return QlooResult(served_from="degraded", ok=False, endpoint=endpoint,
                              status=status, reason=reason, cache_key=key)

        try:
            # Kill switches: ops-level first, then this request only.
            if not self.s.qloo_enabled:
                return finish(QlooResult(served_from="disabled", ok=False, endpoint=endpoint,
                                         reason="Qloo is switched off on this server",
                                         cache_key=key))
            if not ctx.qloo_enabled:
                return finish(QlooResult(served_from="disabled", ok=False, endpoint=endpoint,
                                         reason="Qloo grounding is off for this request",
                                         cache_key=key))
            try:
                norm = self._validate(endpoint, params)
            except InvalidQuery as exc:
                return finish(degraded(f"invalid query: {exc.reason}"))
            key = cache_key("GET", endpoint, norm)

            if not bypass_cache:
                hit = self.cache.get(key)
                if hit is not None:
                    src = "snapshot" if hit.source == "snapshot" else "cache"
                    return finish(self._ok(src, endpoint, key, hit.status, hit.body,
                                           fetched_at=hit.fetched_at), log_params=norm)
                snap = self.snapshot.get(key)
                if snap is not None:
                    return finish(self._ok("snapshot", endpoint, key, int(snap.get("status", 200)),
                                           snap["body"], fetched_at=snap.get("fetched_at")),
                                  log_params=norm)

            if self.mode == "replay":
                return finish(degraded(
                    "offline replay mode: this query is not in the bundled snapshot or cache. "
                    "Use the hosted demo, or add your own QLOO_API_KEY to run it live."),
                    log_params=norm)
            if not ctx.allow_live:
                return finish(degraded("live Qloo calls are not allowed for this request"),
                              log_params=norm)

            res, err = await self._coalesced_live(key, endpoint, norm, ctx)
            return finish(res, error=err, log_params=norm)
        except Exception as exc:  # last line of defence: never surface a crash
            log.exception("unexpected error in Qloo request: %s", redact(type(exc).__name__))
            try:
                return finish(degraded("internal error while contacting Qloo"))
            except Exception:
                return degraded("internal error while contacting Qloo")

    async def _coalesced_live(self, key, endpoint, norm, ctx) -> tuple[QlooResult, str | None]:
        """Identical concurrent queries share one live call (saves quota under click floods)."""
        fut = self._inflight.get(key)
        if fut is not None:
            res, err = await asyncio.shield(fut)
            return res.model_copy(update={"reason": "shared a concurrent identical live call"}), err
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            out = await self._live(key, endpoint, norm, ctx)
            fut.set_result(out)
            return out
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
                fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(key, None)

    async def _live(self, key, endpoint, norm, ctx) -> tuple[QlooResult, str | None]:
        t0 = time.perf_counter()

        def degraded(reason, exc=None, status=None):
            return QlooResult(
                served_from="degraded", ok=False, endpoint=endpoint,
                status=status if status is not None else getattr(exc, "status", None),
                reason=reason, cache_key=key, latency_ms=(time.perf_counter() - t0) * 1000,
                error_body=getattr(exc, "body", None), rate_headers=dict(self.last_rate_headers))
        try:
            status, body = await self._send(endpoint, norm, ctx)
        except BudgetExceeded as exc:
            return degraded(f"live budget reached ({exc.scope}): {exc.reason}"), exc.reason
        except BreakerOpen as exc:
            return degraded(exc.reason), exc.reason
        except QlooAuthError as exc:
            return degraded("Qloo rejected the server's API key", exc), redact(exc.reason)
        except QlooRateLimited as exc:
            return degraded("Qloo is rate-limiting requests; try again shortly", exc, 429), exc.reason
        except QlooBadRequest as exc:
            return degraded(f"Qloo rejected the query: {redact(exc.reason)}", exc), exc.reason
        except QlooError as exc:
            return degraded("Qloo is unavailable right now", exc), redact(exc.reason)
        latency = (time.perf_counter() - t0) * 1000
        self.cache.put(key, "GET", endpoint, norm, status, body)
        if self.mode == "record":
            self._record_raw(endpoint, norm, status, body, key)
        res = self._ok("live", endpoint, key, status, body, latency_ms=latency, fetched_at=time.time())
        res.rate_headers = dict(self.last_rate_headers)
        return res, None

    async def _send(self, endpoint: str, norm: dict[str, str], ctx: RequestContext):
        last: QlooError | None = None
        for attempt in range(1, self.s.qloo_max_attempts + 1):
            if not self.breaker.allow():
                raise BreakerOpen(
                    f"Qloo is rate-limiting; pausing live calls for {self.breaker.retry_in():.0f}s")
            try:
                self.budget.reserve(ctx)
            except BudgetExceeded:
                self.breaker.record_failure_other()
                raise
            await self.bucket.acquire()
            retry_after: float | None = None
            try:
                async with self.sem:
                    resp = await self._client().get(endpoint, params=norm)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.breaker.record_failure_other()
                last = QlooServerError(f"network error: {type(exc).__name__}")
            else:
                st = resp.status_code
                self.last_rate_headers = {k: v for k, v in resp.headers.items() if RATE_HEADER_RE.search(k)}
                if 200 <= st < 300:
                    self.breaker.record_success()
                    try:
                        return st, resp.json()
                    except ValueError as exc:
                        raise QlooServerError("Qloo returned non-JSON", status=st) from exc
                msg = redact(_error_message(resp))
                ebody = redact(resp.text[:1000])
                if st == 429:
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                    self.breaker.record_429(retry_after)
                    last = QlooRateLimited(msg or "rate limited", retry_after=retry_after,
                                           status=st, body=ebody)
                    if retry_after is not None and retry_after > MAX_RETRY_AFTER_S:
                        raise last
                elif st >= 500:
                    self.breaker.record_failure_other()
                    last = QlooServerError(msg or "server error", status=st, body=ebody)
                else:
                    self.breaker.record_failure_other()
                    if st == 401:
                        raise QlooAuthError(msg or "unauthorized", status=st, body=ebody)
                    raise QlooBadRequest(msg or "bad request", status=st, body=ebody)
            if attempt < self.s.qloo_max_attempts:
                backoff = min(8.0, 0.5 * 2 ** (attempt - 1)) * (0.5 + random.random())  # noqa: S311
                await self._sleep(max(backoff, retry_after or 0.0))
        assert last is not None
        raise last

    def _ok(self, served_from, endpoint, key, status, body, *, fetched_at=None,
            latency_ms=None) -> QlooResult:
        return QlooResult(
            served_from=served_from, ok=True, endpoint=endpoint, status=status, cache_key=key,
            body=body, fetched_at=fetched_at, latency_ms=latency_ms,
            entities=[Entity.parse(x) for x in extract_items(body)],
        )

    def _record_raw(self, endpoint, norm, status, body, key) -> None:
        d = Path(self.s.qloo_raw_dir)
        d.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%dT%H%M%S')}_{endpoint.strip('/').replace('/', '_')}_{key[:10]}.json"
        doc = {"endpoint": endpoint, "params": norm, "status": status, "body": body}
        (d / name).write_text(redact(json.dumps(doc, ensure_ascii=False, indent=1)), encoding="utf-8")

    # ------------------------------------------------------------------ typed API
    async def search_entities(self, query: str, ctx: RequestContext, *, types: list[str] | None = None,
                              take: int = 5, **extra: Any) -> QlooResult:
        if types and any(t not in SEARCH_TYPES for t in types):
            return await self._invalid("/search", ctx, "unsupported search type")
        return await self.request("/search", {"query": query, "types": types, "take": take, **extra}, ctx)

    async def get_entities(self, entity_ids: list[str], ctx: RequestContext) -> QlooResult:
        return await self.request("/entities", {"entity_ids": entity_ids}, ctx)

    async def search_tags(self, ctx: RequestContext, *, query: str | None = None,
                          tag_types: list[str] | None = None, parent_types: list[str] | None = None,
                          take: int = 10, **extra: Any) -> QlooResult:
        return await self.request("/v2/tags", {
            "filter.query": query, "filter.tag.types": tag_types,
            "filter.parents.types": parent_types, "take": take, **extra}, ctx)

    async def insights(self, q: InsightsQuery | Mapping[str, Any], ctx: RequestContext) -> QlooResult:
        try:
            query = q if isinstance(q, InsightsQuery) else InsightsQuery.model_validate(dict(q))
        except ValueError as exc:
            return await self._invalid("/v2/insights", ctx, redact(str(exc).splitlines()[0]))
        return await self.request("/v2/insights", query.to_params(), ctx)

    async def score_candidates(self, filter_type: str, candidate_ids: list[str], ctx: RequestContext,
                               **signals: Any) -> QlooResult:
        """Rank a fixed candidate list against a signal via filter.results.entities.
        Documented by Qloo ("often used to assess affinity"); UNVERIFIED until recon E7."""
        params = {"filter.type": filter_type, "filter.results.entities": candidate_ids,
                  "take": min(len(candidate_ids), self.s.qloo_max_take), **signals}
        return await self.insights(params, ctx)

    async def list_audiences(self, ctx: RequestContext, *, parent_types: list[str] | None = None,
                             audience_types: list[str] | None = None, take: int = 20) -> QlooResult:
        """GET /v2/audiences (documented; UNVERIFIED on the hackathon host until recon E5)."""
        return await self.request("/v2/audiences", {
            "filter.parents.types": parent_types, "filter.audience.types": audience_types,
            "take": take}, ctx)

    async def list_audience_types(self, ctx: RequestContext, take: int = 50) -> QlooResult:
        return await self.request("/v2/audiences/types", {"take": take}, ctx)

    async def _invalid(self, endpoint: str, ctx: RequestContext, reason: str) -> QlooResult:
        key = cache_key("GET", endpoint, {})
        res = QlooResult(served_from="degraded", ok=False, endpoint=endpoint,
                         reason=f"invalid query: {reason}", cache_key=key)
        res.call_id = self.calls.record(ctx, endpoint, {}, "degraded", error=res.reason, cache_key=key)
        return res

    # ------------------------------------------------------------------ status
    def status(self, ctx: RequestContext | None = None) -> dict[str, Any]:
        """Safe to expose publicly: contains no secrets."""
        return {
            "mode": self.mode,
            "configured_mode": self.s.qloo_mode,
            "qloo_enabled": self.s.qloo_enabled,
            "key_configured": self.s.has_key,
            # salted hash of the caller's IP: lets you check proxy handling without exposing IPs
            "your_client_id": ctx.client_id if ctx else None,
            "budget": self.budget.remaining(ctx.client_id if ctx else None),
            "cache_entries": self.cache.size(),
            "snapshot_loaded": self.snapshot.loaded,
            "snapshot_entries": len(self.snapshot.entries),
            "seeded_from_snapshot": self.seeded,
            "breaker": {"state": self.breaker.state, "trips": self.breaker.trips,
                        "retry_in_s": round(self.breaker.retry_in(), 1)},
        }
