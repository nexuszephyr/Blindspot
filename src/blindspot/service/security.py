"""Client identification, per-IP HTTP rate limiting and security headers."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from starlette.requests import Request

log = logging.getLogger("blindspot.service")


def client_ip(request: Request, trusted_hops: int) -> str:
    """Requester IP. X-Forwarded-For is trusted only for the configured number of proxy hops;
    each trusted proxy appends the address it saw, so the client is `trusted_hops` from the right.
    Render needs 3: Cloudflare, Render edge, Render internal proxy (#80). Too few entries -> peer.
    If Cloudflare's cf-connecting-ip is present and disagrees, the hop count is wrong: log it (no
    addresses) so a misconfigured deploy is visible; the configured entry still wins."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    if len(hops) < trusted_hops:
        return peer
    chosen = hops[-trusted_hops]
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf and cf != chosen:
        log.warning(
            "client_ip: cf-connecting-ip disagrees with X-Forwarded-For[-%d]; check TRUSTED_PROXY_HOPS",
            trusted_hops,
        )
    return chosen


class SlidingWindowLimiter:
    """In-memory per-key limiter (single process). Enough for one free-tier instance."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic, max_keys: int = 50_000):
        self.limit = limit
        self.window = window_s
        self.clock = clock
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, float]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        with self._lock:
            if len(self._hits) > self.max_keys:  # crude memory guard under abuse
                self._hits.clear()
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(0.0, self.window - (now - q[0]))
            q.append(now)
            return True, 0.0


# The API and anything not on the UI list: nothing may load, run or frame (D19).
API_CSP = "default-src 'none'; frame-ancestors 'none'"
# The web UI (#47): same-origin assets only and no 'unsafe-inline', so no inline script, style= or on*=.
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
UI_PATHS = ("/",)
UI_PREFIXES = ("/static/",)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": API_CSP,
    "Cache-Control": "no-store",
}


def security_headers(path: str) -> dict[str, str]:
    """Headers for a response to `path`. Only the UI page and its assets get the self-only CSP;
    every other path, `/api/*` included, keeps `default-src 'none'`."""
    headers = dict(SECURITY_HEADERS)
    if path in UI_PATHS or path.startswith(UI_PREFIXES):
        headers["Content-Security-Policy"] = UI_CSP
    return headers
