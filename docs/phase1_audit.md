# Phase 1 independent audit

Date: 2026-10-15. The auditor relied only on what they observed. Every PASS cites a command and its result, or a file:line.
Scratch work lives in `data/audit/` (gitignored) and the session scratchpad. The repo was not modified, apart from this file.

## 1. Verdict: **NOT READY**

1. The core is sound and was observed working. That covers the client: cache, retries, budgets, breaker, kill switches and redaction. It also covers the service, with 50 concurrent requests, fuzzing, CORS and bounded memory, and both local run modes from a fresh clone. The real key works.
2. Gates 2 (E1–E10) and 3 (coverage matrix) have **no data**. Recon and the coverage matrix have never run.
3. The scripts that would produce that data are **broken**. `InsightsQuery` rejects plain-string list params, so every dining and bridge cell, plus parts of E4 and E5, would come back as `error` without a single call being made (F1).
4. Two mutations survived (M9 raw IP, M1c key on stdout). A third survived only because the 401 branch of the redaction test never runs (same root cause as F1).
5. The code breaks three CLAUDE.md rules (`videogame`, a POST probe, hard-coded cities). The hosted demo, the public repo and the Docker re-verification are still pending.

## 2. Gate table

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Real authenticated call | **PASS** | `data/audit/live.py`: search returned 200 (`live`, 3 results); insights returned 200 (`live`, 5 results); the budget counter went up by 1 per call |
| 2 | Reality doc answers E1–E10 | **FAIL** | `docs/qloo_api_reality.md` "Status … recon has NOT run yet"; every row is PENDING |
| 3 | Coverage matrix complete | **FAIL** | `docs/coverage_matrix.md` "Status: PENDING"; no `docs/coverage_grid.md`; the script is broken (F1) |
| 4 | Client features | **PASS** | `data/audit/behave_run.log`: checks B2 retries, B4 breaker, B5 budgets (client, daily+reserve, run, lifetime), B6 cache/TTL/bypass, B9 kill switches, B10 semaphore/RPS. Replay mode: S1, J1 |
| 5 | Offline tests, ruff, pip-audit | **PASS (with gaps)** | 3 runs, 2 of them randomized, each `50 passed, 1 skipped`; no key; httpx blocked via a dead-port proxy. `ruff check` 0 errors. `pip-audit` found no known vulns (runtime and dev locks). Gaps: F2, F3 |
| 6 | Fresh clone, no key, `make demo` | **PARTIAL** | J1: healthy in 46 s, `mode: replay`, unknown query returns `degraded` with guidance. **A snapshot-served search is impossible for a stranger**, because no snapshot ships (by design, for terms reasons) |
| 7 | Live with key, graceful when disabled | **PASS** | J2: fresh clone with `.env` gives `mode: live`; search returned `live` 5 → `cache` 5 → `qloo=false` returned `disabled` 0; no key in the server logs |
| 8 | 50 concurrent, cap respected | **PASS** | S2: 50 × 200 with varied XFF, live_used 0 (replay). B5: upstream count = budget counter = 6. `test_fifty_concurrent_requests_respect_cap` passes |
| 9 | Secret-free tree, history, image | **PARTIAL** | Tree and history: `scan_secrets.py` with the real key loaded found nothing; a planted key in a temp clone's history was detected. **Image: UNVERIFIED in this audit**, because the Docker daemon was down |
| 10 | JUDGES.md executed | **PARTIAL** | Python paths re-run (J1, J2). The `git clone <repo-url>` placeholder is not runnable. Docker paths UNVERIFIED (daemon down). macOS untested |
| 11 | Hosted skeleton | **PENDING-USER** | No URL anywhere in the repo |
| 12 | MIT license, gitignore | **PASS** | `LICENSE` contains "MIT License"; `git check-ignore` covers `.env`, `data/`, `*.sqlite`; `git log --all -- .env data '*.sqlite'` shows 0 commits |
| 13 | Live calls under budget | **PASS** | See §8: 5 authenticated calls by the audit; Phase 1 itself used 0 |

## 3. Findings (by severity)

### BLOCKER
None. No secret leaked and nothing crashes. F1 blocks gates 2 and 3, but it's a small fix.

### MAJOR

