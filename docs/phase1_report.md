# Phase 1 report

Date: 2026-10-13. **The Qloo API key was not available**: there's no `.env` on the dev machine and no `QLOO_API_KEY` in the environment. The owner chose to do all key-free work first and stop before any live call. Everything that needs the key is ready and marked PENDING.

## What works (tested)

- **Client:**
  - Cache with normalized keys and TTL; call log queryable by run id.
  - Retries (429 with Retry-After, 5xx, timeouts; no retry on other 4xx); token-bucket rate limiting; global concurrency semaphore; hard timeouts.
  - `take` clamping and param size limits; endpoint allow-list.
  - Budgets at four scopes with a demo reserve; circuit breaker; per-request kill switch; ops master switch.
  - Coalescing of identical concurrent queries; replay mode; record mode (raw bodies with no headers).
  - Snapshot export and startup seeding; fallback when the DB path is unwritable; key redaction.
- **Tests:** 50 offline tests pass on Windows (Python 3.11) and on Linux (`python:3.12`); the one live test is opt-in. Ruff is clean. pip-audit found no known vulnerabilities in `requirements.lock`.
- **Service:** `/healthz` (GET/HEAD), `/api/qloo/status` (no secrets; checked by a test and in Docker with a fake key), `/api/qloo/search`, per-IP rate limit, trusted-proxy XFF handling, restricted CORS, security headers.
- **Run modes:** a fresh clone with no `.env` runs `make demo` in replay mode on Linux (container) and Windows (`mingw32-make`), healthy in about 78–80 s on first run (dependency install). The Docker image (206 MB, non-root) is healthy about 3.2 s after `docker run`. `docker compose up --build` works.
- **Secrets:** the scanner checks the tree, git history and the Docker image (layers + history + inspect). A planted fake key was detected in all three; the real tree, history and image are clean.
- **Real host checks without a key:** the auth error envelope, 401 behavior, latency, and that the host is in AWS us-east-1.

## What doesn't work or isn't done yet

| Item | Status | Needed |
|---|---|---|
| Authenticated Qloo call (gate 1) | PENDING-USER | `.env` with `QLOO_API_KEY` |
| E1–E10 answers (gate 2) | Questions, documented answers and probes written; live answers pending | `python scripts/recon.py` |
| Coverage matrix (gate 3) | Script ready; grid and recommendation pending | `python scripts/coverage_matrix.py` |
| Live mode with a key (gate 7, live half) | Only checked with a fake key (it degrades correctly to "Qloo rejected the server's API key") | key |
| Real snapshot | Mechanism tested with a **synthetic** snapshot only | key, then `warm_cache.py` + `export_snapshot.py` |
| Hosted deploy (gate 11) | PENDING-USER | GitHub push + Render blueprint (steps in JUDGES.md) |
| Response models | Field names come from docs and SDKs (UNVERIFIED); parsing is tolerant and keeps `raw` | `recon.py --shape-check` after recon |

## Next steps once the key is in place (about 1 hour, about 221 calls)

1. `python scripts/smoke.py`. If it fails with 401, check the key and base URL.
2. `python scripts/recon.py`, then `python scripts/recon.py --shape-check`.
3. Update `docs/qloo_api_reality.md` from `data/recon/*.jsonl`: move each row to VERIFIED or FAILED, and adjust the models if the shapes differ.
4. `python scripts/coverage_matrix.py`, adding `--other-loc none` or `--place-loc signal` if E4 points that way. Then write the recommendation in `docs/coverage_matrix.md`.
5. `python scripts/warm_cache.py && python scripts/export_snapshot.py`, then `QLOO_LIVE_TESTS=1 pytest -m live`.

## Candidates to cut in Phase 2 (decide after recon)

- **Location for non-place domains.** The entity-type guide lists no `signal.location*` for artist, movie or brand. If E4 shows results don't change by city, localize those domains through audiences, regional tags or local-place bridge signals, or drop them. Dining (places) is the safest domain.
- **Swap-pair probe.** Drop it for any domain whose core results overlap heavily between cities.
- **`bias.trends` (freshness).** Drop it if E6 and the freshness probe show no measurable change.
- **Audience ids.** If `/v2/audiences` is unavailable on the hackathon host, use only the age and gender brackets.
- **Candidate scoring via `filter.results.entities` (E7).** It's central to "score the agent's own recommendations". If it fails, fall back to search → insights overlap scoring, which is weaker.

## Open questions

1. **Terms:** may a minimal snapshot (ids, names, scores) be committed so judges can run fully offline? Ask in Qloo's Discord #api-help. Until then, none is committed (decisions D5).
2. What is the hackathon key's monthly quota and rate limit? It's unpublished; ask on Discord, especially before opening the hosted demo to judges.
3. Does `video_game` or `videogame` work? (E3)
4. The docs say POST bodies fail for `/v2/insights`, yet weighted signals and `*.query` params are documented as POST-only. Which is right?
5. Render specifics (spin-down time, Secret File mount path, the `X-Forwarded-For` chain) need checking after deploy.

## Live calls used

**0 of 600** with the hackathon key. Six requests went to the real host with no key or a fake key, for connectivity, error-shape and latency checks.
