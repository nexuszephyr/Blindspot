"""Key redaction. The key is registered once at client construction and scrubbed everywhere."""

from __future__ import annotations

import logging
import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()
MASK = "[REDACTED]"
# Header-style leaks, in case something stringifies a request.
_HEADER_RE = re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+")


def register_secret(value: str) -> None:
    value = (value or "").strip()
    if len(value) >= 6:
        with _lock:
            _secrets.add(value)


def redact(text: object) -> str:
    s = "" if text is None else str(text)
    with _lock:
        values = list(_secrets)
    for v in values:
        if v in s:
            s = s.replace(v, MASK)
    return _HEADER_RE.sub(lambda m: m.group(1) + MASK, s)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def install_log_redaction() -> None:
    """Attach the filter to the root logger's handlers and to known noisy loggers."""
    f = RedactingFilter()
    root = logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(x, RedactingFilter) for x in h.filters):
            h.addFilter(f)
    for name in ("", "httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access", "blindspot"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactingFilter) for x in lg.filters):
            lg.addFilter(f)
