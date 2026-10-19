# Parameter evidence

Qloo silently ignores parameters it doesn't support (200 with unchanged results), so the client's
strict guard (`src/blindspot/qloo/params.py`) only sends parameters with recorded evidence that they
change results. This file is that evidence. Rows in the last table are written by
`scripts/promote_param.py`; don't edit them by hand.

## Method

```bash
python scripts/promote_param.py <filter.type> <param> <value> [--baseline k=v ...] [--city <id> ...]
    [--resolve param=name[@urn:entity:type] ...] [--apply] [--dry-run]
```

- **Cities:** from `config/cities.yaml`. Default is the first tier-1 (rich) and the first tier-2
  (focus) city.
- **Baseline:** the domain's standard query from `config/domains.yaml` (filter.type, the domain's
  params, the city's location, `take`), extended by any `--baseline k=v`. Sent in strict mode, so
  every baseline parameter is already VERIFIED.
- **Variant:** baseline + the parameter under test, sent with `strict=False`. If any *other*
  unverified parameter shows up in the variant, the run is INCONCLUSIVE.
- **`@std:N` values:** the top N entity ids of the city's standard query. Used for
  `filter.exclude.entities` (exclude the top N) and `filter.results.entities` (a candidate pool). Only
  the token is recorded; the ids never are. "@std hits" counts how many of those ids came back in the
  variant (exclude: expect 0; candidates: how many of N Qloo scored).
- **Names:** `--resolve` turns a name into an id with the client's resolver (cached `/search` or
  `/v2/tags`). Names are never sent as free text and never written here.
- **Every call** goes through `QlooClient` (cache, budgets, breaker, call log) with purpose `recon`.
  Re-running the same arguments is all cache hits within the cache TTL and adds no row.
- **`bias.trends` rows for movie and brand** extend the baseline with
  `signal.demographics.age=35_and_younger` only so both queries are the cached Phase 1 coverage
  freshness pair (0 live calls). Baseline and variant carry the same age, so the verdict is about
  `bias.trends`, not age.
- **Rows are never rewritten.** If a re-run gives a different verdict, a new dated row `<id>.2`
  (`.3`, …) is appended, the earlier one stays, and the script prints a warning (noting when the
  parameter is already in `PROMOTED`).
- `--dry-run` uses a replay-mode client: it lists each planned request as cached or LIVE and never
  reaches the network.
