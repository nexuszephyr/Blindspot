"""Smoke test: one call each to search, tags, entities and insights (about 4 live calls).

    python scripts/smoke.py

Raw bodies (no headers) are saved to data/raw/. Stops on 401 with instructions.
"""

from __future__ import annotations

import asyncio
import sys

from _common import Recorder, ctx, live_used, make_client, record_row


async def main() -> int:
    client = make_client("record")
    rec = Recorder(client, "smoke")
    c = ctx("smoke")
    before = live_used(client)
    try:
        r = await client.search_entities("Radiohead", c, types=["urn:entity:artist"], take=3)
        rec.add(record_row("smoke", "search Radiohead", r))
        if r.status == 401 or (r.reason and "API key" in r.reason):
            print("\nAUTH FAILED (401). Check that QLOO_API_KEY in .env is the hackathon key and that "
                  "QLOO_BASE_URL is https://hackathon.api.qloo.com (staging/production keys differ).")
            return 2
        if not r.ok:
            print(f"\nsearch failed: {r.reason}")
            return 1
        rec.add(record_row("smoke", "tags 'jazz'", await client.search_tags(c, query="jazz", take=3)))
        eid = r.entities[0].entity_id if r.entities else None
        if eid:
            rec.add(record_row("smoke", "entities by id", await client.get_entities([eid], c)))
            rec.add(record_row("smoke", "insights artist<-entity", await client.insights(
                {"filter.type": "urn:entity:artist", "signal.interests.entities": [eid], "take": 3}, c)))
        print(f"\nlive calls used by smoke: {live_used(client) - before}; lifetime total: {live_used(client)}")
        print(f"rows: {rec.path}")
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
