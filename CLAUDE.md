# CLAUDE.md: Blindspot project invariants (read before every task)

## Qloo API facts (from the official hackathon developer guide)
- Base URL is ONLY https://hackathon.api.qloo.com. Staging/production URLs return 401 with a hackathon key.
- Auth: `X-Api-Key` header. Never a query param, never a Bearer token.
- `/v2/insights` is a GET with a query string. Never POST/JSON body. Always append a path.
- Do NOT use `/recommendations` or `/recs` (legacy, unsupported).
- Insights `filter.type` values: urn:entity:artist, book, brand, destination, movie, person, place, podcast, tv_show, video_game. (It is `video_game`, not `videogame`.) 403 = unsupported type. Music = artist. Food/drink venues = place or brand.
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
