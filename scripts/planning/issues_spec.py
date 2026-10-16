"""Blindspot issue plan: labels, milestones, issues with dependencies.

Each issue: key, milestone, title, labels, size (S <= 2h, M <= half day, L <= a day),
priority (P0 critical path, P1 needed for submission, P2 nice to have),
goal, criteria (acceptance), deps (keys of blocking issues), refs.
"""

LABELS = {
    "phase-2": ("1d76db", "Phase 2: scenarios and answer key"),
    "phase-3": ("0e8a16", "Phase 3: targets, resolver, metrics"),
    "phase-4": ("5319e7", "Phase 4: auditor agent"),
    "phase-5": ("b60205", "Phase 5: fix loop"),
    "phase-6": ("fbca04", "Phase 6: web UI"),
    "phase-7": ("d93f0b", "Phase 7: hardening, findings, write-up"),
    "submission": ("000000", "Devpost submission"),
    "needs-user": ("e99695", "Only the maintainer can do this (accounts, dashboards, decisions)"),
    "critical-path": ("b60205", "On the critical path to submission; delays slip the deadline"),
    "P0": ("b60205", "Priority 0: critical path"),
    "P1": ("d93f0b", "Priority 1: needed for submission"),
    "P2": ("c5def5", "Priority 2: nice to have, cut first"),
    "size:S": ("ededed", "Up to about 2 hours"),
    "size:M": ("cccccc", "Up to about half a day"),
    "size:L": ("bbbbbb", "Up to about a day"),
    "task": ("0052cc", "Planned work"),
    "chore": ("c2e0c6", "Maintenance"),
    "stretch": ("f9d0c4", "Stretch goal; cut first if behind"),
    "deferred": ("cfd3d7", "Known and accepted for now"),
    "security": ("ee0701", "Secrets, abuse, privacy"),
    "judge-experience": ("ff9f1c", "Anything a judge would notice"),
    "qloo-api": ("6f42c1", "Qloo behavior, parameters, quota"),
    "ui": ("fef2c0", "Web UI"),
    "docs": ("0075ca", "Documentation"),
    "llm": ("bfdadc", "Uses an LLM (never for scoring)"),
}

MILESTONES = [
    ("Maintainer actions", "2026-10-20", "Account, dashboard and decision tasks only the maintainer can do."),
    (
        "Phase 2: Answer key",
        "2026-10-18",
        "Scenarios and a validated Qloo answer key. See docs/architecture.md Section 19.",
    ),
    ("Phase 3: Scoring", "2026-10-20", "Target adapters, resolver, metrics, calibration."),
    ("Phase 4: Auditor agent", "2026-10-22", "Agent that finds and confirms blindspots."),
    ("Phase 5: Fix loop", "2026-10-23", "Qloo-grounded re-run with honest before and after."),
    ("Phase 6: Web UI", "2026-10-26", "Blind Map, Truth Table, Truth Receipts, kill switch."),
    ("Phase 7: Ship", "2026-10-27", "Hardening, precompute, findings, write-up, final QA."),
    ("Submission", "2026-10-28", "Devpost submission. Devpost closes 2026-10-30."),
    ("Backlog", None, "Deferred and post-hackathon items."),
]

ARCH = "docs/architecture.md"

ISSUES = []


def add(key, ms, title, labels, size, prio, goal, criteria, deps=(), refs=()):
    ISSUES.append(
        dict(
            key=key,
            ms=ms,
            title=title,
            labels=[*labels, f"size:{size}", prio],
            goal=goal,
            criteria=criteria,
            deps=list(deps),
            refs=list(refs),
        )
    )


MU, P2, P3, P4, P5, P6, P7, SUB, BL = (m[0] for m in MILESTONES)

