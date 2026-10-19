# Qloo API reality

This is what the hackathon API actually does: endpoints, parameters, scores, quirks and limits.
Recon ran on 2026-10-15 (`scripts/recon.py`, 194 live attempts, purpose `recon`). Raw rows are in
`data/recon/*.jsonl` and raw bodies in `data/raw/`, both gitignored. This doc records field
names, outcomes and counts only, never Qloo names, ids or scores.

| Label | Meaning |
|---|---|
| **VERIFIED** | Seen in a recorded live response from `hackathon.api.qloo.com` |
| **FAILED** | Tried live; it did not behave as documented |
| **UNVERIFIED** | From documentation only; not exercised |

Sources:
- [HG] the hackathon developer guide
- [PR] the parameter reference
- [EP] the entity-type parameter guide
- [DD] the Insights deep dive
- [SR] the `/search` reference (all at docs.qloo.com)
- [R-En] a recon experiment row; [AUD] the Phase 1 audit live checks

## Basics and limits

| Claim | Label | Source |
|---|---|---|
| Base URL `https://hackathon.api.qloo.com`; key in the `X-Api-Key` header | VERIFIED | [AUD], R-E1..E11 |
| No key returns `401 {"error":{"reason":"No API key found in request","message":"Unauthorized","code":401}}` | VERIFIED | R-E9 |
| Bad key returns `401 …"reason":"API keys mismatch"…` | VERIFIED | R-E9 |
| Unknown path returns `404 {"error":{"reason":"Not Found","message":"The requested resource could not be found.","code":404}}` | VERIFIED | R-E9 |
| Bare base URL `GET /` returns **200** with API info (`title, version, description, documentation, contact, deployment_date`). The guide says 404 | FAILED (doc) | R-E9 |
| Insights validation errors use a **second envelope**: `400 {"errors":[{"message":…,"path":…}]}`. The client parses both | VERIFIED | R-E3, E4, E9 |
| Unsupported `filter.type` returns **400** `{"errors":[{"message":"The value … is not supported for filter.type"}]}`. The guide says 403 | FAILED (doc) | R-E3, E9 |
| Quota: `x-month-ratelimit-limit: 10000`, `x-second-ratelimit-limit: 5` (plus `-remaining` and `-reset`) on 200 responses; none on 401 | VERIFIED | [AUD], R-E9 |
| Insights `take` must be 1–50 (`take must be an integer value between 1 and 50`) | VERIFIED | R-E9 |
| `/search` `take` max is **100** (250 → `take: must be <= 100`) | VERIFIED | R-E1 |
| Pagination: `page=2` returns exactly the same ids as `offset=5` (`take=5`), with no overlap with page 1 | VERIFIED | R-E9 |
| Invalid params are silently ignored: a bogus param returned identical results | VERIFIED | [AUD] L3 |
| A free-text value in `signal.interests.tags` made the host **hang** (ReadTimeout at 15 s) instead of erroring. The client now refuses non-id values locally | VERIFIED | R-E9 |
| Latency: search 0.3–1.2 s; insights entity-seeded 0.3–4.7 s; location-filtered places about 0.7–4.4 s; `signal.location.query` + `filter.tags` on places 28 s or **timed out** (4 of 6 cities at 4×10 s) | VERIFIED | R-E4, coverage |

## Entity types

| Claim | Label | Source |
|---|---|---|
| Insights accepts `urn:entity:videogame` (200) and **rejects `urn:entity:video_game`** (400). The guide and CLAUDE.md said the opposite; CLAUDE.md is now corrected | VERIFIED | R-E3 + probe |
| `/search` accepts `types=urn:entity:videogame` and rejects `video_game` (400 lists the allowed values) | VERIFIED | R-E1 |
| Insights with an entity seed works for `artist, movie, tv_show, brand, person, book, podcast, destination, videogame` | VERIFIED | R-E3 |
| Place insights work with `filter.location.query` alone | VERIFIED | R-E4 |

## E1. Search

- **Response shape** (VERIFIED): `{"results":[…]}` only. There is no `success` or `duration`, so the synthetic fixture was corrected.
- **Item fields** (VERIFIED): `entity_id, name, types[], popularity, properties, tags, disambiguation`. Some items also have `location`.
- **Typed searches** (VERIFIED): returned results for six seeds, including a Nigerian and an Indian artist.
- **Name handling** (VERIFIED): the ambiguous untyped `Mercury` returned 10 mixed results. Japanese-script `宮崎駿` returned 5. Both an accented name and a one-letter typo returned 3.
- **Localities** (VERIFIED): `types=urn:entity:locality` returned results for all six configured cities.
- **Limits** (VERIFIED): `take` 1/50/100 worked; 250 was rejected.
- **Entity ids** (VERIFIED): uppercase UUIDs.

