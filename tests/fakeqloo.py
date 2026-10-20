"""A fake Qloo server for black-box tests (#17). Synthetic bodies only.

`FakeQloo` is an in-process ASGI app that answers like the hackathon host would in each failure
mode. `FakeTransport` puts it behind the real `QlooClient` the way a network would: it applies the
client's read timeout (raising `httpx.ReadTimeout`) and can reset the connection (`httpx.ReadError`),
neither of which an ASGI app can do on its own. Every request that reaches the "network" is
recorded, including its auth header and full URL, so tests can check header-only auth and compare
budget counters with the number of upstream requests exactly.

Behaviours: ok, empty, bad_request (400 `{"errors": [...]}`), unauthorized (401), not_found (404),
rate_limited (429, no Retry-After), rate_limited:<seconds> (429 with Retry-After), server_500,
server_502, server_503, slow (sleeps `slow_s`, then ok), malformed (200, invalid JSON), html (200,
HTML body), oversized (200, body over the client's MAX_BODY_BYTES), reset (connection reset).

A misspelled behaviour must never pass silently: the client's catch-all would turn a fake's error
into an ordinary `degraded` result. So behaviours are validated when they are set, and anything
unexpected the fake meets at request time is recorded in `errors`, which the harness asserts empty.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import httpx

from blindspot.qloo.client import MAX_BODY_BYTES

SEARCH_RESULT = {
    "results": [
        {
            "entity_id": "00000000-0000-4000-8000-0000000000a1",
            "name": "Synthetic Result One",
            "types": ["urn:entity:artist"],
            "popularity": 0.5,
        }
    ]
}
SIMPLE = {
    "ok", "empty", "bad_request", "unauthorized", "not_found", "rate_limited",
    "server_500", "server_502", "server_503", "slow", "malformed", "html", "oversized", "reset",
}  # fmt: skip
RETRY_AFTER = re.compile(r"^rate_limited:\d+$")
BEHAVIOUR_HEADER = "x-fake-qloo-behaviour"  # added by the transport after recording the request


def check_behaviour(b: str) -> str:
    if b not in SIMPLE and not RETRY_AFTER.match(b):
        raise ValueError(f"unknown fake Qloo behaviour {b!r}; known: {sorted(SIMPLE)} or rate_limited:<s>")
    return b


@dataclass
class Seen:
    path: str
    params: dict[str, str]
    url: str  # full URL as sent: a key here would be a leak
    key_header: str | None  # the X-Api-Key value as sent


class FakeQloo:
    def __init__(self, behaviour: str = "ok", *, slow_s: float = 4.0):
        self._behaviour = check_behaviour(behaviour)
        self.slow_s = slow_s
        self._queue: list[str] = []
        self.seen: list[Seen] = []
        self.errors: list[str] = []  # anything unexpected at request time; must stay empty

    @property
    def behaviour(self) -> str:
        return self._behaviour

    @behaviour.setter
    def behaviour(self, b: str) -> None:
        self._behaviour = check_behaviour(b)

    def then(self, *behaviours: str) -> None:
        """Answer the next requests with these behaviours, in order, before the default."""
        self._queue += [check_behaviour(b) for b in behaviours]

    def next_behaviour(self) -> str:
        return self._queue.pop(0) if self._queue else self._behaviour

    def count(self, query: str | None = None) -> int:
        return sum(1 for s in self.seen if query is None or s.params.get("query") == query)

    async def __call__(self, scope, receive, send):  # ASGI
        headers = dict(scope["headers"])
        b = headers.get(BEHAVIOUR_HEADER.encode(), b"").decode()
        try:
            status, body, out_headers = await self._answer(check_behaviour(b))
        except Exception as exc:  # recorded for the harness to fail on, then a 500 like a broken server
            self.errors.append(f"{type(exc).__name__}: {exc}")
            status, body, out_headers = 500, b'{"message": "fake failed"}', {}
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in out_headers.items()],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _answer(self, b: str) -> tuple[int, bytes, dict[str, str]]:
        ok = (200, json.dumps(SEARCH_RESULT).encode(), {"content-type": "application/json"})
        if b == "ok":
            return ok
        if b == "slow":
            await asyncio.sleep(self.slow_s)
            return ok
        if b.startswith("rate_limited"):
            extra = {"retry-after": b.split(":", 1)[1]} if ":" in b else {}
            return 429, b'{"message": "API rate limit exceeded"}', extra
        if b.startswith("server_"):
            return int(b.removeprefix("server_")), b'{"message": "synthetic upstream failure"}', {}
        return {
            "empty": (200, b'{"results": []}', {}),
            "bad_request": (400, b'{"errors": [{"message": "synthetic bad parameter"}]}', {}),
            "unauthorized": (401, b'{"message": "No API key found in request"}', {}),
            "not_found": (404, b'{"message": "no route"}', {}),
            "malformed": (200, b'{"results": [ {"entity_id": ', {}),
            "html": (200, b"<html><body>Bad gateway</body></html>", {"content-type": "text/html"}),
            "oversized": (200, b'{"results": [], "pad": "' + b"x" * (MAX_BODY_BYTES + 1) + b'"}', {}),
        }[b]


class FakeTransport(httpx.AsyncBaseTransport):
    """The network between QlooClient and FakeQloo: records, times out and resets like a socket."""

    def __init__(self, fake: FakeQloo):
        self.fake = fake
        self._asgi = httpx.ASGITransport(app=fake)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(parse_qsl(request.url.query.decode()))
        self.fake.seen.append(
            Seen(request.url.path, params, str(request.url), request.headers.get("x-api-key"))
        )
        behaviour = self.fake.next_behaviour()
        if behaviour == "reset":
            raise httpx.ReadError("connection reset by peer", request=request)
        request.headers[BEHAVIOUR_HEADER] = behaviour  # after recording: never part of what was "sent"
        timeout = (request.extensions.get("timeout") or {}).get("read")
        try:
            return await asyncio.wait_for(self._asgi.handle_async_request(request), timeout)
        except TimeoutError:
            raise httpx.ReadTimeout("read timed out", request=request) from None

    async def aclose(self) -> None:
        await self._asgi.aclose()


@dataclass
class Harness:
    """The real app and client wired to a FakeQloo; see the `harness` fixture in conftest.py."""

    fake: FakeQloo
    client: object
    http: httpx.AsyncClient
    sleeps: list[float] = field(default_factory=list)

    async def get(self, path: str, *, ip: str | None = None, **params) -> httpx.Response:
        headers = {"X-Forwarded-For": ip} if ip else {}
        return await self.http.get(path, params=params, headers=headers)

    async def json(self, path: str, *, ip: str | None = None, **params) -> dict:
        r = await self.get(path, ip=ip, **params)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.headers.get("content-type", "").startswith("application/json"), r.text[:200]
        return r.json()

    async def search(self, q: str, *, ip: str | None = None, **params) -> dict:
        return await self.json("/api/qloo/search", ip=ip, q=q, **params)

    async def status(self, *, ip: str | None = None) -> dict:
        return await self.json("/api/qloo/status", ip=ip)