# ---------------------------------------------------------------- maintainer
add(
    "U1",
    MU,
    "Deploy the hosted demo on Render (gate 11)",
    ["needs-user", "judge-experience", "critical-path"],
    "S",
    "P0",
    "Get a public URL early so hosting problems surface now, not on submission day.",
    [
        "Render: New → Blueprint → this repo; `render.yaml` is picked up",
        "`QLOO_API_KEY` set in the Render dashboard only (never in the repo)",
        "`/healthz` answers from a phone on mobile data; cold-start time noted",
        "`/api/qloo/status` shows no secrets",
        "Repo variable `BLINDSPOT_URL` set so the keep-alive workflow can ping it",
        "URL added to the README's Live demo section and `docs/JUDGES.md`",
    ],
    refs=["docs/NEEDS_FROM_USER.md step 2", "docs/JUDGES.md → Deploying the hosted demo"],
)
add(
    "U2",
    MU,
    "Run the Docker image secret scan (gates 9 and 10)",
    ["needs-user", "security"],
    "S",
    "P1",
    "Close the last Phase 1 security gate.",
    [
        "Docker Desktop started; `make docker-scan` passes",
        "Docker commands in `docs/JUDGES.md` executed and confirmed",
        "Gates 9 and 10 marked PASS in `docs/phase1_report.md`",
    ],
    refs=["docs/NEEDS_FROM_USER.md step 3"],
)
add(
    "U3",
    MU,
    "Ask Qloo on Discord about the snapshot, quota and `videogame`",
    ["needs-user", "qloo-api"],
    "S",
    "P1",
    "Find out whether a minimal real snapshot may be committed and whether quota can be raised for judging.",
    [
        "Message from `docs/NEEDS_FROM_USER.md` step 4 posted in `#api-help`, plus the `videogame` vs `video_game` question",
        "Answer recorded in `docs/decisions.md`; ADR 0002 and ADR 0003 updated if the answer changes them",
    ],
    refs=["docs/adr/0002-no-real-qloo-data-in-git.md", "docs/adr/0003-videogame-entity-type.md"],
)
add(
    "U4",
    MU,
    "Choose target models and provide their API keys on the host",
    ["needs-user", "llm", "critical-path"],
    "S",
    "P0",
    "Phase 3 needs real models to audit. Pick 3 to 4 preloaded targets (mix of providers and sizes) behind OpenAI-compatible endpoints.",
    [
        "Target list decided (provider, model, endpoint) and written to `docs/decisions.md`",
        "API keys stored as host env vars and in local `.env` only; never in git",
        "Monthly spend cap set at each provider",
        "Free or cheap options considered so judges' clicks can't run up a bill",
    ],
    refs=[f"{ARCH} Section 8.1"],
)
add(
    "U5",
    MU,
    "Pick private storage for the real demo snapshot",
    ["needs-user", "judge-experience"],
    "S",
    "P1",
    "The hosted demo needs real Qloo data without committing it.",
    [
        "Option chosen: Render secret file, private repo release, private bucket, or paid disk",
        "`SNAPSHOT_URL`/token or secret-file path set on the host",
        "Decision recorded in `docs/decisions.md`",
    ],
    deps=["P2-12"],
    refs=["docs/adr/0002-no-real-qloo-data-in-git.md"],
)
add(
    "U6",
    MU,
    "Review seed matches and the plausibility sample",
    ["needs-user", "qloo-api"],
    "S",
    "P1",
    "A human check that the answer key looks right before agents are scored against it.",
    [
        "`docs/seed_review.md`: every ambiguous seed confirmed or corrected",
        "`docs/plausibility_review.md`: verdict column filled for the 30 sampled cells",
        "Any cell marked wrong is dropped or fixed before Phase 3 scoring",
    ],
    deps=["P2-8"],
)