**F1. List params passed as strings are rejected, so the recon and coverage scripts produce fake errors.**
- Evidence: `InsightsQuery` rejects `filter.tags`, `signal.interests.entities` and `signal.demographics.audiences` when given a string (checked with an audit snippet: 4 REJECTED, list form ACCEPTED). The fields are typed `list[str] | None` in `src/blindspot/qloo/models.py`. The scripts pass strings at:
  - `scripts/coverage_matrix.py:34` (dining `filter.tags`)
  - `scripts/coverage_matrix.py:109` (bridge seed)
  - `scripts/recon.py:145` (E4 `filter.tags`)
  - `scripts/recon.py:180`, `:183` (E5 audiences)

  The CLI's `insights -p k=v` passes strings too. The same cause makes `tests/test_redaction.py`'s insights call stop at "invalid query", so its 401 branch never runs.
- Impact: every dining cell and every bridge cell, and E4/E5 partially, would report `error` without any request being sent. The city and domain recommendation would rest on zero data.
- Fix: add a `mode="before"` validator that wraps a `str` into `[str]`, or splits on commas, matching `normalize_params`. Fix the test fixture to send a list. **S**

**F2. Mutation M9 survives: a raw IP used as `client_id` isn't caught.**
- Evidence: §5. `test_client_ids_are_salted_hashes` tests the helper, not `routes._ctx`.
- Impact: the "never store raw IPs" rule could regress silently.
- Fix: a service test asserting that `/status` `your_client_id` starts with `c_` and doesn't contain the test-client IP. **S**

**F3. Mutation M1a survives (the 401 redaction path never runs), and so does M1c (key printed to stdout).**
- Evidence: §5. The debug run showed reason="invalid query…".
- Impact: a leak through an auth-error message or a `print` would pass CI.
- Fix: fix the test input (F1). Add `capsys` and `capfd` assertions that the key is absent from stdout and stderr. **S**

**F4. One user request can be held for about 45–62 s.**
- Evidence:
  - B3: 4 attempts × 1 s timeout took 9.7 s. With the default `QLOO_TIMEOUT_S=10`, that's about 45 s.
  - B13: `Retry-After=3` held a request 9.4 s. With 20 s, the cap the client still honours, that's about 62 s.
- Impact: a judge clicking a button waits up to a minute on a bad day.
- Fix: an overall deadline per web request (for example 8 s), and fewer attempts for web-originated contexts. **M**

**F5. The code breaks CLAUDE.md invariants.**
- `videogame` is accepted: `src/blindspot/qloo/models.py:21`, and sent by `scripts/recon.py:131`.
- A POST is sent to insights: `scripts/recon.py:233`.
- Cities and domains are hard-coded with no `config/`: `scripts/coverage_matrix.py:30-35`, `scripts/recon.py:22`, `:140`, `:146`, `:156`, and more.

Impact: this goes against the project rules, and Phase 2 can't swap cities without code edits. Fix: drop `videogame` from the insights types (keep it for `/search` if E1 shows it's valid there), remove the POST probe, add `config/cities.yaml` and `config/domains.yaml`. **M**

**F6. The call log's `live` rows don't equal the upstream requests.**
- Evidence: B12. One upstream request produced 2 `live` call-log rows (`budget_used_total=3`, `calllog_live=4`). Requests coalesced onto someone else's in-flight call are logged as `live`, and they bypass the follower's own per-client cap.
- Impact: "Truth Receipts" would show live calls that never hit Qloo, and quota reconciliation can't use the call log.
- Fix: log followers with a `coalesced=1` column, or label them `shared`. **S**

**F7. Nothing detects silently ignored parameters (expected NOTE per the brief, but now CONFIRMED live).**
- Evidence: L3. Adding `filter.bogus_audit_param` returned the same 5 ids, and `client_flagged=None`. The response's top-level `query` holds only `localities`, so it doesn't echo the applied params and can't be used to detect them.
- Fix (Phase 2): a per-type param allowlist from the entity-type guide, plus "params changed results" probes. **M**

**F8. The public repo URL is missing, so a stranger can't follow JUDGES.md.**
- Evidence: `docs/JUDGES.md:18`, `:29` say `git clone <repo-url>`.
- Fix: push to GitHub and replace the placeholder. **User, S**

### MINOR

