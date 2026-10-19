"""Promote a Qloo insights parameter with recorded evidence (#12).

    python scripts/promote_param.py <filter.type> <param> <value> [--baseline k=v ...]
        [--city <id> ...] [--resolve param=name[@type] ...] [--apply] [--dry-run]

For each city (default: the first tier-1 and the first tier-2 city in config/cities.yaml) it runs
the baseline and the baseline + <param>=<value> through QlooClient.insights() and compares them.

- Baseline: the domain's standard query from config/domains.yaml (filter.type, its params, the
  city's location, take). `--baseline k=v` EXTENDS that query; it never replaces it. It is sent in
  strict mode, so every baseline param must already be VERIFIED.
- Variant: baseline + the parameter under test, sent with strict=False. Anything else unverified in
  the variant makes the run INCONCLUSIVE.
- Value `@std:N` means "the top N entity ids of this city's standard query" (for
  filter.exclude.entities and filter.results.entities). Only the token is written, never the ids.
- `--resolve P=name` looks the name up with the client's resolver (cached /search or /v2/tags) and
  adds the id to P: to the tested value if P is the tested param (pass value `-`), else to the
  baseline. Names are never sent as free text and never written.

One row per distinct argument set goes to docs/param_evidence.md (aggregates only: no names,
no result ids, no scores). Re-running the same arguments adds no row. `--apply` records the
verdict in src/blindspot/qloo/params.py: VERIFIED -> PROMOTED, INEFFECTIVE -> the do-not-retry
list. It only ever adds. `--dry-run` uses a replay-mode client: it lists every planned request as
cached or LIVE and never reaches the network.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, ctx, live_used, make_client
from blindspot.catalog import load_cities, load_domains
from blindspot.config import Settings
from blindspot.qloo.client import QlooClient
from blindspot.qloo.params import VERIFIED_INSIGHTS, assert_param_effective, check_params, split_list

CONFIG = ROOT / "config"
EVIDENCE = ROOT / "docs" / "param_evidence.md"
PARAMS_PY = ROOT / "src" / "blindspot" / "qloo" / "params.py"

# Thresholds (stated in docs/param_evidence.md too). One swap in a top 10 gives Jaccard 0.82.
JACCARD_CHANGED = 0.9  # set changed: Jaccard <= this
RANK_SHIFT_CHANGED = 1.0  # order changed: mean absolute rank shift over the shared items >= this

STD = re.compile(r"^@std:(\d+)$")
PROMOTED_END = "    # end PROMOTED"
NO_RETRY_END = "# end do-not-retry"


def rank_shift(a: list[str], b: list[str]) -> float | None:
    """Mean |rank in a - rank in b| over items in both lists; None when nothing is shared."""
    shared = set(a) & set(b)
    if not shared:
        return None
    return round(sum(abs(a.index(x) - b.index(x)) for x in shared) / len(shared), 2)


def measure(param: str, base, var, token_ids: list[str] | None) -> dict[str, Any]:
    """Compare two QlooResults. Aggregates only: nothing here may carry a name or a result id."""
    ids_b = [e.entity_id for e in base.entities]
    ids_v = [e.entity_id for e in var.entities]
    cell: dict[str, Any] = {
        "n": f"{base.count}→{var.count}",
        "served_from": f"{base.served_from}/{var.served_from}",
        "scores": "→".join(
            "yes" if any(e.affinity is not None for e in r.entities) else "no" for r in (base, var)
        ),
        "latency_ms": round(var.latency_ms or 0),
        "jaccard": None,
        "shift": None,
        "hits": None,
    }
    if base.outcome != "ok":
        return {**cell, "effect": "error", "reason": f"baseline {base.outcome}: {(base.reason or '')[:80]}"}
    if var.outcome not in ("ok", "empty"):  # an empty variant is a measured change, not an error
        return {**cell, "effect": "error", "reason": f"variant {var.outcome}: {(var.reason or '')[:80]}"}
    extra = sorted(set(var.unverified_params) - {param})
    if extra:
        return {**cell, "effect": "error", "reason": f"other unverified params in variant: {extra}"}
    eff = assert_param_effective(ids_b, ids_v)
    shift = rank_shift(ids_b, ids_v)
    cell.update(jaccard=eff["jaccard"], shift=shift)
    if token_ids is not None:
        cell["hits"] = f"{len(set(token_ids) & set(ids_v))}/{len(token_ids)}"
    if not eff["changed"]:
        return {**cell, "effect": "identical", "reason": ""}
    if param == "filter.exclude.entities" and set(token_ids or []) & set(ids_v):
        return {**cell, "effect": "small", "reason": "excluded ids still returned"}
    big = eff["jaccard"] <= JACCARD_CHANGED or (shift is not None and shift >= RANK_SHIFT_CHANGED)
    return {
        **cell,
        "effect": "changed" if big else "small",
        "reason": "" if big else "change below threshold",
    }


def verdict(cells: list[dict[str, Any]], tiers: list[int]) -> tuple[str, str]:
    """VERIFIED: a change beyond threshold in >= 1 city and no errors. INEFFECTIVE: identical in
    >= 2 cities including a rich (tier 1) and a focus (tier 2) one. Otherwise INCONCLUSIVE."""
    errors = [c["reason"] for c in cells if c["effect"] == "error"]
    if errors:
        return "INCONCLUSIVE", "; ".join(errors)
    if any(c["effect"] == "changed" for c in cells):
        return "VERIFIED", ""
    if cells and all(c["effect"] == "identical" for c in cells):
        if {1, 2} <= set(tiers):
            return "INEFFECTIVE", ""
        return "INCONCLUSIVE", "identical, but not yet in both a tier-1 and a tier-2 city"
    return "INCONCLUSIVE", "; ".join(sorted({c["reason"] for c in cells if c["reason"]}))


def parse_kv(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        k, sep, v = item.partition("=")
        if not sep or not k.strip():
            raise SystemExit(f"expected k=v, got {item!r}")
        out[k.strip()] = v.strip()
    return out


def evidence_id(a: argparse.Namespace, ftype: str, cities: list[str]) -> str:
    key = [ftype, a.param, a.value, sorted(a.baseline), cities, sorted(a.resolve)]
    return "ev_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:8]


def write_row(ev: str, cells_row: list[str], verdict_: str) -> str:
    """Append the row, or replace the row with the same id if its verdict changed. Never duplicates."""
    lines = EVIDENCE.read_text(encoding="utf-8").splitlines()
    row = "| " + " | ".join(c.replace("|", "/") for c in [ev, *cells_row]) + " |"
    for i, line in enumerate(lines):
        if line.startswith(f"| {ev} |"):
            if f"| {verdict_} |" in line:
                return "unchanged"
            lines[i] = row
            break
    else:
        lines.append(row)
    EVIDENCE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return "written"


def apply(ftype: str, param: str, value: str, verdict_: str, ev: str) -> str:
    """Record the verdict in params.py. Only adds; removing an entry needs the maintainer."""
    src = PARAMS_PY.read_text(encoding="utf-8")
    allowed = param in VERIFIED_INSIGHTS.get(ftype, set())
    if verdict_ == "VERIFIED":
        if allowed or f'("{ftype}", "{param}",' in src:
            return "already verified"
        line = f'    ("{ftype}", "{param}", "{ev}"),  # docs/param_evidence.md {ev}\n'
        src = src.replace(PROMOTED_END, line + PROMOTED_END, 1)
    elif verdict_ == "INEFFECTIVE":
        if allowed:
            return f"{param} is allowlisted for {ftype}; not removed (needs the maintainer's approval)"
        if f"#   {ftype} {param}=" in src:
            return "already listed"
        line = f"#   {ftype} {param}={value} (docs/param_evidence.md {ev})\n"
        src = src.replace(NO_RETRY_END, line + NO_RETRY_END, 1)
    else:
        return "nothing to apply"
    PARAMS_PY.write_text(src, encoding="utf-8", newline="\n")
    return "applied"


async def run(a: argparse.Namespace, client: QlooClient) -> dict[str, Any]:
    ftype = a.filter_type if a.filter_type.startswith("urn:") else f"urn:entity:{a.filter_type}"
    domain = next((d for d in load_domains(CONFIG) if d.filter_type == ftype), None)
    all_cities = load_cities(CONFIG)
    if a.city:
        by_id = {c.id: c for c in all_cities}
        missing = [c for c in a.city if c not in by_id]
        if missing:
            raise SystemExit(f"unknown city id(s) {missing}; see config/cities.yaml")
        cities = [by_id[c] for c in a.city]
    else:
        cities = [next(c for c in all_cities if c.tier == t) for t in (1, 2)]
    c = ctx("promote_param", purpose="recon")
    used0 = live_used(client)
    plan: list[str] = []
    overrides: dict[str, Any] = parse_kv(a.baseline)
    value: Any = "" if a.value == "-" else a.value

    for p, name in parse_kv(a.resolve).items():
        name, _, etype = name.partition("@")
        ids, missing = await client.resolve_ids(p, [(name, etype) if etype else name], c)
        if missing:
            raise SystemExit(f"no Qloo id found for {p} ({'not cached' if a.dry_run else 'lookup failed'})")
        if p == a.param:
            value = split_list(value) + ids
        else:
            overrides[p] = split_list(overrides.get(p)) + ids
    if value in ("", []):
        raise SystemExit("empty value: pass a value, @std:N, or --resolve for the tested param")

    def note(city, label, r):
        live = r.served_from not in ("cache", "snapshot")
        plan.append(f"[{city.id}] {label}: {'LIVE' if live else r.served_from}")

    cells = []
    for city in cities:
        std = {"filter.type": ftype, "take": 10}
        if domain:
            std = {"filter.type": ftype, **domain.params, **domain.location_params(city), "take": domain.take}
        base_q = {**std, **overrides}
        check_params("/v2/insights", base_q, strict=True)  # a bad baseline fails here, before any call
        base = await client.insights(base_q, c)
        note(city, "baseline", base)
        val, token_ids = value, None
        m = STD.match(value) if isinstance(value, str) else None
        if m:
            pool = base if base_q == std else await client.insights(std, c)
            if pool is not base:
                note(city, "standard query (@std pool)", pool)
            token_ids = val = [e.entity_id for e in pool.entities[: int(m[1])]]
            if not token_ids:
                if a.dry_run:
                    plan.append(f"[{city.id}] variant: LIVE (needs the pool first)")
                err = {"effect": "error", "reason": f"{city.id}: @std pool empty"}
                cells.append({**measure(a.param, pool, pool, None), **err})
                continue
        var = await client.insights({**base_q, a.param: val}, c, strict=False)
        note(city, "variant", var)
        cells.append(measure(a.param, base, var, token_ids))

    v, reason = verdict(cells, [ct.tier for ct in cities])
    ev = evidence_id(a, ftype, [ct.id for ct in cities])
    out = {"id": ev, "verdict": v, "reason": reason, "cells": cells, "plan": plan}
    out["live_calls"] = live_used(client) - used0
    if a.dry_run:
        return out

    def col(key):
        return ", ".join("-" if x[key] is None else str(x[key]) for x in cells)

    shown_value = value if isinstance(value, str) else ",".join(value)
    base_desc = f"std({domain.id})" if domain else "filter.type+take"
    base_desc += "".join(f" + {k}={','.join(split_list(v))}" for k, v in overrides.items())
    notes = "; ".join(
        x
        for x in [
            reason,
            f"@std hits {col('hits')}" if any(x["hits"] for x in cells) else "",
            f"variant ms {col('latency_ms')}",
        ]
        if x
    )
    row = [
        time.strftime("%Y-%m-%d"),
        ftype.removeprefix("urn:entity:"),
        f"`{a.param}={shown_value}`",
        base_desc,
        ", ".join(ct.id for ct in cities),
        col("n"),
        col("jaccard"),
        col("shift"),
        col("scores"),
        col("served_from"),
        f"**{v}**",
        str(out["live_calls"]),
        notes,
    ]
    out["row"] = write_row(ev, row, f"**{v}**")
    if a.apply:
        out["apply"] = apply(ftype, a.param, shown_value, v, ev)
    return out


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("filter_type", help="e.g. place or urn:entity:place")
    p.add_argument("param")
    p.add_argument("value", help="literal value, @std:N, or - when --resolve supplies it")
    p.add_argument("--baseline", action="append", default=[], help="k=v added to the standard query")
    p.add_argument("--city", action="append", default=[], help="city id from config/cities.yaml")
    p.add_argument("--resolve", action="append", default=[], help="param=name[@urn:entity:type]")
    p.add_argument("--apply", action="store_true", help="record the verdict in params.py")
    p.add_argument("--dry-run", action="store_true", help="list planned calls; make none")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.dry_run:
        client = QlooClient(Settings(qloo_mode="replay"))  # never reaches the network
    else:
        client = make_client("live")
    try:
        out = asyncio.run(run(a, client))
    finally:
        client.db.close()
    for line in out["plan"]:
        print(line)
    if a.dry_run:
        live = sum(" LIVE" in line for line in out["plan"])
        print(f"dry run: {len(out['plan'])} requests, {live} would be live (plus any --resolve misses)")
        return 0
    for cell in out["cells"]:
        print({k: cell[k] for k in ("n", "jaccard", "shift", "scores", "served_from", "effect", "hits")})
    print(
        f"{out['id']}: {out['verdict']} {out['reason']} | row {out['row']} | live calls {out['live_calls']}"
    )
    if "apply" in out:
        print(f"apply: {out['apply']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
