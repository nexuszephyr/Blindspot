# Coverage matrix

The coverage matrix ran on 2026-10-15 (`scripts/coverage_matrix.py`, 88 live calls, purpose `coverage`). It covered 6 cities × 4 domains (both from `config/`) × 4 probe types, plus a swap analysis.
- The generated grid is in [coverage_grid.md](coverage_grid.md), with a CSV in [coverage_matrix.csv](coverage_matrix.csv). Both hold statuses, counts, latency and overlap only.
- Full rows with result names are in `data/coverage/`, which is gitignored (Qloo terms).

## Probes

| Probe | Query |
|---|---|
| core | domain + location + `signal.demographics.age=35_and_younger` |
| bridge | location + a seed entity taken from another domain's core result in the same city |
| audience | location + `gender=female` + `age=55_and_older` |
| freshness | core + `bias.trends=high` |
| swap | offline: mean Jaccard overlap of core results between every pair of cities |

Dining uses `filter.location.query` plus the restaurant tag. The other domains use `signal.location.query`, as E4 recommends.

## Result

**96 of 96 cells are `ok` with 10 results each.** There were no `empty`, `qloo_error` or `resolution_failed` cells, and every cell has affinity scores, because each probe includes an age or entity signal. Plausibility for dining: 239 of 240 dining results (24 cells × 10) geo-match their city (`properties.geocode` country or city), and the only miss is one New York freshness result. The music, film and brands cells were not checked for plausibility automatically; their city-specificity is shown by the near-zero swap overlap.

| Domain | Swap: mean core overlap between cities | Audience probe vs core | Freshness vs core | Bridge vs core | Median / max latency |
|---|---|---|---|---|---|
| dining | 0.00 | **1.00 (no effect)** | 0.00 (strong) | 0.05 | 2.4 s / 12.5 s |
| music | 0.01 | 0.00 (strong) | **1.00 (no effect)** | 0.02 | 1.0 s / 1.5 s |
| film_tv | 0.12 | 0.04 (strong) | **1.00 (no effect)** | 0.06 | 1.2 s / 2.9 s |
| brands | 0.02 | 0.00 (strong) | **1.00 (no effect)** | 0.04 | 0.8 s / 3.5 s |

Reading the table:
- **Every city is rich.** Lagos and Bengaluru returned full, city-specific result sets in every domain, the same as New York and Berlin. The tier-2 ("audit focus") cities aren't thin.
- **Cities are strongly distinct** (swap overlap ≤ 0.12). That makes "swap the city" a powerful test of cultural blindness: an agent that gives the same answer for Lagos and Berlin will score badly against Qloo.
- **Demographic signals don't move places.** For dining, the audience probe's results are identical to core. To create an audience contrast in dining, use **audience ids** (`urn:audience:…`, which changed place results in E5 and E11), not age or gender.
- **`bias.trends` only moves places.** For music, film and brands it's a no-op.

## Recommendation

- **Cities: keep all six** (Bengaluru, Lagos, Tokyo, São Paulo, Berlin, New York). Every city/domain cell has data, and the cities are clearly distinct from each other.
- **Domains: keep all four.**
  - music, film/TV and brands are fast (about 1 s), and every signal type works.
  - dining works with `filter.location.query` and a tag, but it's slower (median 2.4 s), and `signal.location.query` on places can time out (E4).
- **Viable probe types:**

  | Probe | Verdict |
  |---|---|
  | core | yes, all domains |
  | bridge | yes, all domains (it changes results) |
  | audience | yes for music, film and brands with age/gender. For dining, use audience ids, because age and gender have no effect |
  | freshness (`bias.trends`) | **dining only**; drop it for the other domains |
  | swap | yes, all domains (the strongest probe) |
- **Phase 2 scoring:** per E11, use rank-based metrics, with affinity only within a query.
