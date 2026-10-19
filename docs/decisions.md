# Decisions

Each entry: decision, then reason. "Review first" marks the ones the project owner should look at.

## Repo and tooling

**D1. The project has its own git repository.** The working folder sat inside a git repo rooted at the home directory (`C:/Users/ombis`), so committing there would have mixed this project into it. I ran `git init -b main` in the project folder; the parent repo is untouched.

**D2. venv + pip, with pip-tools lockfiles** (`requirements.lock` for runtime, `requirements-dev.lock` for dev). `uv` isn't installed and the brief says to fall back. The lock is compiled on Python 3.11. The dev lock pins `colorama` (pulled in on Windows), which is pure Python and harmless on Linux/macOS. Verified: the lock installs and the tests pass on Windows (3.11) and Linux (`python:3.12` container).

**D3. `scripts/dev.py` is the cross-platform task runner, and the Makefile only delegates to it.** Windows has no standard `make`, and recipes written in shell differ between `sh` and `cmd`. Tested: `make demo`/`make test` on Linux, `mingw32-make demo` on Windows, plus `python scripts/dev.py <task>` directly.

**D4. Line endings are LF** via `.gitattributes`, so the Dockerfile and shell bits behave the same everywhere.

## Terms and data (review first)

**D5. TERMS CHECK: no Qloo response data in the public repo.** The Qloo hackathon developer guide says: *"There is no time limit on private server-side caching of responses. Don't store Qloo response data in a public repository."* So:
- `data/` (cache, raw recordings, snapshot) is gitignored.
- Test fixtures are **synthetic**: they copy the documented shape, with invented ids, names and scores (`tests/fixtures/README.md`).
- The committed coverage grid and CSV hold statuses, counts, latency and overlap only, never result names.
- The demo query list (`config/demo_queries.json`) holds queries only, no ids from Qloo.

**Open question for you:** is even a *minimal* snapshot (ids, names, scores for about 50 demo queries) allowed in the public repo so judges can run fully offline? Ask in Qloo's Discord #api-help. Until then the committed snapshot is **empty**, marked "needs terms check", and the no-snapshot fallback works: replay mode returns `served_from: degraded` with "use the hosted demo, or add your own QLOO_API_KEY".

**D6. The real snapshot lives only in `data/snapshot/` locally, or as a host secret file.** On Render that's a Secret File `snapshot.json` mounted at `/etc/secrets/snapshot.json` (path still UNVERIFIED: no snapshot has been uploaded yet, #20), set via `QLOO_SNAPSHOT_PATH`. On startup the service seeds an empty cache from it, because free hosts have ephemeral disks. Snapshot-seeded rows don't expire and are always labelled `served_from: snapshot`, never `cache` or `live`.

**D7 (review first). Local with no key can't be fully offline with real data.** Bundling real data would break D5. Proposal for Phase 2: a `remote` mode in which a local checkout with no key sends its Qloo lookups to the hosted demo's guarded API. The hosted server still enforces budgets, and nothing gets stored in git. Not built in Phase 1.

## Qloo client behavior

**D8. Budgets count live HTTP attempts, retries included,** and each attempt is reserved *before* it's sent, inside one lock. Counters persist in SQLite (`live_attempts`). That makes the caps a hard upper bound on requests that reach Qloo, even under concurrency (tested with 50 concurrent requests and a cap of 20).

**D9. Budget scopes:**
- Lifetime: `QLOO_MAX_LIVE_CALLS`, 600 for this phase.
- Rolling 24 h: `QLOO_DAILY_LIVE_CAP`.
- Per client per rolling hour: `QLOO_CLIENT_HOURLY_CAP`.
- Per run: `QLOO_RUN_CAP`.

