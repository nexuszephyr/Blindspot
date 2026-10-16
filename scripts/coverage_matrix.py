"""Coverage matrix: cities x domains (both from config/) x probe types.

    python scripts/coverage_matrix.py [--max-calls 150]

Probe types:
  core       domain + location + audience (age bracket)
  bridge     core + a signal entity from a *different* domain in the same city
  audience   location + a contrasting demographic (female, 55_and_older)
  freshness  core + bias.trends=high
  swap       computed offline: how different the core results are between cities

Each cell's status comes from the client outcome: ok | empty (200, no items) | qloo_error (Qloo
said no) | resolution_failed (no id; nothing sent) | invalid | not_live (budget/breaker).
A local refusal is never recorded as a Qloo error.

Full rows (with result names) go to data/coverage/ (gitignored: Qloo data stays out of git).
docs/coverage_matrix.csv and docs/coverage_grid.md get statuses, counts, latency and overlap only.
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
from blindspot.catalog import load_cities, load_domains

BRIDGE_FROM = {"dining": "music", "music": "dining", "film_tv": "music", "brands": "music"}
PROBES = ["core", "bridge", "audience", "freshness"]


def geo_match(rows: list[dict[str, Any]], city) -> str:
    """Rough plausibility for places: how many results carry the city's country/city in geocode/address."""
    hints = {city.location.lower(), city.display_name.lower(), city.country.lower()}
    hits = 0
    for e in rows:
        props = e.get("properties") or {}
        geo = props.get("geocode") or {}
        blob = json.dumps([props.get("address"), geo], ensure_ascii=False).lower()
        hits += (
            any(h in blob for h in hints) or str(geo.get("country_code", "")).lower() == city.country.lower()
        )
    return f"geo-match {hits}/{len(rows)}"