- **F9.** Empty 200s are cached as successes for the 7-day TTL (B3 empty: `live` 0, then `cache`). A bad-param empty result gets pinned. Fix: skip caching empties, or cache them with a short TTL.
- **F10.** No response-size limit: a 5 MB payload was accepted and cached (B3 huge).
- **F11.** The breaker counts every 429 *attempt*. One rate-limited request (3 attempts) trips the breaker for everyone (B2: "pausing live calls for 29s" after a single request).
- **F12.** The scanner's pattern rule needs quoted values of 24+ characters. An unquoted `SOME_KEY=…` belonging to another key isn't flagged.
- **F13.** `ruff format --check` would reformat 23 files, and no type checker is configured.
- **F14.** `live_attempts` has no index on `client_id`/`run_id`, and the `calls` and `live_attempts` tables have no retention, so a public host grows its DB without limit (about 1 row per request).
- **F15.** The CLI never installs the log-redaction filter (`src/blindspot/qloo/cli.py`). Scripts do (`scripts/_common.py`).
- **F16.** `last_rate_headers` is a single shared attribute on the client, so concurrent requests can attach each other's rate headers.
- **F17.** In replay mode, expired (non-snapshot) cache rows aren't served. A no-key user loses data they already had once the TTL passes.
- **F18.** `render.yaml:2` cites "decisions.md D11", but the hosting decision is D22.
- **F19.** An empty `.dist/` directory keeps reappearing in the repo root and isn't gitignored.

### NOTE

- **N1.** Measured RPS was 2.45/s against `QLOO_RPS=2` (B10). That's measurement skew: the first arrival includes the connect time. It is well under Qloo's real **5/s** limit.
- **N2.** The `server: uvicorn` header is sent; no version is leaked.
- **N3.** SQLite calls are synchronous inside async handlers. That's fine at this scale.
- **N4.** The HTTP limiter clears all keys once it passes 50,000, which briefly resets everyone's window. Memory stays bounded: about 69 → 74 MB over 4,000 requests (S7).
- **N5 (audit deviation).** The live spot-checks wrote to the main `data/qloo_cache.sqlite` rather than `data/audit/`. That's why it now shows 4 live attempts.

## 4. Claims table

| # | Claim (source) | Audit verdict |
|---|---|---|
| 1 | Fresh clone with no key comes up healthy in about 78–80 s (report, JUDGES) | CONFIRMED (46 s here, Windows, warm pip cache) |
| 2 | 50 offline tests pass; the live test is opt-in (report) | CONFIRMED (×5 runs) |
| 3 | pip-audit is clean (report) | CONFIRMED |
| 4 | Ruff is clean (report) | CONFIRMED for lint; format not enforced (F13) |
| 5 | Budgets at four scopes, reserve honoured (decisions D8/D9) | CONFIRMED (B5) |
| 6 | Retries count against the budget; the cap is a hard bound (D8) | CONFIRMED (B5 lifetime incl. retries: upstream 2, used 2) |
| 7 | Breaker 3×429, 30 s, single probe (D11) | CONFIRMED, but F11 |
| 8 | `Retry-After` > 20 s degrades immediately (D12) | CONFIRMED by test; the hold below 20 s is long (F4) |
| 9 | Coalescing; followers labelled live (D10) | CONFIRMED; creates the reconciliation mismatch in F6 |
| 10 | Key never in logs, call log, cache, /status (D16) | CONFIRMED (B8, S1, J2, live run); untested paths in F3 |
| 11 | Docker image 206 MB, non-root, about 3.2 s start (report) | UNVERIFIED (daemon down) |
| 12 | `docker compose up --build` works (report) | UNVERIFIED (daemon down) |
| 13 | Planted key detected in tree, history and image (report) | CONFIRMED for history (temp clone); image UNVERIFIED |
| 14 | `XFF` trusted only with hops ≥ 1 (D17) | CONFIRMED (S3: 30×200 then 5×429 despite forged XFF) |
| 15 | No-key 401 envelope `{"error":{reason,message,code}}` (reality doc, VERIFIED) | CONFIRMED (L4) |
| 16 | Invalid key returns 401 "Unauthorized" (VERIFIED) | CONFIRMED; reason is "API keys mismatch" |
| 17 | 401s carry no rate-limit headers (VERIFIED) | CONFIRMED |
| 18 | Host in AWS us-east-1 (VERIFIED by DNS) | Not re-tested (no quota cost; DNS only) |
| 19 | "Quota numbers unpublished" (reality doc, report open question 2) | **CONTRADICTED.** Headers show `x-month-ratelimit-limit: 10000`, `x-second-ratelimit-limit: 5` |
| 20 | `/search` returns `{success, duration, results}` (reality E1, synthetic fixture) | **CONTRADICTED.** Top level is `["results"]` only |
| 21 | Affinity in per-result `query.affinity` (E3, UNVERIFIED) | CONFIRMED for an entity-signal insights call. **Absent** for a location-only place query (`query` = `{distance, measurements}`) |
| 22 | Popularity is a 0–1 field on entities | CONFIRMED present (search and insights) |
| 23 | Invalid params silently ignored (HG) | CONFIRMED (L3) |
| 24 | Places return coords, address, external ids (E10) | CONFIRMED: `location{lat,lon,geohash}`, `external.google_place`, `properties.address/geocode` |
| 25 | Phase 1 live calls used: 0/600 (report) | CONFIRMED for Phase 1. Main DB now shows 4 = this audit's calls (N5) |
| 26 | Hosted deploy pending (report) | CONFIRMED |
| 27 | Snapshot not committed; the fallback message is clear (D5) | CONFIRMED (J1 reason text) |

