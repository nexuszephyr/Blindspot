"""An OpenAI-compatible chat target (#27): any endpoint with `POST {base}/chat/completions`.

Per request: `Authorization: Bearer` (the only place the key goes), temperature 0, `max_tokens` from
config, `seed` only where the target accepts it, `response_format: json_object` where it supports
it, and the target's `extra_body` (D49: Gemini needs `reasoning_effort: low`). Bounded retries on
429 and 5xx that honour `Retry-After`, a per-target timeout and a per-target concurrency limit.

`recommend()` never raises for a model or endpoint failure: every answer is a `TargetResponse`
with an outcome. Replayable answers go to `ResponseCache`, so a repeat makes no network call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..qloo.redact import redact, register_secret
from . import parse
from .cache import ResponseCache, prompt_hash
from .config import EnvLookup, TargetConfig, env_lookup
from .models import Outcome, ParseMethod, Recommendation, TargetResponse, Usage

TEMPERATURE = 0.0
SEED = 7  # fixed, sent only to targets with `seed: true`
MAX_K = 20
MAX_ATTEMPTS = 3  # per chat request: the first try plus two retries on 429 / 5xx / connection errors
MAX_RETRY_AFTER_S = 30.0  # a longer Retry-After ends the request instead of holding it
MAX_BODY_BYTES = 1024 * 1024
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_after(value: str | None) -> float | None:
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


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _usage(body: dict[str, Any]) -> Usage:
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = (
        u.get("completion_tokens_details") if isinstance(u.get("completion_tokens_details"), dict) else {}
    )
    return Usage(
        prompt_tokens=_int(u.get("prompt_tokens")),
        completion_tokens=_int(u.get("completion_tokens")),
        total_tokens=_int(u.get("total_tokens")),
        reasoning_tokens=_int(details.get("reasoning_tokens")),
        calls=1,
    )


@dataclass
class _Reply:
    """One chat completion, or why there isn't one (`failure` set)."""

    text: str = ""
    finish_reason: str | None = None
    refusal: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    failure: Outcome | None = None
    detail: str = ""


