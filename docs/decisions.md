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

*Update 2026-10-19 (#80):* on Render the chain is client → Cloudflare → Render edge → Render internal proxy → app, so `TRUSTED_PROXY_HOPS=3`. VERIFIED with a temporary probe (branch `diag/proxy-headers`, salted hashes only): `X-Forwarded-For` arrives with 3 entries, [public client, public edge, private internal]; a forged value is appended on the left (4 entries, 5 for a two-value forgery); entry `[-3]` is identical across 9 requests from one client and equals both `cf-connecting-ip` and `true-client-ip`, while `[-2]` took 4 values and `[-1]` 3. Headers present: `cf-connecting-ip`, `true-client-ip`, `cf-ray`, `cdn-loop`, `rndr-id`, `x-forwarded-proto`. The earlier settings read a proxy address: with 1 the Render internal hop, with 2 the Cloudflare edge, so identity changed per request and the per-IP limit never accumulated. `client_ip` keeps `X-Forwarded-For[-hops]` as the source (configurable, not Render-specific) and logs a warning, without addresses, when `cf-connecting-ip` disagrees.

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
- Region: **Virginia**, VERIFIED on the Render service page (2026-10-20), as `render.yaml` requests. The `gcp-us-west1-1.origin.onrender.com` hostname is Render's routing layer, not the instance location, so the "next to Qloo's us-east-1" rationale above holds.
- Auto-deploy: every commit to `main` redeploys, and `render.yaml` env changes are applied on that deploy (the #79 cap change showed up in `/api/qloo/status` without a manual Blueprint sync).
- The disk is ephemeral: the budget counters read 0 after the #79 redeploy (D33).
- The service sits behind Cloudflare and two Render proxies; `TRUSTED_PROXY_HOPS=3` (#80, #84; verified chain in D17). Re-verified 2026-10-20 after that deploy: `verify_deploy.py` 8/8, one identity per client. Gate 11 closed (#8).
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

**D34. Answer-key depth 20, membership pool 50, for every domain (E12, #13, 2026-10-20).** In both
test cities take 20, 30 and 50 returned full lists, the top 10 never changed with `take`, and ranks
11-20 and 21-50 had 0.0 overlap between cities and at most 0.07 between a persona and the standard
query. 20 is deep enough for recall at the agent's list length; the top 50 is kept as a membership
pool for scoring picks outside the top 20. Rich/thin thresholds are provisional (every cell filled),
recorded for #24 in the reality doc.

**D35. Dining shape: location filter + restaurant tag + the persona's audience id (E13).** All six
signal shapes were perfectly stable on a cache-bypassed repeat (Jaccard 1.0). Only the audience shape
stayed under the 8 s request deadline (p50 2.55 s, slowest 4.29 s); entity and tag shapes reached 9 to
28 s, and tag+audience timed out in one city. The pre-set rule (slowest call ≤ 4 s) left no shape;
the rule used is p50 ≤ 4 s with every call under the 8 s deadline, as the brief asks ("well under the
8 s deadline"). Latency rests on six calls per shape.
The 8 s deadline binds live request paths: the any-city coverage probe (#24) and hosted audits. The
answer-key build (#18) is a resumable batch job, so it may retry a timed-out dining cell beyond 8 s;
a slow shape is unusable only for live paths, not for ground truth. The audience-only evidence is
thin (six calls, and Phase 1 E11 saw an audience-signal dining query time out in 1 of 2 cities), so
#18 records per-cell timeouts and retries them, and #24 reports a dining timeout as `unknown`, never
`empty`.

**D36. Candidate scoring with a persona signal; fallback is rank membership in the local top 50
(E14).** `filter.results.entities` with the location plus an entity, tag or audience signal returned
24-25 of 25 candidates, all scored, all from the pool, in under 1 s, in every domain (50 of 50 with
an entity signal). Film with an entity seed was partial for the seed tested, in one city (6/10,
18/25, 36/50 in the rich city; 25/25 in the focus city), so film scoring prefers tag or audience
signals or the fallback; entity scoring for film isn't ruled out. The fallback is computable from the E12
recordings.

**D37. The `audience` probe is viable for music, film and brands, marginal for dining (E15).** Mean
pairwise top-10 Jaccard across 4 personas was 0.07-0.24 for music, film and brands in both cities.
Dining was 0.14 in the rich city but 0.75 in the focus city, so dining audience probes need a
per-city coverage check before use (#16).

**D38. Personas are taste-only and each has its own audience set (#14, 2026-10-20).** Every domain's
answer-key shape uses the persona's audience (D35), so two personas with the same audiences would get
identical answer keys; the loader rejects that. Audiences are given as a resolved URN or as
`{name, type}` for #15 to match against `GET /v2/audiences`, since Qloo has no audience name search.
Both forms compare as one key, and #15 re-runs the check on the resolved URNs. Identity-based
audience types (`communities`, `political_preferences`, `lifestyle_preferences_beliefs`) and identity
words (whole tokens, in every text that can reach a prompt) are rejected while `allow_identity` is
false. Config loads strictly: unknown keys, references, probe pairs and `condition` checks fail rather
than fall back (review of #88). Age is a prompt detail only; a shape that sends it refuses UNVERIFIED
bands such as `36_to_55`.
Dining tastes are stated as interest tags, not seed places, because places are city-specific.
`take` stays 10 in `domains.yaml` (the recon scripts' cache keys use it); the answer key uses the new
`depth` (20).

**D39. Committed config may hold bare Qloo ids that queries need; docs and fixtures hash or label
them (#15, 2026-10-20).** `config/resolved_ids.lock.json` commits the seed entity UUIDs and the tag
and audience URNs, because it's functional config: judges need it for the no-key and own-key runs
(hard rule 4). It holds ids only, never names, scores or popularity (D5). Documentation is
different: an id there only illustrates, so it is labelled (`entity#<sha256[:6]>`, as in
`docs/param_evidence.md`, #86), and test fixtures use synthetic ids.

**D40. Scenario generation choices (#16, 2026-10-20).**
- **Own seeds are excluded from the answer key.** The local and global specs carry
  `filter.exclude.entities` with the persona's resolved seeds in the asked domain (VERIFIED, #12).
  A prompt names those seeds, so an agent echoing them must not count as taste, and no prompt can
  contain an answer-key entity.
- **The target is asked for k = 10.** The key is 20 deep with a 50-item pool (D34), so recall has
  room and picks outside the key can still be placed.
- **One fetch per question.** The local spec's `take` is the pool (50), and its top 20 is the key.
  The global baseline has the same params without a location and the same `take`.
- **Balance, pairs and tags.** Seeded greedy balance per probe; swap and audience pairs are picked
  and tagged as units. The holdout is every 5th pick per probe (about 20%). The demo is 30 to 40
  scenarios with at least 8 swap pairs.
- **`demo` is a city field** (all six today, all rich per the coverage matrix).
- **Size.** 104 scenarios need 155 distinct queries (95 local, 60 global), 66 for the demo, which is
  well within Phase 2's 1,200-call budget for #18.
- **A prompt names only seeds with a locked id.** Only those can be excluded from the key. A
  persona-domain whose only seeds are unresolved and that has no interest tags is skipped and
  listed (today: classical_and_jazz/brands, until Steinway is replaced).
- **Holdout follows the question, not the probe.** The same question can be asked under two probes
  (5 such questions today), so the holdout decision is shared by every copy and by whole pairs.
  Otherwise the fix loop could tune on one copy and be scored on another (review of #92).
- **`conditions` are evaluated by #18, not the generator.** `personas_distinct_in_city` needs answer
  keys, so the generator records it on the scenario, and the answer-key build must check it before
  a scenario is scored.
- **`scenarios_per_cell` is removed.** Its cap logic did the opposite of its description, and one
  question per cell is all the exam needs (review of #92).

**D41. Answer-key build choices (#18, 2026-10-20).**
- **Store and keys.** A separate SQLite file, `data/groundtruth.sqlite` (gitignored). Runs are keyed
  by spec content key, so 104 scenarios need 155 stored runs, each fetched once (identical in-flight
  specs share one request).
- **Checkpoint and resume.** Each run lands with its items in one transaction. When both of a
  scenario's runs are stored, its status becomes `fetched`: the checkpoint. `--resume` (the default)
  skips stored runs and fetched scenarios, and drops scenarios no longer in the exam (their runs,
  keyed by spec, stay reusable).
- **Stops.** `--max-calls` is a hard stop on requests the builder starts. The builder reserves each
  request itself, with the concurrency slot held, rather than reading the client's call count. On a
  stop no new request starts, but every request already sent finishes and is stored, so a clean stop
  repeats nothing. A hard kill can repeat only the requests in flight at the kill (tested).
- **Validation first.** Every distinct spec passes the strict guard and the id check before anything
  is written or sent. A config bug fails at once and lists every bad spec (`SpecError`).
- **Readable only when checked.** After every build, stopped or not, `finalize()` lints each fetched
  scenario and evaluates its `conditions`, and only then gives it a readable status: `done`, `thin` or
  `excluded`. A dining audience half whose partner isn't fetched yet stays `fetched`, which
  `get_answer_key` refuses. An empty local list gets status `empty`, which is also refused, so it can't
  be scored by accident.
- **One store path.** A relative `GROUNDTRUTH_PATH` resolves from the repo root (the parent of the
  config dir) for both the build and every reader (`default_store_path`).
- **Below threshold.** First a param audit: the strict guard and the id check run before every
  request. Then one broader retry that drops only the optional narrowing params (`filter.exclude.entities`,
  `bias.trends`). Never the location, the type or the persona signal: without those, the list
  answers another question. If the exclusion was dropped, the persona's own seeds are filtered out
  of the result (D40). Still short: recorded as `thin` (10 to 19), `sparse` (1 to 9) or `empty`, never
  hidden. Thresholds are the provisional ones from #13.
- **Failures** are not stored. The scenario is `failed`, and a re-run retries it.
- **Conditions.** `personas_distinct_in_city` excludes a dining audience pair whose top-20 Jaccard
  is over 0.5 (the E15 viability line).
- **Prompt lint.** Any scenario whose prompt contains one of its answer-key names is excluded and
  counted.
- **Provenance.** `get_answer_key` serves stored data as `cache`, or `snapshot` when the snapshot
  supplied it, never `live`. `fetched_from` and `fetched_at` keep how and when it was fetched (hard
  rule 5).
- **Purpose.** Build calls are logged with purpose `groundtruth` (new), so the budget report
  separates them from recon.

**D43. Dining's famousness baseline is the city with no persona; answer-key builds use a 45 s timeout
and 2 attempts (#18, 2026-10-21).**
- **Why.** In the #18 live build every dining baseline (the restaurant query with the persona's audience
  and no location) timed out, on every retry. A probe of the city with no persona (location filter +
  restaurant tag, `take` 50) returned all 50 in 3.3 s, without affinity scores, as E11 predicts for a
  place query with no signal.
- **What `G` means.** Famousness counts picks in `G` but not in `L`. For music, film and brands `G` stays
  the same taste with no location ("famous anywhere"). Restaurants belong to one city, so a no-location
  list would hold places an agent here can't name, and famousness would read near 0 for every agent.
  For dining `G` is the city's list without the persona ("what anyone is shown here"). The domain field
  `baseline: city` selects it; `AnswerKey.baseline` and the stored run `kind` say which one a key has.
- **Rejected.** Building `G` offline from the persona's other cities (famousness near 0 by
  construction, and the own city would leak in); a smaller `take` (untested, about 20 calls to find out,
  the same timeout risk); no dining baseline (loses V5 and famousness for a third of the exam).
- **Shape.** The baseline differs from the local spec in one thing only: no persona signal. It keeps the
  location filter, restaurant tag, `take`, the probe's params (freshness keeps `bias.trends`) and any
  own-seed exclusion. Every persona asked about a city under the same probe shares one baseline: 12 for
  the current exam, which replaces 19 global dining specs.
- **Timeouts.** The build is a batch job, so `blindspot groundtruth build` sets `--timeout 45` and
  `--attempts 2` instead of the web defaults (10 s, 4 attempts). Freshness keys took 16 to 20 s, and
  four 10 s tries paid four calls for nothing.

**D44. `httpx2` as a dev dependency for Starlette's TestClient (#69, 2026-10-21).**

Starlette 1.9 warns that `starlette.testclient` with `httpx` is deprecated and prefers `httpx2`
(maintained by Pydantic Services). `httpx2` is added to the `dev` extra only and to
`requirements-dev.lock`; the app and the Qloo client still use `httpx`, and `respx` still mocks it.
The TestClient makes no network calls. `requirements.lock` is unchanged.

**D45. Scenario and answer-key routes (#25, 2026-10-21).**
- **Provenance of the exam.** `/api/scenarios` serves scenarios generated offline from config and the id
  lock; no Qloo result is in them, so `served_from` is `disabled` (no Qloo involved), not `cache` or
  `snapshot`. Answer keys keep the stored key's `cache` or `snapshot`.
- **Names are returned as Qloo gives them.** JSON is not HTML: the response is `application/json`
  with `nosniff` and CSP `default-src 'none'`, so escaping here would only corrupt names ("A & B"
  would read "A &amp; B" while `norm_name` stays raw) and differ from `/api/qloo/search`. Whatever
  renders a name into HTML (the Phase 6 UI) escapes it there.
- **Synthetic label.** The store has no marker of its own, so `GROUNDTRUTH_SYNTHETIC=true` labels the
  answer-key responses `synthetic: true`. Set it wherever the store was built from synthetic data.
- **No budget spend.** Both routes read stored data. They run under the request deadline and the
  per-client rate limit, and use `qloo=False` contexts, so a live call cannot happen from them.
- **OpenAPI stays off (D19).** The issue asks for the routes in `/docs`; D19 keeps `/docs` and the schema
  off as a small attack surface. Left as is and raised in `docs/NEEDS_FROM_USER.md`.
- **CLI.** `blindspot param promote` calls `scripts/promote_param.py`. `validity run` belongs to #21.


**D46. Coverage probe for any location (#24, 2026-10-21).**
- **Query.** Each domain's standard location-only query from `config/domains.yaml`, no persona signal:
  dining is the location filter plus the restaurant tag (fast and complete, D43), the other domains the
  location signal. `take` is the answer-key depth (20), not the recon `take` (10): the verdict asks
  "is there an answer key here?", and at 10 no domain could ever be `rich`.
- **Verdicts** reuse the builder's `coverage()` thresholds (`rich` ≥ depth, `thin` 10 to 19, `sparse` 1
  to 9, `empty` 0) and add `unknown` for no usable answer (timeout, budget, breaker, kill switch, Qloo
  error), never `empty` (D35). Confidence is `high` (rich or a definite empty), `medium` (short lists or
  an unchecked location) or `low` (unknown).
- **Empty 200 and short lists (AGENTS §10).** The client's strict guard is the param check. Below depth,
  one broader retry drops the domain's fixed narrowing params (dining's restaurant tag) and keeps the
  type and location. The verdict still comes from the standard query, the one the answer key uses; the
  retry's count goes in the note, so "no restaurants tagged" reads differently from "no places at all".
- **Ignored locations.** Qloo ignores what it can't use, so a `signal` domain is compared with its
  no-location list (cached after one call, shared by every location). An identical top list means the
  location was likely ignored: `unknown`, never `rich`. A filter domain's VERIFIED "unable to resolve to
  a valid locality" 400 is `empty` with high confidence and `location_resolved: false`. No new
  threshold: only identical lists count.
- **At most 3 calls per domain** (standard, broader, no-location), 7 for a rich location today, run
  concurrently under the 8 s request deadline and the caller's hourly and per-run budgets.
- **Provenance.** Top-level `served_from` is the weakest source among the standard queries (snapshot,
  then cache, then live), so cached data never reads as live; each call keeps its own label. Results
  hold counts and outcomes only, no names or ids. A 400 isn't cached, so an unresolvable location costs
  a call each time it's asked.
- **Synthetic label.** `Snapshot.synthetic` is read from the `-SYNTHETIC` name suffix that
  `export_snapshot.py --synthetic` writes; a coverage answer served from such a snapshot says
  `synthetic: true`.

**D47. Entity resolver (#29, 2026-10-21).**
- **Order.** Index exact or alias, then index fuzzy, then one budgeted `/search`. Normalization is
  `groundtruth.normalize` with every configured city's words, the same as the answer-key build, so
  forms compare equal. The resolver reads and extends `entity_index` only through `groundtruth`'s
  public functions (`find_entities`, `list_entities`, `entities_by_id`, `add_search_hits`).
- **Fuzzy, standard library.** A token-set ratio on `difflib` (no `rapidfuzz`), made stricter: a
  name inside a longer one counts only if it covers 60% of it ("cafe" is not "cafe coffee day"), and
  when each name has a word the other lacks, those words must be spellings of one another ("Studio 54
  Band" is not "Studio 84 Band"). Names under 4 characters are matched exactly or not at all.
- **Thresholds are provisional.** `FUZZY_THRESHOLD` 0.9, the 0.6 subset cover, the 0.75 word-variant
  line, the confidences and the 60 km city radius are named constants marked PROVISIONAL. **Retune them
  on V8 data from #21** (pending, live). No V8 number is assumed.
- **Never guess a tie.** `/search` gives no confidence, and same-name films are common (#15). Several
  candidates are separated only by the answer key, then the pool, then an exact (not alias) name, then,
  for places, being in the city; anything still tied is `unresolved` (`ambiguous`). An exact tie in the
  index is final and makes no search: a search can only add same-name hits, and it keeps re-runs
  offline. A tie-broken match keeps 0.9 of its confidence.
- **`/search`** goes through `QlooClient` with the caller's `RequestContext` (per-run budget, deadline,
  cache, breaker, call log) and sends only `query` (the normalized name), `types` and `take` 5. There is
  no VERIFIED city hint, so none is sent. `max_live_searches` caps the resolver's own live searches;
  past it, searches still read the cache. Hits go into `entity_index` (an existing row is kept: the
  build's insights row is richer), so a re-run resolves them with no `/search` call. Not when the
  answer key came from a snapshot (the hosted demo): live hits would sit beside snapshot rows and be
  served as `snapshot`, possibly real names in the synthetic store (rules 2 and 5). There the hits
  serve only the current run, and the client cache keeps a re-run free of live calls.
- **Search types are data.** `search_types` in `config/domains.yaml`, validated against the `/search`
  type list (rule 9). `film_tv` searches `movie` and `tv_show`: a TV pick is real even though the key
  holds films. Index lookups use the same types.
- **The city without coordinates in config.** For a domain localized by a filter (places), the city's
  centre is the median of the answer-key pool's coordinates (E10). A same-name place farther than the
  radius is not this one; if every candidate is far, the pick is `unresolved` (`elsewhere`): it exists,
  so it isn't a phantom.
- **Statuses.** `matched_in_key` (in the top `depth`), `matched_outside_key` (in the pool or the index
  or found by search, outside the key), `phantom_candidate` (a clean search, ok or empty, with no name
  match and no same-name entity anywhere), `unresolved` with a reason (`ambiguous`, `elsewhere`,
  `budget`, `deadline`, `offline`, `disabled`, `error`, `no_search`, `empty`). `unresolved` is never a
  phantom. No `/entities` calls (out of scope for #29).
- **Provenance.** An index match, or an `ambiguous` or `elsewhere` read from the index, carries the
  answer key's label (`cache`, or `snapshot` when the key came from one; `cache` with no key), never
  `live`; a search result keeps the client's label.
- **Match rate** is reported per domain and script (`script_of`, from Unicode names; kana folded into
  one), with unresolved reasons, and compared with a V8 ceiling when one is given; until #21 it reads
  `pending (#21)`. Reports hold counts only.
- **Left for the maintainer:** match rate against the V8 ceiling (needs #21) and the 50 hand-labelled
  precision check (local, gitignored).

**D48. Web UI shell: static files in the package, a CSP split and one label table (#47, 2026-10-21).**
- **Stack.** Plain HTML, CSS and JS, no build step, no npm, no CDN. The files live in
  `src/blindspot/web/static/` and ship as package data (`pyproject.toml`), so the Docker image, which
  copies only `src/` and `config/`, needs no change. Every asset is same-origin, so the UI works offline
  and in all three run modes.
- **Routes.** `GET /` (and `HEAD /`) serves the page; assets are under `/static/`. The #83 JSON moved to
  `GET /api` with a `ui` link added. The UI is served from the API's origin, so `ALLOWED_ORIGINS` stays
  empty (CORS closed).
- **CSP split (amends D19).** `/` and `/static/*` get `default-src 'self'; script-src 'self'; style-src
  'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none';
  form-action 'self'`, with no `'unsafe-inline'`: no inline `<script>`, `<style>`, `style=` or `on*=`.
  Every other path, `/api/*` and `/healthz` included, keeps `default-src 'none'`. The split is by exact
  path or prefix in `service/security.py`; the other headers (`nosniff`, `X-Frame-Options DENY`,
  `no-store`) are unchanged everywhere.
- **The data label.** `web/static/labels.json` is the one table; `labels.js` is a pure function over
  it (tests run it under Node). `synthetic: true` always reads **Synthetic sample data**; otherwise
  `served_from` picks **Live Qloo data** (`live` only), **Qloo data (stored)** (`cache`, `snapshot`),
  **Qloo off** (`disabled`) or **Qloo unavailable** plus `degraded_reason`. A view on a route where
  `disabled` means "no Qloo data involved" (the scenario routes, D45) passes `noQlooRoute` and reads
  **No Qloo data**, not the kill switch. A missing or unknown value reads **Data source unknown**,
  never live; so does a failure to load the table.
- **Untrusted text.** Data goes into the page with `textContent` only; a test fails on `innerHTML`,
  `insertAdjacentHTML`, `document.write` or `eval` in the scripts.
- **Cold start.** The page polls `/healthz` (8 s per try, backoff 2 to 15 s, six retries, then a
  "Try again" button) and shows "Waking up the demo…" once a check takes over 1.5 s or fails. The page
  itself is never blank: its static text renders before any script runs.
- **Mock data.** The shell renders `web/static/mock/scenarios.json`: synthetic (`synthetic: true`),
  invented ids and names, shaped exactly like the `/api/scenarios` envelope (a test compares its keys
  with the real route's). The views (#48 onward) swap it for the real routes.
- **Left for the maintainer:** desktop and 360 px screenshots in light and dark from a real device, the
  keyboard-only pass and the Lighthouse-style check.

**D49. Target models (#11, 2026-10-22).** The maintainer chose the lineup: a big closed model, a large
and a small open model, and a multilingual model from a non-US lab, all on free tiers behind
OpenAI-compatible endpoints. Each was checked live on 2026-10-22 with 16 requests (Groq 9, Gemini 7):
the model listing, a structured JSON answer, tool calling with a follow-up turn, and a Japanese prompt.
Groq no longer serves any Llama chat model (its listing has none), so the planned Llama 3.3 70B, Llama
3.1 8B and Qwen3 32B became their closest current equivalents: GPT-OSS 120B, GPT-OSS 20B and
Qwen3.8 27B. The ids below name the real models.

| | gemini-flash | gpt-oss-120b | gpt-oss-20b | qwen-27b (optional) |
|---|---|---|---|---|
| Display name | Gemini 3.8 Flash | GPT-OSS 120B | GPT-OSS 20B | Qwen3.8 27B |
| Role | Big closed model; audited target, precomputed in batches | Large open model; fix-loop agent (tool calling) (was llama-70b) | Small open model (was llama-8b) | Multilingual, non-US lab (was qwen-32b) |
| Provider | Google AI Studio, OpenAI-compatible | Groq | Groq | Groq |
| Base URL | `https://generativelanguage.googleapis.com/v1beta/openai/` | `https://api.groq.com/openai/v1` | same | same |
| Model id | `models/gemini-3.8-flash` as listed; sent as `gemini-3.8-flash` | `openai/gpt-oss-120b` | `openai/gpt-oss-20b` | `qwen/qwen3.8-27b` (a Groq **Preview** model) |
| Context / output limit | 1,048,576 / 65,536 (VERIFIED, native model API) | 131,072 / 65,536 (VERIFIED, `/models`) | 131,072 / 65,536 (VERIFIED) | 131,072 / 16,384 (VERIFIED) |
| Price | Free tier $0. Paid $0.75 in / $3.75 out per 1M until 2026-12-31, then $1.50 / $7.50 (UNVERIFIED, ai.google.dev pricing page) | Free tier $0. Paid $0.15 / $0.60 per 1M (UNVERIFIED, Groq models page) | Free tier $0. Paid $0.075 / $0.30 per 1M (UNVERIFIED) | Free tier $0. Paid $0.80 / $4.00 per 1M (UNVERIFIED) |
| Tool calling | Yes | Yes | Yes | Not tested |
| JSON mode (`json_object`) | Yes, but see the thinking quirk | Yes | Yes | Yes |
| `seed` | **No** (400 "Unknown name seed") | Yes | Yes | Yes |
| Free-tier limits | Not sent in headers; the rate-limits page lists none and points to AI Studio (UNVERIFIED) | 1,000 requests/day and 8,000 tokens/min (VERIFIED, `x-ratelimit-limit-*` headers); 30 requests/min and 200K tokens/day (UNVERIFIED, Groq docs) | Same as gpt-oss-120b | Same as gpt-oss-120b |
| Latency seen | 15 to 62 s per request | 0.9 to 1.5 s | 0.8 to 1.0 s | 0.7 s |
| `live_in_demo` | No, precomputed | No, precomputed | **Yes, capped** | No, precomputed |

**Live-demo policy.** Only gpt-oss-20b is called live from the hosted demo, behind a per-client cap of 3
audits per hour and a global cap of 300 target requests per day (well under Groq's 1,000/day), both
enforced in #38. Every other target is precomputed (#57). There is no paid model: the keys are free-tier
keys with no billing attached, so a judge can't create a bill.

- **Fix-loop agent.** gpt-oss-120b is the tool-calling agent for #42/#43: ~1 s per tool round trip and
  1,000 requests/day (VERIFIED). Gemini is too slow on the free tier (15 to 62 s) and its daily limit
  is unknown, so it stays an audited target, precomputed in batches (#57); check its daily request
  limit in AI Studio before precomputing. gpt-oss-20b is the tool-calling backup.

**Data.** Google's pricing page says free-tier content is used to improve Google's products (outside
the EU, UK and EEA). Our prompts hold only a persona's interests, an age band and a city (AGENTS
section 8), never personal data.

**Approval.** These are new external network services (AGENTS section 13), approved by the maintainer
for this lineup.

**Quirks, for #27 and #42.**
- **Set `max_tokens` to at least 1,000 for every target** (reasoning tokens count against it; a 400-token
  tool follow-up ended `length`). Try `reasoning_effort: low` on GPT-OSS too (UNVERIFIED on Groq here).
- **Gemini thinks by default, and thinking counts against `max_tokens`.** With 400, about 380 hidden
  thinking tokens left 16 visible ones: finish `length`, truncated JSON. The usage block doesn't report
  them (total minus prompt minus completion is the hidden part). `reasoning_effort: "low"` fixed it:
  valid JSON, 3 items, finish `stop`, no hidden tokens. Send it, and don't send `seed`.
- **Gemini is slow on the free tier** (15 to 62 s). Use a timeout of at least 90 s; it's precomputed only.
- **GPT-OSS models report `completion_tokens_details.reasoning_tokens`** (25 to 109 here), which also
  count against `max_tokens`. The content itself is clean JSON. One 400-token tool follow-up from
  gpt-oss-120b finished `length` (a long answer, not an error).
- **Tool calls** come back in the OpenAI shape on all three tested models: one call, with an id,
  arguments that parse as JSON with both fields, finish `tool_calls`, and no parallel calls. A
  follow-up with `role: tool` plus `tool_call_id` produced a final answer everywhere; Gemini needed no
  `name` on the tool message.
- **Non-Latin prompts:** a Japanese prompt to gpt-oss-20b round-tripped as UTF-8 on Windows. The
  reasons came back in Japanese, but all 3 shop names were in Latin script, so the resolver (#29) should
  expect romanized names for non-Latin cities.

Suggested `config/targets.yaml` for #27 (an example only; #27 creates the file, and values come from
env):

```yaml
targets:
  - id: gemini-flash
    display_name: Gemini 3.8 Flash
    base_url_env: GEMINI_BASE_URL   # default https://generativelanguage.googleapis.com/v1beta/openai/
    api_key_env: GEMINI_API_KEY
    model: gemini-3.8-flash
    live_in_demo: false
    tool_calling: true
    fix_loop_agent: false
    max_tokens: 1000
    seed: false                     # Gemini rejects it
    extra_body: {reasoning_effort: low}
    timeout_s: 90
  - id: gpt-oss-120b
    display_name: GPT-OSS 120B
    base_url_env: GROQ_BASE_URL     # default https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
    model: openai/gpt-oss-120b
    live_in_demo: false
    tool_calling: true
    fix_loop_agent: true            # #42/#43
    max_tokens: 1000
  - id: gpt-oss-20b
    display_name: GPT-OSS 20B
    base_url_env: GROQ_BASE_URL
    api_key_env: GROQ_API_KEY
    model: openai/gpt-oss-20b
    live_in_demo: true              # capped in #38
    tool_calling: true
    fix_loop_agent: false           # tool-calling backup
    max_tokens: 1000
  - id: qwen-27b                    # optional; a Groq Preview model
    display_name: Qwen3.8 27B
    base_url_env: GROQ_BASE_URL
    api_key_env: GROQ_API_KEY
    model: qwen/qwen3.8-27b
    live_in_demo: false
    tool_calling: false             # not tested
    fix_loop_agent: false
    max_tokens: 1000
```