## E2. Tags

| Claim | Label |
|---|---|
| `GET /v2/tags?filter.query=` → `{"success","duration","results":{"tags":[…]}}`; items have `id, name, type, parents, popularity, properties, tags` | VERIFIED |
| Tag ids look like `urn:tag:<family>:<namespace>:<value>`, e.g. families `genre:place`, `genre:music`, `genre:media`, `cuisine:qloo`, `category:place`, `amenity:place`, `dining_option:qloo`, `keyword:qloo` | VERIFIED |
| Place categories: "restaurant", "cafe", "music venue" and "street food" each returned 10 tags; "afrobeats" 4; "bollywood" and "anime" 10 each | VERIFIED |
| The configured dining tag `urn:tag:genre:place:restaurant` exists (`filter.results.tags` lookup) and narrows place results (Jaccard 0–0.11 against the location-only results) | VERIFIED |
| `feature.typo_tolerance=true` with "itallian" returned 5 tags | VERIFIED |
| `/v2/tags/types` → `{"results":{"tag_types":[{"type","parents":[{"type"}]}]}}` (the client originally missed this container; fixed) | VERIFIED |

## E3. Insights by type, scores and order

| Claim | Label |
|---|---|
| Response: `{"success","results":{"entities":[…]}}`, plus a top-level `"query":{"localities":…}` when a location is used | VERIFIED |
| Entity fields: `entity_id, name, type, subtype, popularity, properties, tags, external, disambiguation, query`; places add `location{lat,lon[,geohash]}` and `references` | VERIFIED |
| **Affinity is per result in `query.affinity`.** It's present for every entity-seeded query (all types in E3) and results are sorted by affinity, descending | VERIFIED |
| `query.measurements` (`audience_growth`) appears alongside affinity; place queries with a location add `query.distance` | VERIFIED |
| `popularity` is present on every result (search and insights) | VERIFIED |
| Cross-domain seeding works (artist → movie, brand → artist) | VERIFIED |

## E4. Location

| Claim | Label |
|---|---|
| Places: `filter.location.query` returns 10 results for all six cities. Name variants "Bangalore", "Bengaluru, India", "Sao Paulo", "NYC" and "Osaka" all resolve | VERIFIED |
| Unresolvable locality returns `400 {"errors":[{"message":"filter.location.query is unable to resolve to a valid locality."}]}` | VERIFIED |
| **Non-place domains are localized by `signal.location.query`.** Music, film and brands gave completely different results across three cities (Jaccard 0.0 for every pair) | VERIFIED |
| Places with `signal.location.query` + `filter.tags`: 2 of 6 cities returned (28 s and 4.7 s); 4 timed out. Use `filter.location.query` for places | VERIFIED |
| Using both filter and signal on places | UNVERIFIED (not run this pass) |

## E5. Audiences and demographics

| Claim | Label |
|---|---|
| `/v2/audiences/types` → `{"results":{"audience_types":[{"type":"urn:audience:<group>","parents":[…]}]}}`, 10 types | VERIFIED |
| `/v2/audiences` with no filter returns 400 "At least one of the following parameters must be provided: filter.results.audiences, filter.parents.types, filter.audience.types, …" | VERIFIED |
| `/v2/audiences?filter.parents.types=urn:audience:<group>` → `{"results":{"audiences":[…]}}`. Items have **both** `id` (`urn:audience:<group>:<name>`, the value to send) and `entity_id` (a UUID, not accepted as an audience) | VERIFIED |
| Demographics alone return `400 "at least one valid signal or filter is required for entity insights"` | VERIFIED |
| Artists: `signal.demographics.age` (55_and_older, 35_and_younger) and `gender=female` each change results (Jaccard 0.11 against the baseline; the three variants differ from each other) | VERIFIED |
| An audience id changes artist results (Jaccard 0.82) and place results in Lagos | VERIFIED |
| **Places: age and gender signals do NOT change the result set** (Jaccard 1.0 against location+age and in the coverage audience probe). They only add affinity scores | VERIFIED |

## E6. Popularity and trends

