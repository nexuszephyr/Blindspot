"""Typed errors. Messages never contain the API key (see redact())."""

from __future__ import annotations


class QlooError(Exception):
    """Base class. `reason` is a short human-readable string safe to show users."""

    def __init__(self, reason: str, *, status: int | None = None, body: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.body = body


class QlooAuthError(QlooError):
    """401/403 from Qloo: missing, wrong, or unsupported key/entity type."""


class QlooRateLimited(QlooError):
    def __init__(self, reason: str, *, retry_after: float | None = None, **kw):
        super().__init__(reason, **kw)
        self.retry_after = retry_after


class QlooBadRequest(QlooError):
    """4xx other than 401/403/429. Never retried."""


class QlooServerError(QlooError):
    """5xx, timeouts and transport failures after retries are exhausted."""


class BudgetExceeded(QlooError):
    def __init__(self, reason: str, *, scope: str):
        super().__init__(reason)
        self.scope = scope


class QlooDisabled(QlooError):
    """Qloo access switched off globally (QLOO_ENABLED=false) or for this request."""


class BreakerOpen(QlooError):
    """Circuit breaker is open after repeated 429s; calls are short-circuited."""


class InvalidQuery(QlooError):
    """Rejected locally before any network call (too large, bad type, ...)."""
