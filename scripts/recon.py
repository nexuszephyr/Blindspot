"""Recon experiments E1-E10 against the real API (about 120 live calls; capped by --max-calls).

    python scripts/recon.py                 # all experiments
    python scripts/recon.py --only E1,E4    # a subset
    python scripts/recon.py --shape-check   # compare committed synthetic fixtures with recorded shapes (0 calls)

Rows go to data/recon/<timestamp>.jsonl; raw bodies to data/raw/. Re-running is cheap: anything
already cached is served from the cache and costs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from typing import Any

from _common import DATA, ROOT, Recorder, ctx, live_used, make_client, raw_probe, record_row

CITIES = ["Bengaluru", "Lagos", "Tokyo", "Osaka", "São Paulo", "Berlin", "New York"]


class Budget(Exception):
    pass


class Recon:
    def __init__(self, client, max_calls: int):
        self.client = client
        self.rec = Recorder(client, "recon")
        self.c = ctx("recon")
        self.start = live_used(client)
        self.max_calls = max_calls
        self.ids: dict[str, str] = {}

    def check(self) -> None:
        used = live_used(self.client) - self.start
        if used >= self.max_calls:
            raise Budget(f"recon stopped at its cap of {self.max_calls} live calls")

    async def search(self, exp, label, query, types=None, take=5, **kw):
        self.check()
        r = await self.client.search_entities(query, self.c, types=types, take=take, **kw)
        return r, self.rec.add(record_row(exp, label, r))

    async def insights(self, exp, label, params, notes=""):
        self.check()
        r = await self.client.insights(params, self.c)
        if r.served_from == "degraded" and r.status in (None,) and "invalid query" in (r.reason or ""):
            notes = (notes + " [rejected locally]").strip()
        row = record_row(
            exp,
            label,
            r,
            notes,
            affinity_sorted=_is_sorted([e.affinity for e in r.entities]),
            has_affinity=any(e.affinity is not None for e in r.entities),
            has_popularity=any(e.popularity is not None for e in r.entities),
            query_keys=sorted((r.body or {}).get("query", {}).keys())
            if isinstance((r.body or {}).get("query"), dict)
            else [],
        )
        return r, self.rec.add(row)

    async def seed(self, name, etype) -> str | None:
        key = f"{etype}:{name}"
        if key not in self.ids:
            r, _ = await self.search("seed", f"seed {name}", name, types=[etype], take=1)
            if r.entities and r.entities[0].entity_id:
                self.ids[key] = r.entities[0].entity_id
        return self.ids.get(key)

    async def tags(self, exp, label, **kw):
        self.check()
        r = await self.client.search_tags(self.c, **kw)
        return r, self.rec.add(record_row(exp, label, r))

    async def get(self, exp, label, path, params):
        self.check()
        r = await self.client.request(path, params, self.c)
        return r, self.rec.add(record_row(exp, label, r))

    # ------------------------------------------------------------------ experiments
    async def e1(self):
        for q, t in [
            ("Radiohead", "urn:entity:artist"),
            ("The Godfather", "urn:entity:movie"),
            ("Breaking Bad", "urn:entity:tv_show"),
            ("Nike", "urn:entity:brand"),
            ("Burna Boy", "urn:entity:artist"),
            ("A. R. Rahman", "urn:entity:artist"),
        ]:
            r, _ = await self.search("E1", f"typed {q}", q, types=[t], take=5)
            if r.entities and r.entities[0].entity_id:
                self.ids[f"{t}:{q}"] = r.entities[0].entity_id
        await self.search("E1", "ambiguous 'Mercury' untyped", "Mercury", take=10)
        await self.search("E1", "non-latin 宮崎駿", "宮崎駿", take=5)
        await self.search("E1", "accented 'Caetano Veloso'", "Caetano Veloso", take=3)
        await self.search("E1", "unaccented 'Caetano Veloso' typo", "Caetano Velozo", take=3)
        await self.search("E1", "locality São Paulo", "São Paulo", types=["urn:entity:locality"], take=3)
        await self.search("E1", "place 'Toit' Bengaluru", "Toit Brewpub", types=["urn:entity:place"], take=3)
        for take in (1, 50):
            await self.search("E1", f"take={take}", "love", types=["urn:entity:movie"], take=take)
        # beyond our own clamp: probe the API's real ceiling directly
        for take in (100, 250):
            self.check()
            p = await raw_probe(
                self.client,
                self.c,
                "GET",
                "/search",
                {"query": "love", "types": "urn:entity:movie", "take": take},
            )
            n = len(p["body"].get("results", [])) if isinstance(p["body"], dict) else None
            self.rec.add(
                {
                    "exp": "E1",
                    "label": f"raw take={take}",
                    "status": p["status"],
                    "count": n,
                    "served_from": "live",
                    "latency_ms": p["latency_ms"],
                    "top": [],
                    "error_body": None if p["status"] == 200 else str(p["body"])[:500],
                    "rate_headers": p["rate_headers"],
                }
            )

    async def e2(self):
        await self.get("E2", "tag types (all)", "/v2/tags/types", {"take": 50})
        await self.get(
            "E2",
            "tag types parent=place",
            "/v2/tags/types",
            {"filter.parents.types": "urn:entity:place", "take": 50},
        )
        for q in ("restaurant", "cafe", "music venue", "afrobeats", "bollywood", "anime", "street food"):
            await self.tags("E2", f"tags '{q}'", query=q, take=10)
        await self.tags(
            "E2",
            "tags typo 'itallian' typo_tolerance",
            query="itallian",
            take=5,
            **{"feature.typo_tolerance": True},
        )
        await self.tags(
            "E2",
            "tags type=urn:tag:genre:place:restaurant",
            query="indian",
            tag_types=["urn:tag:genre:place:restaurant"],
            take=10,
        )

    async def e3(self):
        seeds = [
            ("Radiohead", "urn:entity:artist", "urn:entity:artist"),
            ("The Godfather", "urn:entity:movie", "urn:entity:movie"),
            ("Breaking Bad", "urn:entity:tv_show", "urn:entity:tv_show"),
            ("Nike", "urn:entity:brand", "urn:entity:brand"),
            ("Radiohead", "urn:entity:artist", "urn:entity:movie"),  # cross-domain
            ("Nike", "urn:entity:brand", "urn:entity:artist"),
        ]
        for name, stype, out in seeds:
            eid = await self.seed(name, stype)
            if eid:
                await self.insights(
                    "E3",
                    f"{out} <- {name}",
                    {"filter.type": out, "signal.interests.entities": [eid], "take": 10},
                )
        rid = await self.seed("Radiohead", "urn:entity:artist")
        for t in (
            "urn:entity:person",
            "urn:entity:book",
            "urn:entity:podcast",
            "urn:entity:video_game",
            "urn:entity:videogame",
            "urn:entity:destination",
        ):
            await self.insights(
                "E3", f"{t} <- Radiohead", {"filter.type": t, "signal.interests.entities": [rid], "take": 5}
            )
        await self.insights(
            "E3",
            "place <- Radiohead in Berlin",
            {
                "filter.type": "urn:entity:place",
                "signal.interests.entities": [rid],
                "filter.location.query": "Berlin",
                "take": 10,
            },
        )

    async def e4(self):
        rest = "urn:tag:genre:place:restaurant"
        for city in ["Bengaluru", "Lagos", "Tokyo", "São Paulo", "Berlin", "New York"]:
            await self.insights(
                "E4",
                f"place filter.location.query={city}",
                {"filter.type": "urn:entity:place", "filter.location.query": city, "take": 10},
            )
            await self.insights(
                "E4",
                f"place signal.location.query={city} +tag restaurant",
                {
                    "filter.type": "urn:entity:place",
                    "signal.location.query": city,
                    "filter.tags": rest,
                    "take": 10,
                },
            )
        for city in ["Bengaluru", "Bangalore", "Bengaluru, India", "Sao Paulo", "NYC", "Osaka"]:
            await self.insights(
                "E4",
                f"name format {city!r}",
                {"filter.type": "urn:entity:place", "filter.location.query": city, "take": 5},
            )
        await self.insights(
            "E4",
            "place both filter+signal location Lagos",
            {
                "filter.type": "urn:entity:place",
                "filter.location.query": "Lagos",
                "signal.location.query": "Lagos",
                "take": 10,
            },
        )
        await self.insights(
            "E4",
            "unmatched locality name",
            {"filter.type": "urn:entity:place", "filter.location.query": "Xyzzyville Qwerty", "take": 5},
        )
        # Does a location signal move NON-place domains? Compare the same query across cities.
        for t in ("urn:entity:artist", "urn:entity:movie", "urn:entity:brand"):
            for city in ("Lagos", "Tokyo", "Berlin"):
                await self.insights(
                    "E4",
                    f"{t} signal.location.query={city}",
                    {"filter.type": t, "signal.location.query": city, "take": 10},
                )

    async def e5(self):
        r, _ = await self.get("E5", "audience types", "/v2/audiences/types", {"take": 50})
        await self.get("E5", "audiences (no filter)", "/v2/audiences", {"take": 50})
        types = [e.entity_id or e.raw.get("type") or e.raw.get("id") for e in r.entities][:2]
        aud_ids: list[str] = []
        for t in [x for x in types if x]:
            ra, _ = await self.get(
                "E5", f"audiences parent={t}", "/v2/audiences", {"filter.parents.types": t, "take": 20}
            )
            aud_ids += [
                e.entity_id or e.raw.get("id") for e in ra.entities if (e.entity_id or e.raw.get("id"))
            ]
        rid = await self.seed("Radiohead", "urn:entity:artist")
        await self.insights(
            "E5",
            "artist demographics only (expect error)",
            {"filter.type": "urn:entity:artist", "signal.demographics.age": "35_and_younger", "take": 5},
        )
        for label, extra in [
            ("age 55_and_older", {"signal.demographics.age": "55_and_older"}),
            ("age 35_and_younger", {"signal.demographics.age": "35_and_younger"}),
            ("gender female", {"signal.demographics.gender": "female"}),
        ]:
            await self.insights(
                "E5",
                f"artist <- Radiohead + {label}",
                {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 10, **extra},
            )
        if aud_ids:
            await self.insights(
                "E5",
                f"artist <- Radiohead + audience {aud_ids[0]}",
                {
                    "filter.type": "urn:entity:artist",
                    "signal.interests.entities": [rid],
                    "signal.demographics.audiences": aud_ids[0],
                    "take": 10,
                },
            )
            await self.insights(
                "E5",
                f"place Lagos + audience {aud_ids[0]}",
                {
                    "filter.type": "urn:entity:place",
                    "filter.location.query": "Lagos",
                    "signal.demographics.audiences": aud_ids[0],
                    "take": 10,
                },
            )
        await self.insights(
            "E5",
            "urn:demographics <- Radiohead",
            {"filter.type": "urn:demographics", "signal.interests.entities": [rid]},
        )

    async def e6(self):
        rid = await self.seed("Radiohead", "urn:entity:artist")
        base = {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 10}
        for label, extra in [
            ("baseline", {}),
            ("popularity.max=0.5", {"filter.popularity.max": 0.5}),
            ("popularity.min=0.9", {"filter.popularity.min": 0.9}),
            ("bias.trends=off", {"bias.trends": "off"}),
            ("bias.trends=high", {"bias.trends": "high"}),
        ]:
            await self.insights("E6", f"artist <- Radiohead {label}", {**base, **extra})
        for label, extra in [
            ("trends off", {"bias.trends": "off"}),
            ("trends high", {"bias.trends": "high"}),
        ]:
            await self.insights(
                "E6",
                f"place New York {label}",
                {"filter.type": "urn:entity:place", "filter.location.query": "New York", "take": 10, **extra},
            )

    async def e7(self):
        rid = await self.seed("Radiohead", "urn:entity:artist")
        pool, _ = await self.insights(
            "E7",
            "candidate pool (artist <- Radiohead, take 50)",
            {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 50},
        )
        ids = [e.entity_id for e in pool.entities if e.entity_id]
        burna = await self.seed("Burna Boy", "urn:entity:artist")
        if burna:
            ids = [burna, *ids]
        for n in (5, 25, len(ids)):
            if n <= len(ids):
                await self.insights(
                    "E7",
                    f"score {n} fixed candidates vs signal Lagos",
                    {
                        "filter.type": "urn:entity:artist",
                        "filter.results.entities": ids[:n],
                        "signal.location.query": "Lagos",
                        "take": min(n, 50),
                    },
                    notes=f"sent {n} candidates; check all returned + scored",
                )
        if burna:
            await self.insights(
                "E7",
                "score candidates vs entity signal (Burna Boy)",
                {
                    "filter.type": "urn:entity:artist",
                    "filter.results.entities": ids[:10],
                    "signal.interests.entities": [burna],
                    "take": 10,
                },
            )

    async def e8(self):
        rid = await self.seed("Radiohead", "urn:entity:artist")
        await self.insights(
            "E8",
            "explainability artist",
            {
                "filter.type": "urn:entity:artist",
                "signal.interests.entities": [rid],
                "feature.explainability": True,
                "take": 5,
            },
        )
        await self.insights(
            "E8",
            "explainability place Tokyo",
            {
                "filter.type": "urn:entity:place",
                "signal.interests.entities": [rid],
                "filter.location.query": "Tokyo",
                "feature.explainability": True,
                "take": 5,
            },
        )

    async def e9(self):
        probes: list[tuple[str, str, str, dict, dict]] = [
            ("no key header", "GET", "/search", {"query": "x", "take": 2}, {"send_key": False}),
            ("bad key", "GET", "/search", {"query": "x", "take": 2}, {"api_key": "invalid-key-for-recon"}),
            ("unknown path", "GET", "/v2/nope", {}, {}),
            ("bare base url", "GET", "/", {}, {}),
            ("POST insights", "POST", "/v2/insights", {"filter.type": "urn:entity:movie"}, {}),
            ("insights missing filter.type", "GET", "/v2/insights", {"take": 2}, {}),
            (
                "insights invalid filter.type",
                "GET",
                "/v2/insights",
                {"filter.type": "urn:entity:food_and_drink", "filter.location.query": "Berlin"},
                {},
            ),
            (
                "insights take=0",
                "GET",
                "/v2/insights",
                {"filter.type": "urn:entity:place", "filter.location.query": "Berlin", "take": 0},
                {},
            ),
            (
                "insights take=200",
                "GET",
                "/v2/insights",
                {"filter.type": "urn:entity:place", "filter.location.query": "Berlin", "take": 200},
                {},
            ),
            (
                "insights invalid tag",
                "GET",
                "/v2/insights",
                {"filter.type": "urn:entity:place", "signal.interests.tags": "not-a-tag"},
                {},
            ),
        ]
        for label, method, path, params, kw in probes:
            self.check()
            p = await raw_probe(self.client, self.c, method, path, params, **kw)
            self.rec.add(
                {
                    "exp": "E9",
                    "label": label,
                    "status": p["status"],
                    "served_from": "live",
                    "latency_ms": p["latency_ms"],
                    "count": None,
                    "top": [],
                    "error_body": json.dumps(p["body"])[:600] if p["status"] != 200 else None,
                    "rate_headers": p["rate_headers"],
                }
            )
        base = {"filter.type": "urn:entity:place", "filter.location.query": "Berlin", "take": 5}
        p1, _ = await self.insights("E9", "page 1", {**base, "page": 1})
        p2, _ = await self.insights("E9", "page 2", {**base, "page": 2})
        o5, _ = await self.insights("E9", "offset 5", {**base, "offset": 5})
        overlap = {e.entity_id for e in p1.entities} & {e.entity_id for e in p2.entities}
        self.rec.add(
            {
                "exp": "E9",
                "label": "pagination analysis",
                "status": None,
                "served_from": "n/a",
                "count": None,
                "top": [],
                "notes": f"page1∩page2={len(overlap)}; "
                f"page2==offset5: {[e.entity_id for e in p2.entities] == [e.entity_id for e in o5.entities]}",
            }
        )

    async def e10(self):
        r, _ = await self.insights(
            "E10",
            "places Tokyo for cross-refs",
            {"filter.type": "urn:entity:place", "filter.location.query": "Tokyo", "take": 5},
        )
        ids = [e.entity_id for e in r.entities if e.entity_id][:3]
        if ids:
            ents, _row = await self.get("E10", "GET /entities for 3 places", "/entities", {"entity_ids": ids})
            for e in ents.entities[:3] or r.entities[:3]:
                props = e.raw.get("properties", {}) if isinstance(e.raw, dict) else {}
                self.rec.add(
                    {
                        "exp": "E10",
                        "label": f"fields of {e.name}",
                        "status": None,
                        "served_from": "analysis",
                        "count": None,
                        "top": [],
                        "notes": json.dumps(
                            {
                                "has_location": e.location,
                                "property_keys": sorted(props.keys()),
                                "external_keys": sorted((e.raw.get("external") or {}).keys()),
                                "address": props.get("address"),
                                "geocode": props.get("geocode"),
                            },
                            ensure_ascii=False,
                        )[:800],
                    }
                )
        await self.search(
            "E10",
            "search place 'Toit' with location bias",
            "Toit",
            types=["urn:entity:place"],
            take=3,
            **{"filter.location": "12.9716,77.5946"},
        )


def shape_check() -> int:
    """Compare committed synthetic fixtures with field names seen in real recordings (0 calls)."""
    raw = sorted((DATA / "raw").glob("*.json"))
    if not raw:
        print("no recordings in data/raw yet; run smoke.py/recon.py with a key first")
        return 1
    seen: dict[str, set[str]] = {}
    for p in raw:
        doc = json.loads(p.read_text(encoding="utf-8"))
        ep = doc.get("endpoint")
        from blindspot.qloo.models import extract_items

        items = extract_items(doc.get("body"))
        if items and isinstance(items[0], dict):
            seen.setdefault(ep, set()).update(items[0].keys())
    fx = {"/search": "search_synthetic.json", "/v2/insights": "insights_synthetic.json"}
    ok = True
    for ep, fname in fx.items():
        from blindspot.qloo.models import extract_items

        body = json.loads((ROOT / "tests" / "fixtures" / fname).read_text(encoding="utf-8"))
        fkeys = set(extract_items(body)[0].keys())
        real = seen.get(ep, set())
        missing = sorted(fkeys - real) if real else []
        print(
            f"{ep}: fixture keys not seen in real data: {missing or 'none'}; "
            f"real keys not in fixture: {sorted(real - fkeys) or 'none'}"
        )
        ok &= not missing
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma list, e.g. E1,E4")
    ap.add_argument("--max-calls", type=int, default=300)
    ap.add_argument("--shape-check", action="store_true")
    a = ap.parse_args()
    if a.shape_check:
        return shape_check()
    client = make_client("record")
    r = Recon(client, a.max_calls)
    order = ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10"]
    chosen = [x.strip().upper() for x in a.only.split(",")] if a.only else order
    summary: dict[str, Any] = {}
    try:
        for e in order:
            if e in chosen:
                print(f"\n=== {e} ===")
                before = live_used(client)
                try:
                    await getattr(r, e.lower())()
                except Budget as exc:
                    print(f"STOP: {exc}")
                    break
                summary[e] = live_used(client) - before
        if client.breaker.state != "closed":
            print("WARNING: breaker is not closed (429s seen); pausing is advised")
    finally:
        await client.aclose()
    print(f"\nlive calls per experiment: {summary}")
    print(f"recon total: {live_used(client) - r.start}; lifetime total: {live_used(client)}")
    print(f"rows: {r.rec.path}")
    return 0


def _is_sorted(vals: list[float | None]) -> bool | None:
    v = [x for x in vals if x is not None]
    return None if len(v) < 2 else all(a >= b for a, b in itertools.pairwise(v))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