# ---------------------------------------------------------------- phase 2
add(
    "P2-1",
    P2,
    "Phase 2: promote the audit's black-box and fake-Qloo tests into the permanent suite",
    ["phase-2", "task", "security"],
    "M",
    "P0",
    "The fix pass refactored the client but didn't re-run the audit's service and fake-server suites. Make them permanent so regressions are caught automatically.",
    [
        "Fake Qloo server fixture covering 401, 403/400, 404, 429 with and without `Retry-After`, 5xx, slow, malformed, HTML, both error envelopes",
        "Tests: retries, breaker open and recover, budgets at every scope, deadline (p95 < deadline + 0.5 s, no hangs in 50 concurrent requests against always-429), per-request kill switch isolation, truthful `served_from`, `/status` leak check, input fuzzing, per-IP limiter, forged `X-Forwarded-For`",
        "All offline, run in CI on Ubuntu and Windows",
    ],
    refs=["docs/phase1_audit.md", f"{ARCH} Section 18"],
)
add(
    "P2-2",
    P2,
    "Phase 2: parameter promotion with recorded evidence",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    "The strict guard refuses any parameter not proven to change results. Add the process to prove them.",
    [
        "`scripts/promote_param.py <filter.type> <param> <value>` runs baseline vs variant via `assert_param_effective`",
        "Evidence rows in `docs/param_evidence.md` (params, overlap, rank change, scores present)",
        "`params.py` marks VERIFIED or INEFFECTIVE with a link to the evidence row",
        "Promoted for every parameter Phase 2 needs: location filter, entity and tag signals, audiences, `take`, `bias.trends` (dining), popularity filters, `filter.exclude.entities`, `filter.results.entities`",
        "Existing Phase 1 evidence reused from the cache (no repeat calls)",
    ],
    refs=["docs/adr/0005-strict-parameter-guard.md"],
)
add(
    "P2-3",
    P2,
    "Phase 2: answer-key experiments E12 to E15",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    "Settle the query shapes before designing scenarios.",
    [
        "E12 result depth: `take` 10/20/50 per domain; real max; chosen answer-key depth; coverage thresholds recalibrated from the real distribution",
        "E13 place query shape: location filter + entity, tag, audience signals; which gives scores, stable ranks and persona-specific results",
        "E14 candidate scoring with a taste signal: how many of N come back, with scores; fallback proposed with evidence if it fails",
        "E15 persona sensitivity: top-10 overlap when only the persona changes",
        "Results in `docs/qloo_api_reality.md` as E12 to E15, each VERIFIED/FAILED",
    ],
    deps=["P2-2"],
    refs=[f"{ARCH} Section 7"],
)
add(
    "P2-4",
    P2,
    "Phase 2: personas, probe types and domain query shapes as config",
    ["phase-2", "task", "critical-path"],
    "M",
    "P0",
    "The exam is defined by data, validated by schema.",
    [
        "`config/personas.yaml`: 12 to 16 neutral personas with age band, audience IDs (required for dining), interest tags, seed entities per domain",
        "`config/probe_types.yaml`: core, swap, bridge, audience (only where E15 shows sensitivity), freshness (dining only)",
        "`config/domains.yaml` gains the standard query shape (E13), depth (E12) and allowed probes",
        "Pydantic validation; a dining persona with only age/gender is rejected; identity audiences behind `audience_policy.allow_identity=false`",
    ],
    deps=["P2-3"],
    refs=[f"{ARCH} Section 7.2"],
)
add(
    "P2-5",
    P2,
    "Phase 2: resolve and lock seed, tag and audience IDs",
    ["phase-2", "task", "qloo-api"],
    "S",
    "P0",
    "Make scenario generation reproducible with no network.",
    [
        "`scripts/resolve_seeds.py` resolves via `/search`, `/v2/tags` and the verified audience list",
        "`config/resolved_ids.lock.json` commits only IDs and our query text; returned names in gitignored `data/resolved_ids.detail.json`",
        "Ambiguous matches listed in `docs/seed_review.md` for maintainer review",
    ],
    deps=["P2-4"],
)
add(
    "P2-6",
    P2,
    "Phase 2: scenario schema and deterministic generator",
    ["phase-2", "task", "critical-path"],
    "M",
    "P0",
    "Build the exam questions.",
    [
        "Pydantic `Scenario` with content-hash id, local and global-baseline query specs, 3 neutral prompt variants, swap-pair links",
        "Seeded generator; same config gives identical ids (tested)",
        "80 to 120 scenarios weighted to swap and core; distribution table reported",
        "Demo subset of 30 to 40 covering every demo city and domain with at least 8 swap pairs",
        "Prompts never leak Qloo answers; read like a real user",
    ],
    deps=["P2-4", "P2-5"],
    refs=[f"{ARCH} Section 7"],
)
add(
    "P2-7",
    P2,
    "Phase 2: ground-truth builder, `entity_index` and `get_answer_key()`",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "L",
    "P0",
    "Fetch and store the answer key once, reliably.",
    [
        "`blindspot groundtruth build [--demo-only] [--resume]`: async, budgeted, checkpointed, resumable, idempotent (interrupt-and-resume test)",
        "Local and global lists at the E12 depth with rank, scores if present (`score_present`), tags, external IDs, coordinates, params, `served_from`",
        "Tables `scenarios`, `ground_truth_runs`, `ground_truth_items`, `entity_index`; tested name normalization (accents, punctuation, Japanese, transliteration)",
        "`get_answer_key(scenario_id)` is the single read path",
        "Thin results trigger a param audit and one broader retry before being recorded thin",
        "Live calls within the Phase 2 budget (1,200 total)",
    ],
    deps=["P2-6", "P2-1"],
    refs=[f"{ARCH} Section 7.3"],
)
add(
    "P2-8",
    P2,
    "Phase 2: validity suite V1 to V9 and cell dispositions",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    "Prove the answer key is stable and discriminating before agents are scored against it.",
    [
        "`docs/ground_truth_validity.md` with numbers for V1 determinism, V2 param effectiveness, V3 discrimination (swap and persona), V4 coverage, V5 local vs famous, V6 score sanity and hub entities, V7 existence readiness, V8 name round-trip (resolver ceiling)",
        "V9 `docs/plausibility_review.md` (30 cells) for maintainer review",
        "Every cell KEEP/DOWNGRADE/DROP with a reason; final recommended scenario set; non-viable probe types named",
    ],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 7.4"],
)
add(
    "P2-9",
    P2,
    "Phase 2: metrics design note",
    ["phase-2", "task", "docs", "critical-path"],
    "S",
    "P0",
    "Fix the metric formulas from evidence before writing metric code.",
    [
        "`docs/metrics_design.md`: overlap@k, rank-weighted taste-fit, famousness and local hit share, swap sameness gap, phantom rate, Blindspot Score",
        "How out-of-key recommendations are treated (from E14); hub down-weighting (V6); thin-cell rules",
        "Calibration tests Phase 3 must pass, with expected ranges",
    ],
    deps=["P2-8"],
    refs=[f"{ARCH} Section 9", "docs/adr/0001-rank-based-metrics.md"],
)
add(
    "P2-10",
    P2,
    "Phase 2: coverage probe for any location and `GET /api/coverage`",
    ["phase-2", "task", "judge-experience"],
    "M",
    "P1",
    "Let judges try their own city and see honestly whether Qloo has enough data.",
    [
        "`coverage_probe(location, domains)`: about 3 cached calls per domain, verdict `rich | thin | empty` with a confidence note",
        "`GET /api/coverage?location=`: budget-guarded, deadline-bound, `served_from`-labeled",
        "Verified on the demo cities and two obscure places; results in the validity report",
    ],
    deps=["P2-3"],
)
add(
    "P2-11",
    P2,
    "Phase 2: scenario and ground-truth API routes and CLI",
    ["phase-2", "task"],
    "S",
    "P1",
    "Expose the exam and answer key to later phases and the UI.",
    [
        "`GET /api/scenarios` (filter by city, domain, probe, demo), `GET /api/scenarios/{id}`, `GET /api/groundtruth/{id}`",
        "CLI: `blindspot scenarios build|list|show`, `groundtruth build|status|export`, `validity run`, `param promote`",
        "Envelope with `served_from` and `synthetic`; escaped text; OpenAPI updated; black-box tests",
    ],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 12"],
)
add(
    "P2-12",
    P2,
    "Phase 2: synthetic public snapshot and private real snapshot loader",
    ["phase-2", "task", "judge-experience", "security"],
    "M",
    "P0",
    "A fresh clone works offline; the hosted demo serves real data without committing it.",
    [
        "Synthetic snapshot with the real schema, clearly fake names, `synthetic=true` in responses",
        "Real demo snapshot loaded at startup from a secret path or `SNAPSHOT_URL`(+token), validated by schema version and checksum, falling back to synthetic with a warning",
        "`make export-private-snapshot` writes the real one to `data/private/`",
        "Fresh-clone CI job serves scenarios and synthetic ground truth with no `.env`",
        "No quota spent on boot unless `WARM_ON_BOOT=true` within its own cap",
    ],
    deps=["P2-7"],
    refs=["docs/adr/0002-no-real-qloo-data-in-git.md", f"{ARCH} Section 15"],
)
add(
    "P2-13",
    P2,
    "Phase 2: report, docs and JUDGES.md update",
    ["phase-2", "docs"],
    "S",
    "P1",
    "Close Phase 2 with evidence.",
    [
        "`docs/phase2_report.md`: gates, query shapes, E14 verdict, discrimination numbers, final scenario counts, live calls by purpose",
        "`docs/scenarios.md`, `docs/JUDGES.md`, architecture and roadmap updated; every JUDGES.md command executed",
    ],
    deps=["P2-8", "P2-9", "P2-10", "P2-11", "P2-12"],
)

