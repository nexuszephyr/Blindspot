# Qloo API reality (Phase 1)

**Status on 2026-10-13: recon has NOT run yet.** There is no `QLOO_API_KEY` on this machine, so
every live experiment (E1–E10) is pending. What follows is the documented behavior plus the few
things checked against the real host without a key. After recon runs, each row gets updated to
VERIFIED or FAILED.

## Labels and sources

| Label | Meaning |
|---|---|
| **VERIFIED** | Seen in a recorded real response from `hackathon.api.qloo.com` |
| **FAILED** | Tried against the real API; it did not behave as documented |
| **UNVERIFIED (doc)** | Stated in official Qloo docs; not yet probed |
| **UNVERIFIED (3p)** | Only from third-party packages (`@devma/qloo` LLM.txt, `@buildwithlayer/qloo-mcp` tools.json) |

Sources:
- [HG] Hackathon developer guide: https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide
- [PR] Parameter reference: https://docs.qloo.com/reference/parameters
- [EP] Entity-type parameter guide: https://docs.qloo.com/reference/available-parameters-by-entity-type
- [DD] Insights API deep dive: https://docs.qloo.com/reference/insights-api-deep-dive
- [SR] `/search` reference: https://docs.qloo.com/reference/get-search
- [LLM] `@devma/qloo@1.0.1` `LLM.txt`
- [MCP] `@buildwithlayer/qloo-mcp@0.0.4` `dist/tools.json` (an OpenAPI-derived parameter list; it points at `staging.api.qloo.com`/`api.qloo.com`, which the guide says to avoid)
- [PROBE] My own requests on 2026-10-13. These were sent with no key or a fake key, so none came out of the hackathon quota.

## Basics

| Claim | Label | Source |
|---|---|---|
| Base URL is `https://hackathon.api.qloo.com`. Staging and production hosts return 401 for hackathon keys | host reachable: **VERIFIED**; 401 on other hosts: UNVERIFIED (doc) | [PROBE], [HG] |
| Auth goes in the `X-Api-Key` header. Query-param and Bearer auth are not accepted | header name: UNVERIFIED (doc) | [HG] |
| No key returns `401 {"error":{"reason":"No API key found in request","message":"Unauthorized","code":401}}` | **VERIFIED** | [PROBE] |
| An invalid key returns 401 with message `Unauthorized` (about 1.2 s cold, about 0.76 s warm, from the dev machine) | **VERIFIED** | [PROBE] |
| The host resolves to AWS us-east-1 address ranges (52.201.x, 35.169.x, 50.16.x). TCP connect from the dev machine takes about 250 ms | **VERIFIED** (DNS + timing); "us-east-1" is inferred from the IP ranges | [PROBE] |
| 401 responses carry no rate-limit headers | **VERIFIED** (401 only) | [PROBE] |
| `/v2/insights` is GET with query-string params. POST with a JSON body fails | UNVERIFIED (doc) | [HG] |
| `/recommendations` and `/recs` are legacy and unsupported. The bare base URL returns 404 | UNVERIFIED (doc) | [HG] |
| Invalid parameters are **silently ignored**, and `200` with empty `entities` often means a bad param | UNVERIFIED (doc), recon E9 | [HG] |
| Hackathon keys have rate and monthly quotas (numbers unpublished). Limits can be raised via Discord #api-help | UNVERIFIED (doc) | [HG] |
| Private server-side caching is allowed with no time limit. **"Don't store Qloo response data in a public repository."** | doc (terms) | [HG] |
| Keys are deactivated after the hackathon closes; they stay active through judging | UNVERIFIED (doc) | [HG] |

## Endpoints

| Endpoint | Purpose | Label |
|---|---|---|
| `GET /search` (`query`, `types`, `take`, `page`, `filter.location`, `filter.radius` in miles, `filter.tags`, `filter.popularity`, `filter.exists`, `sort_by=match\|distance\|popularity`, `bias.location*`) | Name → entity ids | UNVERIFIED (doc) [SR] |
| `GET /entities?entity_ids=` (plus `external.<source>.ids` lookups: imdb, spotify, musicbrainz, wikidata, resy, michelin, …) | Ids → entities | UNVERIFIED (3p) [MCP] |
| `GET /v2/insights` | Recommendations / affinity | UNVERIFIED (doc) [HG] |
| `GET /v2/tags` (`filter.query`, `filter.tag.types`, `filter.parents.types`, `feature.typo_tolerance`) | Tag search | UNVERIFIED (doc/3p) [HG][MCP] |
| `GET /v2/tags/types` | Tag types per entity type | UNVERIFIED (3p) [MCP] |
| `GET /v2/audiences` (`filter.parents.types`, `filter.audience.types`, `filter.results.audiences`) and `GET /v2/audiences/types` | Audience ids for `signal.demographics.audiences` | UNVERIFIED (doc) [DD][MCP] |
| `GET /v2/insights/compare`, `GET /analysis`, `GET /trends/entity`, `GET /trends/category` | Comparison, taste analysis, trends | UNVERIFIED (3p) [MCP]. Not in the client's allow-list yet |

## Entity types (`filter.type`)

`urn:entity:artist|book|brand|destination|movie|person|place|podcast|tv_show|video_game` per [HG]. **Discrepancy:**
[MCP] and [SR] spell the last one `urn:entity:videogame`. Recon E3 tries both. [HG] says a 403
"You do not have permission to access urn:entity:…" means the type is unsupported. Use
`artist`, not `music`; use `place`/`brand`, not `food_and_drink`. `urn:heatmap` exists [EP];
`urn:demographics` appears in use-case pages (UNVERIFIED). `/search` also accepts `urn:entity:locality` [SR].