class OpenAICompatTarget:
    """A `Target` over an OpenAI-compatible chat endpoint, configured by one `TargetConfig`."""

    def __init__(
        self,
        cfg: TargetConfig,
        cache: ResponseCache | None = None,
        *,
        env: EnvLookup | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.cfg = cfg
        self.id = cfg.id
        self.cache = cache
        lookup = env or env_lookup()
        self._key = lookup(cfg.api_key_env)
        register_secret(self._key)  # scrubbed from logs, errors and every response from here on
        self.base_url = (lookup(cfg.base_url_env) or cfg.base_url_default).rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._sem = asyncio.Semaphore(cfg.max_concurrency)
        self._http: httpx.AsyncClient | None = None

    def __repr__(self) -> str:  # never the key
        return f"OpenAICompatTarget(id={self.id!r}, model={self.cfg.model!r})"

    @property
    def configured(self) -> bool:
        u = urlsplit(self.base_url)
        return bool(self._key) and u.scheme in {"http", "https"} and bool(u.hostname)

    def request_body(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self.cfg.extra_body,
            "model": self.cfg.model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": self.cfg.max_tokens,
        }
        if self.cfg.seed:
            body["seed"] = SEED
        if self.cfg.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------ recommend
    async def recommend(self, prompt: str, k: int, *, scenario_id: str | None = None) -> TargetResponse:
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be 1 to {MAX_K}, got {k}")
        messages = [
            {"role": "system", "content": parse.system_prompt(k)},
            {"role": "user", "content": prompt},
        ]
        phash = prompt_hash(messages)
        key = (self.id, self.cfg.model, phash, TEMPERATURE, self.cfg.max_tokens)
        if self.cache is not None and (hit := self.cache.get(*key)) is not None:
            return hit.model_copy(update={"served_from": "cache"})
        if not self.configured:
            return self._response(
                phash, "error", detail=f"not configured: set {self.cfg.api_key_env} (and a base URL)"
            )

        start = time.perf_counter()
        resp = await self._answer(messages, k, phash)
        resp.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        resp = self._scrubbed(resp)
        if self.cache is not None:
            self.cache.put(resp, self.cfg.model, TEMPERATURE, self.cfg.max_tokens, scenario_id)
        return resp

    async def _answer(self, messages: list[dict[str, str]], k: int, phash: str) -> TargetResponse:
        first = await self._chat(messages)
        if first.failure:
            return self._response(phash, first.failure, detail=first.detail, usage=first.usage)

        def done(outcome: Outcome, items=(), method: ParseMethod = "none", detail="", usage=first.usage):
            return self._response(
                phash, outcome, items=list(items), method=method, detail=detail, reply=first, usage=usage
            )

        text = first.text
        if first.refusal or first.finish_reason == "content_filter":
            return done("refused", detail="the model declined")
        if not text.strip():
            if first.finish_reason == "length":
                return done("malformed", detail="truncated before any output (finish_reason: length)")
            return done("empty", detail="no text in the answer")

        items = parse.items_from_json(parse.first_json(text), k)
        if items:
            return done("ok", items, "json")
        if items == []:
            return done("empty", detail="an empty recommendation list")
        if first.finish_reason == "length":  # truncated: keep the complete items it has
            partial = parse.items_from_partial(text, k)
            if partial:
                return done("ok", partial, "partial", detail="truncated; complete items kept")
            return done("malformed", detail="truncated (finish_reason: length) with no complete item")
        if parse.looks_refused(text):
            return done("refused", detail="the model declined")

        repair = await self._chat(
            [
                *messages,
                {"role": "assistant", "content": text},
                {"role": "user", "content": parse.REPAIR_PROMPT},
            ]
        )
        usage = first.usage + repair.usage
        if not repair.failure and repair.text.strip():
            items = parse.items_from_json(parse.first_json(repair.text), k)
            if items:
                return done("ok", items, "repair", usage=usage)
        lines = parse.items_from_lines(text, k)
        if lines:
            return done("ok", lines, "lines", usage=usage)
        return done("malformed", detail="no JSON object or list lines in the answer", usage=usage)

    def _response(
        self,
        phash: str,
        outcome: Outcome,
        *,
        items: list[Recommendation] | None = None,
        method: ParseMethod = "none",
        detail: str = "",
        reply: _Reply | None = None,
        usage: Usage | None = None,
    ) -> TargetResponse:
        return TargetResponse(
            target_id=self.id,
            outcome=outcome,
            items=items or [],
            raw_text=reply.text if reply else "",
            model=(reply.model if reply and reply.model else self.cfg.model),
            usage=usage or Usage(),
            finish_reason=reply.finish_reason if reply else None,
            parse_method=method,
            prompt_hash=phash,
            detail=detail,
        )

    @staticmethod
    def _scrubbed(resp: TargetResponse) -> TargetResponse:
        """Redact every text field (a hostile endpoint may echo the key back)."""
        items = [Recommendation(name=redact(i.name), why=redact(i.why)) for i in resp.items]
        return resp.model_copy(
            update={
                "items": items,
                "raw_text": redact(resp.raw_text),
                "detail": redact(resp.detail),
                "model": redact(resp.model),
            }
        )

    # ------------------------------------------------------------------ one chat request
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(transport=self._transport, follow_redirects=False)
        return self._http

    async def _chat(self, messages: list[dict[str, str]]) -> _Reply:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        body = self.request_body(messages)
        detail = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = min(8.0, 2.0 ** (attempt - 1))
            try:
                async with self._sem:
                    r = await self._client().post(url, json=body, headers=headers, timeout=self.cfg.timeout_s)
            except httpx.TimeoutException:
                return _Reply(failure="timeout", detail=f"no answer within {self.cfg.timeout_s:g} s")
            except httpx.HTTPError as exc:  # connection refused or reset: retried
                detail = f"connection failed ({type(exc).__name__})"
            else:
                if r.status_code in RETRY_STATUSES:
                    ra = _retry_after(r.headers.get("Retry-After"))
                    detail = f"HTTP {r.status_code}" + (f", Retry-After {ra:g} s" if ra is not None else "")
                    if ra is not None and ra > MAX_RETRY_AFTER_S:
                        return _Reply(failure="error", detail=detail + " (too long to wait)")
                    wait = max(wait, ra or 0.0)
                elif r.status_code >= 400:
                    return _Reply(failure="error", detail=f"HTTP {r.status_code}: {self._error_text(r)}")
                else:
                    return self._read(r)
            if attempt < MAX_ATTEMPTS:
                await self._sleep(wait)
        return _Reply(failure="error", detail=f"{detail}; gave up after {MAX_ATTEMPTS} attempts")

    @staticmethod
    def _error_text(r: httpx.Response) -> str:
        try:
            b = r.json()
            err = b.get("error") if isinstance(b, dict) else None
            msg = err.get("message") if isinstance(err, dict) else err
            text = str(msg or b)
        except ValueError:
            text = r.text
        return parse.clean(redact(text), 200)

    def _read(self, r: httpx.Response) -> _Reply:
        if len(r.content) > MAX_BODY_BYTES:
            return _Reply(failure="error", detail="response too large")
        try:
            body = r.json()
            choice = body["choices"][0]
            msg = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return _Reply(failure="error", detail="unreadable response body")
        if not isinstance(body, dict) or not isinstance(msg, dict):
            return _Reply(failure="error", detail="unreadable response body")
        content = msg.get("content")
        refusal = msg.get("refusal")
        finish = choice.get("finish_reason")
        return _Reply(
            text=content if isinstance(content, str) else "",
            finish_reason=finish if isinstance(finish, str) else None,
            refusal=refusal if isinstance(refusal, str) else "",
            model=parse.clean(body.get("model") or "", 100),
            usage=_usage(body),
        )


__all__ = ["MAX_ATTEMPTS", "MAX_K", "SEED", "TEMPERATURE", "OpenAICompatTarget"]