- `--apply` adds a VERIFIED parameter to `PROMOTED` in `params.py` (with this row's id) and an
  INEFFECTIVE one to the do-not-retry list. It never removes an existing entry.

## Thresholds

Per city, comparing the baseline's ordered result ids with the variant's:

| Measure | Definition |
|---|---|
| Jaccard | shared ids / all ids. **Set changed** when Jaccard ≤ **0.9** (one swap in a top 10 gives 0.82) |
| Rank shift | mean absolute rank difference over the shared ids (`-` when none are shared). **Order changed** when ≥ **1.0** |
| Identical | the same ids in the same order |

| Verdict | Rule |
|---|---|
| **VERIFIED** | set or order changed beyond a threshold in at least 1 city, and no errors |
| **INEFFECTIVE** | identical in at least 2 cities, including a tier-1 and a tier-2 city |
| **INCONCLUSIVE** | any error, timeout or empty baseline; a change below the thresholds; or identical in too few cities. The reason is recorded |

For `filter.exclude.entities`, a change only counts if none of the excluded ids come back. For
`filter.results.entities`, it only counts if every returned id is from the candidate pool. The
"variant ms" note is `-` when the variant came from the cache.

## What is not recorded

No entity names, no result ids, no affinity values (D5, ADR 0002). Input values are labelled before
they are written: an entity id (a seed from `--baseline` or `--resolve`) becomes `entity#<first 6 hex
of its sha256>`, which is stable, so rows with the same seed stay comparable. Tag and audience URNs
(`urn:tag:…`, `urn:audience:…`) are Qloo's vocabulary, not response data, and are kept as sent.

## Phase 1 evidence (cited, not re-run)

From the recon run of 2026-10-15 ([qloo_api_reality.md](qloo_api_reality.md) E3 to E11):

| filter.type | Parameter | Verdict | Source |
|---|---|---|---|
| artist | `filter.popularity.max=0.5` | VERIFIED (Jaccard 0.0) | E6 |
| artist | `filter.results.entities` + entity signal | VERIFIED (9 of 10 candidates scored) | E7 |
| artist | `filter.results.entities` + location only | returns 1 of N | E7 |
| artist | age, gender, audience | VERIFIED (Jaccard 0.11 / 0.82) | E5 |
| artist | `bias.trends=off/high` | INEFFECTIVE (unchanged; also unchanged in all 6 cities in the coverage freshness probe) | E6, coverage |
| artist | `filter.popularity.min=0.9` | INCONCLUSIVE (the seed's neighbours were already popular) | E6 |
| place | `bias.trends=high` | VERIFIED (Jaccard 0.0, adds affinity, all 6 cities) | E6, coverage |
| place | age, gender | INEFFECTIVE (Jaccard 1.0; adds affinity only) | E5, E11 |
| place | `signal.location.query` | **DISCOURAGED**: allowed, but timed out in 4 of 6 cities; use `filter.location.query` | E4 |
| artist, movie, brand | `signal.location.query` | VERIFIED (Jaccard 0.0 between cities) | E4 |

## Runs

| id | date | type | param=value | baseline | cities | counts | Jaccard | rank shift | scores | served_from | verdict | live calls | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ev_a1647925 | 2026-10-20 | place | `filter.exclude.entities=@std:3` | std(dining) | tokyo, bengaluru | 10→10, 10→10 | 0.538, 0.538 | 3.0, 3.0 | no→no, no→no | cache/live, cache/live | **VERIFIED** | 2 | @std hits 0/3, 0/3; variant ms 5695, 759 |
| ev_15455188 | 2026-10-20 | artist | `filter.exclude.entities=@std:3` | std(music) | tokyo, bengaluru | 10→10, 10→10 | 0.538, 0.538 | 3.0, 3.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 0/3, 0/3; variant ms 2099, 501 |
| ev_ef4684c9 | 2026-10-20 | movie | `filter.exclude.entities=@std:3` | std(film_tv) | tokyo, bengaluru | 10→10, 10→10 | 0.538, 0.538 | 3.0, 3.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 0/3, 0/3; variant ms 1982, 576 |
| ev_3b8677bd | 2026-10-20 | brand | `filter.exclude.entities=@std:3` | std(brands) | tokyo, bengaluru | 10→10, 10→10 | 0.538, 0.538 | 3.0, 3.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 0/3, 0/3; variant ms 2156, 971 |
| ev_a76125b5 | 2026-10-20 | place | `filter.popularity.max=0.5` | std(dining) | tokyo, bengaluru | 10→10, 10→10 | 0.0, 0.0 | -, - | no→no, no→no | cache/live, cache/live | **VERIFIED** | 2 | variant ms 2738, 519 |
| ev_7106748a | 2026-10-20 | movie | `filter.popularity.max=0.5` | std(film_tv) | tokyo, bengaluru | 10→10, 10→10 | 0.25, 0.0 | 3.75, - | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | variant ms 1446, 390 |
| ev_db9f8c70 | 2026-10-20 | brand | `filter.popularity.max=0.5` | std(brands) | tokyo, bengaluru | 10→10, 10→10 | 0.0, 0.0 | -, - | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | variant ms 1518, 416 |
| ev_2e9b0769 | 2026-10-20 | movie | `bias.trends=high` | std(film_tv) + signal.demographics.age=35_and_younger | tokyo, bengaluru | 10→10, 10→10 | 1.0, 1.0 | 0.0, 0.0 | yes→yes, yes→yes | cache/cache, cache/cache | **INEFFECTIVE** | 0 | variant ms -, - |
| ev_7ab34baf | 2026-10-20 | brand | `bias.trends=high` | std(brands) + signal.demographics.age=35_and_younger | tokyo, bengaluru | 10→10, 10→10 | 1.0, 1.0 | 0.0, 0.0 | yes→yes, yes→yes | cache/cache, cache/cache | **INEFFECTIVE** | 0 | variant ms -, - |
| ev_8d124bfb | 2026-10-20 | movie | `signal.demographics.audiences=urn:audience:global_issues:animal_issues` | std(film_tv) | tokyo, bengaluru | 10→10, 10→10 | 0.0, 0.25 | -, 3.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | variant ms 2537, 1118 |
| ev_6df30cce | 2026-10-20 | brand | `signal.demographics.audiences=urn:audience:global_issues:animal_issues` | std(brands) | tokyo, bengaluru | 10→10, 10→10 | 0.0, 0.111 | -, 3.5 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | variant ms 1831, 2338 |
| ev_0d51fb5f | 2026-10-20 | place | `filter.results.entities=@std:10` | std(dining) + signal.interests.entities=entity#173f2c | tokyo, bengaluru | 10→10, 10→9 | 0.0, 0.0 | -, - | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 10/10, 9/10; variant ms 2570, 656 |
| ev_cb6ddf4d | 2026-10-20 | movie | `filter.results.entities=@std:10` | std(film_tv) + signal.interests.entities=entity#173f2c | tokyo, bengaluru | 10→6, 10→10 | 0.0, 0.111 | -, 4.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 6/10, 10/10; variant ms 1449, 395 |
| ev_b38f3365 | 2026-10-20 | brand | `filter.results.entities=@std:10` | std(brands) + signal.interests.entities=entity#173f2c | tokyo, bengaluru | 10→10, 10→10 | 0.0, 0.053 | -, 0.0 | yes→yes, yes→yes | cache/live, cache/live | **VERIFIED** | 2 | @std hits 10/10, 10/10; variant ms 1446, 311 |
| ev_ed53c522 | 2026-10-20 | place | `signal.interests.tags=urn:tag:genre:place:restaurant:italian` | std(dining) | tokyo, bengaluru | 10→10, 10→10 | 0.053, 0.053 | 0.0, 5.0 | no→yes, no→yes | cache/cache, cache/cache | **VERIFIED** | 0 | variant ms -, - |