## E1–E10

Every row starts as **PENDING (needs key)**. The "Documented answer" column is what we expect.
`scripts/recon.py` runs the probe that settles it.

| # | Question | Documented answer (label) | Recon probe |
|---|---|---|---|
| E1 | Search fields, ambiguity, non-English names, `take` limit | Response is `{success, duration, results:[…]}`; item fields (`entity_id`, `name`, `types`, `properties`, `popularity`, `location`, `external`) are referenced but not shown [SR]. `take` max 199 per schema `exclusiveMaximum: 200` [SR]; the client clamps at 50 | Typed searches for 6 names, untyped "Mercury", 宮崎駿, accents/typos, locality "São Paulo", take 1/50/100/250 |
| E2 | Tag ids and families; place categories | Tag ids look like `urn:tag:genre:place:restaurant:italian` / `urn:tag:genre:music:pop` (UNVERIFIED (3p) [LLM]). Tags must be valid ids, or you get "at least one valid signal or filter is required" (UNVERIFIED (doc) [HG]) | `/v2/tags/types` (all and place), tag search for restaurant/cafe/music venue/afrobeats/bollywood/anime/street food, typo tolerance, tag-type filter |
| E3 | Insights per type; where scores live; ordering | Response is `{success, results:{entities:[…], duration}}` [DD][LLM]. Popularity is a 0–1 percentile [PR]. Affinity is in `query.affinity`/affinity ranks per result [DD]; exact shape undocumented. Default ordering is affinity (`sort_by=affinity\|distance` for places) [MCP] | Seeded insights for artist/movie/tv/brand plus cross-domain; person/book/podcast/video_game vs videogame/destination; the client records `has_affinity`, `has_popularity`, `affinity_sorted` |
| E4 | Location: signal vs filter; name formats; city coverage | `filter.location.query` = fuzzy locality name that **restricts** results (place/destination); `signal.location.query` = locality that **influences** affinity [DD][MCP]. Unmatched names return **400** [DD]. Radius default 15 km (max 800 km) [DD]. [EP] does **not** list `signal.location*` for artist/movie/brand, so location may not move non-place domains (key risk) | 6 cities × filter vs signal; Bengaluru/Bangalore/"Bengaluru, India"/Sao Paulo/NYC/Osaka; both together; an unmatched name; artist/movie/brand with `signal.location.query` across 3 cities (do results differ?) |
| E5 | Audiences and demographics | `signal.demographics.age` ∈ `35_and_younger\|36_to_55\|55_and_older`; `signal.demographics.gender` ∈ `male\|female` [MCP]; audiences are `urn:audience:*` ids from `/v2/audiences` [DD]. Demographics alone fail; they need a primary signal (UNVERIFIED (3p) [LLM]) | Audience types and lists; demographics-only (expect error); age/gender modifiers on an artist seed; an audience id on artist and place; `urn:demographics` |
| E6 | Popularity and trends effects | `filter.popularity.min/max` = 0–1 percentile [PR]; `bias.trends` ∈ off…very_high, default medium [DD] ([MCP] lists only off/low/medium/high) | Baseline vs popularity.max=0.5 / min=0.9 / trends off / trends high (artist), trends off vs high (NY places). Compare overlap |
| E7 | Score a fixed candidate list | `filter.results.entities` = "comma-separated entity IDs … often used to assess the affinity of an entity towards input" [PR][MCP]. The candidate limit is undocumented; `take` max 50 [DD] | Take a 50-entity pool, then score 5/25/all candidates against `signal.location.query=Lagos`, and 10 against an entity signal |
| E8 | Explainability | `feature.explainability=true` adds per-result `query.explainability` (input contributions normalized 0–1) and top-level aggregates for top-3/5/10/all; on failure, `query.explainability.warning` [DD] | Explainability on artist and place queries |
| E9 | Limits, pagination, error shapes | `take` default 20 / max 50 for insights [DD] (the MCP spec says default 5); `page` 1–100 recommended, `offset` also available [DD]. Error envelope: **VERIFIED** for 401 (see Basics). 429 is documented with `code/reason/message` but the example is copy-pasted, and **no rate-limit headers are documented** [SR] | No key, bad key, unknown path, bare `/`, POST insights, missing/invalid `filter.type`, take=0/200, invalid tag; page 1 vs 2 vs offset 5. Deliberate 429s are **not** triggered (quota) |
| E10 | Place cross-refs (coords, address, external ids) | Places have geocode fields (`properties.geocode.{name,admin1_region,admin2_region,country_code}`) [PR]; external Resy/Michelin/Tablet/Tripadvisor data appear as filters [PR], so some `external.*` data is likely present; lat/lon via WKT/geohash support [PR] | Tokyo places → `GET /entities` for 3 ids, dump `properties` and `external` keys; place search with a location bias |

## Quirks worth knowing before Phase 2 (all from docs)

- Invalid params are ignored silently. An empty 200 is ambiguous ("no data" vs "bad param"), so Phase 2 needs a sanity check for empty results.
- The API can't filter response fields, and some responses carry verbose fields (`akas`). The snapshot minimizer drops them.
- `filter.geocode.name` is exact-match and meant for small towns only [MCP]; for cities use `filter.location.query`.
- Weighted signals and `*.query` name-resolution parameters work via POST only [LLM][PR]. That contradicts "POST fails" in [HG]; needs a probe if Phase 2 wants weights.