| Claim | Label |
|---|---|
| Artist `filter.popularity.max=0.5` replaces the whole result set (Jaccard 0.0) | VERIFIED |
| Artist `filter.popularity.min=0.9`: no change (the seed's neighbours are already popular), so inconclusive | VERIFIED (no effect here) |
| Artist `bias.trends=off` or `high`: no change | VERIFIED (no effect) |
| Place `bias.trends=high` replaces the whole set (Jaccard 0.0) and **adds affinity**; `off` matches the baseline | VERIFIED |
| Coverage freshness probe: `bias.trends=high` changes dining in every city (overlap with core 0.0) and **does not change** music, film or brands (overlap 1.0) | VERIFIED |
| `filter.popularity.max=0.5` changes place, movie and brand results too (Jaccard 0.0 to 0.25, 2 cities each) | VERIFIED ([param_evidence.md](param_evidence.md)) |

## E7. Candidate scoring (`filter.results.entities`)

| Claim | Label |
|---|---|
| With an **entity signal**, 10 candidates returned 9 scored, sorted results, all from the candidate list | VERIFIED |
| With a **location signal only**, sending 5, 25 or 51 candidates returned **1** result each time | VERIFIED |
| With an entity signal, place, movie and brand also score candidates: 10 candidates from each city's standard query returned 6 to 10 scored results (2 cities each, [param_evidence.md](param_evidence.md)) | VERIFIED |
| Maximum candidate count | UNVERIFIED (51 was accepted; the output cap is `take` ≤ 50) |

Implication: scoring an agent's own recommendations needs a taste signal (entity, tag or audience) together with the location. Location alone ranks almost nothing.

## E8. Explainability

`feature.explainability=true` adds `query.explainability` per result and a top-level `query.explainability`, for both artist and place queries (VERIFIED). The detailed structure of the "why" is not documented here, because it contains Qloo data.

## E9. Limits and errors

See "Basics and limits". No 429 or 5xx was observed in about 290 calls at 4 req/s; both shapes are UNVERIFIED (deliberately not triggered). Timeouts were observed on slow place queries.

## E10. Place cross-references

| Claim | Label |
|---|---|
| Insights places include `location{lat,lon,geohash}`, `external.google_place`, `properties.address`, and `properties.geocode{name,city,admin1_region,country,country_code,metro}` | VERIFIED |
| `GET /entities?entity_ids=` for 3 places returns `location{lat,lon}`, `properties.address`, `geocode`, `phone`, `website`, `hours`, `business_rating`; `external` was empty there | VERIFIED |
| Yelp or TripAdvisor ids | UNVERIFIED (not seen) |

## E11. Where scores come from (new)

Each domain was tested with signal combinations in one rich and one focus city (from `config/`), plus a rank-stability check. Every row was `outcome=ok`, with 10 results.

| Domain | Location only | + entity | + tag | + age | + audience | Entity only |
|---|---|---|---|---|---|---|
| dining (place, `filter.location.query` + restaurant tag) | **no affinity** (`distance` only) | affinity, set changes | affinity, set changes | affinity, **set unchanged** | affinity, set changes (1 of 2 cities timed out) | affinity |
| music (artist, `signal.location.query`) | affinity | affinity | affinity | affinity | affinity | affinity |
| film_tv (movie) | affinity | affinity | affinity | affinity | affinity | affinity |
| brands | affinity | affinity | affinity | affinity | affinity | affinity |

- Wherever affinity is present, results are sorted by it, descending (`affinity_sorted=true`).
- **Rank stability:** in every domain, the top 10 of `take=20` equals `take=10` exactly. Order is deterministic even with no scores.

**Recommendation for Phase 2:**
1. Use **rank-based metrics** (overlap@k, rank correlation and position of the agent's items in Qloo's list) as the primary measure. They work in every domain, and ranks are stable.
2. Use `query.affinity` only as a secondary signal, and only within one query. Absolute values aren't comparable across queries.
3. For dining, always add a taste signal (tag, entity or audience id) if you want scores.
4. To score an agent's own candidates, use `filter.results.entities` **with a taste signal**, never location alone (E7).

## Parameters VERIFIED as effective

The strict guard (`src/blindspot/qloo/params.py`) only allows these, per type. Evidence for each
promotion since Phase 1, with method and thresholds: [param_evidence.md](param_evidence.md).

| Type | Parameters |
|---|---|
| artist | entities, tags, `signal.location.query`, age, gender, audiences, `filter.popularity.max`, `filter.results.entities`, `filter.exclude.entities`, `feature.explainability` |
| movie, brand | entities, tags, `signal.location.query`, age, audiences, `filter.popularity.max`, `filter.results.entities`, `filter.exclude.entities` |
| place | `filter.location.query`, `filter.tags`, `signal.location.query` (**DISCOURAGED**: timed out in 4 of 6 cities, E4), entities, tags, audiences, `bias.trends`, `filter.popularity.max`, `filter.results.entities`, `filter.exclude.entities`, `feature.explainability` |
| other types | `signal.interests.entities` |

All types also accept `filter.type`, `take`, `page` and `offset`.

Deliberately not allowlisted (accepted but no measured effect; listed under "Do not retry" in
`params.py`): age and gender on places, and `bias.trends` on artists, movies and brands.
`filter.popularity.min` on artists was inconclusive (the seed's neighbours were already popular).