# ---------------------------------------------------------------- phase 3
add(
    "P3-1",
    P3,
    "Phase 3: target adapter interface and OpenAI-compatible client",
    ["phase-3", "task", "llm", "critical-path"],
    "M",
    "P0",
    "Ask any target model the exam questions and get structured answers back.",
    [
        "Adapter interface; OpenAI-compatible chat client with timeouts and retries",
        "Fixed system prompt for JSON recommendations; tolerant parsing with one repair retry",
        "Response cache keyed by target, model version and prompt hash; replays cost nothing",
        "Preloaded targets from config; keys from env only",
    ],
    deps=["P2-6", "U4"],
    refs=[f"{ARCH} Section 8.1"],
)
add(
    "P3-2",
    P3,
    "Phase 3: untrusted output handling and bring-your-own-endpoint guard",
    ["phase-3", "task", "security"],
    "S",
    "P1",
    "Target output is untrusted; user-supplied endpoints are an SSRF risk.",
    [
        "Target text escaped everywhere, never executed, never fed to tools unfiltered; injection-style fixtures tested",
        "BYO endpoint off by default; when on, private, loopback and link-local ranges blocked; strict limits",
    ],
    deps=["P3-1"],
    refs=["SECURITY.md"],
)
add(
    "P3-3",
    P3,
    "Phase 3: entity resolver",
    ["phase-3", "task", "qloo-api", "critical-path"],
    "L",
    "P0",
    "Turn free-text recommendations into Qloo entities, honestly.",
    [
        "Normalize → `entity_index` exact/alias → fuzzy → budgeted `/search` with domain types and location hint → existence evidence",
        "Per item: qloo_id, confidence, method, status (`matched_in_key`, `matched_outside_key`, `phantom_candidate`, `unresolved`)",
        "Match rate reported per domain and script and compared with the V8 ceiling",
        "Unresolved never counted as phantom",
    ],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 8.2"],
)
add(
    "P3-4",
    P3,
    "Phase 3: synthetic calibration targets",
    ["phase-3", "task"],
    "S",
    "P0",
    "Targets with known answers to prove the metrics measure what we claim.",
    [
        "Qloo-copy, global-top-10-for-everyone, random-real-entities, invented-names targets",
        "Run offline from cached ground truth",
    ],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 9"],
)
add(
    "P3-5",
    P3,
    "Phase 3: metrics implementation",
    ["phase-3", "task", "critical-path"],
    "M",
    "P0",
    "Score each cell and target deterministically.",
    [
        "Implement the formulas in `docs/metrics_design.md`; hub down-weighting; thin cells not scored",
        "Per-cell and overall Blindspot Score with components",
        "Unit tests for each metric with hand-computed cases",
    ],
    deps=["P2-9", "P3-3"],
    refs=[f"{ARCH} Section 9"],
)
add(
    "P3-6",
    P3,
    "Phase 3: calibration tests pass",
    ["phase-3", "task", "critical-path"],
    "S",
    "P0",
    "No score is shown until calibration passes.",
    [
        "Qloo copy near 100; global top-10 low taste-fit and high famousness and sameness; random near zero; invented names phantom rate near 1",
        "Calibration runs in CI",
    ],
    deps=["P3-4", "P3-5"],
)
add(
    "P3-7",
    P3,
    "Phase 3: end-to-end scoring CLI for real targets",
    ["phase-3", "task", "critical-path"],
    "M",
    "P0",
    "Score a real model on the demo scenarios from the command line.",
    [
        "`blindspot score --target <id> --demo` runs prompts, resolution and metrics end to end",
        "Two preloaded models scored; per-cell table and match rates reported",
    ],
    deps=["P3-1", "P3-6"],
)
add(
    "P3-8",
    P3,
    "Phase 3: report",
    ["phase-3", "docs"],
    "S",
    "P1",
    "Close Phase 3 with evidence.",
    [
        "`docs/phase3_report.md`: calibration results, match rates vs ceiling, first real scores, cost and calls"
    ],
    deps=["P3-2", "P3-7"],
)

