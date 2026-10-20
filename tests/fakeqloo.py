"""A fake Qloo server for black-box tests (#17). Synthetic bodies only.

`FakeQloo` is an in-process ASGI app that answers like the hackathon host would in each failure
mode. `FakeTransport` puts it behind the real `QlooClient` the way a network would: it applies the
client's read timeout (raising `httpx.ReadTimeout`) and can reset the connection (`httpx.ReadError`),
neither of which an ASGI app can do on its own. Every request that reaches the "network" is
recorded, so tests can compare budget counters with the number of upstream requests exactly.

Behaviours: ok, empty, bad_request (400 `{"errors": [...]}`), unauthorized (401), not_found (404),
rate_limited (429, no Retry-After), rate_limited:<seconds> (429 with Retry-After), server_500,
server_502, server_503, slow (sleeps `slow_s`, then ok), malformed (200, invalid JSON), html (200,
HTML body), oversized (200, body over the client's MAX_BODY_BYTES), reset (connection reset).
"""

from __future__ import annotations

import asyncio
import json
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


@dataclass
class Seen:
    path: str
    params: dict[str, str]
    has_key_header: bool


@dataclass
class FakeQloo:
    behaviour: str = "ok"
    slow_s: float = 3.0
    queue: list[str] = field(default_factory=list)  # consumed first, one per request
    by_query: dict[str, str] = field(default_factory=dict)  # `query` param -> behaviour
    seen: list[Seen] = field(default_factory=list)

    def next_behaviour(self, params: dict[str, str]) -> str:
        if self.queue:
            return self.queue.pop(0)
        return self.by_query.get(params.get("query", ""), self.behaviour)

    def count(self, query: str | None = None) -> int:
        return sum(1 for s in self.seen if query is None or s.params.get("query") == query)

    async def __call__(self, scope, receive, send):  # ASGI
        assert scope["type"] == "http"
        b = scope["state"]["behaviour"]
        status, body, headers = 200, json.dumps(SEARCH_RESULT).encode(), {"content-type": "application/json"}
        if b == "slow":
            await asyncio.sleep(self.slow_s)
        elif b == "empty":
            body = b'{"results": []}'
        elif b == "bad_request":
            status, body = 400, b'{"errors": [{"message": "synthetic bad parameter"}]}'
        elif b == "unauthorized":
            status, body = 401, b'{"message": "No API key found in request"}'
        elif b == "not_found":
            status, body = 404, b'{"message": "no route"}'
        elif b.startswith("rate_limited"):
            status, body = 429, b'{"message": "API rate limit exceeded"}'
            if ":" in b:
                headers["retry-after"] = b.split(":", 1)[1]
        elif b.startswith("server_"):
            status, body = int(b.removeprefix("server_")), b'{"message": "synthetic upstream failure"}'
        elif b == "malformed":
            body = b'{"results": [ {"entity_id": '
        elif b == "html":
            body, headers = b"<html><body>Bad gateway</body></html>", {"content-type": "text/html"}
        elif b == "oversized":
            body = b'{"results": [], "pad": "' + b"x" * (MAX_BODY_BYTES + 1) + b'"}'
        elif b != "ok":
            raise AssertionError(f"unknown fake behaviour {b!r}")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            }
        )
        await send({"type": "http.response.body", "body": body})


class FakeTransport(httpx.AsyncBaseTransport):
    """The network between QlooClient and FakeQloo: records, times out and resets like a socket."""

    def __init__(self, fake: FakeQloo):
        self.fake = fake

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(parse_qsl(request.url.query.decode()))
        self.fake.seen.append(Seen(request.url.path, params, bool(request.headers.get("x-api-key"))))
        behaviour = self.fake.next_behaviour(params)
        if behaviour == "reset":
            raise httpx.ReadError("connection reset by peer", request=request)

        async def app(scope, receive, send):  # carries this request's behaviour in the ASGI state
            await self.fake({**scope, "state": {"behaviour": behaviour}}, receive, send)

        timeout = (request.extensions.get("timeout") or {}).get("read")
        try:
            return await asyncio.wait_for(httpx.ASGITransport(app=app).handle_async_request(request), timeout)
        except TimeoutError:
            raise httpx.ReadTimeout("read timed out", request=request) from None
