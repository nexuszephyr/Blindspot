"""python -m blindspot.qloo.cli {search,insights,calls,budget}"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ..config import get_settings
from .client import QlooClient
from .context import RequestContext


def _print(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _result(res) -> dict:
    return {"served_from": res.served_from, "ok": res.ok, "status": res.status,
            "reason": res.reason, "count": res.count, "latency_ms": res.latency_ms,
            "call_id": res.call_id, "top": res.summary(10)}


async def _main(args: argparse.Namespace) -> int:
    client = QlooClient(get_settings())
    client.startup()
    ctx = RequestContext.script("cli")
    try:
        if args.cmd == "search":
            types = args.types.split(",") if args.types else None
            res = await client.search_entities(args.query, ctx, types=types, take=args.take)
            _print(_result(res))
        elif args.cmd == "insights":
            params: dict[str, str] = {"filter.type": args.type, "take": str(args.take)}
            for kv in args.param or []:
                k, _, v = kv.partition("=")
                params[k.strip()] = v.strip()
            res = await client.insights(params, ctx)
            _print(_result(res))
        elif args.cmd == "calls":
            rows = client.calls.for_run(args.run) if args.run else client.calls.last(args.last)
            _print(rows)
        elif args.cmd == "budget":
            _print(client.status())
        return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m blindspot.qloo.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search entities by name")
    s.add_argument("query")
    s.add_argument("--types", help="comma-separated, e.g. urn:entity:artist")
    s.add_argument("--take", type=int, default=5)
    i = sub.add_parser("insights", help="GET /v2/insights")
    i.add_argument("--type", required=True, help="filter.type, e.g. urn:entity:movie")
    i.add_argument("--param", "-p", action="append", help="extra param as key=value (repeatable)")
    i.add_argument("--take", type=int, default=5)
    c = sub.add_parser("calls", help="show the call log")
    c.add_argument("--last", type=int, default=20)
    c.add_argument("--run", help="only calls for this run id")
    sub.add_parser("budget", help="show remaining budgets, cache size, breaker state")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
