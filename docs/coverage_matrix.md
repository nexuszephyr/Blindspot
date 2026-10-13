# Coverage matrix

**Status: PENDING (needs key).** The matrix hasn't run because there's no `QLOO_API_KEY` on the
dev machine. `scripts/coverage_matrix.py` is written and ready (about 96 live calls). When it
runs, it writes the generated grid to `docs/coverage_grid.md` and `docs/coverage_matrix.csv`
(statuses, counts, latency and overlap only). Full rows with result names go to
`data/coverage/` (gitignored, per Qloo's no-public-storage rule). After the run, the
recommendation below gets rewritten from the data.

## Design

- **Cities (6):** Bengaluru, Lagos, Tokyo (Osaka is tried as a name format in recon E4), São Paulo, Berlin, New York.
- **Domains (4):**

| Domain | `filter.type` | Extra | Location parameter |
|---|---|---|---|
| dining | `urn:entity:place` | `filter.tags=urn:tag:genre:place:restaurant` (tag id to be confirmed by E2) | `filter.location.query` (restricts) |
| music | `urn:entity:artist` | none | `signal.location.query` (influences; may be ignored, see E4) |
| film/TV | `urn:entity:movie` | none | `signal.location.query` |
| brands | `urn:entity:brand` | none | `signal.location.query` |

- **Probes per cell:**

| Probe | Query | What it tells us |
|---|---|---|
| core | domain + location + `signal.demographics.age=35_and_younger` | Is there data for "this audience in this place"? |
| bridge | core + `signal.interests.entities` = top core entity from another domain in the same city (dining↔music; film/brands ← music) | Do cross-domain signals work and stay local? |
| audience | location + `gender=female` + `age=55_and_older` | Does a contrasting audience change results? (`audience_vs_core` overlap) |
| freshness | core + `bias.trends=high` | Does trend bias change results? (`freshness_vs_core` overlap) |
| swap | computed offline, no calls | Mean Jaccard overlap of core top-10 between city pairs. Low overlap means "swap the city" is a usable test |

- **Statuses:** `ok` (results returned), `empty` (200 with no results, which may mean an ignored param), `error` (degraded, reason recorded), `skipped` (call cap reached or no bridge seed).
- **Plausibility:** for dining, an automatic geo-match (how many results mention the city or country in their address or geocode). For other domains, a manual reading of `data/coverage/*.csv` after the run.
- **Flags:** `--place-loc signal` and `--other-loc none` let you re-run with the location mode that recon E4 shows actually works.

## Recommendation: PROVISIONAL (from documentation, not data)

This is a hypothesis to test, not a finding:

1. **Dining is the safest domain.** Location filtering is native for `urn:entity:place` and documented in detail (`filter.location.query`, geocode, radius).
2. **Music, film/TV and brands carry the main risk.** The entity-type parameter guide lists no `signal.location*` parameter for artist, movie or brand, so a city may not move their results at all. If E4 shows identical results across cities, Phase 2 should localize those domains another way:
   - (a) audience ids (E5)
   - (b) region or genre tags (E2, e.g. afrobeats, bollywood)
   - (c) bridge signals seeded from local places (the bridge probe)

   If none of these work, cut the domain.
3. **Cities:** keep all six until the data says otherwise. Tokyo is preferred over Osaka because larger metros probably have denser coverage. The cities most at risk of thin data are Lagos and Bengaluru, which are also the most interesting ones for a cultural-blindness audit.
