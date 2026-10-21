"""`blindspot groundtruth build|status|export` (#18).

    blindspot groundtruth build [--tag demo|extended|all] [--fresh] [--max-calls N] [--concurrency N]
                                [--timeout S] [--attempts N] [--dry-run]
    blindspot groundtruth status
    blindspot groundtruth export [--out data/groundtruth/answer_keys.json]

`build` regenerates the exam from config/ and the id lock (#16; deterministic), then fetches every
answer key it still lacks through QlooClient (purpose `groundtruth`). It is resumable: re-running
skips finished scenarios, and `--fresh` starts over (the Qloo cache still saves the calls).
`--max-calls` stops before a lookup that would be live once reached. `--timeout` and `--attempts`
replace the web defaults (10 s, 4 attempts) for this batch job: some answer-key queries take 16 to
20 s (dining freshness), and retrying a slow query four times pays four calls for nothing.
`--dry-run` reports the specs to fetch and how many would be live, and touches nothing. Output is
counts only; Qloo names stay in the store under data/ (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..catalog import load_catalog
from ..config import Settings
from ..console import utf8_console
from ..idlock import load_lock
from ..qloo.client import QlooClient
from ..qloo.redact import install_log_redaction
from ..scenarios.generator import generate
from .api import NotBuilt, default_store_path, get_answer_key
from .builder import Builder, Report, SpecError
from .store import Store

EXPORT_REL = Path("data/groundtruth/answer_keys.json")
BUILD_TIMEOUT_S = 45.0  # the slowest answer-key queries took 16 to 20 s (freshness, #18 live run)
BUILD_ATTEMPTS = 2  # one retry: a query that timed out twice won't be retried here again this run


def live_settings(timeout: float, attempts: int) -> Settings:
    """Client settings for the build: live, with the batch timeout and retry count."""
    if timeout <= 0 or attempts < 1:
        raise SystemExit("--timeout must be > 0 and --attempts >= 1")
    return Settings(qloo_mode="live", qloo_timeout_s=timeout, qloo_max_attempts=attempts)


def _scenarios(config: Path, tag: str):
    cat = load_catalog(config)
    sc = generate(cat, load_lock(config)).scenarios
    words = [w for c in cat.cities for w in (c.display_name, c.location)]
    return (sc if tag == "all" else [s for s in sc if tag in s.tags]), words


def _print_report(r: Report, dry: bool) -> None:
    print(f"scenarios: {r.scenarios}  distinct specs: {r.specs}  already stored: {r.reused}")
    if dry:
        print(f"to fetch: {r.specs - r.reused}  would be live (not cached): {r.planned_live}")
        return
    print(f"fetched this run: {r.fetched}  broader retries used: {r.broadened}  live calls: {r.live_calls}")
    print("status: " + ", ".join(f"{k} {v}" for k, v in sorted(r.statuses.items())))
    if r.excluded:
        print(f"excluded: {len(r.excluded)} (conditions or prompt lint; see `status`)")
    print(f"prompt lint problems: {r.lint_problems}")
    if r.stopped:
        print(f"STOPPED: {r.stopped}. Re-run to resume.")


def _status(store: Store) -> int:
    counts = store.status_counts()
    print(
        "scenarios: "
        + ", ".join(f"{k} {counts.get(k, 0)}" for k in ("done", "thin", "excluded", "failed", "pending"))
    )
    cov = store.query("SELECT kind, coverage, COUNT(*) n FROM ground_truth_runs GROUP BY kind, coverage")
    print("specs: " + ", ".join(f"{r['kind']} {r['coverage']} {r['n']}" for r in cov))
    src = store.query("SELECT served_from, COUNT(*) n FROM ground_truth_runs GROUP BY served_from")
    print("fetched from: " + ", ".join(f"{r['served_from']} {r['n']}" for r in src))
    for r in store.query(
        "SELECT status, reason, COUNT(*) n FROM scenario_status "
        "WHERE status IN ('thin','excluded','empty','fetched','failed') "
        "GROUP BY status, reason ORDER BY status, n DESC"
    ):
        print(f"  {r['status']}: {r['n']} x {r['reason']}")
    print("rows: " + ", ".join(f"{k} {v}" for k, v in store.table_counts().items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blindspot groundtruth", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=Path("config"))
    ap.add_argument("--db", type=Path, help="answer-key store (default: GROUNDTRUTH_PATH or <repo>/data/...)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--tag", choices=["demo", "extended", "all"], default="all")
    b.add_argument("--fresh", action="store_true", help="forget stored runs and start over")
    b.add_argument("--max-calls", type=int, default=450)
    b.add_argument("--concurrency", type=int, default=4)
    b.add_argument("--timeout", type=float, default=BUILD_TIMEOUT_S, help="seconds per Qloo request")
    b.add_argument(
        "--attempts", type=int, default=BUILD_ATTEMPTS, help="tries per request (timeout, 5xx, 429)"
    )
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    ex = sub.add_parser("export")
    ex.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    utf8_console()
    install_log_redaction()
    root = a.config.resolve().parent
    db = a.db or default_store_path(a.config)
    store = Store(db)
    try:
        if a.cmd == "status":
            return _status(store)
        scenarios, words = _scenarios(a.config, getattr(a, "tag", "all"))
        if a.cmd == "export":
            keys = []
            for s in scenarios:
                try:
                    keys.append(get_answer_key(s.id, store).model_dump(mode="json"))
                except NotBuilt:
                    continue
            out = a.out or root / EXPORT_REL
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(keys, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"exported {len(keys)} of {len(scenarios)} answer keys to {out} (gitignored)")
            return 0
        if a.dry_run:
            client = QlooClient(Settings(qloo_mode="replay"))  # never reaches the network
        else:
            client = QlooClient(live_settings(a.timeout, a.attempts))
            if not client.s.has_key:
                client.db.close()
                raise SystemExit("QLOO_API_KEY is missing: put it in .env (the build needs live Qloo access)")
            client.startup()
        try:
            builder = Builder(
                client, store, concurrency=a.concurrency, max_calls=a.max_calls, location_words=words
            )
            try:
                if a.dry_run:
                    _print_report(builder.plan(scenarios, resume=not a.fresh), dry=True)
                    return 0
            except SpecError as exc:
                print(exc, file=sys.stderr)
                return 2

            async def run() -> Report:
                try:
                    return await builder.build(scenarios, resume=not a.fresh)
                finally:
                    await client.aclose()  # same event loop as the requests

            try:
                report = asyncio.run(run())
            except SpecError as exc:  # raised before any call is made
                print(exc, file=sys.stderr)
                return 2
            _print_report(report, dry=False)
            return 1 if report.stopped else 0
        finally:
            client.db.close()
    finally:
        store.close()
