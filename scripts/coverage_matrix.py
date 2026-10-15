"""Coverage matrix: 6 cities x 4 domains x probe types (about 96 live calls).

    python scripts/coverage_matrix.py [--max-calls 150] [--place-loc filter|signal] [--other-loc signal|none]

Probe types:
  core       domain + location + audience (age bracket)
  bridge     core + a signal entity from a *different* domain in the same city
  audience   location + a contrasting demographic (female, 55_and_older)
  freshness  core + bias.trends=high
  swap       computed offline: how different the core results are between cities

Full rows (with result names) go to data/coverage/ (gitignored: Qloo data stays out of git).
docs/coverage_matrix.csv and docs/coverage_grid.md get statuses, counts, latency and overlap only;
docs/coverage_matrix.md holds the hand-written reading and recommendation.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import sys
import time
from typing import Any

from _common import DATA, ROOT, ctx, live_used, make_client

CITIES = {
    "Bengaluru": ("bengaluru", "bangalore", "IN"),
    "Lagos": ("lagos", "NG"),
    "Tokyo": ("tokyo", "JP"),
    "São Paulo": ("são paulo", "sao paulo", "BR"),
    "Berlin": ("berlin", "DE"),
    "New York": ("new york", "brooklyn", "manhattan", "US"),
}
DOMAINS = {
    "dining": ("urn:entity:place", {"filter.tags": "urn:tag:genre:place:restaurant"}),
    "music": ("urn:entity:artist", {}),
    "film_tv": ("urn:entity:movie", {}),
    "brands": ("urn:entity:brand", {}),
}
BRIDGE_FROM = {"dining": "music", "music": "dining", "film_tv": "music", "brands": "music"}
PROBES = ["core", "bridge", "audience", "freshness"]


def geo_match(rows: list[dict[str, Any]], hints: tuple[str, ...]) -> str:
    """Rough plausibility for places: how many results mention the city/country."""
    hits = 0
    for e in rows:
        props = e.get("properties") or {}
        blob = json.dumps(
            [props.get("address"), props.get("geocode"), e.get("location")], ensure_ascii=False
        ).lower()
        hits += any(h.lower() in blob for h in hints)
    return f"geo-match {hits}/{len(rows)}"


def jaccard(a: set[str], b: set[str]) -> float | None:
    return None if not a or not b else len(a & b) / len(a | b)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=150)
    ap.add_argument("--place-loc", choices=["filter", "signal"], default="filter")
    ap.add_argument("--other-loc", choices=["signal", "none"], default="signal")
    ap.add_argument("--take", type=int, default=10)
    a = ap.parse_args()
    client = make_client("record")
    c = ctx("coverage")
    start = live_used(client)
    rows: list[dict[str, Any]] = []
    tops: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def loc_params(etype: str, city: str) -> dict[str, str]:
        if etype == "urn:entity:place":
            return {f"{a.place_loc}.location.query": city}
        return {} if a.other_loc == "none" else {"signal.location.query": city}

    async def probe(city, domain, kind, params):
        if live_used(client) - start >= a.max_calls:
            res = None
        else:
            res = await client.insights(params, c)
        if res is None:
            status, n, lat, top, reason = "skipped", 0, None, [], "coverage call cap reached"
        else:
            n = res.count
            status = "ok" if res.ok and n else ("empty" if res.ok else "error")
            lat, reason = res.latency_ms, res.reason
            top = [e.raw for e in res.entities]
        tops[(city, domain, kind)] = top
        note = geo_match(top, CITIES[city]) if domain == "dining" and top else ""
        row = {
            "city": city,
            "domain": domain,
            "probe": kind,
            "status": status,
            "count": n,
            "latency_ms": None if lat is None else round(lat),
            "served_from": None if res is None else res.served_from,
            "auto_note": note,
            "reason": reason or "",
            "params": json.dumps(params, ensure_ascii=False),
            "top5": " | ".join(str(e.get("name")) for e in top[:5]),
        }
        rows.append(row)
        print(
            f"{city:10} {domain:8} {kind:9} {status:6} n={n:<3} {note} {(reason or '')[:60]} :: {row['top5'][:80]}"
        )

    try:
        for city in CITIES:
            for domain, (etype, extra) in DOMAINS.items():
                await probe(
                    city,
                    domain,
                    "core",
                    {
                        "filter.type": etype,
                        **extra,
                        **loc_params(etype, city),
                        "signal.demographics.age": "35_and_younger",
                        "take": a.take,
                    },
                )
        for city in CITIES:
            for domain, (etype, extra) in DOMAINS.items():
                src = tops.get((city, BRIDGE_FROM[domain], "core")) or []
                seed = next((e.get("entity_id") for e in src if e.get("entity_id")), None)
                if seed:
                    await probe(
                        city,
                        domain,
                        "bridge",
                        {
                            "filter.type": etype,
                            **extra,
                            **loc_params(etype, city),
                            "signal.interests.entities": seed,
                            "take": a.take,
                        },
                    )
                else:
                    rows.append(
                        {
                            "city": city,
                            "domain": domain,
                            "probe": "bridge",
                            "status": "skipped",
                            "count": 0,
                            "reason": "no seed entity from bridge domain",
                        }
                    )
                await probe(
                    city,
                    domain,
                    "audience",
                    {
                        "filter.type": etype,
                        **extra,
                        **loc_params(etype, city),
                        "signal.demographics.gender": "female",
                        "signal.demographics.age": "55_and_older",
                        "take": a.take,
                    },
                )
                await probe(
                    city,
                    domain,
                    "freshness",
                    {
                        "filter.type": etype,
                        **extra,
                        **loc_params(etype, city),
                        "signal.demographics.age": "35_and_younger",
                        "bias.trends": "high",
                        "take": a.take,
                    },
                )
    finally:
        await client.aclose()

    # swap-pair feasibility: low overlap between two cities' core results = a usable swap pair
    for domain in DOMAINS:
        sets = {
            city: {e.get("entity_id") for e in tops.get((city, domain, "core"), [])} - {None}
            for city in CITIES
        }
        pairs = [(x, y, jaccard(sets[x], sets[y])) for x, y in itertools.combinations(CITIES, 2)]
        valid = [p for p in pairs if p[2] is not None]
        mean = sum(p[2] for p in valid) / len(valid) if valid else None
        feasible = mean is not None and mean < 0.3 and len(valid) >= 10
        rows.append(
            {
                "city": "*",
                "domain": domain,
                "probe": "swap",
                "status": "ok" if feasible else ("empty" if not valid else "weak"),
                "count": len(valid),
                "auto_note": f"mean core overlap between cities={'n/a' if mean is None else round(mean, 2)}",
                "reason": json.dumps(
                    {f"{x}~{y}": None if j is None else round(j, 2) for x, y, j in pairs}, ensure_ascii=False
                ),
            }
        )
        # freshness / audience sensitivity: did the extra signal change results?
        for kind in ("freshness", "audience", "bridge"):
            ov = [
                jaccard(
                    {e.get("entity_id") for e in tops.get((city, domain, "core"), [])} - {None},
                    {e.get("entity_id") for e in tops.get((city, domain, kind), [])} - {None},
                )
                for city in CITIES
            ]
            ov = [x for x in ov if x is not None]
            rows.append(
                {
                    "city": "*",
                    "domain": domain,
                    "probe": f"{kind}_vs_core",
                    "status": "info",
                    "count": len(ov),
                    "auto_note": f"mean overlap with core={round(sum(ov) / len(ov), 2) if ov else 'n/a'}",
                }
            )

    stamp = time.strftime("%Y%m%dT%H%M%S")
    (DATA / "coverage").mkdir(parents=True, exist_ok=True)
    full = DATA / "coverage" / f"coverage_{stamp}.csv"
    fields = [
        "city",
        "domain",
        "probe",
        "status",
        "count",
        "latency_ms",
        "served_from",
        "auto_note",
        "reason",
        "params",
        "top5",
    ]
    with full.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    public = ROOT / "docs" / "coverage_matrix.csv"
    with public.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields[:8], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    write_markdown(rows)
    print(f"\ncoverage live calls: {live_used(client) - start}; lifetime total: {live_used(client)}")
    print(f"full rows (with names, gitignored): {full}\npublic grid: {public} + docs/coverage_grid.md")
    return 0


def write_markdown(rows: list[dict[str, Any]]) -> None:
    sym = {"ok": "ok", "empty": "EMPTY", "error": "ERR", "skipped": "skip"}
    lines = [
        "<!-- generated by scripts/coverage_matrix.py; edit the 'Recommendation' section by hand -->",
        "# Coverage matrix (generated grid)",
        "",
        "Cell = status / result count. Full rows with result names are in data/coverage/ (gitignored).",
        "",
    ]
    for kind in PROBES:
        lines += [
            f"## Probe: {kind}",
            "",
            "| city | " + " | ".join(DOMAINS) + " |",
            "|---|" + "---|" * len(DOMAINS),
        ]
        for city in CITIES:
            cells = []
            for d in DOMAINS:
                r = next(
                    (x for x in rows if x["city"] == city and x["domain"] == d and x["probe"] == kind), None
                )
                cells.append(
                    "-"
                    if r is None
                    else f"{sym.get(r['status'], r['status'])} / {r['count']}"
                    + (f" ({r['auto_note']})" if r.get("auto_note") else "")
                )
            lines.append(f"| {city} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Derived", "", "| domain | probe | status | note |", "|---|---|---|---|"]
    for r in rows:
        if r["city"] == "*":
            lines.append(f"| {r['domain']} | {r['probe']} | {r['status']} | {r.get('auto_note', '')} |")
    (ROOT / "docs" / "coverage_grid.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
