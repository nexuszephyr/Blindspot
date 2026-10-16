"""Fill the cache from a query list (demo priority: may use the reserved budget slice).

    python scripts/warm_cache.py [--file config/demo_queries.json] [--refresh]

Already-cached queries cost nothing; --refresh re-fetches them live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from _common import ROOT, ctx, live_used, make_client


def load_queries(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["queries"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "config" / "demo_queries.json"))
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    queries = load_queries(a.file)
    client = make_client("live")
    c = ctx("warm", purpose="app")
    start = live_used(client)
    seen: Counter[str] = Counter()
    try:
        for q in queries:
            res = await client.request(q["path"], q["params"], c, bypass_cache=a.refresh)
            seen[res.served_from] += 1
            print(f"{q['label']:22} {res.served_from:9} n={res.count:<3} {res.reason or ''}")
    finally:
        await client.aclose()
    print(
        f"\n{dict(seen)}; live calls used: {live_used(client) - start}; lifetime total: {live_used(client)}"
    )
    return 0 if not seen.get("degraded") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
