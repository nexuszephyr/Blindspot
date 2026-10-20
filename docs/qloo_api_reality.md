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
| 2026-10-20 (#15): listings by parent type with `take=50` returned hobbies_and_interests 42, leisure 11, life_stage 4, global_issues 11 audiences | VERIFIED |
| Audience listing pagination (`page=2` when a type fills `take`): not exercised, because no type filled 50 (largest 42). The resolver asks for page 2 and records the verdict if a type ever does | UNVERIFIED |
| There is no audience name search: 9 of 11 audience names written in our own words matched no name in their type's listing; matching works only on the listing's own wording (#15, `docs/seed_review.md`) | VERIFIED |

### Name lookups for the id lock (#15, 2026-10-20)

| Claim | Label |
|---|---|
| `/search` with the seed's own type: 53 of 54 persona seeds returned hits of the requested type (a piano maker returned no brand hits under either its short or its full name); for 6 film titles the top 5 held 2 or 3 entries with the exact same name and type (remakes or same-name films), and some of those carry no `popularity` | VERIFIED |
| `/search` returns no confidence score; for 3 seeds (2 brands, 1 artist) the top hit's name only contains ours (a longer official or collaboration name), and for 1 brand none of the top 3 names matched | VERIFIED |
| `/v2/tags?filter.query=` returns the same tag name in many families: up to 6 exact-name tags across the dining families for one word (for example `genre:place:restaurant:*`, `category:place:*`, `dining_option:qloo:*`), plus non-place families (`amenity:place`, `genre:place:hotel:*`, `specialty_dish:place`, `menu_highlight:qloo`, `good_for:qloo`, `ambience:*`) | VERIFIED |
| A cuisine word can match a non-cuisine tag first: "french" also exists as `amenity:place:french`, and "traditional" as a hotel genre | VERIFIED |

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
| With the location plus an entity, tag or audience signal, 25 candidates from the take=50 standard list come back 24 or 25 of 25, all scored, all from the pool, in under 1 s, for all four domains; 50 of 50 with an entity signal. Exception: film with an entity seed returned 6/10, 18/25 and 36/50 in one city (E14) | VERIFIED |
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

<!-- BEGIN experiments_p2: generated by scripts/experiments_p2.py --report; don't edit by hand -->

Run 2026-10-20 by `scripts/experiments_p2.py` (purpose `recon`) in tokyo, bengaluru (first tier-1 and tier-2 city in `config/cities.yaml`), with the draft personas in `config/experiments_p2.yaml`. Recordings with ids are in `data/experiments_p2/`; these tables are regenerated from `summary.json` with `--report`.

Thresholds: a rank band is *generic* when its between-city Jaccard is above 0.3; a shape is *stable* at a repeat Jaccard ≥ 0.9; a dining shape is *fast* at p50 ≤ 4.0 s with its slowest call under the 8.0 s request deadline; candidate scoring is *useful* when ≥ 0.8 of N come back, all from the pool, within 4.0 s; the audience probe is *viable* when the mean pairwise persona Jaccard is < 0.5.

## E12. Answer-key depth (2026-10-20)

| Domain | Counts at take 10/20/30/50 (per city) | Scores | Top 10 unchanged at take 20/30/50 | City Jaccard 1-10 / 11-20 / 21-50 | Persona tail Jaccard (21-50 vs standard) | Depth | Membership pool |
|---|---|---|---|---|---|---|---|
| dining | tokyo: 10/20/30/50; bengaluru: 10/20/30/50 | no | yes/yes/yes | 0.0 / 0.0 / 0.0 | 0.017, 0.0 | **20** | 50 |
| music | tokyo: 10/20/30/50; bengaluru: 10/20/30/50 | yes | yes/yes/yes | 0.0 / 0.0 / 0.0 | 0.0, 0.017 | **20** | 50 |
| film_tv | tokyo: 10/20/30/50; bengaluru: 10/20/30/50 | yes | yes/yes/yes | 0.0 / 0.0 / 0.0 | 0.0, 0.017 | **20** | 50 |
| brands | tokyo: 10/20/30/50; bengaluru: 10/20/30/50 | yes | yes/yes/yes | 0.0 / 0.0 / 0.0 | 0.071, 0.0 | **20** | 50 |

Counts over 32 cells: min 10, median 25.0, max 50. Every cell returned exactly `take` results, so the distribution can't separate rich from thin cells: the thresholds below are **provisional** for #24.

Depth rule: 20 when both cities fill take=20 and ranks 11-20 stay city-specific, else 10. Membership pool: 50 when take=50 fills and ranks 21-50 stay city-specific (E14 fallback).

| Claim | Label |
|---|---|
| take 20, 30 and 50 return full lists in every domain and city | VERIFIED |
| The top 10 stays the same as take grows to 20, 30 and 50 | VERIFIED |
| Ranks 11-20 and 21-50 stay city-specific (between-city Jaccard ≤ 0.3) | VERIFIED |
| A persona audience changes ranks 21-50 (Jaccard vs the standard query ≤ 0.3) | VERIFIED |
| The location-only standard query carries scores for: music, film_tv, brands; not for: dining (as E11) | VERIFIED |

## E13. Dining query shape (2026-10-20)

Base: `filter.location.query` + the restaurant `filter.tags` (`signal.location.query` is DISCOURAGED for places). Persona A runs twice (the second with the cache bypassed); persona B runs once.

| Shape | All ok | Scores | Count | Repeat Jaccard / rank shift (per city) | Persona A vs B Jaccard | Latency p50 / max |
|---|---|---|---|---|---|---|
| entity | yes | yes | 10, 10 | 1.0 / 0.0, 1.0 / 0.0 | 0.0, 0.0 | 5.06 s / 9.14 s |
| tag | yes | yes | 10, 10 | 1.0 / 0.0, 1.0 / 0.0 | 0.053, 0.0 | 15.82 s / 28.04 s |
| audience | yes | yes | 10, 10 | 1.0 / 0.0, 1.0 / 0.0 | 0.0, 0.667 | 2.55 s / 4.29 s |
| entity+tag | yes | yes | 10, 10 | 1.0 / 0.0, 1.0 / 0.0 | 0.053, 0.053 | 7.33 s / 9.63 s |
| entity+audience | yes | yes | 10, 10 | 1.0 / 0.0, 1.0 / 0.0 | 0.0, 0.111 | 5.18 s / 9.44 s |
| tag+audience | no | no | 0, 10 | - / -, 1.0 / 0.0 | -, 0.667 | 1.17 s / 2.1 s |

**Pick: `audience`** (eligible: audience). Rule: all cells ok, scores in every cell, stable in both cities, p50 ≤ 4.0 s and the slowest call under 8.0 s, then the lowest persona Jaccard; ties go to fewer signals.

| Claim | Label |
|---|---|
| Every shape that answered is stable on repeat (Jaccard ≥ 0.9) | VERIFIED |
| A taste signal gives dining scores in every shape that answered | VERIFIED |
| Shapes whose slowest call stayed under 8.0 s: audience | VERIFIED |
| Shapes with failed calls (timeouts after retries): tag+audience (tokyo) | VERIFIED |
| Under a stricter rule (slowest call ≤ 4.0 s) the eligible shapes would be: none | VERIFIED |

First runs served from the Phase 1 cache (tag (bengaluru), tag (tokyo)) compare a result from about five days earlier with today's, so their repeat figure measures drift, not immediate repeatability.

## E14. Candidate scoring (2026-10-20)

`score_candidates()` (`filter.results.entities`) with the location and one persona signal, over the E12 take=50 standard list. Cells: returned/N, share scored, latency; a cross means some returned ids were outside the pool.

| Domain | entity 10 (tokyo) | entity 25 (tokyo) | entity 50 (tokyo) | tag 25 (tokyo) | audience 25 (tokyo) | entity 25 (bengaluru) | tag 25 (bengaluru) | audience 25 (bengaluru) | entity / tag / audience | Useful max (entity) |
|---|---|---|---|---|---|---|---|---|---|---|
| dining | 10/10, 1.0, 0.5 s | 25/25, 1.0, 0.7 s | 50/50, 1.0, 0.7 s | 25/25, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | 24/25, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | works / works / works | 50 |
| music | 10/10, 1.0, 0.8 s | 25/25, 1.0, 0.3 s | 50/50, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | works / works / works | 50 |
| film_tv | 6/10, 1.0, 0.4 s | 18/25, 1.0, 0.3 s | 36/50, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.4 s | 25/25, 1.0, 0.4 s | partial / works / works | - |
| brands | 10/10, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 50/50, 1.0, 0.4 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | 25/25, 1.0, 0.3 s | works / works / works | 50 |

Verdict: *works* when the 25-candidate cell passes in both cities, *partial* when it passes in one or at least half of N comes back from the pool in both, *fails* otherwise. Location alone is not re-run: it returned 1 of N (E7). Fallback for anything short of *works*: rank membership in the local top-50 from E12 (computable from the recordings: yes).

| Claim | Label |
|---|---|
| entity signal: works for dining, music, brands; partial for film_tv; fails for none | VERIFIED |
| tag signal: works for dining, music, film_tv, brands; partial for none; fails for none | VERIFIED |
| audience signal: works for dining, music, film_tv, brands; partial for none; fails for none | VERIFIED |
| Every returned id was in the candidate pool | VERIFIED |
| Every returned candidate carries a score | VERIFIED |
| Useful maximum with an entity signal: dining 50, music 50, film_tv -, brands 50 | VERIFIED |
| Location alone ranks 1 of N | VERIFIED (Phase 1 E7, not re-run) |

## E15. Persona sensitivity (2026-10-20)

4 personas per domain and city, top 10 each. Signal: the persona's audience; dining uses the E13 pick (`audience`). Six pairwise Jaccards per cell.

| Domain | Signal | tokyo mean / max | bengaluru mean / max | Audience probe |
|---|---|---|---|---|
| dining | audience | 0.138 / 0.333 | 0.748 / 1.0 | **marginal** |
| music | audience | 0.135 / 0.333 | 0.071 / 0.25 | **viable** |
| film_tv | audience | 0.22 / 0.538 | 0.236 / 0.538 | **viable** |
| brands | audience | 0.163 / 0.538 | 0.18 / 0.667 | **viable** |

Call: *viable* when the mean is below 0.5 in both cities (with all 4 personas returned), *marginal* in one, *not viable* in neither.

| Claim | Label |
|---|---|
| Audience probe viable for: music, film_tv, brands | VERIFIED |
| Marginal for: dining | VERIFIED |
| Not viable for: none | VERIFIED |

### Phase 1 cross-checks

| Check | Phase 1 | Now |
|---|---|---|
| Top 10 unchanged at take 20 | yes (E11) | yes |
| Max between-city top-10 Jaccard | ≤ 0.12 (coverage swap) | 0 |
| Dining p50, picked shape (audience) | about 2.4 s (coverage, age signal) | 2.55 s |
| Dining p50, all 6 shapes (median) | - | 5.12 s |
| Dining audience-only failures | 1 of 2 cities timed out (E11) | 0 of 6 requests, 0 of 2 cities |
| Dining tag+audience failures | - | 3 of 6 requests, 1 of 2 cities |

<!-- END experiments_p2 -->

## Standard query shape per domain (for #14)

From E12 to E15 (2026-10-20). Not applied to `config/domains.yaml` yet: #14 owns that file.

| Domain | filter.type | Location | Fixed params | Persona signal | Answer-key depth | Membership pool | Candidate scoring |
|---|---|---|---|---|---|---|---|
| dining | place | `filter.location.query` (never `signal.location.query`) | `filter.tags` = the restaurant tag | audience id (E13: the only shape under the 8 s deadline; entity or tag signals took 5 to 28 s) | 20 | 50 | entity, tag, audience: works (about 0.4 to 0.7 s) |
| music | artist | `signal.location.query` | none | audience id (E15: viable) | 20 | 50 | entity, tag, audience: works |
| film_tv | movie | `signal.location.query` | none | audience id (E15: viable) | 20 | 50 | tag, audience: works; entity: partial (use the membership fallback) |
| brands | brand | `signal.location.query` | none | audience id (E15: viable) | 20 | 50 | entity, tag, audience: works |

Provisional coverage thresholds for #24 (every E12 cell returned exactly `take`, so the real
distribution doesn't yet separate rich from thin): **rich** = a full list at the answer-key depth
(20 of 20); **thin** = 10 to 19; **sparse** = 1 to 9; **empty** = 0 or an empty 200. Recalibrate when
#24 finds a cell that doesn't fill.

Proposed YAML for #14 (ids by type; names stay in the personas file and are resolved):

```yaml
domains:
  - id: dining
    filter_type: urn:entity:place
    location_param: filter            # filter.location.query; signal.location.query is DISCOURAGED for places
    params: {filter.tags: urn:tag:genre:place:restaurant}
    signal: audience                  # signal.demographics.audiences: the persona's audience URN (E13)
    take: 20                          # answer-key depth (E12)
    membership_pool: 50               # rank-membership fallback for candidate scoring (E14)
    candidate_scoring: [entity, tag, audience]
  - id: music
    filter_type: urn:entity:artist
    location_param: signal
    signal: audience
    take: 20
    membership_pool: 50
    candidate_scoring: [entity, tag, audience]
  - id: film_tv
    filter_type: urn:entity:movie
    location_param: signal
    signal: audience
    take: 20
    membership_pool: 50
    candidate_scoring: [tag, audience]  # entity: partial in one city (E14); use the membership fallback
  - id: brands
    filter_type: urn:entity:brand
    location_param: signal
    signal: audience
    take: 20
    membership_pool: 50
    candidate_scoring: [entity, tag, audience]
```

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