def jaccard(a: set[str], b: set[str]) -> float | None:
    return None if not a or not b else len(a & b) / len(a | b)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=150)
    a = ap.parse_args()
    cities = load_cities(ROOT / "config")
    domains = {d.id: d for d in load_domains(ROOT / "config")}
    client = make_client("record")
    c = ctx("coverage", purpose="coverage")
    start = live_used(client)
    rows: list[dict[str, Any]] = []
    tops: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    async def probe(city, d, kind, extra):
        params = {
            "filter.type": d.filter_type,
            **d.params,
            **d.location_params(city),
            **extra,
            "take": d.take,
        }
        if live_used(client) - start >= a.max_calls:
            res = None
            outcome, n, lat, top, reason, unv = "skipped", 0, None, [], "coverage call cap reached", []
        else:
            res = await client.insights(params, c, strict=False)
            outcome, n, lat, reason, unv = (
                res.outcome,
                res.count,
                res.latency_ms,
                res.reason,
                res.unverified_params,
            )
            top = [e.raw for e in res.entities]
        tops[(city.id, d.id, kind)] = top
        note = geo_match(top, city) if d.filter_type == "urn:entity:place" and top else ""
        has_aff = bool(res and any(e.affinity is not None for e in res.entities))
        row = {
            "city": city.id,
            "domain": d.id,
            "probe": kind,
            "status": outcome,
            "count": n,
            "latency_ms": None if lat is None else round(lat),
            "served_from": None if res is None else res.served_from,
            "has_affinity": has_aff,
            "auto_note": note,
            "reason": reason or "",
            "unverified_params": ",".join(unv),
            "params": json.dumps(params, ensure_ascii=False),
            "top5": " | ".join(str(e.get("name")) for e in top[:5]),
        }
        rows.append(row)
        print(
            f"{city.id:10} {d.id:8} {kind:9} {outcome:17} n={n:<3} aff={has_aff!s:5} {note} {(reason or '')[:60]}"
        )

    try:
        for city in cities:
            for d in domains.values():
                await probe(city, d, "core", {"signal.demographics.age": "35_and_younger"})
        for city in cities:
            for d in domains.values():
                src = tops.get((city.id, BRIDGE_FROM.get(d.id, "music"), "core")) or []
                seed = next((e.get("entity_id") for e in src if e.get("entity_id")), None)
                if seed:
                    await probe(city, d, "bridge", {"signal.interests.entities": [seed]})
                else:
                    rows.append(
                        {
                            "city": city.id,
                            "domain": d.id,
                            "probe": "bridge",
                            "status": "resolution_failed",
                            "count": 0,
                            "reason": "no seed entity: the bridge domain's core probe returned none",
                        }
                    )
                await probe(
                    city,
                    d,
                    "audience",
                    {"signal.demographics.gender": "female", "signal.demographics.age": "55_and_older"},
                )
                await probe(
                    city, d, "freshness", {"signal.demographics.age": "35_and_younger", "bias.trends": "high"}
                )
    finally:
        await client.aclose()

    ids = lambda k: {e.get("entity_id") for e in tops.get(k, [])} - {None}  # noqa: E731
    for d in domains.values():
        sets = {city.id: ids((city.id, d.id, "core")) for city in cities}
        pairs = [(x, y, jaccard(sets[x], sets[y])) for x, y in itertools.combinations(sets, 2)]
        valid = [p for p in pairs if p[2] is not None]
        mean = sum(p[2] for p in valid) / len(valid) if valid else None
        feasible = mean is not None and mean < 0.3 and len(valid) >= max(1, len(pairs) * 2 // 3)
        rows.append(
            {
                "city": "*",
                "domain": d.id,
                "probe": "swap",
                "status": "ok" if feasible else ("empty" if not valid else "weak"),
                "count": len(valid),
                "auto_note": f"mean core overlap between cities={'n/a' if mean is None else round(mean, 2)}",
                "reason": json.dumps({f"{x}~{y}": None if j is None else round(j, 2) for x, y, j in pairs}),
            }
        )
        for kind in ("freshness", "audience", "bridge"):
            ov = [jaccard(ids((city.id, d.id, "core")), ids((city.id, d.id, kind))) for city in cities]
            ov = [x for x in ov if x is not None]
            rows.append(
                {
                    "city": "*",
                    "domain": d.id,
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
        "has_affinity",
        "auto_note",
        "reason",
        "unverified_params",
        "params",
        "top5",
    ]
    with full.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    public_fields = fields[:9]  # no result names, no raw reasons from Qloo
    with (ROOT / "docs" / "coverage_matrix.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=public_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    write_markdown(rows, cities, domains)
    print(f"\ncoverage live calls: {live_used(client) - start}; lifetime total: {live_used(client)}")
    print(f"full rows (gitignored): {full}\npublic: docs/coverage_matrix.csv + docs/coverage_grid.md")
    return 0


def write_markdown(rows, cities, domains) -> None:
    lines = [
        "<!-- generated by scripts/coverage_matrix.py; the reading is in docs/coverage_matrix.md -->",
        "# Coverage grid (generated)",
        "",
        "Cell = outcome / result count (`aff` = affinity scores present). Outcomes: ok, empty (200, no items),"
        " qloo_error, resolution_failed (no id; nothing sent), skipped.",
        "",
    ]
    for kind in PROBES:
        lines += [
            f"## Probe: {kind}",
            "",
            "| city | " + " | ".join(domains) + " |",
            "|---|" + "---|" * len(domains),
        ]
        for city in cities:
            cells = []
            for d in domains:
                r = next(
                    (x for x in rows if x["city"] == city.id and x["domain"] == d and x["probe"] == kind),
                    None,
                )
                cells.append(
                    "-"
                    if r is None
                    else f"{r['status']} / {r['count']}"
                    + (" aff" if r.get("has_affinity") else "")
                    + (f" ({r['auto_note']})" if r.get("auto_note") else "")
                )
            lines.append(f"| {city.display_name} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Derived", "", "| domain | probe | status | note |", "|---|---|---|---|"]
    lines += [
        f"| {r['domain']} | {r['probe']} | {r['status']} | {r.get('auto_note', '')} |"
        for r in rows
        if r["city"] == "*"
    ]
    (ROOT / "docs" / "coverage_grid.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
