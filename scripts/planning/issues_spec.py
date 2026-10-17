"""Blindspot issue plan: labels, milestones and issues with dependencies.

Each issue has a stable key (never reuse or rename one; the creation script finds existing issues by
key). Fields: why, scope_in, scope_out, notes (implementation pointers into the real code), criteria
(acceptance), checks (how to verify, with commands), calls (live Qloo budget), risks, deps, refs.
Sizes: S <= 2 h, M <= half a day, L <= a day. Priority: P0 critical or required, P1 needed for a
strong submission, P2 first to cut.
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
    "critical-path": ("b60205", "Zero slack: a delay here delays submission day"),
    "P0": ("b60205", "Priority 0: critical path or required to submit"),
    "P1": ("d93f0b", "Priority 1: needed for a strong submission"),
    "P2": ("c5def5", "Priority 2: nice to have, cut first"),
    "size:S": ("ededed", "Up to about 2 hours"),
    "size:M": ("cccccc", "Up to about half a day"),
    "size:L": ("bbbbbb", "Up to about a day"),
    "task": ("0052cc", "Planned work"),
    "chore": ("c2e0c6", "Maintenance"),
    "stretch": ("f9d0c4", "Stretch goal; cut first if behind"),
    "deferred": ("cfd3d7", "Known and accepted for now"),
    "decision": ("5319e7", "A decision is needed before work can proceed"),
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
REALITY = "docs/qloo_api_reality.md"
DECISIONS = "docs/decisions.md"

ISSUES = []


def add(
    key,
    ms,
    title,
    labels,
    size,
    prio,
    *,
    why,
    criteria,
    checks=(),
    scope_in=(),
    scope_out=(),
    notes=(),
    calls="0",
    risks=(),
    deps=(),
    refs=(),
):
    ISSUES.append(
        dict(
            key=key,
            ms=ms,
            title=title,
            labels=[*labels, f"size:{size}", prio],
            why=why,
            scope_in=list(scope_in),
            scope_out=list(scope_out),
            notes=list(notes),
            criteria=list(criteria),
            checks=list(checks),
            calls=calls,
            risks=list(risks),
            deps=list(deps),
            refs=list(refs),
        )
    )


MU, P2, P3, P4, P5, P6, P7, SUB, BL = (m[0] for m in MILESTONES)

# ================================================================== maintainer actions
add(
    "U1",
    MU,
    "Deploy the hosted demo on Render (gate 11)",
    ["needs-user", "judge-experience", "critical-path"],
    "S",
    "P0",
    why="Judges use the hosted demo first. Hosting problems (cold starts, env vars, secret files, region) "
    "take time to surface, so deploy the Phase 1 skeleton now rather than on submission day.",
    scope_in=[
        "Render web service from `render.yaml`",
        "Secret env vars in the dashboard",
        "Keep-alive wiring",
        "Recording the URL and cold-start numbers",
    ],
    scope_out=[
        "Uploading the real snapshot (#U5)",
        "Any code change; open a `fix/` issue if the deploy needs one",
    ],
    notes=[
        "Render: New → Blueprint → `nexuszephyr/Blindspot`. `render.yaml` sets a free Docker web service in "
        "`virginia` with health check `/healthz` (decision D22: next to Qloo's us-east-1 host)",
        "Set only in the dashboard: `QLOO_API_KEY` (secret). Leave `ALLOWED_ORIGINS` empty until the UI exists",
        "Everything else comes from `render.yaml` (`IP_HASH_SALT` generated, `TRUSTED_PROXY_HOPS=1`, "
        "`QLOO_RPS=4`, `REQUEST_DEADLINE_S=8`, caps, `QLOO_SNAPSHOT_PATH=/etc/secrets/snapshot.json`)",
        "Keep-alive: set repo variable `BLINDSPOT_URL` (Settings → Secrets and variables → Actions → Variables); "
        "`.github/workflows/keepalive.yml` pings every 10 minutes. Add an external 5-minute `HEAD /healthz` monitor too",
    ],
    criteria=[
        "Service is live and `/healthz` returns 200 with `mode: live`",
        "`/api/qloo/status` shows `key_configured: true` and no secret values, paths or hostnames",
        "`/api/qloo/search?q=...` returns `served_from: live` the first time and `cache` the second",
        "Sending a forged `X-Forwarded-For` doesn't change the per-IP limit identity",
        "Cold-start time after idle measured from a phone on mobile data and recorded",
        "`BLINDSPOT_URL` repo variable set; keepalive workflow run is green",
        "URL added to the README's Live demo section and `docs/JUDGES.md` (small PR)",
        "Gate 11 marked PASS in `docs/phase1_report.md`; `docs/NEEDS_FROM_USER.md` step 2 ticked",
    ],
    checks=[
        "`curl -fsS $URL/healthz` and `curl -fsS $URL/api/qloo/status` from a network other than home",
        'Follow `docs/JUDGES.md` → "Deploying the hosted demo" checklist line by line',
        "Open the URL on a phone with Wi-Fi off; note seconds until first byte after 20+ minutes idle",
    ],
    calls="About 2 (one search, cache hit on repeat)",
    risks=[
        "Free instances sleep after about 15 minutes idle; first request can take 30 to 60 s",
        "Render's secret-file path `/etc/secrets/` is UNVERIFIED until deploy (D6); confirm it",
        "Never paste the key into `render.yaml`, a commit, an issue or a chat",
    ],
    refs=["docs/NEEDS_FROM_USER.md step 2", "docs/JUDGES.md", f"{DECISIONS} D6, D22", "render.yaml"],
)

add(
    "U2",
    MU,
    "Run the Docker image secret scan (gates 9 and 10)",
    ["needs-user", "security"],
    "S",
    "P1",
    why="The image is what Render runs. It must contain no `.env`, no key and no real Qloo data. CI checks the "
    "image history, but the full local scan and the JUDGES.md Docker commands haven't been run.",
    scope_in=["`make docker-scan`", "Running the Docker section of `docs/JUDGES.md`"],
    scope_out=["Changing the Dockerfile unless a check fails (then open a `fix/` issue)"],
    notes=[
        "Start Docker Desktop first (`CLAUDE.md`: it's often stopped to save memory)",
        "Without make: `docker build -t blindspot:local .` then `python scripts/scan_secrets.py --docker blindspot:local`",
    ],
    criteria=[
        "`make docker-scan` passes",
        "`docker compose up --build` serves `/healthz` locally",
        "Every Docker command in `docs/JUDGES.md` runs as written (fix the doc if not)",
        "Image runs as a non-root user if the Dockerfile says so; image size recorded",
        "Gates 9 and 10 marked PASS in `docs/phase1_report.md`",
    ],
    checks=[
        "`docker history --no-trunc blindspot:local | grep -i qloo_api_key` prints nothing",
        "`docker run --rm --entrypoint sh blindspot:local -c 'ls -a /app; test ! -f /app/.env'`",
    ],
    risks=[
        "A `.dockerignore` gap would copy `data/` (real Qloo data) into the image; confirm `data/` is excluded"
    ],
    refs=["docs/NEEDS_FROM_USER.md step 3", "Dockerfile", ".dockerignore"],
)

add(
    "U3",
    MU,
    "Ask Qloo on Discord about the snapshot, quota and `videogame`",
    ["needs-user", "qloo-api"],
    "S",
    "P1",
    why="Decision D5 keeps all Qloo response data out of the public repo because the developer guide says not "
    "to store it in a public repository. Qloo's answer decides whether judges can run a real-data demo "
    "offline, and whether quota can grow for public judging.",
    scope_in=["Posting the prepared message", "Recording the answer and updating docs"],
    notes=[
        "Message is ready in `docs/NEEDS_FROM_USER.md` step 4; add one line asking whether "
        "`urn:entity:videogame` (accepted live) or `urn:entity:video_game` (in the guide, rejected live) is correct",
        "Channel: `#api-help` in the Qloo Hackathon Discord (linked from the developer guide)",
    ],
    criteria=[
        "Message posted; link to the thread saved in `docs/decisions.md` under D5",
        "Answer recorded verbatim in our own words (no long quotes) with date",
        "If a minimal snapshot is allowed: ADR 0002 updated and #U7 re-decided",
        "If quota can be raised: new cap recorded in `AGENTS.md` Qloo facts and `.env.example` comments",
        "If `videogame` guidance changes: ADR 0003 updated",
    ],
    checks=["Docs PR links the Discord thread and touches only docs"],
    risks=["No reply before Phase 2 ends: proceed on D5 (no Qloo data in git); don't block on it"],
    refs=[
        f"{DECISIONS} D5",
        "docs/adr/0002-no-real-qloo-data-in-git.md",
        "docs/adr/0003-videogame-entity-type.md",
    ],
)

add(
    "U4",
    MU,
    "Choose target models and put their API keys on the host",
    ["needs-user", "llm", "critical-path"],
    "S",
    "P0",
    why="Phase 3 audits real models. Someone has to pick them, create keys, cap spend and decide which models "
    "judges can trigger from the hosted demo without running up a bill.",
    scope_in=[
        "3 to 4 preloaded targets behind OpenAI-compatible endpoints",
        "Keys and spend caps",
        "Which targets are live-callable from the hosted demo vs precomputed only",
    ],
    scope_out=["Writing adapter code (#P3-1)"],
    notes=[
        "Mix providers and sizes for an interesting leaderboard (for example one frontier model, one small "
        "open model, one regional or multilingual model). Free tiers or local models keep cost at zero",
        "Record for each: display name, provider, base URL, model id, context and output limits, price per "
        "million tokens, whether tool calling works (needed for #P5-1)",
        "Keys go in `.env` locally and Render env vars; never in `config/`, commits or issues",
        "Add the variable names (not values) to `.env.example`",
    ],
    criteria=[
        "Target list with the fields above written to `docs/decisions.md` (new decision number)",
        "Keys created; monthly spend cap set at each provider; caps recorded",
        "Variable names added to `.env.example` with comments",
        "Decision recorded: live-callable from the hosted demo vs precomputed only, per target",
        "At least one target supports tool calling (needed for the fix loop)",
    ],
    checks=["A one-line test request per key succeeds locally (don't paste output containing keys)"],
    risks=[
        "A judge spamming audits can burn paid tokens: prefer precomputed demo results for paid models",
        "Some providers' OpenAI compatibility differs on tool calls and JSON mode; note quirks",
    ],
    refs=[f"{ARCH} Section 8.1", "SECURITY.md"],
)

add(
    "U7",
    MU,
    "Decide how a keyless local clone gets data (D7)",
    ["needs-user", "decision", "judge-experience"],
    "S",
    "P0",
    why="D5 forbids Qloo data in the public repo, so a local clone with no key has no real answer key. "
    "Phase 2 must know which no-key experience to build before it builds the snapshot work.",
    scope_in=["Choosing one option and recording it"],
    notes=[
        "Option A, synthetic snapshot: public repo ships invented entities with the real schema, labeled "
        "`synthetic: true`. Fully offline; obviously fake data",
        "Option B, remote mode (D7 proposal): a keyless local checkout sends Qloo lookups to the hosted "
        "demo's guarded API. Real data, budgets still enforced server-side, nothing in git; needs the hosted "
        "demo up and adds an API surface to secure",
        "Option C, both: synthetic by default, remote mode opt-in with `QLOO_MODE=remote`",
        "Recommendation: C, with A built first (no dependency on hosting) and B only if time allows",
    ],
    criteria=[
        "Option chosen and recorded in `docs/decisions.md` (update D7) with the reason",
        "#P2-12 updated to match if the choice differs from its default (A)",
    ],
    checks=["Decision text names the mode, what judges see, and what's labeled synthetic"],
    refs=[f"{DECISIONS} D5, D7", "docs/adr/0004-three-run-modes.md"],
)

add(
    "U5",
    MU,
    "Upload the real demo snapshot as a Render secret file",
    ["needs-user", "judge-experience"],
    "S",
    "P1",
    why="The hosted demo must serve real Qloo data instantly after every cold start, without committing it (D5, D6).",
    scope_in=["Uploading the file exported by #P2-12", "Confirming the mount path"],
    notes=[
        "Export locally with the command from #P2-12 (writes under `data/private/`, gitignored)",
        "Render → service → Environment → Secret Files → `snapshot.json`; `render.yaml` points "
        "`QLOO_SNAPSHOT_PATH` at `/etc/secrets/snapshot.json`",
        "Redeploy, then confirm `/api/qloo/status` reports the snapshot loaded and its entry count",
    ],
    criteria=[
        "Secret file uploaded; service restarted",
        "`/api/qloo/status` shows the snapshot loaded with the expected entry count and schema version",
        "A demo scenario's ground truth returns `served_from: snapshot` with zero live calls",
        "Mount path confirmed and D6's UNVERIFIED note updated",
    ],
    checks=["Restart the service twice; snapshot-served responses survive each restart"],
    risks=["Snapshot size limits on secret files are UNVERIFIED; keep it minimal (demo subset only)"],
    deps=["P2-12", "U1"],
    refs=[f"{DECISIONS} D6", "render.yaml"],
)

add(
    "U6",
    MU,
    "Review seed matches and the plausibility sample",
    ["needs-user", "qloo-api"],
    "S",
    "P1",
    why="Everything is scored against the answer key. A human check catches wrong seeds (a persona's 'jazz' "
    "resolving to a band named Jazz) and implausible answer keys before agents are judged against them.",
    scope_in=["`docs/seed_review.md` (from #P2-5)", "`docs/plausibility_review.md` (from #P2-8)"],
    notes=[
        "Both files may list real Qloo names; if so they live under `data/` (gitignored) and the docs file "
        "just points to them. Review locally; never paste names into issues or PRs",
        "Budget about 30 minutes",
    ],
    criteria=[
        "Every ambiguous seed confirmed or corrected; corrections applied to `config/personas.yaml` and the lock regenerated",
        "Verdict filled for all 30 plausibility rows (plausible / doubtful / wrong, with a short reason)",
        "Cells marked wrong are DROPPED or fixed before #P3-7 scores anything",
        "Summary (counts only, no names) added to `docs/phase2_report.md`",
    ],
    checks=[
        "Re-run `scripts/resolve_seeds.py` and the scenario build after corrections; scenario ids change only where seeds changed"
    ],
    deps=["P2-5", "P2-8"],
)

# ================================================================== phase 2
add(
    "P2-1",
    P2,
    "Phase 2: promote the audit's black-box and fake-Qloo tests into the permanent suite",
    ["phase-2", "task", "security"],
    "M",
    "P0",
    why="The fix pass refactored the client, but the audit's service black-box and fake-server suites weren't "
    "re-run afterwards (Phase 1 report). Every later phase leans on the client, so its safety properties "
    "must be guarded by permanent, offline tests.",
    scope_in=["A reusable fake Qloo server fixture", "Black-box tests against the real FastAPI app"],
    scope_out=["New client features", "Live calls"],
    notes=[
        "Build the fake server as a pytest fixture (an in-process ASGI app or `respx` routes) serving: 200 "
        'normal, 200 empty, 400 `{"errors": [...]}`, 401, 404, 429 with and without `Retry-After`, 500/502/503, '
        "slow (beyond `qloo_timeout_s`), malformed JSON, HTML body, oversized body (> `MAX_BODY_BYTES`), connection reset",
        "Use `settings_for_tests(...)` from `src/blindspot/config.py` and `main_app()` from "
        "`src/blindspot/service/app.py`; drive requests with httpx `ASGITransport`",
        "Existing tests to extend rather than duplicate: `tests/test_retries.py`, `test_budget.py`, "
        "`test_service.py`, `test_leaks.py`, `test_redaction.py`",
        "Budget assertions read `live_attempts` (retries count) and `calls.served_from`",
    ],
    criteria=[
        "Retries: attempts per failure type match policy; `Retry-After` honored up to `MAX_RETRY_AFTER_S`; other 4xx not retried; `qloo_max_attempts` respected",
        "Breaker opens after `qloo_breaker_threshold` 429s, serves `degraded` instantly to every client, recovers after cooldown",
        "Budgets: global daily, per-client hourly, per-run and demo reserve each trip with a `degraded` reason; counters equal the fake server's request count exactly",
        "Deadline: with an always-429 `Retry-After: 30` server, 50 concurrent requests all return within `request_deadline_s + 0.5` s; none hang",
        "Per-request kill switch: client A disabled and client B enabled concurrently; B unaffected, A gets `disabled` with zero upstream calls",
        "`served_from` truthful for live, cache, snapshot, disabled, degraded",
        "`/api/qloo/status` body contains no key, key fragment, env values, file paths or internal hosts",
        "Fuzzing `q`: empty, 10 KB, RTL text, NUL, control chars, SQL and shell metacharacters, path traversal, duplicate params → 4xx or safe handling, never a 500 or a traceback",
        "Forged `X-Forwarded-For` from a direct client doesn't change the limiter identity when `trusted_proxy_hops=0`",
        "Suite runs offline, keyless, under 60 s, green on Ubuntu and Windows CI",
    ],
    checks=[
        "`python scripts/dev.py test` (or `make test`) locally with `.env` moved aside",
        "Mutation spot-check in a temp copy: disable `Retry-After` handling, make the kill switch global, drop the deadline; each must turn a test red. Record results in the PR",
    ],
    risks=["Timing-based tests can flake on Windows CI; use generous bounds and fake clocks where possible"],
    refs=["docs/phase1_audit.md", f"{ARCH} Sections 6 and 18", "AGENTS.md Section 7"],
)

add(
    "P2-2",
    P2,
    "Phase 2: parameter promotion with recorded evidence",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    why="Qloo silently ignores unknown parameters, so `params.py` only allows parameters proven to change "
    "results. Phase 2 needs several more (and needs some ruled out). Promotion must be a repeatable, "
    "evidence-backed step rather than an edit to a set.",
    scope_in=["`scripts/promote_param.py`", "`docs/param_evidence.md`", "Updating `VERIFIED_INSIGHTS`"],
    scope_out=["Changing strict mode semantics"],
    notes=[
        "Reuse `assert_param_effective()` in `src/blindspot/qloo/params.py`; it compares result id sets",
        "Usage: `python scripts/promote_param.py <filter.type> <param> <value> [--baseline k=v ...] [--city <id>]`; "
        "baseline and variant run through `QlooClient.insights()` with `purpose='recon'` and `strict=False` for the variant only",
        "Evidence row: date, filter.type, param=value, baseline params (no key), result counts, Jaccard, rank change, "
        "scores present before/after, served_from, verdict VERIFIED or INEFFECTIVE",
        "Already VERIFIED from Phase 1 (don't re-spend): see the 'Parameters VERIFIED as effective' table in "
        f"`{REALITY}`. Already INEFFECTIVE: age/gender for places, `bias.trends` and `filter.popularity.min=0.9` for artists",
        "To test: `filter.exclude.entities` (all types), `filter.popularity.max` for place/movie/brand, `bias.trends` "
        "for movie/brand (expect INEFFECTIVE), `signal.demographics.audiences` for movie/brand, `filter.results.entities` "
        "for place/movie/brand with a taste signal, `signal.interests.tags` for place with the restaurant tag filter",
    ],
    criteria=[
        "Script runs baseline vs variant, writes one evidence row, and (with `--apply`) edits `VERIFIED_INSIGHTS` with a comment linking the row",
        "INEFFECTIVE parameters are recorded too and listed in `params.py` comments so nobody re-tries them",
        "Every parameter Phase 2's query shapes use is VERIFIED for its `filter.type`",
        "Strict mode still raises `QlooParamError` before any network call for non-VERIFIED params (existing tests pass)",
        "Unit tests for the script with a mocked client (verdict logic, idempotent re-run, no duplicate rows)",
    ],
    checks=[
        "Run twice with the same args: second run is all cache hits and writes no new row",
        "`python -m blindspot.qloo.cli budget` before and after; report calls used by purpose",
    ],
    calls="About 40 to 60 (each promotion is 2 calls; cached baselines reused)",
    risks=[
        "A parameter can be effective in one city and not another; test in one rich and one focus city",
        "Jaccard alone misses reorderings; record rank change as well",
    ],
    refs=["docs/adr/0005-strict-parameter-guard.md", f"{REALITY} E3 to E11", "src/blindspot/qloo/params.py"],
)

add(
    "P2-3",
    P2,
    "Phase 2: answer-key experiments E12 to E15",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    why="Scenario design depends on four open questions: how deep the answer key can be, the best query shape "
    "for places, whether we can score an agent's own picks, and whether personas change answers enough to probe.",
    scope_in=[
        "Experiments E12 to E15",
        "Writing results into the reality doc",
        "Choosing standard query shapes",
    ],
    scope_out=["Building scenarios (#P2-6)"],
    notes=[
        "Write a `scripts/recon.py` extension or a new `scripts/experiments_p2.py`; cities and domains from "
        "`config/` via `src/blindspot/catalog.py` (no hard-coded names)",
        "E12 depth: insights `take` max is 50 and top-10 of take=20 equals take=10 (Phase 1). Measure take=20/30/50 "
        "per domain in 2 cities: count, score presence, whether the tail stays persona- and city-specific (overlap of "
        "ranks 21 to 50 between cities). Recommend depth (20 expected) and recalibrate rich/thin thresholds from the "
        "real distribution",
        "E13 place shape: `filter.location.query` + restaurant tag, plus one of entity / tag / audience signal, and pairs. "
        "Measure scores present, stability (repeat, cache bypass), persona specificity, latency (places are about 2.4 s; "
        "the location signal timed out, so never use it for places)",
        "E14 candidate scoring: E7 showed `filter.results.entities` with an entity signal returns 9 of 10 scored. "
        "Confirm with tag and audience signals, for places, and at 25 and 50 candidates; find the useful maximum",
        "E15 persona sensitivity: same city and domain, 4 personas; pairwise top-10 Jaccard. Below 0.5 means persona "
        "probes are viable",
    ],
    criteria=[
        "E12 to E15 each written in the reality doc with a table and VERIFIED / FAILED labels and the date",
        "Chosen answer-key depth with justification; rich and thin thresholds recalibrated from data",
        "One standard query shape per domain, recorded for #P2-4",
        "Candidate-scoring verdict: works for which signals and domains, max useful candidates, and the fallback for the rest",
        "Persona-sensitivity numbers per domain and a viable / not viable call for the `audience` probe",
        "No Qloo names, ids or scores in committed docs: counts, overlaps, booleans and latencies only (D5)",
    ],
    checks=["Re-run the experiment script: all cache hits", "Results reproducible from `data/` recordings"],
    calls="About 120 to 160",
    risks=["Thin tails in focus cities may cap depth lower than 20 for some domains; allow per-domain depth"],
    deps=["P2-2"],
    refs=[f"{ARCH} Section 7", f"{REALITY} E4, E7, E9, E11"],
)

add(
    "P2-4",
    P2,
    "Phase 2: personas, probe types and domain query shapes as config",
    ["phase-2", "task", "critical-path"],
    "M",
    "P0",
    why="The exam must be data, validated by schema, so cities, personas and probes can change without code "
    "and so a judge can read exactly what was tested.",
    scope_in=[
        "`config/personas.yaml`",
        "`config/probe_types.yaml`",
        "Extending `config/domains.yaml`",
        "Pydantic models and loaders in `src/blindspot/catalog.py`",
    ],
    scope_out=["Resolving names to IDs (#P2-5)"],
    notes=[
        "Persona fields: `id`, `descriptor` (neutral, about one line), `age_band`, `audiences` (names to resolve; "
        "required for dining since age/gender don't affect places), `interest_tags` (names), `seeds` per domain "
        "(1 to 3 entity names each), optional `notes`",
        "12 to 16 personas spanning age bands and interest clusters (for example street food, fine dining, "
        "indie music, mainstream pop, cinephile, family, budget traveler, fitness). No ethnicity, religion or "
        "orientation framing; describe tastes, not identities",
        "`probe_types.yaml`: `core`, `swap`, `bridge`, `audience` (only if E15 viable), `freshness` (dining only, "
        "`bias.trends=high`), each with which domains allow it and how many scenarios to generate",
        "`domains.yaml` gains `query_shape` (from E13), `depth` (from E12) and `probes`; keep `location_param`",
        "`audience_policy.allow_identity: false` at the top of `personas.yaml`; the loader rejects identity "
        "audiences unless it's true",
    ],
    criteria=[
        "Loaders validate every file; bad values fail with a clear message naming file and field",
        "A dining-enabled persona with no audience is rejected",
        "Identity-based audiences rejected while `allow_identity` is false",
        "Unknown domain, city or probe references rejected",
        "`tests/fixtures/config/` gains small test versions; the no-hard-coded-city test still passes",
        "`docs/scenarios.md` explains every field and how to add a persona",
    ],
    checks=[
        '`python -c "from blindspot.catalog import ...; print(len(load_personas()))"` succeeds',
        "Unit tests for each validation rule",
    ],
    risks=["Stereotyped personas would undermine the project's credibility; keep descriptors about taste"],
    deps=["P2-3"],
    refs=[f"{ARCH} Section 7.2", "AGENTS.md Section 9 (add a persona)"],
)

add(
    "P2-5",
    P2,
    "Phase 2: resolve and lock seed, tag and audience IDs",
    ["phase-2", "task", "qloo-api"],
    "S",
    "P0",
    why="Insights need real IDs, never free text (a free-text tag id even hangs the host, Phase 1). Locking IDs "
    "makes scenario generation reproducible offline and keeps resolution calls to a one-off cost.",
    scope_in=["`scripts/resolve_seeds.py`", "`config/resolved_ids.lock.json`", "`docs/seed_review.md`"],
    notes=[
        "Use `QlooClient.resolve_ids()`, `search_entities()` (with `/search`'s own `types`), `search_tags()`, "
        "`list_audiences()`; audience signals must use the URN `id`, not the UUID `entity_id` (Phase 1 surprise)",
        "Lock file (committed): for each item our query text, kind, domain, chosen Qloo id, confidence, ambiguous flag. "
        "No Qloo names (D5)",
        "Detail file (gitignored): `data/resolved_ids.detail.json` with returned names and the top alternatives",
        "Ambiguous when top-2 confidence gap is small or the type doesn't match the domain",
    ],
    criteria=[
        "Every persona seed, interest tag and audience resolved or explicitly marked unresolved",
        "Lock file deterministic (sorted keys) and free of Qloo names",
        "`docs/seed_review.md` lists ambiguous items by our query text and points to the detail file",
        "Re-running with an unchanged config produces a byte-identical lock file and spends 0 calls",
    ],
    checks=[
        "`git diff config/resolved_ids.lock.json` empty after a re-run",
        "`python scripts/scan_secrets.py` clean; grep the lock for any non-id strings from Qloo",
    ],
    calls="About 60 to 90",
    deps=["P2-4"],
    refs=[f"{REALITY} E1, E2, E5"],
)

add(
    "P2-6",
    P2,
    "Phase 2: scenario schema and deterministic generator",
    ["phase-2", "task", "critical-path"],
    "M",
    "P0",
    why="Scenarios are the exam questions: what the target is asked and which Qloo queries form the answer key. "
    "They must be reproducible so before/after comparisons and judges' reruns line up.",
    scope_in=[
        "`src/blindspot/scenarios/` package",
        "CLI `blindspot scenarios build|list|show`",
        "Distribution report",
    ],
    scope_out=["Fetching ground truth (#P2-7)"],
    notes=[
        "`Scenario` (Pydantic): `id` = `sc_` + short sha256 of canonical content, `version`, `persona_id`, `city_id`, "
        "`domain_id`, `probe_type`, `local_spec` and `global_spec` (insights params built from `query_shape`), "
        "`prompt_variants` (3), `k`, `tags` (`demo`, `extended`, `holdout`), `pair_id` for swap pairs",
        "Global baseline spec has no location and is shared across scenarios with the same persona and domain",
        "Prompt templates: neutral, read like a real user, mention only the persona's own seed interests, age band and "
        "city; never a Qloo result. Example shape: \"I'm in my twenties and love {seed}. I'm in {city}. Recommend "
        "{k} {phrasing} I'd enjoy, with one line each on why.\"",
        "Bridge probes state taste in domain A (seed) and ask for domain B",
        "Seeded RNG; mark about 20% as `holdout` for the fix loop (#P5-2)",
    ],
    criteria=[
        "80 to 120 scenarios, weighted to swap and core, balanced across cities and domains",
        "Demo subset of 30 to 40 covering every demo city and domain with at least 8 swap pairs",
        "Holdout set tagged and balanced",
        "Same config and lock → identical ids and content (test)",
        "Prompt lint test: no prompt contains any answer-key entity name or id",
        "Distribution table (counts by city, domain, probe) printed by the CLI and saved in `docs/scenarios.md`",
    ],
    checks=[
        "`blindspot scenarios build` twice → identical output hash",
        "Unit tests for spec building per domain query shape",
    ],
    risks=["Too many scenarios multiply ground-truth calls; the budget in #P2-7 caps them"],
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
    why="The answer key is fetched once, stored, and read everywhere else. It must survive interruptions and quota "
    "limits, and expose one read path so later phases never query Qloo for ground truth themselves.",
    scope_in=[
        "`src/blindspot/groundtruth/` package",
        "CLI `blindspot groundtruth build|status|export`",
        "`entity_index` with normalization",
    ],
    scope_out=["Snapshot export format (#P2-12)", "Resolver logic (#P3-3)"],
    notes=[
        "New tables (migrate idempotently like `MIGRATIONS` in `qloo/db.py`): `scenarios`, `ground_truth_runs` "
        "(scenario, kind local/global, params, served_from, fetched_at), `ground_truth_items` (run, rank, qloo_id, "
        "affinity, score_present), `entity_index` (qloo_id, name, norm_name, type, aliases, external_ids, lat, lon)",
        "Names stay in SQLite under `data/` (gitignored). Store `external.google_place` and `location` for places (E10)",
        "Build: async with bounded concurrency through `QlooClient`; checkpoint after each scenario; `--resume` skips done ones",
        "Raise the local lifetime cap first: `QLOO_MAX_LIVE_CALLS` defaults to 600 and about 290 are already used. "
        "Set it in local `.env` to cover the Phase 2 budget",
        "Normalization function (shared with #P3-3): Unicode NFKC, casefold, strip accents, punctuation and "
        "location tails, collapse whitespace; keep non-Latin text (Japanese) rather than dropping it",
        "`get_answer_key(scenario_id) -> AnswerKey(local, global_, served_from, depth, coverage)`",
    ],
    criteria=[
        "Local and global lists stored at the E12 depth for every kept scenario",
        "Interrupt mid-build and resume: no repeated live calls, identical final tables (test with the fake server)",
        "Below-threshold result: param audit plus one broader retry before recording `thin`",
        "`get_answer_key` is the only public read path and returns `served_from` truthfully",
        "Normalization unit tests: accents, punctuation, casing, Japanese, a transliteration sample",
        "Live calls within budget, reported by purpose",
    ],
    checks=[
        "`blindspot groundtruth status` shows done, pending, thin and failed counts",
        "Kill the process during a live build, re-run with `--resume`, compare row counts",
        "`python -m blindspot.qloo.cli budget` before and after",
    ],
    calls="About 350 to 450 (scenarios × local list, plus shared global baselines)",
    risks=[
        "Place queries take about 2.4 s; full build may take 20+ minutes at 4 rps: run in the background, resumable",
        "Hub entities (one chain everywhere) will appear; keep them, #P2-8 measures them",
    ],
    deps=["P2-6", "P2-1"],
    refs=[f"{ARCH} Sections 7.3 and 14", f"{REALITY} E10, E11"],
)

add(
    "P2-8",
    P2,
    "Phase 2: validity suite V1 to V9 and cell dispositions",
    ["phase-2", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    why="If the answer key is unstable, barely changes between cities or personas, or is implausible, every "
    "score built on it is meaningless. This proves (or disproves) it with numbers before Phase 3.",
    scope_in=[
        "`scripts/validity.py` / `blindspot validity run`",
        "`docs/ground_truth_validity.md`",
        "Dispositions and final scenario set",
    ],
    notes=[
        "V1 determinism: 30 scenarios re-fetched with cache bypass; set stability and rank movement",
        "V2 param effectiveness: summary of `docs/param_evidence.md`",
        "V3 discrimination: swap-pair Jaccard and rank correlation per domain (expect ≤ 0.12 from Phase 1); persona variants from E15",
        "V4 coverage verdicts with the E12 thresholds",
        "V5 local vs famous: local list vs global baseline overlap per domain",
        "V6 score sanity and hub entities: frequency of each entity across answer keys; list the top hubs by id count only",
        "V7 existence readiness: share of places with coordinates and `external.google_place`",
        "V8 name round-trip: about 150 entities searched by their own name; top-1 and top-3 hit rates by domain and "
        "script (the resolver's ceiling)",
        "V9 plausibility: 30 random cells written to a gitignored file for #U6",
    ],
    criteria=[
        "Each check reports numbers, a threshold and pass / concern / fail",
        "Every cell gets KEEP, DOWNGRADE or DROP with a reason; DROP removes it from the demo subset",
        "Final recommended scenario set and demo subset recorded",
        "Probe types or domains that fail V3 marked not viable, with numbers",
        "Committed report contains no Qloo names, ids or scores beyond aggregates (D5)",
    ],
    checks=[
        "Validity re-run from cache costs 0 calls except V1's deliberate bypass",
        "Report renders on GitHub (tables, no broken links)",
    ],
    calls="About 180 to 220 (V1 30, V8 about 150, small extras)",
    risks=[
        "If V3 shows weak discrimination for a domain, drop that domain from swap probes rather than forcing it"
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
    why="Metric code in Phase 3 should implement settled formulas, chosen from Phase 2 evidence, not guesses.",
    scope_in=["`docs/metrics_design.md`"],
    scope_out=["Metric code (#P3-5)"],
    notes=[
        "Start from the proposal in architecture Section 9 (overlap@k, rank-weighted taste-fit, famousness, local "
        "hit share, swap sameness gap, phantom rate, Blindspot Score)",
        "Decide: k per domain (from depth), hub down-weighting formula (V6), treatment of `matched_outside_key` using "
        "E14 candidate scoring, thin-cell rule, component weights, the confirmation threshold for #P4-3",
        "Define each calibration target's expected range",
    ],
    criteria=[
        "Every metric has a formula, range, direction (higher or lower is better) and a worked example on synthetic data",
        "Out-of-key recommendations: rule chosen with E14 evidence (score via candidate ranking where it works; otherwise neutral, never negative)",
        "Calibration expectations listed for Qloo-copy, global-top-10, random-real and invented-names targets",
        "ADR 0001 updated if anything changes",
    ],
    checks=["Worked examples recomputed by hand by the reviewer"],
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
    why="Judges will want to try their own city. Blindspot should say honestly whether Qloo has enough data "
    "there before scoring anything.",
    scope_in=["`coverage_probe(location, domains)`", "`GET /api/coverage?location=`"],
    notes=[
        "About 3 cached calls per domain using each domain's standard query shape from E13",
        "Verdict per domain `rich | thin | empty` with the E12 thresholds and a short confidence note",
        "Route follows `routes.py` patterns: `RequestContext`, deadline, budgets, envelope with `served_from`; "
        "location string length-limited and treated as untrusted",
    ],
    criteria=[
        "Works for any location string, not just configured cities",
        "Budget-guarded per client; degraded responses explain why",
        "Verified on the demo cities and two deliberately obscure places; results (verdicts only) in the validity report",
        "Black-box tests including fuzzed locations",
    ],
    checks=["`curl localhost:8000/api/coverage?location=<city>` twice: second is all cache"],
    calls="About 30",
    deps=["P2-3"],
    refs=[f"{ARCH} Section 12"],
)

add(
    "P2-11",
    P2,
    "Phase 2: scenario and ground-truth API routes and CLI",
    ["phase-2", "task"],
    "S",
    "P1",
    why="Later phases and the UI need the exam and answer key over HTTP, with the same safety envelope as every other route.",
    scope_in=[
        "`GET /api/scenarios`",
        "`GET /api/scenarios/{id}`",
        "`GET /api/groundtruth/{id}`",
        "CLI completion",
    ],
    notes=[
        "Filters: city, domain, probe, demo; pagination",
        "Envelope: `data`, `served_from`, `synthetic`, `degraded_reason`, `run_id`",
        "Ground-truth responses expose names only in hosted/own-key mode; in synthetic mode they're synthetic",
    ],
    criteria=[
        "Routes return the envelope, are deadline-bound, budget-guarded and escape text",
        "OpenAPI at `/docs` lists them with schemas",
        "Black-box tests for each route, including unknown ids (404 JSON, no traceback)",
        "CLI: `blindspot scenarios build|list|show`, `groundtruth build|status|export`, `validity run`, `param promote`",
    ],
    checks=["Fresh-clone CI job hits `/api/scenarios` with no key"],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 12"],
)

add(
    "P2-12",
    P2,
    "Phase 2: no-key data path and private real snapshot",
    ["phase-2", "task", "judge-experience", "security"],
    "M",
    "P0",
    why="Judges cloning the repo have no key, and keys die after judging. The hosted demo must serve real data "
    "after every cold start. Neither may put Qloo data in git (D5).",
    scope_in=[
        "The no-key mode chosen in #U7 (default: synthetic snapshot)",
        "Real snapshot export for the hosted demo",
        "Startup loading and validation",
    ],
    notes=[
        "Existing pieces: `src/blindspot/qloo/snapshot.py` (`FORMAT`, `minimize_body`, `Snapshot.load`, "
        "`seed_cache`), `scripts/export_snapshot.py`, setting `QLOO_SNAPSHOT_PATH`",
        "Synthetic snapshot: same schema, invented entities that are obviously fake, `synthetic: true` in metadata "
        "and every response; generated by a script so it stays in sync with the schema",
        "Real snapshot: demo subset's ground truth + `entity_index` minimal fields only, written to `data/private/` "
        "via `make export-private-snapshot` (and a `scripts/dev.py` equivalent), uploaded by #U5",
        "Startup: load from `QLOO_SNAPSHOT_PATH`; verify `FORMAT`, schema version and checksum; on failure fall back to "
        "synthetic with a warning (no secrets in the message)",
        "If #U7 picks remote mode as well, implement it behind `QLOO_MODE=remote` with the hosted URL as a setting",
    ],
    criteria=[
        "Fresh clone, no `.env`: `make demo` serves scenarios and synthetic ground truth offline, labeled synthetic",
        "Real snapshot loads, validates and labels `served_from: snapshot`; checksum mismatch falls back safely",
        "No quota spent on boot unless `WARM_ON_BOOT=true`, with its own cap",
        "Committed files contain no real Qloo data (scan the synthetic snapshot for any real id from `data/`)",
        "`docs/JUDGES.md` explains synthetic vs real data and the key-deactivation note; every command executed",
    ],
    checks=[
        "CI fresh-clone job extended to call `/api/scenarios` and `/api/groundtruth/{demo id}` and assert `synthetic: true`",
        "Local test: start with a corrupted snapshot file and confirm fallback",
    ],
    risks=[
        "Accidentally committing the real snapshot: keep `data/private/` gitignored and add a test that fails if "
        "any committed JSON contains ids from the local cache"
    ],
    deps=["P2-7", "U7"],
    refs=["docs/adr/0002-no-real-qloo-data-in-git.md", f"{DECISIONS} D5 to D7", f"{ARCH} Section 15"],
)

add(
    "P2-13",
    P2,
    "Phase 2: report, docs and JUDGES.md update",
    ["phase-2", "docs"],
    "S",
    "P1",
    why="Each phase closes with evidence so the next phase starts from facts, not memory.",
    scope_in=[
        "`docs/phase2_report.md`",
        "`docs/scenarios.md`",
        "Roadmap, architecture and AGENTS.md touch-ups",
    ],
    criteria=[
        "Report: gate table, chosen query shapes and depth, E14 verdict, discrimination numbers per domain, final scenario counts, live calls by purpose, quota remaining",
        "AGENTS.md Qloo facts updated with any new VERIFIED behavior",
        "Architecture 'Planned' markers removed for what now exists; module map matches the code",
        "Roadmap Phase 2 status updated; every JUDGES.md command executed on Windows and Linux",
    ],
    checks=["Link and anchor check on all changed docs", "No Qloo names, ids or scores in committed docs"],
    deps=["P2-8", "P2-9", "P2-10", "P2-11", "P2-12"],
)

# ================================================================== phase 3
add(
    "P3-1",
    P3,
    "Phase 3: target adapter interface and OpenAI-compatible client",
    ["phase-3", "task", "llm", "critical-path"],
    "M",
    "P0",
    why="Blindspot must ask any model the exam questions and get comparable, structured answers back, cheaply and repeatably.",
    scope_in=["`src/blindspot/targets/`", "Target config", "Response cache"],
    scope_out=["Tool calling for the fix loop (#P5-1)"],
    notes=[
        "`Target` protocol: `async recommend(prompt, k) -> TargetResponse(items, raw_text, model, usage, latency)`",
        "OpenAI-compatible chat client with httpx: timeouts, bounded retries, per-target concurrency",
        'System prompt asks for JSON `{"recommendations": [{"name": "...", "why": "..."}]}`; tolerant parse '
        "(strip fences, extract the first JSON object), one repair retry, then fall back to line parsing",
        "Targets defined in `config/targets.yaml` (ids, display names, env var names for base URL and key, model id, "
        "flags such as `live_in_demo`); values from env only",
        "Cache table `target_responses_cache` keyed by target id, model id, prompt hash, temperature; replay costs nothing",
        "Temperature 0 (or lowest supported) and a fixed seed where the provider supports it",
    ],
    criteria=[
        "Two real targets answer a demo scenario and parse into 1 to k items",
        "Cache hit on repeat; no network call",
        "Malformed JSON, empty output, refusal and timeout each produce a typed outcome, never a crash",
        "Keys never logged; redaction test covers target keys too",
        "Offline tests with a mocked OpenAI-compatible server",
    ],
    checks=[
        "`blindspot target ask --target <id> --scenario <id>` prints parsed names (local only, not committed)",
        "Token usage recorded per call",
    ],
    risks=["Providers differ on JSON mode; don't depend on it, rely on tolerant parsing"],
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
    why="Target output is attacker-controllable text, and a user-supplied endpoint is a server-side request "
    "forgery risk. Both must be safe before anything reaches the UI.",
    scope_in=["Output sanitation rules", "BYO endpoint validation (off by default)"],
    notes=[
        "Never pass target text to a shell, SQL, template engine or tool unfiltered; HTML-escape at render",
        "Cap item count, name length and total response size",
        "BYO endpoint: HTTPS only, resolve DNS and block private, loopback, link-local and metadata ranges (IPv4 "
        "and IPv6), no redirects to blocked ranges, short timeout, separate tight budget",
    ],
    criteria=[
        'Fixtures with injection-style output ("ignore previous instructions", HTML, script tags, very long names) are stored and displayed inertly',
        "BYO endpoint disabled unless `ALLOW_BYO_ENDPOINT=true`",
        "With it enabled, 127.0.0.1, 10.x, 169.254.169.254, ::1 and a DNS name resolving to a private IP are all rejected (tests)",
    ],
    checks=["Security tests run in CI on both OSes"],
    deps=["P3-1"],
    refs=["SECURITY.md", f"{ARCH} Section 16"],
)

add(
    "P3-3",
    P3,
    "Phase 3: entity resolver",
    ["phase-3", "task", "qloo-api", "critical-path"],
    "L",
    "P0",
    why="Scores are only as honest as the match between an agent's free text and Qloo entities. The resolver must "
    "be accurate, show its confidence, and never mistake 'couldn't match' for 'doesn't exist'.",
    scope_in=["`src/blindspot/resolve/`", "Match statuses and confidences", "Match-rate reporting"],
    notes=[
        "Pipeline: normalize (shared function from #P2-7) → `entity_index` exact or alias → fuzzy (token-set ratio "
        "with a threshold tuned on V8 data) → budgeted `/search` with the domain's `/search` types and the city "
        "as a hint → existence evidence",
        "Existence evidence for places: coordinates or `external.google_place` from insights or `/entities` (E10)",
        "Statuses: `matched_in_key`, `matched_outside_key`, `phantom_candidate`, `unresolved` (budget, ambiguity, or error)",
        "New `/search` hits go into `entity_index` so later runs resolve offline",
    ],
    criteria=[
        "Each item gets qloo_id or none, confidence, method and status",
        "Match rate reported per domain and script and compared with the V8 ceiling",
        "Unresolved never counted as phantom",
        "Precision check: on 50 hand-labeled matches (local, gitignored), precision ≥ 0.9 for `matched_*` statuses; numbers recorded without names",
        "Search fallback respects per-run budget and the request deadline",
    ],
    checks=[
        "Unit tests per stage with synthetic index data",
        "Re-running a resolved audit makes zero `/search` calls",
    ],
    calls="About 150 to 300 across Phase 3 (search fallback)",
    risks=["Fuzzy thresholds tuned too loose create false matches that inflate taste-fit; prefer unresolved"],
    deps=["P2-7"],
    refs=[f"{ARCH} Section 8.2", f"{REALITY} E1, E10"],
)

add(
    "P3-4",
    P3,
    "Phase 3: synthetic calibration targets",
    ["phase-3", "task"],
    "S",
    "P0",
    why="We need targets with known answers to prove the metrics measure what we claim.",
    scope_in=["Four built-in targets in `src/blindspot/targets/calibration.py`"],
    notes=[
        "Qloo-copy: returns the scenario's local answer key",
        "Global-top-10: returns the same globally popular list for every scenario (from global baselines)",
        "Random-real: random entities from `entity_index` of the right type",
        "Invented: plausible but fake names",
        "All run offline from cached ground truth; seeded",
    ],
    criteria=[
        "Each target implements the adapter interface and is selectable by id",
        "Deterministic with a seed",
        "Zero network calls (test asserts it)",
    ],
    checks=["`blindspot score --target calib:qloo-copy --demo` runs offline"],
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
    why="Turn resolved recommendations into the numbers on the Blind Map, deterministically.",
    scope_in=["`src/blindspot/metrics/`"],
    notes=[
        "Implement exactly `docs/metrics_design.md`; pure functions over AnswerKey + resolved items",
        "Per-cell aggregation across scenarios and prompt variants; keep per-scenario values for bootstrap (#P4-3)",
        "Thin or empty cells return `not_scored` with a reason",
    ],
    criteria=[
        "Every metric has unit tests with hand-computed cases, including edge cases (empty list, all unresolved, duplicates)",
        "Hub down-weighting applied as designed",
        "Blindspot Score 0 to 100 with its components exposed",
        "No LLM involvement anywhere (grep test for target or LLM imports in `metrics/`)",
    ],
    checks=["`pytest tests/test_metrics*.py`", "Property test: score is invariant to input order"],
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
    why="No score is shown to anyone until known-answer targets land where they should.",
    criteria=[
        "Qloo-copy scores near 100 (range from the design note)",
        "Global-top-10: low taste-fit, high famousness, high swap sameness",
        "Random-real near zero taste-fit; invented-names phantom rate near 1 (with the resolver's real behavior)",
        "Calibration runs in CI on the synthetic snapshot and passes",
    ],
    checks=["CI job output shows the four calibration scores"],
    risks=[
        "If calibration fails, fix metrics or resolver, not the expected ranges, unless the design note is wrong (then update it in the same PR)"
    ],
    deps=["P3-4", "P3-5"],
    refs=[f"{ARCH} Section 9"],
)

add(
    "P3-7",
    P3,
    "Phase 3: end-to-end scoring CLI for real targets",
    ["phase-3", "task", "critical-path"],
    "M",
    "P0",
    why="Proves the whole pipeline on real models before the agent and UI are built on top of it.",
    scope_in=["`blindspot score --target <id> [--demo|--scenarios ...]`"],
    notes=[
        "Prompts → target → resolve → metrics; stores results in tables Phase 4 will extend",
        "Prints a per-cell table and match rates; writes a JSON report under `data/`",
    ],
    criteria=[
        "Two preloaded real targets scored on the demo subset end to end",
        "Re-run is fully cached (0 target calls, 0 Qloo calls)",
        "Report includes match rate, not-scored cells and cost (tokens)",
    ],
    checks=["Run with the kill switch on: scoring refuses with 'no answer key' (proves Qloo dependence)"],
    calls="Qloo: resolver fallback only (counted in #P3-3). Target tokens: record",
    deps=["P3-1", "P3-6"],
)

add(
    "P3-8",
    P3,
    "Phase 3: report",
    ["phase-3", "docs"],
    "S",
    "P1",
    why="Close Phase 3 with evidence.",
    criteria=[
        "`docs/phase3_report.md`: calibration results, match rate vs V8 ceiling per domain and script, first real scores (aggregates), token cost, Qloo calls by purpose",
        "Architecture and AGENTS.md updated where the design changed",
    ],
    checks=["No Qloo names in committed docs"],
    deps=["P3-2", "P3-7"],
)

# ================================================================== phase 4
add(
    "P4-1",
    P4,
    "Phase 4: audit run model and persistence",
    ["phase-4", "task", "critical-path"],
    "M",
    "P0",
    why="Audits must be resumable, replayable and inspectable, for the UI, for receipts and for judges re-opening a result.",
    scope_in=["Tables `audit_runs`, `target_responses`, `resolutions`, `cell_scores`, `audit_events`"],
    notes=[
        "Schema from architecture Section 14; migrate idempotently",
        "Audit id `run_...` reused as the Qloo `RequestContext.run_id`, so the call log links receipts to audits",
    ],
    criteria=[
        "Crash mid-audit and resume with no repeated target or Qloo calls",
        "Replay an audit from stored rows with zero network",
        "Retention rule for old audits documented",
    ],
    checks=["Integration test with the fake Qloo server and a mock target"],
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
    why="This is what makes Blindspot an agent: it decides where to look next instead of running a fixed grid.",
    scope_in=["`src/blindspot/auditor/`"],
    notes=[
        "States: plan → probe → score → select → confirm → report",
        "Plan: one probe per cell first (balanced), then adaptive",
        "Select: upper-confidence-bound on 'blindness' (low score, high uncertainty); explainable in one sentence",
        "Deterministic given seed and cached responses",
    ],
    criteria=[
        'Each transition emits an event with the reason ("probing dining × Lagos: lowest score, widest interval")',
        "Stops when budgets are spent or every cell is settled",
        "Same seed and caches → same sequence (test)",
    ],
    checks=[
        "Simulation tests with calibration targets: the auditor spends most probes on the global-top-10 target's worst cells"
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
    why="A blindspot claim must survive resampling; one bad answer isn't a blindspot.",
    notes=[
        "Bootstrap over scenarios and prompt variants in the cell (for example 1,000 resamples, seeded)",
        "Confirmed when the 90% interval's upper bound is below the threshold from the design note, with at least N probes",
    ],
    criteria=[
        "Cells reported with point score, interval and confirmed flag",
        "Synthetic tests: Qloo-copy never confirms a blindspot; global-top-10 confirms in most cells",
        "Minimum-probe rule enforced",
    ],
    checks=["Unit tests with fixed seeds"],
    deps=["P4-2"],
)

add(
    "P4-4",
    P4,
    "Phase 4: budgets for calls, tokens and time with clean stops",
    ["phase-4", "task", "judge-experience", "critical-path"],
    "S",
    "P0",
    why="An audit must never run away with Qloo quota, provider spend or a judge's patience.",
    notes=[
        "Per-audit caps: Qloo calls (via `RequestContext` run cap), target tokens, wall time",
        "Hosted demo defaults stricter than local",
    ],
    criteria=[
        "Hitting each cap stops cleanly with partial results and a reason event",
        "Caps configurable via settings and visible in audit status",
    ],
    checks=["Tests for each cap with mocked target and fake Qloo"],
    deps=["P4-2"],
)

add(
    "P4-5",
    P4,
    "Phase 4: audit API routes, event stream and receipts",
    ["phase-4", "task", "critical-path"],
    "M",
    "P0",
    why="The UI needs to start audits, watch them live and show the Qloo evidence behind every number.",
    scope_in=[
        "`POST /api/audits`",
        "`GET /api/audits/{id}`",
        "`GET /api/audits/{id}/events` (SSE)",
        "`GET /api/audits/{id}/receipts`",
    ],
    notes=[
        "Receipts come from `CallLog.for_run(run_id)`; strip nothing secret because nothing secret is logged, but "
        "re-check with the redaction test",
        "Hosted demo: only preloaded targets; one concurrent audit per client; precomputed audits returned instantly",
    ],
    criteria=[
        "Routes use the envelope, deadlines and budgets; SSE reconnect resumes from last event id",
        "Receipts list endpoint, params, served_from, status, latency and result count for every call behind a score",
        "Black-box tests including concurrent audits from different clients",
    ],
    checks=["`curl -N .../events` streams events during a mocked audit"],
    deps=["P4-3", "P4-4"],
    refs=[f"{ARCH} Section 12"],
)

add(
    "P4-6",
    P4,
    "Phase 4: explanations from computed numbers and Qloo explainability",
    ["phase-4", "task", "llm", "qloo-api"],
    "S",
    "P1",
    why="Judges need one plain sentence per blindspot. Qloo's own `feature.explainability` (VERIFIED, E8) can "
    "say why its answer fits, which is a strong Qloo highlight.",
    notes=[
        "Request `feature.explainability=true` on answer-key queries for demo cells (VERIFIED for artist and place)",
        "Explanation inputs: computed metrics, matched and missed entities, Qloo's explainability fields",
        "LLM phrases; template fallback with no LLM (replay mode); scores never change",
    ],
    criteria=[
        "Each confirmed blindspot has a one-to-two sentence explanation",
        "Explainability data stored with the answer key and shown as 'Why Qloo ranks this' in receipts",
        "Template fallback tested",
    ],
    checks=["Explanations never contain numbers that differ from computed ones (test)"],
    calls="Small: explainability on demo cells only",
    deps=["P4-3"],
    refs=[f"{REALITY} E8"],
)

add(
    "P4-7",
    P4,
    "Phase 4: report with audits of two models",
    ["phase-4", "docs"],
    "S",
    "P1",
    why="Close Phase 4 with evidence.",
    criteria=[
        "`docs/phase4_report.md`: confirmed blindspots with intervals for two models (aggregates), probes per cell, calls and tokens, stop behavior"
    ],
    deps=["P4-5", "P4-6"],
)

# ================================================================== phase 5
add(
    "P5-1",
    P5,
    "Phase 5: Qloo tools for the grounded agent",
    ["phase-5", "task", "qloo-api", "critical-path"],
    "M",
    "P0",
    why="The fix gives the target Qloo tools. They must go through our client so budgets, cache, strict params and receipts apply.",
    notes=[
        "Tools: `qloo_search(query, type)`, `qloo_recommend(domain, city, interests)` that builds the domain's "
        "standard query shape internally (the model never writes raw Qloo params)",
        "Tool results trimmed to names, types and ids; logged under the audit run id",
    ],
    criteria=[
        "Tool-calling loop works with at least one target from #U4",
        "Model-supplied arguments validated; free text resolved to ids before insights",
        "Strict params enforced; budget and deadline respected",
    ],
    checks=["Mocked tool-calling target in tests"],
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
    why="The before and after is the demo's punchline. It has to be honest: same exam, plus a held-out set.",
    notes=[
        "Grounding rule: recommend only entities returned by Qloo tools in this conversation (enforced by checking "
        "resolved items against tool results)",
        "Re-run the baseline audit's exact scenarios and prompt variants, plus holdout scenarios (#P2-6)",
    ],
    criteria=[
        "Per-cell and overall delta reported for baseline scenarios and holdout separately",
        "Grounding-rule violations counted and reported",
        "Calibration still passes on the grounded path",
    ],
    checks=["Delta reproducible from cache"],
    deps=["P5-1", "P4-5"],
)

add(
    "P5-3",
    P5,
    "Phase 5: fix API and export of prompt plus MCP config",
    ["phase-5", "task"],
    "S",
    "P1",
    why="Users should leave with something they can install.",
    criteria=[
        "`POST /api/audits/{id}/fix` and `GET /api/audits/{id}/export`",
        "Export: system prompt plus an MCP server config using placeholder env var names, documented in README",
        "No keys or real Qloo data in the export",
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
    why="Record the real delta, even if modest.",
    criteria=[
        "`docs/phase5_report.md`: before and after per cell and holdout for the demo targets; grounding violations; calls and tokens"
    ],
    deps=["P5-3"],
)

add(
    "P5-5",
    BL,
    "Stretch: full MCP server exposing Blindspot audits",
    ["phase-5", "stretch"],
    "L",
    "P2",
    why="Other agents could request a Blindspot audit or grounded recommendation as a tool.",
    criteria=[
        "MCP server with `audit` and `grounded_recommend` tools",
        "Install steps documented; budgets enforced",
    ],
    deps=["P5-3"],
)

# ================================================================== phase 6
add(
    "P6-1",
    P6,
    "Phase 6: UI scaffold, data label and cold-start state",
    ["phase-6", "task", "ui"],
    "M",
    "P0",
    why="A fast, honest UI shell that later views slot into, buildable before Phase 4 lands using mock data.",
    notes=[
        "Default: static HTML/CSS/JS served by FastAPI (`web/`), no build step; record the choice in decisions",
        "Persistent badge: 'Live Qloo data' or 'Synthetic sample data' from the envelope's `synthetic` flag",
        "Cold start: poll `/healthz` with a 'Waking up the demo' state",
        "Design tokens, light and dark, 16 px mobile gutters, no horizontal scroll at 360 px",
    ],
    criteria=[
        "Shell renders from mock JSON shaped like the real envelope",
        "Data label always visible and correct",
        "Cold-start state shown when `/healthz` is slow",
        "`ALLOWED_ORIGINS` set only if the UI is served from another origin",
    ],
    checks=["Lighthouse-style manual check on desktop and phone width"],
    deps=["P2-11"],
    refs=[f"{ARCH} Section 13"],
)

add(
    "P6-2",
    P6,
    "Phase 6: start screen with 'Roast an agent'",
    ["phase-6", "task", "ui", "judge-experience"],
    "S",
    "P0",
    why="Judges should see a result within seconds of landing.",
    criteria=[
        "One-click demo opens a precomputed audit instantly (no wait on Qloo or targets)",
        "Pick a preloaded target and cities for a fresh audit (hosted limits apply)",
        "One-paragraph explanation of what Blindspot does and why it needs Qloo",
    ],
    deps=["P6-1"],
)

add(
    "P6-3",
    P6,
    "Phase 6: Blind Map",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    why="The main visual: where the agent is blind, at a glance.",
    criteria=[
        "City × domain grid with score, interval and confirmed flag per cell",
        "Thin cells show 'insufficient Qloo data', not a score",
        "Accessible: values in text, not color alone; keyboard navigation; contrast passes",
        "Click a cell → Truth Table",
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
    why="Show the gap side by side: what the agent said, what it says with Qloo, and Qloo's answer key.",
    criteria=[
        "Three columns: agent alone, agent with Qloo (after fix), Qloo's answer key",
        "Each pick shows match status and confidence; phantoms and unresolved clearly different",
        "Qloo explainability ('why Qloo ranks this') shown where available (#P4-6)",
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
    why="Every number traceable to the Qloo calls behind it; this is the 'thorough use of Qloo' evidence for judges.",
    criteria=[
        "List of calls per score: endpoint, params, result count, `served_from`, latency",
        "Totals per audit: Qloo calls by endpoint, cache hit rate",
        "Nothing secret shown (redaction test on the receipts endpoint)",
    ],
    deps=["P6-4"],
)

add(
    "P6-6",
    P6,
    "Phase 6: Qloo kill switch",
    ["phase-6", "task", "ui", "judge-experience"],
    "S",
    "P0",
    why="The clearest proof the app can't work without Qloo.",
    criteria=[
        "Toggle sets the per-request flag; scores gray out with 'No answer key without Qloo'",
        "Only the viewer's own requests are affected (test with two browser sessions)",
        "State shown in the URL so it's shareable",
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
    why="Watching the agent decide where to look makes the 'agentic' part visible.",
    criteria=[
        "Streams events from SSE with reasons",
        "Ticker shows Qloo calls as they happen",
        "Survives reconnects",
    ],
    deps=["P6-1", "P4-5"],
)

add(
    "P6-8",
    P6,
    "Phase 6: before and after fix view with export",
    ["phase-6", "task", "ui", "critical-path"],
    "M",
    "P0",
    why="The punchline of the demo, shown honestly.",
    criteria=[
        "Per-cell and overall delta; holdout result shown separately",
        "Export button downloads prompt + MCP config",
        "Says plainly when gains are small",
    ],
    deps=["P6-3", "P5-3"],
)

add(
    "P6-9",
    P6,
    "Phase 6: 'Try your own city'",
    ["phase-6", "task", "ui", "judge-experience"],
    "S",
    "P1",
    why="Shows Blindspot is global by design and honest about coverage.",
    criteria=[
        "Location box runs the coverage probe first",
        "Small budgeted audit only when coverage is rich",
        "Clear message for thin or empty coverage",
    ],
    deps=["P6-3", "P2-10"],
)

add(
    "P6-10",
    P6,
    "Phase 6: mobile, accessibility and stranger test",
    ["phase-6", "task", "ui", "judge-experience", "critical-path"],
    "S",
    "P0",
    why="A stranger should understand Blindspot in 90 seconds on any device.",
    criteria=[
        "Phone layout checked at 360 px and 414 px; contrast and keyboard checks pass",
        "Someone who hasn't seen it runs the demo without instructions; notes and fixes recorded",
    ],
    deps=["P6-2", "P6-4", "P6-5", "P6-6", "P6-8"],
)

# ================================================================== phase 7
add(
    "P7-1",
    P7,
    "Phase 7: precompute demo audits and the model leaderboard",
    ["phase-7", "task", "judge-experience", "critical-path"],
    "M",
    "P0",
    why="Judges see results instantly and the demo never waits on quota or provider latency.",
    criteria=[
        "Baseline and fixed audits for every preloaded target precomputed and included in the real snapshot",
        "`GET /api/leaderboard` ('Which AI knows Lagos best?') from precomputed audits",
        "Snapshot re-uploaded (#U5)",
    ],
    calls="About 300 to 600 Qloo, plus target tokens",
    deps=["P5-4", "P2-12"],
)

add(
    "P7-2",
    P7,
    "Phase 7: hosted hardening and load test",
    ["phase-7", "task", "security", "judge-experience", "critical-path"],
    "S",
    "P0",
    why="The hosted demo has to survive judges clicking fast and in parallel.",
    criteria=[
        "Load test against the hosted URL (for example 50 concurrent users for 2 minutes): no 5xx, caps respected, deadlines met",
        "Quota exhaustion and cold-start paths show friendly states",
        "429 and 5xx handling exercised (simulated, since never observed live)",
    ],
    checks=["Load-test script and results committed (aggregates only)"],
    deps=["P7-1", "U1"],
)

add(
    "P7-3",
    P7,
    "Phase 7: findings write-up 'State of Cultural Blindness'",
    ["phase-7", "task", "docs"],
    "M",
    "P1",
    why="A short, press-worthy findings page makes the project memorable and shows real use.",
    criteria=[
        "Audits of 3 to 4 models; key findings with numbers and caveats; Qloo framed as a proxy, not truth",
        "Published as `docs/findings.md` and linked from the README",
        "No Qloo names beyond what D5 allows",
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
    why="Human preference numbers strengthen the claim that Qloo grounding is better.",
    criteria=[
        "Blind A/B of generic vs grounded recommendations; method and results recorded; consent obtained; no personal data stored"
    ],
    deps=["P5-4"],
)

add(
    "P7-5",
    P7,
    "Phase 7: Devpost description, screenshots and architecture image",
    ["phase-7", "task", "docs", "submission"],
    "M",
    "P0",
    why="The text judges read first decides whether they open the demo.",
    criteria=[
        "Opens with the Qloo-vs-LLM-only difference and real findings",
        "Explains why it only works with Qloo, listing every Qloo feature used",
        "Screenshots: Blind Map, Truth Table, receipts, kill switch, before/after; architecture image",
        "Draft reviewed by someone else",
    ],
    deps=["P6-10", "P7-3"],
)

add(
    "P7-6",
    P7,
    "Phase 7: final README and JUDGES.md pass",
    ["phase-7", "docs", "judge-experience", "critical-path"],
    "S",
    "P0",
    why="Repo docs must match the shipped product.",
    criteria=[
        "Live demo URL and screenshots in the README; feature table current",
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
    why="Nothing embarrassing on submission day.",
    criteria=[
        "Incognito desktop, phone on mobile data, another network: demo works end to end",
        "No console errors; data labels and `served_from` correct; kill switch works; cold start handled",
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
    why="CI could fail when a model update lowers the Blindspot Score; a badge spreads the project.",
    criteria=[
        "Reusable workflow running a cached audit and comparing with a baseline",
        "Badge 'Cultural Fit NN/100, powered by Qloo'",
    ],
    deps=["P4-5"],
)

# ================================================================== submission
add(
    "S1",
    SUB,
    "Make the repo public and verify submission requirements",
    ["submission", "needs-user", "critical-path"],
    "S",
    "P0",
    why="Submission requires a public repo with all source and a visible open-source license.",
    criteria=[
        "Final secret scan of history passes right before switching",
        "Repo public; MIT license shows in About",
        "Description and topics set",
        "CodeQL runs; findings reviewed or ticketed",
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
    why="Submit by 2026-10-28, two days before Devpost closes.",
    criteria=[
        "Hosted demo link, public repo link, description, license and all required fields filled",
        "Submission confirmed; screenshot saved",
    ],
    deps=["S1", "P7-5"],
)

# ================================================================== backlog
add(
    "B1",
    BL,
    "Deferred N3: synchronous SQLite inside async handlers",
    ["deferred", "chore"],
    "S",
    "P2",
    why="Sub-millisecond queries on one instance; revisit only if profiling shows it matters.",
    criteria=["Profile under the Phase 7 load test; move to a thread or async driver only if p95 suffers"],
    refs=["docs/phase1_audit.md N3"],
)
add(
    "B2",
    BL,
    "Deferred N4: HTTP limiter clears all keys past 50,000",
    ["deferred", "chore"],
    "S",
    "P2",
    why="Memory stays bounded; a brief reset of everyone's window is acceptable for one free instance.",
    criteria=["Replace with an LRU or time-bucketed limiter if the demo moves beyond one instance"],
    refs=["docs/phase1_audit.md N4"],
)
add(
    "B3",
    BL,
    "Chore: Starlette TestClient deprecation warning",
    ["chore"],
    "S",
    "P2",
    why="Tests warn that `httpx` with `starlette.testclient` is deprecated.",
    criteria=[
        "Follow the recommended replacement or pin a compatible version; warning gone; tests pass on Ubuntu and Windows"
    ],
)


# ================================================================== enrichment for Phases 3 to 7
# Extra notes, checks and risks merged into issues defined above (kept here to keep the definitions short).
EXTRA = {
    "P3-4": dict(
        risks=[
            "Global-top-10 must come from global baselines, not from a hand-written list, or calibration proves nothing"
        ]
    ),
    "P3-5": dict(
        notes=[
            "Module layout: `metrics/overlap.py`, `metrics/locality.py`, `metrics/sameness.py`, `metrics/phantom.py`, "
            "`metrics/score.py`; one pure function per metric with a docstring giving the formula",
            "Inputs are `AnswerKey` (from #P2-7) and resolved items (from #P3-3); no I/O inside metrics",
        ],
        risks=[
            "Hub down-weighting that's too strong punishes correct but popular answers; check against calibration"
        ],
    ),
    "P3-6": dict(
        notes=[
            "Add a CI step (or test marker) that runs the four calibration targets on the synthetic snapshot",
            "Store expected ranges in one place (`metrics/calibration.py`) quoted from `docs/metrics_design.md`",
        ]
    ),
    "P3-7": dict(
        notes=[
            "Results tables created here are the seed of #P4-1; name them as architecture Section 14 does",
            "JSON report under `data/reports/` (gitignored); a counts-only summary is safe to paste in the PR",
        ],
        risks=["Provider rate limits during a full demo run; use the response cache and small concurrency"],
    ),
    "P3-8": dict(checks=["Every number in the report traceable to a stored run id"]),
    "P4-1": dict(
        notes=[
            "`audit_events(audit_id, seq, ts, type, payload_json)` with `seq` monotonic for SSE resume (#P4-5)",
            "Store prompts and responses with their hash so replays are exact (AGENTS.md Section 8)",
        ],
        risks=[
            "SQLite writes from concurrent audits: one writer connection, WAL mode, busy timeout (AGENTS.md Section 6)"
        ],
    ),
    "P4-2": dict(
        risks=[
            "Over-exploiting one cell starves others; keep a minimum probe count per cell before adaptive selection"
        ]
    ),
    "P4-3": dict(
        notes=[
            "Implement with the standard library or NumPy only; no heavy stats dependency",
            "Expose interval width so the UI can show uncertainty",
        ],
        checks=[
            "Coverage check: on synthetic data with a known true score, the 90% interval contains it about 90% of the time"
        ],
    ),
    "P4-4": dict(
        notes=[
            "Token budget needs usage from the target adapter (#P3-1); time budget uses a monotonic clock",
            "Status endpoint shows remaining calls, tokens and seconds per audit",
        ]
    ),
    "P4-5": dict(
        notes=[
            "SSE via FastAPI `StreamingResponse` with `text/event-stream`; `Last-Event-ID` header resumes from `seq`",
            "Precomputed demo audits are returned with `served_from: snapshot` and no new calls",
        ],
        risks=[
            "Render's proxy may buffer SSE; send a comment heartbeat every 15 s and test on the hosted URL"
        ],
    ),
    "P4-6": dict(
        risks=["Explainability fields contain Qloo data: show them in the hosted demo, never commit them"]
    ),
    "P4-7": dict(checks=["Numbers in the report come from stored audits; no Qloo names committed"]),
    "P5-1": dict(
        risks=[
            "Models may call tools in loops; cap tool calls per scenario and count them against the audit budget"
        ]
    ),
    "P5-2": dict(
        notes=[
            "Store grounded responses as a separate audit linked to the baseline audit id",
            "Delta per cell = grounded score − baseline score, with intervals from #P4-3",
        ],
        risks=["Reporting only the improved cells would be dishonest; show all cells and the holdout"],
    ),
    "P5-3": dict(
        notes=[
            "Export the system prompt used by the grounded wrapper and an MCP config whose server would expose the "
            "#P5-1 tools; use placeholder env names like `QLOO_API_KEY`"
        ],
        checks=["Grep the export for any key-shaped string or Qloo id: none"],
    ),
    "P5-4": dict(checks=["Every delta in the report matches the API's numbers for the same audit ids"]),
    "P6-1": dict(
        risks=["A heavy frontend build adds deploy risk; prefer no build step unless there's a clear need"]
    ),
    "P6-2": dict(
        notes=[
            "Copy explains in two lines: what Blindspot checks, and that Qloo is the answer key",
            'Button text: "Roast an agent"; secondary: "Run a fresh audit"',
        ],
        checks=["Time from first byte to visible result under 2 s on a warm instance"],
    ),
    "P6-3": dict(
        notes=[
            "Render as an HTML table or CSS grid with text values in each cell; color is secondary",
            "Use a sequential palette for score and a pattern or icon for confirmed blindspots",
        ],
        checks=[
            'Screen reader reads each cell as "Lagos, dining: 34 of 100, confirmed blindspot" (or similar)'
        ],
    ),
    "P6-4": dict(
        notes=[
            "Columns come from `/api/audits/{id}` (baseline and fixed) and `/api/groundtruth/{scenario}`",
            "Show which prompt variant produced each list",
        ],
        risks=["Long names on phones; truncate with full text on focus or tap"],
    ),
    "P6-5": dict(
        notes=[
            "Data from `/api/audits/{id}/receipts`; group by scenario and endpoint; show params as a compact table"
        ]
    ),
    "P6-6": dict(
        notes=[
            "Implemented as a request header or query flag that sets `RequestContext.qloo_enabled=false` server-side",
            "Never a client-only visual trick: the server must actually refuse to score",
        ],
        checks=["With the switch on, network tab shows zero Qloo-derived values returned"],
    ),
    "P6-7": dict(
        notes=["Use `EventSource`; show the auditor's one-line reason for each step"],
        risks=["Too much motion; keep the ticker calm and pausable"],
    ),
    "P6-8": dict(notes=["Show baseline and grounded side by side per cell with the delta and its interval"]),
    "P6-9": dict(risks=["Free-text location is untrusted input; the server limits length and escapes it"]),
    "P6-10": dict(
        notes=[
            "Recruit someone who hasn't seen Blindspot; give them only the URL; time them; note where they hesitate"
        ],
        checks=["Notes from the stranger test and the fixes made are in the PR"],
    ),
    "P7-1": dict(
        notes=[
            "Precompute script runs every demo audit and its fix, then exports the real snapshot again (#P2-12)",
            "Leaderboard ranks targets by Blindspot Score per city with intervals; label it as 'against Qloo's view of taste'",
        ],
        risks=["Precompute spends real quota and tokens; run once, cache everything, and re-export"],
    ),
    "P7-2": dict(
        notes=[
            "Use a small load tool (for example `locust` or a Python asyncio script) from outside Render",
            "Watch `/api/qloo/status` budgets during the run",
        ],
        risks=[
            "Load testing the hosted demo spends quota if it hits uncached paths; target cached routes and cap it"
        ],
    ),
    "P7-3": dict(
        notes=[
            "Structure: question, method (one paragraph), 3 to 5 findings with numbers and intervals, limits, how to reproduce"
        ],
        risks=["Overclaiming; every finding says 'measured against Qloo's taste graph'"],
    ),
    "P7-4": dict(
        notes=[
            "Show each participant pairs of recommendation lists without labels; ask which fits the persona better"
        ],
        risks=["Small sample; report as indicative, with the count"],
    ),
    "P7-5": dict(
        notes=[
            "Sections: one-line pitch, problem, what it does, why it needs Qloo (feature list), how it works, "
            "findings, how to try it, limits, team",
            "Architecture image exported from `docs/architecture.md` diagrams",
        ],
        checks=["Every claim in the text matches the live demo and the findings page"],
    ),
    "P7-6": dict(checks=["Run each README and JUDGES.md command verbatim in a fresh clone; note timings"]),
    "P7-7": dict(
        notes=[
            "Checklist: landing, Roast an agent, Blind Map, Truth Table, receipts, kill switch, fix view, export, try your own city, cold start"
        ],
        checks=["Recorded results for each device and network in the PR or issue"],
    ),
    "S1": dict(
        notes=[
            "Settings → General → Danger zone → Change visibility. Then check `gh repo view --json licenseInfo`"
        ]
    ),
    "S2": dict(
        notes=[
            "Devpost requires: a working hosted demo link, a public repo with all source and run instructions, a "
            "text description, and an open-source license visible in the repo's About section"
        ]
    ),
}

for _i in ISSUES:
    for _field, _vals in EXTRA.get(_i["key"], {}).items():
        _i[_field] = _i[_field] + list(_vals)
