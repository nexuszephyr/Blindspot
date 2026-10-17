"""Verify a hosted Blindspot deployment (issue #8; reusable for #58 hardening and #63 final QA).

    python scripts/verify_deploy.py https://blindspot-xxxx.onrender.com [--json]
    python scripts/verify_deploy.py https://blindspot-xxxx.onrender.com --cold --confirm-idle

Prints field names, booleans, numbers, counts and timings only: never response values, Qloo
names, ids, scores or client ids. Exit code 1 if any check fails.

The search check costs at most 1 live Qloo call (the second request must come from the cache).
--cold times the first /healthz of an idle instance; pass --confirm-idle to state that nothing
(you, the keepalive workflow, an external monitor) has called the service for 20+ minutes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
Row = tuple[str, bool, str]

# Things /api/qloo/status must never contain.
LEAK_PATTERNS = {
    "key-shaped value": re.compile(r"[A-Za-z0-9_\-+/=]{32,}"),
    "file-system path": re.compile(r"(/app/|/etc/|/home/|/tmp/|/var/|[A-Za-z]:\\|\.sqlite|\.env)"),
    "internal hostname": re.compile(r"(localhost|\.internal\b|\.local\b|\.onrender\.com)"),
    "raw IP": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
}
# Budget caps that /api/qloo/status reports, keyed by the render.yaml variable that sets them.
CAP_VARS = {
    "QLOO_DAILY_LIVE_CAP": ("daily", "cap"),
    "QLOO_CLIENT_HOURLY_CAP": ("client_hourly", "cap"),
    "QLOO_RUN_CAP": ("run_cap",),
    "QLOO_DEMO_RESERVE": ("demo_reserve",),
}


def default_query() -> str:
    queries = json.loads((ROOT / "config" / "demo_queries.json").read_text(encoding="utf-8"))["queries"]
    return next(q["params"]["query"] for q in queries if q["path"] == "/search")


def declared_caps(path: Path = ROOT / "render.yaml") -> dict[str, int]:
    """Budget caps as render.yaml declares them, so the check never hard-codes numbers."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    env = {e["key"]: e.get("value") for e in doc["services"][0]["envVars"]}
    return {k: int(env[k]) for k in CAP_VARS if k in env}


def shape(obj, prefix=""):
    """Field names, with booleans and numbers shown; strings hidden."""
    out = []
    for k, v in obj.items():
        name = f"{prefix}{k}"
        if isinstance(v, dict):
            out += shape(v, name + ".")
        elif isinstance(v, (bool, int, float)):
            out.append(f"{name}={v}")
        else:
            out.append(name)
    return out


def leaks(text: str) -> list[str]:
    return [label for label, pat in LEAK_PATTERNS.items() if pat.search(text)]


def _dig(obj, path):
    for p in path:
        obj = obj.get(p) if isinstance(obj, dict) else None
    return obj


