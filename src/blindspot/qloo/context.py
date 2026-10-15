"""Per-request context. Every Qloo call carries one; nothing here mutates global state."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

Priority = Literal["adhoc", "demo"]
# What a call is for; live-call reporting is broken down by it.
Purpose = Literal["app", "audit", "recon", "coverage", "smoke", "script"]

# Used when IP_HASH_SALT is unset: hashes stay stable for the life of the process only.
_PROCESS_SALT = secrets.token_bytes(32)


def hash_client_ip(ip: str, salt: str = "") -> str:
    """Salted, keyed hash of a requester IP. Raw IPs are never stored."""
    key = salt.encode() if salt else _PROCESS_SALT
    return "c_" + hmac.new(key, ip.strip().encode(), hashlib.sha256).hexdigest()[:20]


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class RequestContext:
    client_id: str
    run_id: str = field(default_factory=new_run_id)
    # Per-request kill switch: False means this request makes no Qloo calls at all.
    qloo_enabled: bool = True
    # False means cache/snapshot only for this request (never touch the network).
    allow_live: bool = True
    # "demo" calls may dip into the reserved slice of the daily budget.
    priority: Priority = "adhoc"
    purpose: Purpose = "app"
    # Absolute time.monotonic() deadline for the whole request chain; None = no deadline (scripts).
    deadline: float | None = None

    def remaining(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()

    @classmethod
    def script(
        cls, name: str = "script", *, priority: Priority = "demo", purpose: Purpose = "script"
    ) -> RequestContext:
        """Context for local scripts and the CLI: patient (no deadline)."""
        return cls(client_id=f"local:{name}", priority=priority, purpose=purpose)
