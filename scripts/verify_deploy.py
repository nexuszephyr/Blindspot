"""Verify a hosted Blindspot deployment (issue #8; reusable for later deploy checks).

    python scripts/verify_deploy.py https://blindspot-xxxx.onrender.com
    python scripts/verify_deploy.py https://blindspot-xxxx.onrender.com --cold

Prints field names, booleans, counts and timings only: never response values, Qloo names, ids,
scores or client ids. Exit code 1 if any check fails.

The search check costs at most 1 live Qloo call (the second request must come from the cache).
--cold waits until 20 minutes have passed since this script's start (the service must not be
touched meanwhile; note the keepalive workflow pings every 10 minutes) and times the first /healthz.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

# Things /api/qloo/status must never contain.
LEAK_PATTERNS = {
    "key-shaped value": re.compile(r"[A-Za-z0-9_\-+/=]{32,}"),
    "file-system path": re.compile(r"(/app/|/etc/|/home/|/tmp/|/var/|[A-Za-z]:\\|\.sqlite|\.env)"),
    "internal hostname or IP": re.compile(
        r"(localhost|127\.0\.0\.1|\.internal\b|\.local\b|\b10\.\d+\.\d+\.\d+|\b192\.168\.\d+\.\d+"
        r"|\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)"
    ),
}


def default_query() -> str:
    queries = json.loads((ROOT / "config" / "demo_queries.json").read_text(encoding="utf-8"))["queries"]
    return next(q["params"]["query"] for q in queries if q["path"] == "/search")


def shape(obj, prefix=""):
    """Field names with booleans only (values other than booleans are hidden)."""
    out = []
    for k, v in obj.items():
        name = f"{prefix}{k}"
        if isinstance(v, dict):
            out += shape(v, name + ".")
        elif isinstance(v, bool):
            out.append(f"{name}={v}")
        else:
            out.append(name)
    return out


def leaks(text: str) -> list[str]:
    return [label for label, pat in LEAK_PATTERNS.items() if pat.search(text)]


def verify(base: str, client: httpx.Client, query: str) -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    rows: list[tuple[str, bool, str]] = []

    t = time.perf_counter()
    r = client.get(f"{base}/healthz")
    ms = (time.perf_counter() - t) * 1000
    mode = r.json().get("mode") if r.status_code == 200 else None
    rows.append(
        (
            "healthz 200, mode live",
            r.status_code == 200 and mode == "live",
            f"status {r.status_code}, mode {mode}, {ms:.0f} ms",
        )
    )

    r = client.get(f"{base}/api/qloo/status")
    body = r.json() if r.status_code == 200 else {}
    rows.append(
        (
            "status key_configured",
            body.get("key_configured") is True,
            f"status {r.status_code}; fields: {', '.join(shape(body)) or 'none'}",
        )
    )
    found = leaks(json.dumps({k: v for k, v in body.items() if k != "your_client_id"}))
    rows.append(
        (
            "status leaks nothing",
            r.status_code == 200 and not found,
            "none found" if not found else "found: " + ", ".join(found),
        )
    )

    seen = []
    for _i in (1, 2):
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
        (
            "forged X-Forwarded-For ignored",
            bool(a) and a == b,
            "client id identical" if a and a == b else "client id CHANGED or missing",
        )
    )
    return rows


def cold(base: str, client: httpx.Client, wait_s: float, sleep=time.sleep) -> tuple[str, bool, str]:
    print(f"cold start: not touching the service for {wait_s / 60:.0f} minutes...", flush=True)
    sleep(wait_s)
    t = time.perf_counter()
    r = client.get(f"{base.rstrip('/')}/healthz")
    s = time.perf_counter() - t
    return ("cold start /healthz", r.status_code == 200, f"status {r.status_code}, {s:.1f} s to response")


def report(rows: list[tuple[str, bool, str]]) -> int:
    width = max(len(n) for n, _, _ in rows)
    print(f"{'check'.ljust(width)}  result  detail")
    for name, ok, detail in rows:
        print(f"{name.ljust(width)}  {'PASS' if ok else 'FAIL':6}  {detail}")
    failed = sum(not ok for _, ok, _ in rows)
    print(f"\n{len(rows) - failed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--cold", action="store_true", help="wait 20 minutes idle, then time /healthz")
    ap.add_argument("--query", help="search text (default: first /search query in config/demo_queries.json)")
    a = ap.parse_args(argv)
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        if a.cold:
            return report([cold(a.base_url, client, 20 * 60)])
        try:
            return report(verify(a.base_url, client, a.query or default_query()))
        except (httpx.HTTPError, ValueError) as exc:
            print(f"FAIL: request error {type(exc).__name__}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
