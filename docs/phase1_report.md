# Phase 1 report

Updated 2026-10-15, after the audit fix pass. The original audit is [phase1_audit.md](phase1_audit.md); its "Fix pass" section lists every finding's status.

## Gates

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Real authenticated call | PASS | smoke and recon: 200 responses with the key, budget counter +1 per call |
| 2 | Reality doc answers E1–E10 (+E11) | PASS | [qloo_api_reality.md](qloo_api_reality.md). Every claim is labelled VERIFIED, FAILED or UNVERIFIED |
| 3 | Coverage matrix + recommendation | PASS | [coverage_matrix.md](coverage_matrix.md), [coverage_grid.md](coverage_grid.md), [coverage_matrix.csv](coverage_matrix.csv): 96/96 cells `ok` |
| 4 | Client features | PASS | 79 offline tests, plus behavioural checks against a fake server during the audit |
| 5 | Offline tests, ruff, pip-audit | PASS | 3 randomized runs, each `79 passed, 1 skipped`, with no key and outbound network blocked. `ruff check` and `ruff format --check` are clean. pip-audit: no known vulnerabilities in either lockfile |
| 6 | Fresh clone, no `.env`, `make demo` | PASS (with caveat) | Healthy in 44 s, `mode: replay`. A snapshot-served search works using the *synthetic* snapshot (`QLOO_SNAPSHOT_PATH`). No real snapshot ships, for terms reasons |
| 7 | Live with key; graceful degradation | PASS | The live run, plus the audit J2 check (`live` → `cache` → `disabled`) |
| 8 | 50 concurrent requests, cap respected | PASS | `test_fifty_concurrent_requests_respect_cap` and `test_fifty_concurrent_429_none_hang` (p95 < 8.5 s) |
| 9 | Secret-free tree, history, image | PARTIAL | Tree and 12 commits are clean with the real key loaded. **Image: PENDING-USER** (Docker Desktop off) |
| 10 | JUDGES.md commands executed | PARTIAL | Python and make paths were re-run from a fresh clone. Docker paths are PENDING-USER. Repo URL is real (https://github.com/nexuszephyr/Blindspot); F8 closed |
| 11 | Hosted skeleton | PASS | https://blindspot-tva4.onrender.com live since 2026-10-16: `/healthz` 200 `mode: live`, `/status` shows no secrets, search `live` then `cache`, 7/8 `verify_deploy.py` checks on 2026-10-17 (the 8th, client identity, is fixed in #80). Keepalive run 39057710749 green. Log in [JUDGES.md](JUDGES.md) |
| 12 | MIT license, gitignore | PASS | unchanged |
| 13 | Live calls under budget | PASS | 285 this pass (budget 300); breakdown below |

## Live calls

The `live_attempts` table counts every upstream request, retries included.

| Purpose | Calls |
|---|---|
| audit (earlier) | 4 |
| smoke | 3 |
| recon | 194 (161 completed calls; the rest are retries of timed-out place queries) |
| coverage | 88 |
| **Total** | **289** |

- This pass used 285 of 300.
- Monthly quota: 10,000. The header read **9,801** remaining before coverage, so about **9,713** remain (plus 1 call made by the audit's judge-simulation clone).

## Recommended set and probes

- **Cities:** all six (Bengaluru, Lagos, Tokyo, São Paulo, Berlin, New York). Every cell has data, and results are strongly city-specific (swap overlap ≤ 0.12).
- **Domains:** all four.
  - Music, film/TV and brands: about 1 s, and every signal works.
  - Dining: about 2.4 s. Use `filter.location.query` + tag, and avoid `signal.location.query` on places, which timed out.
- **Probe types:**

  | Probe | Verdict |
  |---|---|
  | core | viable, all domains |
  | bridge | viable, all domains |
  | swap | viable, all domains |
  | audience | age/gender for non-place domains; **audience ids** for dining (age and gender don't move places) |
  | freshness (`bias.trends`) | **dining only** (a no-op for the other domains) |

## E11 (scores)

- Affinity (`query.affinity`) is missing only for **places queried by location alone**. Any taste signal (entity, tag, audience, even age) adds it.
- Music, film and brands have affinity even with location alone.
- Rank order is deterministic (`take=10` equals the top 10 of `take=20` in every domain).
- `filter.results.entities` with location alone returns only 1 of N candidates; add a taste signal.
- **Recommendation:** use rank-based metrics as the primary measure (overlap@k, rank position, rank correlation). Treat affinity as within-query only, and always pair location with a taste signal when scoring candidates.

## Surprises (all VERIFIED)

- `urn:entity:videogame`, not `video_game`, is accepted, the opposite of the guide.
- Unsupported types return 400, not 403.
- `GET /` returns 200 with API info.
- There's a second error envelope, `{"errors":[…]}`.
- `/search` returns only `results` (no `success` or `duration`); its `take` max is 100, while insights allows 1–50.
- The tag and audience *type* endpoints use the `tag_types` and `audience_types` containers.
- Audience items have a URN `id` and a UUID `entity_id`; only the URN works as a signal.
- A free-text tag id makes the host hang instead of erroring.

## Open questions

1. Terms: may a minimal snapshot be committed? A ready-to-paste message is in NEEDS_FROM_USER.md.
2. Can quota be raised for the public demo during judging?
3. 429 and 5xx shapes were never observed (not triggered deliberately).