# ---------------------------------------------------------------- phase 4
add(
    "P4-1",
    P4,
    "Phase 4: audit run model and persistence",
    ["phase-4", "task", "critical-path"],
    "M",
    "P0",
    "Store audits so they can be resumed, replayed and shown.",
    [
        "Tables `audit_runs`, `target_responses`, `resolutions`, `cell_scores` (see architecture Section 14)",
        "Audit can resume after a crash; replays from stored data with zero new calls",
    ],
    deps=["P3-7"],
    refs=[f"{ARCH} Section 14"],
)
add(
    "P4-2",
    P4,
    "Phase 4: auditor state machine and cell selection",
    ["phase-4", "task", "critical-path"],
    "L",
    "P0",
    "The agentic core: decide which cell to probe next.",
    [
        "States plan → probe → score → select → confirm → report",
        "Upper-confidence-bound style selection favoring low, uncertain cells; explainable in one sentence",
        "Deterministic given a seed and cached responses",
    ],
    deps=["P4-1"],
    refs=[f"{ARCH} Section 10"],
)
add(
    "P4-3",
    P4,
    "Phase 4: bootstrap confirmation of blindspots",
    ["phase-4", "task", "critical-path"],
    "M",
    "P0",
    "Only report a blindspot when the evidence holds.",
    [
        "Bootstrap 90% CI over scenarios and prompt variants per cell; minimum probe count",
        "Confirmed only when the upper bound is below the threshold; tests with synthetic targets",
    ],
    deps=["P4-2"],
)
add(
    "P4-4",
    P4,
    "Phase 4: budgets for calls, tokens and time with clean stops",
    ["phase-4", "task", "judge-experience"],
    "S",
    "P0",
    "An audit can't run away with the quota or the judge's patience.",
    [
        "Per-audit caps for Qloo calls, target tokens and wall time",
        "Hitting any cap stops cleanly and reports partial results with a reason",
    ],
    deps=["P4-2"],
)
add(
    "P4-5",
    P4,
    "Phase 4: audit API routes, event stream and receipts",
    ["phase-4", "task", "critical-path"],
    "M",
    "P0",
    "Expose audits to the UI.",
    [
        "`POST /api/audits`, `GET /api/audits/{id}`, `GET /api/audits/{id}/events` (SSE), `GET /api/audits/{id}/receipts`",
        "Events: plan, probe, score, select, confirm, report",
        "Receipts list every Qloo call behind each score from the call log",
    ],
    deps=["P4-3", "P4-4"],
    refs=[f"{ARCH} Section 12"],
)
add(
    "P4-6",
    P4,
    "Phase 4: plain-language explanations of results",
    ["phase-4", "task", "llm"],
    "S",
    "P1",
    "Explain each confirmed blindspot in a sentence a judge understands.",
    [
        "LLM writes explanations from computed numbers only; scores never change",
        "Template fallback when no LLM is available (replay mode)",
    ],
    deps=["P4-3"],
)
add(
    "P4-7",
    P4,
    "Phase 4: report with audits of two models",
    ["phase-4", "docs"],
    "S",
    "P1",
    "Close Phase 4 with evidence.",
    ["`docs/phase4_report.md`: confirmed blindspots with CIs for two models, calls and cost, stop behavior"],
    deps=["P4-5", "P4-6"],
)