def verify(base: str, client: httpx.Client, query: str, caps: dict[str, int] | None = None) -> list[Row]:
    base = base.rstrip("/")
    rows: list[Row] = []

    t = time.perf_counter()
    r = client.get(f"{base}/healthz")
    ms = (time.perf_counter() - t) * 1000
    h = r.json() if r.status_code == 200 else {}
    rows.append(
        (
            "healthz 200, ok, mode live",
            r.status_code == 200 and h.get("ok") is True and h.get("mode") == "live",
            f"status {r.status_code}, ok {h.get('ok')}, mode {h.get('mode')}, {ms:.0f} ms",
        )
    )

    r = client.get(f"{base}/api/qloo/status")
    body = r.json() if r.status_code == 200 else {}
    public = {k: v for k, v in body.items() if k != "your_client_id"}
    breaker = _dig(body, ("breaker", "state"))
    rows.append(
        (
            "status key_configured, breaker closed",
            body.get("key_configured") is True and breaker == "closed",
            f"status {r.status_code}, breaker {breaker}; fields: {', '.join(shape(public)) or 'none'}",
        )
    )
    if caps:
        got = {var: _dig(body.get("budget") or {}, path) for var, path in CAP_VARS.items() if var in caps}
        bad = {var: (caps[var], got[var]) for var in got if got[var] != caps[var]}
        rows.append(
            (
                "status caps match render.yaml",
                not bad,
                "all match"
                if not bad
                else "; ".join(f"{v}: declared {d}, got {g}" for v, (d, g) in bad.items()),
            )
        )
    found = leaks(json.dumps(public))
    rows.append(
        (
            "status leaks nothing",
            r.status_code == 200 and not found,
            "none found" if not found else "found: " + ", ".join(found),
        )
    )

    seen = []
    for _ in (1, 2):
        r = client.get(f"{base}/api/qloo/search", params={"q": query})
        b = r.json() if r.status_code == 200 else {}
        seen.append((r.status_code, b.get("served_from"), len(b.get("results") or [])))
    ok = (
        seen[0][0] == 200
        and seen[0][1] in ("live", "cache")
        and seen[0][2] > 0
        and seen[1][0] == 200
        and seen[1][1] == "cache"
    )
    rows.append(
        (
            "search live/cache then cache",
            ok,
            "; ".join(
                f"#{i} status {s}, served_from {sf}, {n} results" for i, (s, sf, n) in enumerate(seen, 1)
            ),
        )
    )

    a = client.get(f"{base}/api/qloo/status").json().get("your_client_id")
    b = (
        client.get(f"{base}/api/qloo/status", headers={"X-Forwarded-For": "1.2.3.4"})
        .json()
        .get("your_client_id")
    )
    rows.append(
        ("forged X-Forwarded-For ignored", bool(a) and a == b, "same" if a and a == b else "different")
    )

    r = client.get(f"{base}/no-such-route")
    is_json = r.headers.get("content-type", "").startswith("application/json")
    rows.append(
        (
            "unknown route: JSON 404, no traceback",
            r.status_code == 404 and is_json and "Traceback" not in r.text,
            f"status {r.status_code}, json {is_json}, traceback {'Traceback' in r.text}",
        )
    )
    r = client.get(f"{base}/api/qloo/search", params={"q": "a" * 10_240})
    rows.append(("oversized q: 4xx not 500", 400 <= r.status_code < 500, f"status {r.status_code}"))
    return rows


def cold(base: str, client: httpx.Client) -> Row:
    t = time.perf_counter()
    try:
        r = client.get(f"{base.rstrip('/')}/healthz", timeout=120)
    except httpx.HTTPError as exc:
        return (
            "cold start /healthz",
            False,
            f"no response in {time.perf_counter() - t:.0f} s ({type(exc).__name__})",
        )
    return (
        "cold start /healthz",
        r.status_code == 200,
        f"status {r.status_code}, {time.perf_counter() - t:.1f} s to response",
    )


def report(rows: list[Row], as_json: bool = False) -> int:
    failed = sum(not ok for _, ok, _ in rows)
    if as_json:
        print(
            json.dumps(
                {"checks": [{"check": n, "pass": ok, "detail": d} for n, ok, d in rows], "failed": failed}
            )
        )
        return 1 if failed else 0
    width = max(len(n) for n, _, _ in rows)
    print(f"{'check'.ljust(width)}  result  detail")
    for name, ok, detail in rows:
        print(f"{name.ljust(width)}  {'PASS' if ok else 'FAIL':6}  {detail}")
    print(f"\n{len(rows) - failed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy codepage
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--cold", action="store_true", help="time the first /healthz of an idle instance")
    ap.add_argument(
        "--confirm-idle", action="store_true", help="assert nothing called the service for 20+ min"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--query", help="search text (default: first /search query in config/demo_queries.json)")
    a = ap.parse_args(argv)
    if a.cold and not a.confirm_idle:
        print(
            "--cold needs --confirm-idle: confirm that nothing (you, keepalive, a monitor) has called the service for 20+ minutes"
        )
        return 2
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        try:
            if a.cold:
                return report([cold(a.base_url, client)], a.json)
            return report(verify(a.base_url, client, a.query or default_query(), declared_caps()), a.json)
        except (httpx.HTTPError, ValueError) as exc:
            print(f"FAIL: request error {type(exc).__name__}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
