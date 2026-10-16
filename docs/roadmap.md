# Roadmap

Submission target: **2026-10-28** (Devpost closes 2026-10-30; the last two days are buffer only).
Each phase has a GitHub milestone. Status is updated at the end of each phase.
Goals, deliverables, exit criteria and risks per phase are in
[architecture.md, Section 19](architecture.md#19-phase-plan-what-each-phase-must-achieve); this file
tracks schedule and status.

| Phase | Milestone due | Goal | Status |
|---|---|---|---|
| 1 | 2026-10-15 | Qloo recon, judge-safe client, service skeleton, three run modes | Done (Docker scan and hosting pending maintainer) |
| 2 | 2026-10-18 | Scenario engine and Qloo answer key, with validity evidence | Not started |
| 3 | 2026-10-20 | Target adapters, entity resolver, metrics, calibration tests | Not started |
| 4 | 2026-10-22 | Auditor agent: drill-down and confirmation with confidence intervals | Not started |
| 5 | 2026-10-23 | Fix loop: Qloo-grounded target, before/after, exported patch | Not started |
| 6 | 2026-10-26 | Web UI and the Qloo highlight layer | Not started |
| 7 | 2026-10-27 | Hardening, precomputed demo, findings write-up, user test | Not started |
| Submit | 2026-10-28 | Devpost submission, final checks from a fresh browser and phone | Not started |

## Phase details

### Phase 1: Foundation (done)
Qloo client with typed models, param guard, cache, call log, retries, rate limit, budgets at three
scopes, circuit breaker, per-request kill switch, request deadline, replay mode. FastAPI skeleton.
Recon E1 to E11 and a 6-city × 4-domain coverage matrix. Offline test suite with mutation checks.
Code imported to https://github.com/nexuszephyr/Blindspot (PR `chore/import-phase1`; F8 closed).
Open: Docker image scan, Render deploy, making the repo public.

### Phase 2: Answer key
Parameter promotion with evidence. Experiments E12 to E15 (result depth, taste-signal pairing for
places, candidate scoring with a taste signal, persona sensitivity). Personas and probe types as
config. 80 to 120 deterministic scenarios with a 30 to 40 scenario demo subset. Resumable
ground-truth build. Validity suite V1 to V9. Metrics design note. Coverage probe for any location.
Synthetic public snapshot and private real snapshot.

### Phase 3: Scoring
OpenAI-compatible target adapters with preloaded models. Entity resolver using the offline
`entity_index` first. Taste-fit, famous-vs-local gap, swap sameness, phantom rate, Blindspot Score.
Calibration: Qloo-copy target high, global-top-10 target low, random target near zero.

### Phase 4: Auditor agent
Plan, probe, score, pick the weakest cell, add probes, confirm with bootstrap confidence intervals,
report. Budget limits for calls, tokens and time. Streamed trace events.

### Phase 5: Fix
Target wrapped with Qloo tools and restricted to Qloo-returned entities. Re-run the same exam,
compute the honest delta. Export system prompt plus MCP config.

### Phase 6: UI
In order: Roast-an-agent start screen, Blind Map, Truth Table, Truth Receipts, Qloo kill switch,
live call ticker, before/after view. Responsive for phones.

### Phase 7: Ship
Precomputed demo audits and model leaderboard. Abuse and quota hardening. Blind user test with 15 to
20 people. Devpost write-up, README, screenshots, architecture diagram.

## Cut order if behind

1. CI badge and score-regression GitHub Action
2. Model leaderboard
3. Live call ticker
4. Full MCP server (keep the exported config)
5. Freshness and audience probes

Never cut: calibration tests, Truth Table, kill switch, Truth Receipts.
