"""Shared helpers for the live recon scripts. All of them go through QlooClient, so every
call is cached, logged, rate-limited and counted against QLOO_MAX_LIVE_CALLS."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from blindspot.config import Settings  # noqa: E402
from blindspot.qloo.client import QlooClient  # noqa: E402
from blindspot.qloo.context import RequestContext  # noqa: E402
from blindspot.qloo.redact import install_log_redaction, redact  # noqa: E402

DATA = ROOT / "data"
# Windows consoles default to a legacy codepage; never crash printing non-Latin names.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")


def require_key(mode: str = "record") -> Settings:
    s = Settings(qloo_mode=mode)
    if not s.has_key:
        sys.exit(
            "QLOO_API_KEY is missing.\n"
            f"Create {ROOT / '.env'} containing one line:  QLOO_API_KEY=<your hackathon key>\n"
            "(copy .env.example to .env and fill it in). The key is never printed or logged."
        )
    install_log_redaction()
    return s


def make_client(mode: str = "record") -> QlooClient:
    c = QlooClient(require_key(mode))
    c.startup()
    return c


def ctx(name: str, run_id: str | None = None, purpose: str = "script") -> RequestContext:
    """Patient script context (no deadline), tagged with a purpose for live-call reporting."""
    kw = {"run_id": run_id} if run_id else {}
    return RequestContext(client_id=f"local:{name}", priority="demo", purpose=purpose, **kw)


def record_row(exp: str, label: str, res, notes: str = "", **extra: Any) -> dict[str, Any]:
    first = res.entities[0].raw if res.entities else {}
    return {
        "exp": exp,
        "label": label,
        "endpoint": res.endpoint,
        "call_id": res.call_id,
        "status": res.status,
        "served_from": res.served_from,
        "outcome": res.outcome,
        "unverified_params": res.unverified_params,
        "latency_ms": res.latency_ms,
        "count": res.count,
        "top": res.summary(5),
        "first_keys": sorted(first.keys()) if isinstance(first, dict) else [],
        "top_level_keys": sorted(res.body.keys()) if isinstance(res.body, dict) else [],
        "reason": res.reason,
        "error_body": res.error_body,
        "rate_headers": res.rate_headers,
        "notes": notes,
        **extra,
    }


class Recorder:
    """Appends JSONL rows to data/<name>/<timestamp>.jsonl and keeps them in memory."""

    def __init__(self, client: QlooClient, name: str):
        self.client = client
        self.rows: list[dict[str, Any]] = []
        d = DATA / name
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{time.strftime('%Y%m%dT%H%M%S')}.jsonl"

    def add(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("call_id"):
            log_rows = self.client.db.query("SELECT params_json FROM calls WHERE id=?", (row["call_id"],))
            if log_rows:
                row["params"] = json.loads(log_rows[0]["params_json"])
        self.rows.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(redact(json.dumps(row, ensure_ascii=False, default=str)) + "\n")
        top = ", ".join(str(t.get("name")) for t in row.get("top", [])[:3])
        print(
            f"[{row['exp']}] {row['label']}: {row['served_from']} {row['status']} "
            f"n={row['count']} {(row.get('reason') or '')[:80]} | {top[:90]}"
        )
        return row


async def raw_probe(
    client: QlooClient,
    c: RequestContext,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
    send_key: bool = True,
) -> dict[str, Any]:
    """Deliberate bad requests for E9 (bad paths, POST, wrong key). Bypasses the endpoint
    allow-list but still reserves budget, honours the breaker and is written to the call log."""
    client.budget.reserve(c)
    await client.bucket.acquire()
    headers = {"Accept": "application/json"}
    if send_key:
        headers["X-Api-Key"] = api_key if api_key is not None else client.s.qloo_api_key.get_secret_value()
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=client.s.qloo_base_url, timeout=15) as h:
        try:
            r = await h.request(method, path, params=params, headers=headers)
            status, text = r.status_code, r.text
            rh = {k: v for k, v in r.headers.items() if "rate" in k.lower() or "retry" in k.lower()}
        except httpx.HTTPError as exc:
            status, text, rh = None, f"transport error: {type(exc).__name__}", {}
    latency = (time.perf_counter() - t0) * 1000
    client.calls.record(
        c,
        f"{method} {path}",
        params or {},
        "live",
        status=status,
        latency_ms=latency,
        error=None if status and status < 400 else text[:300],
    )
    try:
        body: Any = json.loads(text)
    except ValueError:
        body = text[:500]
    return {"status": status, "latency_ms": round(latency, 1), "body": body, "rate_headers": rh}


def live_used(client: QlooClient) -> int:
    return client.budget.total_used()
