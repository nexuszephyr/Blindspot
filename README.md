# Blindspot

Blindspot audits AI agents for **cultural blindness**. It uses Qloo's taste graph as ground truth to measure how far an agent's recommendations are from what a given audience in a given place actually loves, then patches the agent with Qloo grounding.

Built for the Qloo Agentic Hackathon. MIT licensed.

> **Judges:** see [docs/JUDGES.md](docs/JUDGES.md). It covers the hosted demo, running locally with or without a key, and what `served_from` / `degraded` mean.

## Phase 1: Qloo client and service skeleton

What exists so far:

- `src/blindspot/qloo/`: an async Qloo client built to survive judges.
  - Resolution order: cache, then snapshot, then live, then `degraded`; every result carries a `served_from` label.
  - Budgets at four scopes: lifetime, rolling daily with a reserved demo slice, per client per hour, and per run.
  - Token-bucket rate limit, retries that honour `Retry-After`, and a circuit breaker for 429 storms.
  - A per-request kill switch, a SQLite cache and call log ("Truth Receipts"), and key redaction everywhere.
- `src/blindspot/service/`: FastAPI with `/healthz`, `/api/qloo/status` and `/api/qloo/search`, behind per-IP rate limiting.
- `scripts/`: smoke test, recon (E1–E10), coverage matrix, cache warming, snapshot export, secret scan, and the `dev.py` task runner.

```bash
make setup      # create .venv and install the locked dependencies (or: python scripts/dev.py setup)
make demo       # run on http://127.0.0.1:8000; replay mode if there's no QLOO_API_KEY
make test       # offline tests, no key needed
make check      # ruff + tests + secret scan
```

With a key in `.env`:

```bash
python scripts/smoke.py                 # about 4 live calls
python scripts/recon.py                 # E1-E11, about 200 live calls cold (cap: --max-calls 250)
python scripts/coverage_matrix.py       # about 96 live calls (cap: --max-calls 150)
```

Windows without `make`: `python scripts/dev.py setup|demo|test|check`.

```bash
python -m blindspot.qloo.cli budget     # remaining budgets, cache size, breaker state
python -m blindspot.qloo.cli search "Burna Boy" --types urn:entity:artist
python -m blindspot.qloo.cli calls --last 10
```

Cities, domains and demo queries are data in `config/`. Steps only the owner can do: [docs/NEEDS_FROM_USER.md](docs/NEEDS_FROM_USER.md).

Docs: [API reality](docs/qloo_api_reality.md) · [coverage matrix](docs/coverage_matrix.md) · [decisions](docs/decisions.md) · [Phase 1 report](docs/phase1_report.md)

**Data note:** no Qloo response data is committed, because Qloo's hackathon guide says not to store it in a public repository. Test fixtures are synthetic. Cache, recordings and snapshots live in `data/`, which is gitignored.