## 5. Mutation results (temp clones, `data/audit/venv`, key unset)

| ID | Mutation | Result |
|---|---|---|
| M1a | Key in the auth-error result message | **SURVIVED** (the test's 401 branch never runs; F1/F3) |
| M1b | Key in a log line | Survived, but the leak is *prevented*: the redaction filter masked it (`caplog` contains no key). Not a gap |
| M1c | Key printed to stdout | **SURVIVED** (F3) |
| M2 | Kill switch as a global flag | KILLED (`test_per_request_kill_switch_is_isolated`) |
| M3 | Ignore `Retry-After` | KILLED (`test_retry_on_429_honours_retry_after`) |
| M4 | Order-dependent cache key | KILLED (`test_key_stable_under_param_reordering`) |
| M5 | No per-client hourly cap | KILLED (`test_client_cap_blocks_one_client_not_another`) |
| M6 | Key as a query param | KILLED (`test_key_never_leaks`) |
| M7 | `served_from` always `live` | KILLED (`test_client_cap_…` and others) |
| M8 | Breaker never recovers | KILLED (`test_breaker_opens_on_repeated_429_and_recovers`) |
| M9 | Raw IP as `client_id` | **SURVIVED** (F2) |

## 6. Judge-simulation log

| Step | Command | Result |
|---|---|---|
| J0 | `git clone <repo-url> blindspot` | **Not runnable for a stranger**: placeholder URL (F8). Audit cloned the local path |
| J0 | `make demo` (Windows) | `make` isn't on PATH. JUDGES documents `python scripts/dev.py demo`, which was used |
| J1 | `python scripts/dev.py demo`, no `.env`, `QLOO_API_KEY` unset | Healthy in **46 s** from clone. `/healthz` → `{"ok":true,"mode":"replay"}`. Search → `degraded`: "offline replay mode … Use the hosted demo, or add your own QLOO_API_KEY" |
| J2 | Copy `.env` into the clone, `python scripts/dev.py demo` | Healthy in 2 s; `mode: live`, `key_configured: true`; Burna Boy search → `live` 5, then `cache` 5, then `qloo=false` → `disabled` 0; no key in the server log |
| J3 | Docker build, history, layer scan, non-root, size | **UNVERIFIED**: the Docker daemon wasn't running during the audit |
| J4 | Platform | The no-make path is documented; the PowerShell `$env:PORT` form is documented; no absolute paths found |
| J5 | Hosted `/healthz` | PENDING: no URL |

Sticking points: the placeholder repo URL; `make` missing on Windows (documented); no-key mode returns almost only `degraded` (expected).

## 7. Phase 2 readiness checklist

| Item | State |
|---|---|
| Stable client API taking `RequestContext` | Yes (`client.request/insights/search_entities`) |
| Call log queryable by `run_id` | Yes (`CallLog.for_run`; B8). Fix F6 first |
| Cache and snapshot seeding | Yes (B6, B11) |
| Resumable or checkpoint-friendly calls | Partly: identical queries are cached, so reruns are free; there's no explicit checkpoint |
| Location handled as data | Partly: it's a param value. Cities are hard-coded in scripts (F5) |
| `config/cities.yaml` | **Missing** |
| Recorded real param names for location, audience, tags, popularity, trends, entity signals | **No.** Only `filter.location.query` and `signal.interests.entities` have been observed working live |
| Where affinity and popularity appear | Partly: `query.affinity` on entity-signal insights; absent on location-only place queries (risk for scoring); `popularity` on entities |
| Quota and limits | **Now known:** 10,000 calls/month, 5 req/s (headers). `take` max not yet probed |
| Detection of silently ignored params | **None** (F7, expected) |

What Phase 2 must do first: F1, then run recon and the coverage matrix, F5 (config/), F7, F4, F6.

## 8. Live calls used by this audit

- **5 authenticated calls:**
  - 4 in `data/audit/live.py`: search, insights, Berlin baseline, Berlin with bogus param. The repeat came from cache.
  - 1 in the judge-sim clone (J2).
- **2 unauthenticated probes** (no key and a bad key; 401, not billed to the key).
- Remaining quota per the last response header: about 9,995 of 10,000 this month (the header read 9996 before the J2 call).
- Reconciliation:
  - Phase 1 report: 0 calls.
  - Main DB: `live_attempts` 4, call-log `live` rows 4, cache entries 4. All four are this audit's calls.
  - Clone DB: `live_attempts` 1.
  - The Phase 1 numbers are consistent.

## 9. Ordered fix list

| # | Fix | Severity | Effort | Who |
|---|---|---|---|---|
| 1 | F1 string-to-list coercion; fix the redaction test input | MAJOR | S | Dev |
| 2 | F2/F3 tests for raw IP and stdout/stderr leaks | MAJOR | S | Dev |
| 3 | F5 drop `videogame` from insights types, drop the POST probe, add `config/` for cities and domains | MAJOR | M | Dev |
| 4 | Run smoke → recon → coverage matrix (about 221 calls of the 10,000/month); update the reality doc and the fixtures' shape (claims 19–21) | gates 2/3 | M | Dev |
| 5 | F4 per-request deadline for web calls | MAJOR | M | Dev |
| 6 | F6 mark coalesced call-log rows | MAJOR | S | Dev |
| 7 | F7 per-type param allowlist and a results-changed check | MAJOR (Phase 2) | M | Dev |
| 8 | F8 push a public repo; replace `<repo-url>` | MAJOR | S | **You** |
| 9 | Deploy to Render: set `QLOO_API_KEY` in the dashboard; then verify `/healthz` from another network | gate 11 | S | **You** |
| 10 | Restart Docker Desktop and re-run the image scan (`make docker-scan`) | gate 9 | S | **You** (start Docker) |
| 11 | Decide the snapshot-terms question (Qloo Discord) | open | S | **You** |

---

## Fix pass (2026-10-15)

The original audit text above is unchanged. Commits:
- `130ded8`: tests F2, F3
- `8b4a7cb`: main fix batch
- `831071e`: F13
- `10d5ce8`: F5 config
- `f2eb3e1`: VERIFIED-facts follow-up
- `6c49485`: deadline-test timing
- the final docs commit

### Finding status

| ID | Status | What changed / evidence | Commit |
|---|---|---|---|
| F1 | FIXED | List params accept `a,b` or lists. Entity, tag and audience params must be ids (UUID, `urn:tag:`, `urn:audience:`), else `outcome=resolution_failed` and no call is sent. Names resolve via `/search` and `/v2/tags` (`insights(resolve=…)`). New `outcome` field on results and call-log rows. Tests: `test_free_text_never_sent_as_id`, `test_names_resolve_to_ids`, `test_unresolvable_name_makes_zero_insights_calls`. The redaction test's 401 branch now really runs (`assert r3.status == 401`) | 8b4a7cb, 130ded8 |
| F2 | FIXED | `test_raw_ip_never_stored` checks a known IP is absent from cache, calls, live_attempts, `/status` and logs. M9 is now killed | 130ded8 |
| F3 | FIXED | `test_key_never_on_stdout_stderr` runs the CLI (4 commands), `scripts/smoke.py`, service startup and error paths under `capfd`/`caplog` with a hostile upstream echoing the key. M1a, M1c and M1d are now killed | 130ded8 |
| F4 | FIXED | Cooperative per-request deadline (`REQUEST_DEADLINE_S=8`, web only); backoff and `Retry-After` never sleep past it; `wait_for` fence; followers survive a cancelled leader. Tests: slow server 20 s with a 1 s deadline returns in < 1.5 s and the budget doesn't grow afterwards; `Retry-After: 5` with a 2 s deadline degrades in < 1 s; 50 concurrent requests against `429 Retry-After: 30` all return, p95 < 8.5 s; the breaker answers in < 0.2 s | 8b4a7cb |
| F5 | FIXED (2 of 3) / **DISPUTED** (1 of 3) | The POST probe is removed (`test_only_get_and_no_legacy_endpoints`). Cities and domains moved to `config/*.yaml` with validation, and `test_no_configured_city_in_src` guards it. **The `videogame` part is disputed with live evidence:** the hackathon host returns 200 for `filter.type=urn:entity:videogame` and 400 "not supported for filter.type" for `urn:entity:video_game`; `/search` behaves the same (recon E1/E3 plus a probe). The enums follow the real API; CLAUDE.md is corrected (D29) | 10d5ce8, f2eb3e1 |
| F6 | FIXED | `coalesced` flag on results and call-log rows; live calls are reported from `live_attempts` (real upstream requests) by purpose. `test_coalesced_rows_are_marked` | 8b4a7cb |
| F7 | FIXED | `qloo/params.py`: per-type allowlist of params *proven effective* by recon; strict by default (`QlooParamError` before any call); `strict=False` logs `unverified_params`; `assert_param_effective()` | 8b4a7cb, f2eb3e1 |
| F8 | DEFERRED (user) | `<repo-url>` stays a clearly marked placeholder; push steps are in `docs/NEEDS_FROM_USER.md` | n/a |
| F9 | FIXED | Empty 200s expire after 1 h (`test_empty_results_expire_after_an_hour`) | 8b4a7cb |
| F10 | FIXED | Bodies over 4 MB are refused (`test_huge_body_rejected`) | 8b4a7cb |
| F11 | FIXED | One rate-limited request counts once toward the breaker (`test_one_rate_limited_request_does_not_trip_breaker`) | 8b4a7cb |
| F12 | FIXED | Scanner pattern for unquoted `*KEY|TOKEN|SECRET|PASSWORD*=value` | 8b4a7cb |
| F13 | FIXED | Repo formatted; `make check` runs `ruff format --check` | 831071e |
| F14 | FIXED | Indexes on `live_attempts(client_id, ts)`, `(run_id)` and `calls(purpose)`; call-log retention 30 days (`live_attempts` kept for the lifetime cap) | 8b4a7cb |
| F15 | FIXED | The CLI installs log redaction (covered by the F3 test) | 8b4a7cb |
| F16 | FIXED | Rate headers are returned per response; the shared attribute is removed | 8b4a7cb |
| F17 | FIXED | Replay mode serves stale cache rows (`test_replay_serves_stale_cache`) | 8b4a7cb |
| F18 | FIXED | `render.yaml` cites D22 | 8b4a7cb |
| F19 | FIXED | `.dist/` gitignored | 8b4a7cb |
| N1 | ACTED | `QLOO_RPS=4`, token-bucket capacity 1 (no burst above 5/s) | 8b4a7cb |
| N2 | ACTED | `--no-server-header` | 8b4a7cb |
| N3 | DEFERRED | Synchronous SQLite in async handlers: sub-millisecond queries on one instance; revisit if profiling shows it | n/a |
| N4 | DEFERRED | The limiter's 50k-key clear: memory stays bounded; acceptable for one free instance | n/a |
| N5 | ACTED | `purpose` column; the audit's rows were re-tagged `audit`; cached responses kept | 8b4a7cb |

Found during the fix pass and fixed:
- The second error envelope `{"errors":[…]}` wasn't parsed.
- `tag_types` and `audience_types` containers weren't extracted, which made lookups look empty.
- Audience URN `id` vs UUID `entity_id`.
- Scripts crashed printing non-Latin names on Windows consoles.

### Mutation results (re-run in temp clones, key unset, outbound network blocked)

| ID | Mutation | Result |
|---|---|---|
| M1a | Key in the auth-error message | KILLED (`test_leaks`) |
| M1b | Key in a log line | Survives: the redaction filter masks it, so the leak is prevented (M1d shows the filter is what stops it) |
| M1c | Key printed to stdout | KILLED (`test_leaks`) |
| M1d | Redaction removed (`redact` returns its input) | KILLED (`test_leaks`) |
| M2 | Kill switch as a global flag | KILLED |
| M3 | Ignore `Retry-After` | KILLED |
| M4 | Order-dependent cache key | KILLED |
| M5 | No per-client hourly cap | KILLED |
| M6 | Key as a query param | KILLED |
| M7 | `served_from` always `live` | KILLED |
| M8 | Breaker never recovers | KILLED |
| M9 | Raw IP as `client_id` | KILLED (`test_raw_ip_never_stored`) |

### Gates after the fix pass

Gates 1–8, 12 and 13 now PASS. Gate 9 (Docker image scan) and gate 10 (Docker commands) are PENDING-USER: Docker Desktop is off. Gate 11 (hosted) is PENDING-USER. Details are in [phase1_report.md](phase1_report.md).