# ---------------------------------------------------------------- phase 5
add(
    "P5-1",
    P5,
    "Phase 5: Qloo tools for the grounded agent",
    ["phase-5", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    "Give the target Qloo tools that go through our client, so budgets, cache and receipts apply.",
    [
        "Tool definitions for search and insights; strict params; `served_from` preserved",
        "Tool calls logged with the audit run id",
    ],
    deps=["P3-1"],
    refs=[f"{ARCH} Section 11"],
)
add(
    "P5-2",
    P5,
    "Phase 5: grounded wrapper and same-exam re-run with held-out set",
    ["phase-5", "task", "critical-path"],
    "L",
    "P0",
    "Show that Qloo closes the gap, honestly.",
    [
        "Grounding rule: only recommend entities Qloo returned in the conversation",
        "Re-run the same scenarios and prompt variants as the baseline audit",
        "Held-out scenarios reported separately; per-cell and overall delta",
    ],
    deps=["P5-1", "P4-5"],
)
add(
    "P5-3",
    P5,
    "Phase 5: fix API and export of prompt plus MCP config",
    ["phase-5", "task"],
    "S",
    "P1",
    "Let users take the fix home.",
    [
        "`POST /api/audits/{id}/fix`, `GET /api/audits/{id}/export`",
        "Export: system prompt + MCP server config, documented",
    ],
    deps=["P5-2"],
)
add(
    "P5-4",
    P5,
    "Phase 5: report with before and after numbers",
    ["phase-5", "docs"],
    "S",
    "P1",
    "Record the real delta, even if modest.",
    ["`docs/phase5_report.md`: before/after per cell and held-out, for the demo targets"],
    deps=["P5-3"],
)
add(
    "P5-5",
    BL,
    "Stretch: full MCP server exposing Blindspot audits",
    ["phase-5", "stretch"],
    "L",
    "P2",
    "Other agents can request a Blindspot audit as a tool.",
    ["MCP server with an `audit` tool and a `grounded_recommend` tool", "Documented install steps"],
    deps=["P5-3"],
)

# ---------------------------------------------------------------- phase 6
add(
    "P6-1",
    P6,
    "Phase 6: UI scaffold, data label and cold-start state",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    "A fast static UI served by the service, honest about its data.",
    [
        "Stack chosen and recorded (static HTML/JS served by FastAPI unless there's a reason otherwise)",
        "'Live Qloo data' vs 'Synthetic sample data' always visible",
        "'Waking up' state for Render cold starts; no blank page",
        "Design tokens, light and dark, phone width without horizontal scroll",
        "Can start against mock data shaped like the API envelope; wired to real audits once Phase 4 lands",
    ],
    deps=["P2-11"],
    refs=[f"{ARCH} Section 13"],
)
add(
    "P6-2",
    P6,
    "Phase 6: start screen with 'Roast an agent'",
    ["phase-6", "task", "ui", "judge-experience", "critical-path"],
    "S",
    "P0",
    "One click to a result.",
    ["Pick a preloaded target and cities; one-click demo loads precomputed results instantly"],
    deps=["P6-1"],
)
add(
    "P6-3",
    P6,
    "Phase 6: Blind Map",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    "The main visual: where the agent is blind.",
    [
        "City × domain heatmap with score and confidence per cell",
        "Thin cells marked 'insufficient Qloo data'; confirmed blindspots highlighted",
        "Accessible: not color-only; keyboard navigable",
    ],
    deps=["P6-1", "P4-5"],
)
add(
    "P6-4",
    P6,
    "Phase 6: Truth Table cell detail",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    "Show the gap side by side.",
    [
        "Agent alone, agent with Qloo, Qloo's answer key in three columns",
        "Each pick shows its match status and confidence",
    ],
    deps=["P6-3"],
)
add(
    "P6-5",
    P6,
    "Phase 6: Truth Receipts",
    ["phase-6", "task", "ui", "qloo-api", "critical-path"],
    "S",
    "P0",
    "Every number traceable to the Qloo calls behind it.",
    ["Endpoint, params (no secrets), result count, `served_from`, time for each call behind a score"],
    deps=["P6-4"],
)
add(
    "P6-6",
    P6,
    "Phase 6: Qloo kill switch",
    ["phase-6", "task", "ui", "judge-experience", "critical-path"],
    "S",
    "P0",
    "Prove the app can't score without Qloo.",
    [
        "Toggle grays out scores with 'no answer key' for this viewer only (per-request flag)",
        "Toggling never affects other viewers (tested)",
    ],
    deps=["P6-3"],
)
add(
    "P6-7",
    P6,
    "Phase 6: live audit view with trace and Qloo call ticker",
    ["phase-6", "task", "ui"],
    "M",
    "P1",
    "Watch the agent work.",
    ["Streams audit events; shows Qloo calls as they happen; handles reconnects"],
    deps=["P6-1", "P4-5"],
)
add(
    "P6-8",
    P6,
    "Phase 6: before and after fix view with export",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    "Show the improvement and let users take the patch.",
    ["Per-cell and overall delta, held-out result shown", "Export button for prompt + MCP config"],
    deps=["P6-3", "P5-3"],
)
add(
    "P6-9",
    P6,
    "Phase 6: 'Try your own city'",
    ["phase-6", "task", "ui", "judge-experience"],
    "S",
    "P1",
    "Global by design, honest about limits.",
    [
        "Location box runs the coverage probe first; runs a small budgeted audit only if coverage is rich; clear message otherwise"
    ],
    deps=["P6-3", "P2-10"],
)
add(
    "P6-10",
    P6,
    "Phase 6: mobile, accessibility and stranger test",
    ["phase-6", "task", "ui", "judge-experience"],
    "S",
    "P0",
    "A stranger understands it in 90 seconds, on any device.",
    [
        "Phone layout checked; contrast and keyboard checks pass",
        "Someone who hasn't seen it runs the demo end to end without instructions; notes recorded",
    ],
    deps=["P6-2", "P6-4", "P6-5", "P6-6", "P6-8"],
)

# ---------------------------------------------------------------- phase 7
add(
    "P7-1",
    P7,
    "Phase 7: precompute demo audits and the model leaderboard",
    ["phase-7", "task", "judge-experience", "critical-path"],
    "M",
    "P0",
    "Judges see results instantly and the demo never waits on quota.",
    [
        "Demo audits for each preloaded target precomputed and loaded from the snapshot",
        "`GET /api/leaderboard`: 'Which AI knows Lagos best?' (leaderboard itself is P2 if behind)",
    ],
    deps=["P5-4", "P2-12"],
)
add(
    "P7-2",
    P7,
    "Phase 7: hosted hardening and load test",
    ["phase-7", "task", "security", "judge-experience", "critical-path"],
    "S",
    "P0",
    "The hosted demo survives judges clicking fast and in parallel.",
    [
        "Load test against the hosted URL: no errors, caps respected, deadlines met",
        "Quota exhaustion and cold-start paths show friendly states",
        "429 and 5xx shapes observed or simulated and handled",
    ],
    deps=["P7-1", "U1"],
)
add(
    "P7-3",
    P7,
    "Phase 7: findings write-up 'State of Cultural Blindness'",
    ["phase-7", "task", "docs"],
    "M",
    "P1",
    "Press-worthy results from auditing several models.",
    [
        "Audits of 3 to 4 models; key findings with numbers and caveats; Qloo framed as a proxy, not truth",
        "Published in the repo and linked from the README",
    ],
    deps=["P7-1"],
)
add(
    "P7-4",
    P7,
    "Phase 7: blind user test with 15 to 20 people",
    ["phase-7", "task", "judge-experience"],
    "S",
    "P2",
    "Real numbers on whether Qloo-grounded output is preferred.",
    ["Blind A/B ratings of generic vs Qloo-grounded recommendations; results and method recorded"],
    deps=["P5-4"],
)
add(
    "P7-5",
    P7,
    "Phase 7: Devpost description, screenshots and architecture image",
    ["phase-7", "task", "docs", "submission", "critical-path"],
    "M",
    "P0",
    "The text judges read first.",
    [
        "Opens with the Qloo vs LLM-only difference and real findings; explains why it only works with Qloo",
        "Screenshots of Blind Map, Truth Table, receipts, kill switch, before/after; architecture image",
        "Lists every Qloo feature used and where",
    ],
    deps=["P6-10", "P7-3"],
)
add(
    "P7-6",
    P7,
    "Phase 7: final README and JUDGES.md pass",
    ["phase-7", "docs", "judge-experience"],
    "S",
    "P0",
    "Repo docs match the shipped product.",
    [
        "Live demo URL and screenshots in the README; feature table statuses current",
        "Every JUDGES.md command re-run from a fresh clone on Windows and Linux",
    ],
    deps=["P6-10", "U1"],
)
add(
    "P7-7",
    P7,
    "Phase 7: final QA from a fresh browser, phone and another network",
    ["phase-7", "task", "judge-experience", "critical-path"],
    "S",
    "P0",
    "Nothing embarrassing on submission day.",
    [
        "Incognito desktop, phone on mobile data, another network: demo works end to end",
        "No console errors; `served_from` and data labels correct; kill switch works",
    ],
    deps=["P7-2", "P7-6"],
)
add(
    "P7-8",
    BL,
    "Stretch: score-regression GitHub Action and badge",
    ["phase-7", "stretch", "chore"],
    "M",
    "P2",
    "CI fails if a model update lowers the Blindspot Score; shareable badge.",
    [
        "Reusable workflow that runs a cached audit and compares with a baseline",
        "Badge 'Cultural Fit NN/100, powered by Qloo'",
    ],
    deps=["P4-5"],
)

# ---------------------------------------------------------------- submission
add(
    "S1",
    SUB,
    "Make the repo public and verify submission requirements",
    ["submission", "needs-user", "critical-path"],
    "S",
    "P0",
    "Submission requires a public repo with a visible open-source license.",
    [
        "Repo public; MIT license shows in the About section; description and topics set",
        "Secret scan of history passes once more right before switching",
        "CodeQL runs and its findings are reviewed",
    ],
    deps=["U2", "P7-7"],
)
add(
    "S2",
    SUB,
    "Submit on Devpost",
    ["submission", "needs-user", "critical-path"],
    "S",
    "P0",
    "Submit by 2026-10-28, two days before Devpost closes.",
    [
        "Hosted demo link, public repo link, description, license, all required fields filled",
        "Submission confirmed on Devpost; screenshot of confirmation saved",
    ],
    deps=["S1", "P7-5"],
)

# ---------------------------------------------------------------- backlog
add(
    "B1",
    BL,
    "Deferred N3: synchronous SQLite inside async handlers",
    ["deferred", "chore"],
    "S",
    "P2",
    "Fine at one instance; revisit if profiling shows it matters.",
    ["Profile under the Phase 7 load test; move to a thread or async driver only if p95 suffers"],
    refs=["docs/phase1_audit.md N3"],
)
add(
    "B2",
    BL,
    "Deferred N4: HTTP limiter clears all keys past 50,000",
    ["deferred", "chore"],
    "S",
    "P2",
    "Memory stays bounded; a brief reset of everyone's window is acceptable for one free instance.",
    ["Replace with an LRU or time-bucketed limiter if the demo moves beyond one instance"],
    refs=["docs/phase1_audit.md N4"],
)
add(
    "B3",
    BL,
    "Chore: Starlette TestClient deprecation warning",
    ["chore"],
    "S",
    "P2",
    "Tests warn that `httpx` with `starlette.testclient` is deprecated.",
    [
        "Follow the recommended replacement or pin a compatible version; warning gone; tests still pass on Ubuntu and Windows"
    ],
)
