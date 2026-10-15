# CLAUDE.md: Blindspot project invariants (read before every task)

## Qloo API facts (from the official hackathon developer guide)
- Base URL is ONLY https://hackathon.api.qloo.com. Staging/production URLs return 401 with a hackathon key.
- Auth: `X-Api-Key` header. Never a query param, never a Bearer token.
- `/v2/insights` is a GET with a query string. Never POST/JSON body. Always append a path.
- Do NOT use `/recommendations` or `/recs` (legacy, unsupported).
- Insights `filter.type` values: urn:entity:artist, book, brand, destination, movie, person, place, podcast, tv_show, videogame. The guide says `video_game`, but the hackathon host REJECTS `urn:entity:video_game` (400) and ACCEPTS `urn:entity:videogame` for both insights and search (VERIFIED live 2026-10-15, docs/qloo_api_reality.md E3). Unsupported types return 400 `{"errors":[...]}` here (the guide says 403). Music = artist. Food/drink venues = place or brand.
- `/search` has its OWN supported `types` list, different from insights `filter.type`.
- Typical flow: /search (entity IDs) or /v2/tags (tag IDs) -> pass as signal/filter params to /v2/insights.
- `filter.tags` and `signal.interests.tags` need valid tag IDs from /v2/tags. `signal.demographics.audiences` needs valid audience URNs. Never free text.
- **INVALID/UNSUPPORTED PARAMETERS ARE SILENTLY IGNORED.** A 200 with an empty entities array can mean bad params, not missing data. Always validate params against the per-type allowlist and test that params change results.
- The hackathon environment may lack data for some entities/regions.
- Keys stay active through judging, then are deactivated. Demo must have a replay/snapshot mode.
- Quota limits can be raised via the Qloo Discord #api-help.

## Project rules
- Secrets: the key lives in `.env` only. Never print/log/commit it. Run scripts/scan_secrets.py before commits.
- Label every API claim VERIFIED / FAILED / UNVERIFIED. Trust real responses over docs or hints.
- No invented API behavior. Record decisions in docs/decisions.md.
- Judge-runnable: hosted demo, local with own key, local with no key (replay/snapshot). Judges do not have my key.
- Every result carries `served_from` (live|cache|snapshot|disabled|degraded). Never fake it.
- Cities and domains are data (config/), never hard-coded.
- Do not reproduce Qloo data in committed files beyond tiny fixtures; the snapshot is "needs terms check".
## Verified facts (Phase 1 audit, 2026-10-15)
- Quota: 10,000 calls/month, max 5 requests/second. Keep QLOO_RPS <= 4 for headroom.
- Unknown/bogus params are silently ignored (VERIFIED live). The client must catch them before sending.
- Affinity scores appear on insights seeded with an entity signal, but NOT on location-only place queries. Never assume scores exist; handle both.
- Every web request must have an overall deadline; never let a judge wait on Qloo retries.