"""Recon experiments E1-E11 against the real API (capped by --max-calls).

    python scripts/recon.py                 # all experiments
    python scripts/recon.py --only E1,E11   # a subset
    python scripts/recon.py --shape-check   # compare synthetic fixtures with recorded shapes (0 calls)

Cities and domains come from config/. Queries run with strict=False (that is the point of recon)
and every row records the params that are not yet VERIFIED plus the client `outcome`
(ok | empty | qloo_error | resolution_failed | invalid | not_live), so a local refusal is never
reported as a Qloo error. Rows -> data/recon/<ts>.jsonl, raw bodies -> data/raw/ (gitignored).
Re-running is free for anything already cached.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from typing import Any

from _common import DATA, ROOT, Recorder, ctx, live_used, make_client, raw_probe, record_row
from blindspot.catalog import load_cities, load_domains
from blindspot.qloo.params import assert_param_effective

SEEDS = {  # well-known global entities used as taste seeds (not cities: those live in config/)
    "artist": ("Radiohead", "urn:entity:artist"),
    "movie": ("The Godfather", "urn:entity:movie"),
    "tv": ("Breaking Bad", "urn:entity:tv_show"),
    "brand": ("Nike", "urn:entity:brand"),
    "artist_ng": ("Burna Boy", "urn:entity:artist"),
    "artist_in": ("A. R. Rahman", "urn:entity:artist"),
}


class Budget(Exception):
    pass


def _is_sorted(vals: list[float | None]) -> bool | None:
    v = [x for x in vals if x is not None]
    return None if len(v) < 2 else all(a >= b for a, b in itertools.pairwise(v))


class Recon:
    def __init__(self, client, max_calls: int):
        self.client = client
        self.rec = Recorder(client, "recon")
        self.c = ctx("recon", purpose="recon")
        self.start = live_used(client)
        self.max_calls = max_calls
        self.ids: dict[str, str] = {}
        self.cities = load_cities(ROOT / "config")
        self.domains = {d.id: d for d in load_domains(ROOT / "config")}
        self.results: dict[str, Any] = {}

    def check(self) -> None:
        if live_used(self.client) - self.start >= self.max_calls:
            raise Budget(f"recon stopped at its cap of {self.max_calls} live calls")

    async def search(self, exp, label, query, types=None, take=5, **kw):
        self.check()
        r = await self.client.search_entities(query, self.c, types=types, take=take, strict=False, **kw)
        return r, self.rec.add(record_row(exp, label, r))

    async def insights(self, exp, label, params, notes="", resolve=None):
        self.check()
        r = await self.client.insights(params, self.c, strict=False, resolve=resolve)
        body = r.body if isinstance(r.body, dict) else {}
        row = record_row(
            exp,
            label,
            r,
            notes,
            affinity_sorted=_is_sorted([e.affinity for e in r.entities]),
            has_affinity=any(e.affinity is not None for e in r.entities),
            has_popularity=any(e.popularity is not None for e in r.entities),
            query_keys=sorted(body["query"].keys()) if isinstance(body.get("query"), dict) else [],
            per_result_query_keys=sorted({k for e in r.entities for k in (e.query or {})}),
        )
        self.rec.add(row)
        return r, row

    async def seed(self, key) -> str | None:
        name, etype = SEEDS[key]
        if key not in self.ids:
            r, _ = await self.search("seed", f"seed {key}", name, types=[etype], take=1)
            if r.entities and r.entities[0].entity_id:
                self.ids[key] = r.entities[0].entity_id
        return self.ids.get(key)

    async def get(self, exp, label, path, params):
        self.check()
        r = await self.client.request(path, params, self.c, strict=False)
        return r, self.rec.add(record_row(exp, label, r))

    async def raw(self, exp, label, path, params, **kw):
        self.check()
        p = await raw_probe(self.client, self.c, "GET", path, params, **kw)
        body = p["body"]
        n = None
        if isinstance(body, dict):
            res = body.get("results")
            n = len(res) if isinstance(res, list) else len((res or {}).get("entities", []) or [])
        return self.rec.add(
            {
                "exp": exp,
                "label": label,
                "status": p["status"],
                "served_from": "live",
                "outcome": "ok" if p["status"] == 200 else "qloo_error",
                "count": n,
                "latency_ms": p["latency_ms"],
                "top": [],
                "params": params,
                "rate_headers": p["rate_headers"],
                "error_body": None if p["status"] == 200 else json.dumps(body)[:600],
                "top_level_keys": sorted(body) if isinstance(body, dict) else [],
            }
        )

    def note(self, exp, label, notes):
        self.rec.add(
            {
                "exp": exp,
                "label": label,
                "status": None,
                "served_from": "analysis",
                "count": None,
                "top": [],
                "notes": notes,
            }
        )

    # ------------------------------------------------------------------ experiments
    async def e1(self):
        for key, (name, etype) in SEEDS.items():
            await self.seed(key)
            await self.search("E1", f"typed {key}", name, types=[etype], take=5)
        await self.search("E1", "ambiguous untyped 'Mercury'", "Mercury", take=10)
        await self.search("E1", "non-latin 宮崎駿", "宮崎駿", take=5)
        await self.search("E1", "accented 'Caetano Veloso'", "Caetano Veloso", take=3)
        await self.search("E1", "typo 'Caetano Velozo'", "Caetano Velozo", take=3)
        for city in self.cities:
            await self.search(
                "E1", f"locality {city.id}", city.location, types=["urn:entity:locality"], take=3
            )
        # /search has its own types list: which video game spelling does the host accept?
        for t in ("urn:entity:video_game", "urn:entity:videogame"):
            await self.raw("E1", f"search types={t}", "/search", {"query": "Zelda", "types": t, "take": 3})
        for take in (1, 50, 100, 250):
            await self.raw(
                "E1",
                f"search take={take}",
                "/search",
                {"query": "love", "types": "urn:entity:movie", "take": take},
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
            await self.get("E2", f"tags '{q}'", "/v2/tags", {"filter.query": q, "take": 10})
        await self.get(
            "E2",
            "tags typo 'itallian' + typo_tolerance",
            "/v2/tags",
            {"filter.query": "itallian", "feature.typo_tolerance": True, "take": 5},
        )
        dining_tag = self.domains["dining"].params.get("filter.tags")
        await self.get(
            "E2", "lookup of configured dining tag id", "/v2/tags", {"filter.results.tags": dining_tag}
        )
        for d in self.domains.values():
            if d.tag_query:
                await self.get(
                    "E2",
                    f"tag_query '{d.tag_query}' ({d.id})",
                    "/v2/tags",
                    {"filter.query": d.tag_query, "take": 5},
                )

    async def e3(self):
        for key, out in [
            ("artist", "urn:entity:artist"),
            ("movie", "urn:entity:movie"),
            ("tv", "urn:entity:tv_show"),
            ("brand", "urn:entity:brand"),
            ("artist", "urn:entity:movie"),
            ("brand", "urn:entity:artist"),
        ]:
            eid = await self.seed(key)
            if eid:
                await self.insights(
                    "E3",
                    f"{out} <- {key}",
                    {"filter.type": out, "signal.interests.entities": [eid], "take": 10},
                )
        rid = await self.seed("artist")
        for t in (
            "urn:entity:person",
            "urn:entity:book",
            "urn:entity:podcast",
            "urn:entity:video_game",
            "urn:entity:destination",
        ):
            await self.insights(
                "E3", f"{t} <- artist", {"filter.type": t, "signal.interests.entities": [rid], "take": 5}
            )

    async def e4(self):
        dining = self.domains["dining"]
        for city in self.cities:
            await self.insights(
                "E4",
                f"place filter.location.query {city.id}",
                {"filter.type": "urn:entity:place", "filter.location.query": city.location, "take": 10},
            )
            await self.insights(
                "E4",
                f"place signal.location.query {city.id} + dining params",
                {
                    "filter.type": "urn:entity:place",
                    "signal.location.query": city.location,
                    **dining.params,
                    "take": 10,
                },
            )
            await self.insights(
                "E4",
                f"place filter.location.query {city.id} + dining params",
                {
                    "filter.type": "urn:entity:place",
                    "filter.location.query": city.location,
                    **dining.params,
                    "take": 10,
                },
            )
        for variant in ("Bangalore", "Bengaluru, India", "Sao Paulo", "NYC", "Osaka"):  # name-format probes
            await self.insights(
                "E4",
                f"name format {variant!r}",
                {"filter.type": "urn:entity:place", "filter.location.query": variant, "take": 5},
            )
        await self.insights(
            "E4",
            "unmatched locality name",
            {"filter.type": "urn:entity:place", "filter.location.query": "Xyzzyville Qwerty", "take": 5},
        )
        # Does a location signal move NON-place domains? Same query across cities -> compare.
        sample = self.cities[:3]
        for d in (x for x in self.domains.values() if x.filter_type != "urn:entity:place"):
            runs = []
            for city in sample:
                r, _ = await self.insights(
                    "E4",
                    f"{d.id} signal.location.query {city.id}",
                    {"filter.type": d.filter_type, "signal.location.query": city.location, "take": 10},
                )
                runs.append(r.entities)
            effects = [assert_param_effective(a, b) for a, b in itertools.combinations(runs, 2)]
            self.note(
                "E4", f"{d.id}: location-signal effect across {len(sample)} cities", json.dumps(effects)
            )

    async def e5(self):
        r, _ = await self.get("E5", "audience types", "/v2/audiences/types", {"take": 50})
        await self.get("E5", "audiences (no filter)", "/v2/audiences", {"take": 50})
        types = [x for x in (e.entity_id or e.raw.get("type") or e.raw.get("id") for e in r.entities) if x][
            :2
        ]
        aud_ids: list[str] = []
        for t in types:
            ra, _ = await self.get(
                "E5", f"audiences parent={t}", "/v2/audiences", {"filter.parents.types": t, "take": 20}
            )
            aud_ids += [x for x in (e.entity_id or e.raw.get("id") for e in ra.entities) if x]
        self.results["audience_ids"] = aud_ids[:3]
        rid = await self.seed("artist")
        await self.insights(
            "E5",
            "artist demographics only (expect error)",
            {"filter.type": "urn:entity:artist", "signal.demographics.age": "35_and_younger", "take": 5},
        )
        base = {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 10}
        b0, _ = await self.insights("E5", "artist <- seed baseline", base)
        variants = [
            ("age 55_and_older", {"signal.demographics.age": "55_and_older"}),
            ("age 35_and_younger", {"signal.demographics.age": "35_and_younger"}),
            ("gender female", {"signal.demographics.gender": "female"}),
        ]
        if aud_ids:
            variants.append(("audience", {"signal.demographics.audiences": [aud_ids[0]]}))
        for label, extra in variants:
            r2, _ = await self.insights("E5", f"artist <- seed + {label}", {**base, **extra})
            self.note(
                "E5", f"effect of {label}", json.dumps(assert_param_effective(b0.entities, r2.entities))
            )
        if aud_ids:
            city = self.cities[1]
            await self.insights(
                "E5",
                f"place {city.id} + audience",
                {
                    "filter.type": "urn:entity:place",
                    "filter.location.query": city.location,
                    "signal.demographics.audiences": [aud_ids[0]],
                    "take": 10,
                },
            )

    async def e6(self):
        rid = await self.seed("artist")
        base = {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 10}
        b0, _ = await self.insights("E6", "artist <- seed baseline", base)
        for label, extra in [
            ("popularity.max=0.5", {"filter.popularity.max": 0.5}),
            ("popularity.min=0.9", {"filter.popularity.min": 0.9}),
            ("bias.trends=off", {"bias.trends": "off"}),
            ("bias.trends=high", {"bias.trends": "high"}),
        ]:
            r2, _ = await self.insights("E6", f"artist <- seed {label}", {**base, **extra})
            self.note(
                "E6",
                f"effect of {label} (artist)",
                json.dumps(assert_param_effective(b0.entities, r2.entities)),
            )
        city = self.cities[-1]
        pbase = {"filter.type": "urn:entity:place", "filter.location.query": city.location, "take": 10}
        p0, _ = await self.insights("E6", f"place {city.id} baseline", pbase)
        for label, extra in [
            ("trends off", {"bias.trends": "off"}),
            ("trends high", {"bias.trends": "high"}),
        ]:
            r2, _ = await self.insights("E6", f"place {city.id} {label}", {**pbase, **extra})
            self.note(
                "E6",
                f"effect of {label} (place)",
                json.dumps(assert_param_effective(p0.entities, r2.entities)),
            )

    async def e7(self):
        rid = await self.seed("artist")
        pool, _ = await self.insights(
            "E7",
            "candidate pool (artist <- seed, take 50)",
            {"filter.type": "urn:entity:artist", "signal.interests.entities": [rid], "take": 50},
        )
        ids = [e.entity_id for e in pool.entities if e.entity_id]
        ng = await self.seed("artist_ng")
        if ng:
            ids = [ng, *ids]
        city = self.cities[1]
        for n in sorted({5, 25, len(ids)}):
            if 0 < n <= len(ids):
                r, row = await self.insights(
                    "E7",
                    f"score {n} fixed candidates vs location {city.id}",
                    {
                        "filter.type": "urn:entity:artist",
                        "filter.results.entities": ids[:n],
                        "signal.location.query": city.location,
                        "take": min(n, 50),
                    },
                )
                returned = {e.entity_id for e in r.entities}
                self.note(
                    "E7",
                    f"coverage for {n} candidates",
                    f"sent {n}; returned {len(returned)}; all_within_candidates={returned <= set(ids[:n])}; "
                    f"scored={row['has_affinity']}",
                )
        if ng:
            await self.insights(
                "E7",
                "score 10 candidates vs entity signal",
                {
                    "filter.type": "urn:entity:artist",
                    "filter.results.entities": ids[:10],
                    "signal.interests.entities": [ng],
                    "take": 10,
                },
            )

    async def e8(self):
        rid = await self.seed("artist")
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
        city = self.cities[2] if len(self.cities) > 2 else self.cities[0]
        await self.insights(
            "E8",
            f"explainability place {city.id}",
            {
                "filter.type": "urn:entity:place",
                "signal.interests.entities": [rid],
                "filter.location.query": city.location,
                "feature.explainability": True,
                "take": 5,
            },
        )

    async def e9(self):
        loc = self.cities[-2].location if len(self.cities) > 1 else self.cities[0].location
        place = {"filter.type": "urn:entity:place", "filter.location.query": loc}
        probes: list[tuple[str, str, dict, dict]] = [  # GET only (CLAUDE.md): error shapes, no POST
            ("no key header", "/search", {"query": "x", "take": 2}, {"send_key": False}),
            ("bad key", "/search", {"query": "x", "take": 2}, {"api_key": "invalid-key-for-recon"}),
            ("unknown path", "/v2/nope", {}, {}),
            ("bare base url", "/", {}, {}),
            ("insights missing filter.type", "/v2/insights", {"take": 2}, {}),
            (
                "insights unsupported filter.type",
                "/v2/insights",
                {"filter.type": "urn:entity:food_and_drink", "filter.location.query": loc},
                {},
            ),
            ("insights take=0", "/v2/insights", {**place, "take": 0}, {}),
            ("insights take=200", "/v2/insights", {**place, "take": 200}, {}),
            (
                "insights free-text tag",
                "/v2/insights",
                {"filter.type": "urn:entity:place", "signal.interests.tags": "not-a-tag"},
                {},
            ),
        ]
        for label, path, params, kw in probes:
            await self.raw("E9", label, path, params, **kw)
        base = {**place, "take": 5}
        p1, _ = await self.insights("E9", "page 1", {**base, "page": 1})
        p2, _ = await self.insights("E9", "page 2", {**base, "page": 2})
        o5, _ = await self.insights("E9", "offset 5", {**base, "offset": 5})
        overlap = {e.entity_id for e in p1.entities} & {e.entity_id for e in p2.entities}
        same = [e.entity_id for e in p2.entities] == [e.entity_id for e in o5.entities]
        self.note("E9", "pagination analysis", f"page1&page2 overlap={len(overlap)}; page2==offset5: {same}")

    async def e10(self):
        city = self.cities[2] if len(self.cities) > 2 else self.cities[0]
        r, _ = await self.insights(
            "E10",
            f"places {city.id} for cross-refs",
            {"filter.type": "urn:entity:place", "filter.location.query": city.location, "take": 5},
        )
        ids = [e.entity_id for e in r.entities if e.entity_id][:3]
        if ids:
            ents, _ = await self.get("E10", "GET /entities for 3 places", "/entities", {"entity_ids": ids})
            for i, e in enumerate(ents.entities[:3] or r.entities[:3]):
                props = e.raw.get("properties", {}) if isinstance(e.raw, dict) else {}
                self.note(
                    "E10",
                    f"fields of place #{i}",
                    json.dumps(
                        {
                            "location_keys": sorted((e.location or {}).keys()),
                            "property_keys": sorted(props.keys()),
                            "external_keys": sorted((e.raw.get("external") or {}).keys()),
                            "has_address": bool(props.get("address")),
                            "geocode_keys": sorted((props.get("geocode") or {}).keys()),
                        }
                    ),
                )

    async def e11(self):
        """Where do affinity scores appear? Per domain x signal combination, plus rank stability."""
        rid = await self.seed("artist")
        cities = [next(c for c in self.cities if c.tier == 1), next(c for c in self.cities if c.tier == 2)]
        aud = (self.results.get("audience_ids") or [None])[0]
        summary: dict[str, Any] = {}
        for d in self.domains.values():
            tag_ids: list[str] = []
            if d.tag_query:
                tag_ids, _ = await self.client.resolve_ids("signal.interests.tags", [d.tag_query], self.c)
            for city in cities:
                loc = d.location_params(city)
                combos: dict[str, dict | None] = {
                    "location_only": {**loc},
                    "location+entity": {**loc, "signal.interests.entities": [rid]},
                    "location+tag": {**loc, "signal.interests.tags": tag_ids} if tag_ids else None,
                    "location+age": {**loc, "signal.demographics.age": "35_and_younger"},
                    "location+audience": {**loc, "signal.demographics.audiences": [aud]} if aud else None,
                }
                if city is cities[0]:
                    combos["entity_only"] = {"signal.interests.entities": [rid]}
                for name, extra in combos.items():
                    if extra is None:
                        summary[f"{d.id}/{city.id}/{name}"] = "skipped (no id resolved)"
                        continue
                    _, row = await self.insights(
                        "E11",
                        f"{d.id} {city.id} {name}",
                        {"filter.type": d.filter_type, **d.params, **extra, "take": 10},
                    )
                    summary[f"{d.id}/{city.id}/{name}"] = {
                        k: row.get(k)
                        for k in (
                            "outcome",
                            "count",
                            "has_affinity",
                            "affinity_sorted",
                            "per_result_query_keys",
                        )
                    }
            # rank stability without scores: does take=10 equal the first 10 of take=20?
            loc = d.location_params(cities[0])
            r10, _ = await self.insights(
                "E11",
                f"{d.id} rank stability take10",
                {"filter.type": d.filter_type, **d.params, **loc, "take": 10},
            )
            r20, _ = await self.insights(
                "E11",
                f"{d.id} rank stability take20",
                {"filter.type": d.filter_type, **d.params, **loc, "take": 20},
            )
            summary[f"{d.id}/rank_stability"] = {
                "top10_identical": [e.entity_id for e in r10.entities]
                == [e.entity_id for e in r20.entities[:10]],
                "n10": r10.count,
                "n20": r20.count,
            }
        self.note("E11", "summary", json.dumps(summary))


def shape_check() -> int:
    """Compare committed synthetic fixtures with field names seen in real recordings (0 calls)."""
    from blindspot.qloo.models import extract_items

    raw = sorted((DATA / "raw").glob("*.json"))
    if not raw:
        print("no recordings in data/raw yet; run smoke.py/recon.py with a key first")
        return 1
    seen: dict[str, set[str]] = {}
    top: dict[str, set[str]] = {}
    for p in raw:
        doc = json.loads(p.read_text(encoding="utf-8"))
        ep, body = doc.get("endpoint"), doc.get("body")
        if isinstance(body, dict):
            top.setdefault(ep, set()).update(body.keys())
        items = extract_items(body)
        if items and isinstance(items[0], dict):
            seen.setdefault(ep, set()).update(items[0].keys())
    ok = True
    for ep, fname in {"/search": "search_synthetic.json", "/v2/insights": "insights_synthetic.json"}.items():
        body = json.loads((ROOT / "tests" / "fixtures" / fname).read_text(encoding="utf-8"))
        fkeys, ftop = set(extract_items(body)[0].keys()), {k for k in body if not k.startswith("_")}
        missing = sorted(fkeys - seen.get(ep, set())) if seen.get(ep) else []
        missing_top = sorted(ftop - top.get(ep, set())) if top.get(ep) else []
        print(
            f"{ep}: fixture item keys never seen live: {missing or 'none'}; "
            f"fixture top-level keys never seen live: {missing_top or 'none'}"
        )
        ok &= not missing and not missing_top
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma list, e.g. E1,E11")
    ap.add_argument("--max-calls", type=int, default=200)
    ap.add_argument("--shape-check", action="store_true")
    a = ap.parse_args()
    if a.shape_check:
        return shape_check()
    client = make_client("record")
    r = Recon(client, a.max_calls)
    order = [f"E{i}" for i in range(1, 12)]
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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