Ad-hoc traffic may use only `daily − QLOO_DEMO_RESERVE`, even when the reserve hasn't been touched. Demo-priority calls (`RequestContext.priority="demo"`) can use the whole daily cap. Local scripts and the CLI (client ids `local:*`) skip the per-client and per-run caps, because those exist to keep anonymous web users apart; the lifetime and daily caps still apply. "Daily" is a rolling window rather than calendar days, which is simpler and can't be gamed at midnight.

**D10. Identical concurrent queries are coalesced into one live call.** When several judges click the same demo button at once, Qloo sees one request. Followers get the same data, still labelled `live` (it *is* fresh), with reason "shared a concurrent identical live call".

**D11. Circuit breaker:** it opens after 3 consecutive 429s and stays open for `max(30 s, Retry-After)`. A half-open state then allows exactly one probe: success closes the breaker, another 429 reopens it. The breaker is process-wide, so retries from many users can't pile up.

**D12. Retries:** at most 4 attempts, exponential backoff with jitter (0.5 s base, capped at 8 s), honouring `Retry-After`. A `Retry-After` over 20 s degrades immediately instead of holding a user request open. There are no retries on 4xx other than 429. 401 maps to `QlooAuthError`; 403 is treated as a bad request, because the hackathon guide says it means an unsupported entity type.

**D13. Validation before any network call:**
- an endpoint allow-list of the documented read endpoints only;
- at most 40 params, 2000 chars per value and 4000 in total;
- `take` clamped to 50 (the documented insights max);
- `filter.type` checked against the known types (both `video_game` and `videogame` accepted until recon settles it).

**D14. The cache key is sha256 of method + path + normalized params.** Keys are sorted, `None` and empty values dropped, bools become `true`/`false`, `5.0` becomes `5`, and lists equal comma strings. Order *within* a list is preserved, because Qloo may give it meaning. TTL defaults to 7 days; snapshot rows never expire.

**D15. Rate-limit headers and error bodies are captured, redacted, on results** (`rate_headers`, `error_body`) so recon can document them. Request headers are never stored. The observed error envelope is `{"error":{"reason","message","code"}}` (VERIFIED), and the client shows "message: reason" to users.

**D16. Redaction:** the key is registered once and scrubbed from logs (a root and library log filter), the call log, cache rows, error bodies, raw recordings and `/status`. `scripts/scan_secrets.py` checks the tree, git history and Docker image for the literal key and common key patterns. Planting a fake key was detected in all three places.

## Service

**D17. Client identity is `HMAC-SHA256(IP_HASH_SALT, ip)`, truncated.** Raw IPs are never stored. `X-Forwarded-For` is trusted only for `TRUSTED_PROXY_HOPS` hops (0 locally, 1 on Render, still to be verified after deploy). `/status` returns the caller's hashed id, so proxy handling can be checked without exposing IPs.

