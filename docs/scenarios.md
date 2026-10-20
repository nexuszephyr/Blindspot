# The exam as config

The exam Blindspot gives an agent is four YAML files in `config/`. The code reads them and never
names a city, domain or persona itself, so you can change the exam without touching code, and a
judge can read exactly what was tested.

| File | Holds | Read by |
|---|---|---|
| `cities.yaml` | Where the exam runs | everything |
| `domains.yaml` | What is asked, and the Qloo query shape per domain | answer-key build (#18), scoring |
| `probe_types.yaml` | How a question is varied | scenario generator (#16) |
| `personas.yaml` | Who is asking (taste only) | resolver (#15), generator (#16), target prompts |

`blindspot.catalog.load_catalog()` loads all four and checks every cross-reference. Anything that
would otherwise load silently and mean something else fails instead: an unknown or misspelled key
(`depht: 30` doesn't fall back to the default), an unknown reference, an unknown `condition`. That's
the config-side version of Qloo's silent-ignore pitfall. A bad value fails with a `ConfigError` that
names the file, the item and the field, for example:

```text
config/personas.yaml: personas[0] (id=street_food_explorer): age_band: Input should be '35_and_younger', '36_to_55' or '55_and_older'
```

Check the config any time with:

```bash
python -c "from blindspot.catalog import load_catalog; c = load_catalog(); print(len(c.personas), 'personas')"
```

Names in these files (seed entities, tags, audiences) are **inputs**, never sent as free text. #15
resolves each one to a Qloo id and locks it; the lock file is what queries use.

### The id lock (`resolved_ids.lock.json`)

`python scripts/resolve_seeds.py` looks up every seed, interest tag and audience in `personas.yaml`
and writes `config/resolved_ids.lock.json`. Each item is keyed `<kind>:<domain>:<our query,
lowercased>` (audiences use domain `*`, and a `{name, type}` audience's query is `<type>:<name>`)
and holds our query text, the chosen Qloo id, a confidence, an `ambiguous` flag and a reason. Only
our text and ids are stored, never Qloo names (D5). Code reads it with
`blindspot.idlock.load_lock().id_for(kind, domain, query)`, which returns None for anything
unresolved. The file is deterministic: re-running with an unchanged config makes 0 live calls and
leaves it byte-identical, and changing one persona's text only looks up the new text. Review what
the rules flagged in `docs/seed_review.md`; run `--dry-run` first to see which lookups would be live.

## domains.yaml

| Field | Meaning | Source |
|---|---|---|
| `id` | Short name used everywhere (`dining`, `music`, `film_tv`, `brands`) | |
| `filter_type` | Qloo insights `filter.type`; must be a supported type (`videogame`, not `video_game`) | ADR 0003 |
| `take` | Size of the standard location-only query used by the recon scripts. Kept at 10 so their cache keys don't change | Phase 1 |
| `phrasing` | How a target prompt names the domain ("restaurants") | |
| `location_param` | `filter` restricts results to the city (places); `signal` only biases ranking | E4 |
| `params` | Fixed insights params for every query in the domain, ids only (dining: the restaurant tag) | E11 |
| `tag_query` | Free text resolved to a tag id for tag-signal experiments | E11 |
| `depth` | Answer-key depth: the top `depth` results are the key | E12, D34 |
| `membership_pool` | Top-N kept to place an agent's pick that falls outside the key; must be ≥ `depth` | E14, D36 |
| `query_shape.signal` | The persona signal added to the location for the answer key: `audience`, `entity` or `tag` | E13, D35 |
| `query_shape.candidate_scoring` | Signals that score an agent's own picks with `filter.results.entities`; for anything else, use membership in the pool | E14, D36 |
| `query_shape.with_age` | Also send the persona's age band (default `false`). Rejected for places (age has no effect, E5), and refused for any persona whose band is UNVERIFIED | E5 |
| `probes` | Probe types enabled for this domain; each must allow the domain in `probe_types.yaml` | E15, D37 |

Today every domain uses `signal: audience`, depth 20 and a pool of 50. Film doesn't list `entity` under
`candidate_scoring` because entity scoring was partial for the seed tested (D36).

## probe_types.yaml

| Field | Meaning |
|---|---|
| `id` | Probe ids come from this file (today `core`, `swap`, `bridge`, `audience`, `freshness`); the generator (#16) implements each and rejects ids it doesn't know |
| `description` | One line on what the probe detects |
| `domains` | Domains where the probe is **allowed**. A domain **enables** it in `domains.yaml` |
| `scenarios_per_cell` | Questions per persona × city × domain cell (1 to 5) |
| `max_scenarios` | Cap for this probe across the exam. The totals aim for 80 to 120 scenarios (#16) |
| `params` | Extra answer-key params, ids only. The loader rejects any param not VERIFIED for every allowed domain's `filter.type`, because Qloo silently ignores the rest (ADR 0005) |
| `requires_pairs` | The taste is stated in another domain (bridge). Then every domain that enables the probe needs a pair |
| `pairs` | Asked domain → the domain the taste is stated in. A persona examined in the asked domain must also have the stated one, or list the probe in `exclude_probes` |
| `condition` | Domain → a check the generator must pass before using the probe there. Only checks the generator implements load (`catalog.KNOWN_CONDITIONS`: `personas_distinct_in_city`), so a typo can't silently drop the guard |

| Probe | Setup | Where | Evidence |
|---|---|---|---|
| `core` | Persona taste + city, ask one domain | all | coverage rich in all 6 cities × 4 domains |
| `swap` | Same persona and question in two cities | all | city top-10 overlap ≤ about 0.12 (E12: 0.0) |
| `bridge` | Taste stated in one domain, asked in another | all, via `pairs` | to validate in V-checks |
| `audience` | Same city and domain, two personas | music, film, brands; dining only if `personas_distinct_in_city` passes | E15, D37 |
| `freshness` | Answer key with `bias.trends=high` | dining only | `bias.trends` changes dining only (E6) |

## personas.yaml

```yaml
audience_policy:
  allow_identity: false        # identity-based audiences are opt-in (AGENTS §13)

personas:
  - id: street_food_explorer
    descriptor: Hunts for cheap, fast street food and busy night markets
    age_band: 35_and_younger
    audiences: [{name: street food, type: hobbies_and_interests}]
    domains: [dining, music, film_tv, brands]
    cities: []                 # optional; empty means every city
    interest_tags: {dining: [street food, night market]}
    seeds:
      music: [{name: Bad Bunny, type: urn:entity:artist}]
      film_tv: [{name: Chef, type: urn:entity:movie}]
      brands: [{name: Vans, type: urn:entity:brand}]
    notes: ""
```

| Field | Meaning | Rules |
|---|---|---|
| `id` | Short name | lowercase, digits, `_`; unique |
| `descriptor` | One neutral line about **taste** | 10 to 120 characters; no identity terms |
| `age_band` | A prompt detail today; a Qloo `signal.demographics.age` value only for a shape with `with_age: true` (none yet) | `35_and_younger`, `36_to_55` (UNVERIFIED until #15) or `55_and_older`; an UNVERIFIED band is refused by any shape that sends age |
| `audiences` | The persona's Qloo audiences: a resolved `urn:audience:<type>:<id>`, or `{name, type}` for #15 to match against `GET /v2/audiences` (there's no audience name search) | 1 to 3 when any enabled domain's shape uses an audience (today: all); no duplicates; **no two personas may have the same audience set**, because their answer keys would be identical. Both forms compare as one key (`{name: fine dining, type: leisure}` equals `urn:audience:leisure:fine_dining`), and #15 re-runs the check with the resolved URNs (`check_distinct_audiences`) |
| `domains` | Domains this persona is examined in | must exist in `domains.yaml` |
| `cities` | Restrict the persona to some cities | ids from `cities.yaml`; empty = all |
| `seeds` | Domain → 1 to 3 entities the persona likes, with their `/search` type | keys must be in `domains` |
| `interest_tags` | Domain → 1 to 3 tag names (cuisines, genres) | keys must be in `domains` |
| `exclude_probes` | Probe types never generated for this persona | ids from `probe_types.yaml` |
| `notes` | Free text for maintainers | not sent anywhere, but still checked for identity terms |

Every enabled domain needs a stated taste (`seeds` or `interest_tags`) because target prompts use it
(AGENTS §8). Dining uses `interest_tags` (cuisines, formats) rather than seed places, since places are
city-specific. If a domain's shape uses an `entity` or `tag` signal, the matching field is required
for it.

### Audience policy

With `allow_identity: false` (the default), the loader rejects:

- audiences whose type is `communities`, `political_preferences` or `lifestyle_preferences_beliefs`;
- religion, ethnicity, orientation, caste, immigration or political words in any text that can reach
  a prompt or a lookup: the descriptor, audience names and URNs, interest tags, seed names and notes.

Words match as whole tokens (split on anything that isn't a letter), so taste words load:
"white-tablecloth", "black coffee", "Southeast Asian street food", "race-day food". An identity token
inside a URN is still caught (`race_ethnicity` via "ethnicity").

#15 checks the resolved URN's type again, because a name can resolve to an identity audience.
Turning `allow_identity` on needs the maintainer's approval (AGENTS §13).

## How to add a persona

1. Pick a taste cluster that no persona covers yet, and write the descriptor about what they like,
   never who they are. Avoid stereotypes: a cuisine or genre is a taste, not a nationality.
2. Choose an age band and **an audience set nobody else has** (its type from the
   `GET /v2/audiences` listing; interest, leisure, life-stage or hobby types).
3. For each domain, add 1 to 3 well-known seeds with their `/search` type, or interest tags.
   Dining uses interest tags.
4. Run `load_catalog()` (command above), then `make test`.
5. Run `scripts/resolve_seeds.py` (#15) and review anything flagged ambiguous in
   `docs/seed_review.md` before the answer-key build.

## How to add a city or domain

- **City:** add it to `cities.yaml` with the exact Qloo location string, run the coverage probe and
  record the verdict (AGENTS §9).
- **Domain:** add it to `domains.yaml` with a supported `filter_type`. Promote any new param with
  `scripts/promote_param.py` first, run E12 to E14 for its depth, shape and candidate scoring, then
  allow it in the probe types you want.

## Scenarios (the generated exam)

`blindspot scenarios build` (or `python -m blindspot scenarios build`) turns the four config files
and the id lock into the exam: `data/scenarios/scenarios.json`. The file is gitignored because it is
regenerated, offline and byte-identically, from committed inputs; the build prints its sha256, so two
builds can be compared. `blindspot scenarios list [--probe --city --domain --tag]` and
`blindspot scenarios show <id>` read it. Code: `src/blindspot/scenarios/`.

| Field | Meaning |
|---|---|
| `id` | `sc_` + 12 hex of sha256 over the scenario's content (everything but `id` and `tags`). Same config and lock, same ids |
| `version` | Schema version (1) |
| `persona_id`, `city_id`, `domain_id` | Who asks, where, and which domain is asked for |
| `probe_type` | `core`, `swap`, `bridge`, `audience` or `freshness` |
| `stated_domain` | Bridge only: the domain the persona's taste is stated in |
| `signal` | The persona signal in the answer key (the domain's `query_shape.signal`) |
| `local_spec` | `/v2/insights` params for this city: the answer key (top `depth`) and the membership pool (`pool`, its `take`). Ids only, from the lock; checked by the strict parameter guard |
| `global_spec` | The same query without a location: the "famous anywhere" baseline. Shared by every scenario with the same persona, domain and probe, so #18 fetches it once (`key`) |
| `depth`, `pool`, `k` | Answer-key depth (20), membership pool (50), and how many recommendations the target is asked for (10) |
| `prompt_variants` | Three phrasings (`t1_direct`, `t2_new_in_town`, `t3_terse`) |
| `pair_id` | Swap: one persona in two cities. Audience: two personas in one city. Both halves share it |
| `conditions` | Checks the answer-key build must pass first (dining audience pairs: `personas_distinct_in_city`, D37) |
| `tags` | `demo` or `extended`, plus `holdout` for about 20% |

**How it's built.**
1. For each probe, every possible unit is listed in a fixed order: one scenario, or a pair for swap
   and audience. A persona whose answer-key signal has no locked id is skipped, and the build lists
   why.
2. A seeded RNG (`DEFAULT_SEED`) shuffles the units. A greedy pick then balances cities, domains and
   personas up to the probe's `max_scenarios`. Swap pairs prefer one rich and one focus city.
3. Every 5th pick of each probe is also `holdout`, a pair as a whole.
4. The demo subset takes 8 swap pairs covering the most new cities and domains, then core
   scenarios to cover the remaining demo city × domain cells, then one bridge, one audience pair and
   one freshness scenario.

**Prompts** use only the persona's own seeds (or interest tags), its age band and the city
(AGENTS §8). The answer key excludes the persona's own seeds in the asked domain
(`filter.exclude.entities`, VERIFIED in #12), so nothing a prompt names can be in its key.
`scenarios.prompts.lint_prompts` checks prompts against ids and, in #18, the real answer-key names.

## Scenario distribution

<!-- BEGIN scenario distribution: generated by `blindspot scenarios build --docs` -->

104 scenarios from seed 20261028 (inputs `in_f026473b62f9`).

| Probe \ city | bengaluru | berlin | lagos | new_york | sao_paulo | tokyo | Total |
|---|---|---|---|---|---|---|---|
| swap | 4 | 4 | 4 | 4 | 4 | 4 | 24 |
| core | 7 | 7 | 6 | 7 | 7 | 6 | 40 |
| bridge | 3 | 3 | 3 | 2 | 2 | 3 | 16 |
| audience | 2 | 2 | 2 | 4 | 4 | 2 | 16 |
| freshness | 1 | 1 | 2 | 1 | 1 | 2 | 8 |
| **Total** | 17 | 17 | 17 | 18 | 18 | 17 | **104** |

| Probe \ domain | brands | dining | film_tv | music | Total |
|---|---|---|---|---|---|
| swap | 6 | 6 | 6 | 6 | 24 |
| core | 10 | 10 | 10 | 10 | 40 |
| bridge | 4 | 4 | 4 | 4 | 16 |
| audience | 4 | 4 | 4 | 4 | 16 |
| freshness | 0 | 8 | 0 | 0 | 8 |
| **Total** | 24 | 32 | 24 | 24 | **104** |

| Subset | Scenarios | Notes |
|---|---|---|
| demo | 40 | 8 swap pairs |
| extended | 64 | |
| holdout | 21 | swap 4, core 8, bridge 3, audience 4, freshness 2 |

<!-- END scenario distribution -->