*Update 2026-10-17 (#80):* on Render the chain is client → Cloudflare → Render proxy → app (VERIFIED: every response carries `Server: cloudflare`, `CF-RAY` and `x-render-origin-server`; the host resolves to `*.origin.onrender.com`), so `TRUSTED_PROXY_HOPS=2`. With `1` the app hashed the Cloudflare edge address, which varies per request: a client could change identity by letting requests land on different edge nodes, and the per-IP limit never accumulated. Forged `X-Forwarded-For` values are appended to, not replaced (Render community reports; consistent with the probe), so the client's own entry is never read at hops=2. The `cf-connecting-ip` header is UNVERIFIED on Render and not used.

**D18. Per-IP HTTP rate limit:** 30 requests per minute on `/api/*`, in memory, for one instance. Health checks are never limited.

**D19. A small attack surface:** JSON only, no OpenAPI or Swagger UI, `CSP default-src 'none'`, `nosniff`, `X-Frame-Options DENY`, `no-store`. CORS stays closed unless `ALLOWED_ORIGINS` is set, and then only GET is allowed.

**D20. An unwritable cache path falls back to a temp DB, and then to an in-memory one,** instead of crashing. Docker Compose uses a named volume, because a root-owned bind mount on Linux wouldn't be writable by the image's non-root user.

**D21. The app is a factory** (`uvicorn blindspot.service.app:main_app --factory`). Importing the module has no side effects, so tests don't touch `data/`.

## Hosting (review first)

**D22. Render, free web service, Docker runtime, region `virginia`.**
- Why Render: no credit card for the free tier, Docker support, a `render.yaml` blueprint that judges can read, health checks, and secret files.
- Alternatives: Fly.io no longer offers a free tier to new organizations; Railway has trial credit only; Hugging Face Spaces (Docker) is the fallback if Render is unsuitable, since it sleeps only after 48 h idle. These are from my knowledge as of 2026 and UNVERIFIED.
- Why Virginia rather than Singapore: `hackathon.api.qloo.com` resolves to AWS us-east-1 ranges (VERIFIED by DNS). A TCP connect from the dev machine takes about 250 ms. One audit run will make many Qloo calls but only a few browser requests, so putting the server next to Qloo cuts total latency, even for viewers in India (about 250 ms RTT to the server, paid per page rather than per Qloo call).

**Deployed 2026-10-16 (#8): https://blindspot-tva4.onrender.com.** Observed, not assumed:
- The Blueprint flow worked from `render.yaml` with no edits: free Docker web service, health check `/healthz`. Only `QLOO_API_KEY` was set by hand in the dashboard.
- Region: `render.yaml` requests `virginia`, but the service's origin hostname is `gcp-us-west1-1.origin.onrender.com` (Oregon). Which one the instance actually runs in is UNVERIFIED until read from the Render dashboard (#8 review); TCP timings from Mumbai (about 50 ms) only reach Cloudflare's local edge and can't settle it. If it is Oregon, the "next to Qloo's us-east-1" rationale above does not hold and the region should be corrected in the dashboard.
- Auto-deploy: every commit to `main` redeploys, and `render.yaml` env changes are applied on that deploy (the #79 cap change showed up in `/api/qloo/status` without a manual Blueprint sync).
- The disk is ephemeral: the budget counters read 0 after the #79 redeploy (D33).
- The service sits behind Cloudflare (`Server: cloudflare`, `CF-RAY`). `TRUSTED_PROXY_HOPS=2` (#81) removed forged-value influence but the identity still varies between plain requests (2026-10-19), so the chain has more than two hops; #80 stays open (D17).
- Cold start: about 60 s, measured 2026-10-19 from a phone on mobile data after 20+ minutes idle (manual count, approximate). That matches Render's own dashboard figure of "50 seconds or more" for a free instance after idle.
- Still UNVERIFIED: the spin-down delay (about 15 minutes per Render's docs), the Secret File mount path (#20), and the 750-hour monthly allowance.

**Keep-alive:** `.github/workflows/keepalive.yml` asks for a `/healthz` ping every 10 minutes, but GitHub runs scheduled workflows best-effort: 9 runs in 28 hours, 2 to 4.5 hours apart. It can't keep a free instance awake on its own. The real mitigation is the UptimeRobot HTTP(s) monitor on `/healthz` every 5 minutes, live since 2026-10-19 (status Up, responses 120 to 226 ms).

## Phase-1 call budget plan

**D23. Planned live calls:** smoke 4, recon about 121 (`--max-calls 300`), coverage about 96 (`--max-calls 150`). That's about 221 of 600, leaving room for re-runs. Everything goes through the client, so cached repeats are free and the lifetime cap of 600 is enforced by code.

**Used so far: 0** hackathon-key calls. Six requests went to the real host with no key or a fake key, purely for connectivity, error-shape and latency checks. They were rejected at authentication, so they should not count toward the key's quota (how Qloo meters rejected requests is unverified).

## Audit fix pass (2026-10-15)

**D24. Ids, not names, go to Qloo.** Entity, tag and audience parameters must hold ids of the VERIFIED shapes: UUID entity ids, `urn:tag:…` and `urn:audience:…`. Anything else is `outcome=resolution_failed`, and no request is sent. Names are resolved explicitly through `client.insights(..., resolve={param: [names]})` using `/search` and `/v2/tags`; each lookup is cached, logged and budgeted. Audiences have no name lookup: use the `id` field from `/v2/audiences`, never its `entity_id` UUID (VERIFIED, E5).

**D25. Every result has an `outcome`:** `ok | empty | qloo_error | resolution_failed | invalid | disabled | not_live`. Reports classify by `outcome`, never by `served_from`, so a local refusal can't be read as a Qloo error.

**D26. Strict parameter guard (`qloo/params.py`).** Qloo ignores unknown parameters silently, so app code refuses any parameter not VERIFIED as *effective* for the `filter.type`, before any network call. Scripts may use `strict=False`, and every unverified parameter they send is logged in the call log. The allowlist only grows with recorded evidence.

**D27. The web deadline is cooperative.** Each web request carries `RequestContext.deadline`, set from `REQUEST_DEADLINE_S` (8 s). Retries, backoff, `Retry-After` and token waits never go past it, and an outer `wait_for` fence plus follower handling make sure a cancelled leader can't crash shared calls. Scripts and the CLI have no deadline.

**D28. Token-bucket capacity 1 at `QLOO_RPS=4`.** With no burst, a cold bucket can't exceed Qloo's VERIFIED limit of 5 req/s.

**D29. Real API over docs: the video game type is `urn:entity:videogame`.** The guide says `video_game`, but the hackathon host rejects it (400) for both insights and search (VERIFIED). The enums follow the API, and CLAUDE.md's fact line was corrected with this evidence.

**D30. Each call-log row has a `purpose`** (`app | audit | recon | coverage | smoke | script`), and live-call reporting breaks down by purpose (`/status` → `live_calls_by_purpose`). The audit's earlier rows were re-tagged `audit` by a one-off local SQL update; the cached responses were kept.

**D31. Cities, domains and demo queries are data in `config/`,** validated by `blindspot.catalog`. A test fails if a configured city name appears in `src/`.

**D32. Small hardening:**
- Empty 200s are cached for 1 h only.
- Response bodies over 4 MB are refused.
- One rate-limited request counts once toward the breaker.
- Rate-limit headers are recorded per response.
- Replay mode serves stale cache rows, labelled `cache` with their `fetched_at`.
- Call-log rows are deleted after 30 days. `live_attempts` rows are kept, because the lifetime cap counts them.
- The CLI installs log redaction.
- `--no-server-header` is set.
- `ruff format` is enforced by `make check`.

**D33. Hosted live-call caps: 150/day with a 50 reserve (#78, 2026-10-17).** The hosted demo shares the key's 10,000-calls/month quota with local Phase 2-7 work. The quota window started at first use on 2026-10-15 (`x-month-ratelimit-reset` read 30 days) and covers the whole hackathon to 2026-10-30. Worst case, with every cap hit every day:

| Item | Calls |
|---|---|
| Already used (audit, recon, coverage, deploy check) | about 290 |
| Planned local Phase 2-7 work | 2,000 to 3,000 |
| Hosted, 2026-10-17 to 10-30 (14 days × 150; web traffic alone is ad-hoc, so at most 100/day) | 2,100 |
| Redeploys: counters reset each time, so each one can add another 150 that day (assume 20 before 10-30) | 3,000 |
| **Worst-case total** | **about 8,400 of 10,000** |

The old setting (500/day, 400 ad-hoc) allowed about 12,000 a month on its own. Budget counters live in SQLite on Render's ephemeral disk, so they reset on every redeploy until durable counters land (#20). Revisit in #57/#58: once the demo is precomputed, most judge traffic is served from the snapshot and the cap can be set from measured use.
