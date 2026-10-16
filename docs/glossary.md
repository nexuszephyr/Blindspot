# Glossary

**Affinity.** Qloo's score for how strongly an entity matches the query's audience or location.
Comparable only within one query. Places only get it when a taste signal is present.

**Answer key.** Qloo's ranked results for a scenario: a local list (audience in this place) and a
global baseline (same audience, no location). Read through `get_answer_key(scenario_id)`.

**Audience.** A Qloo audience URN used as `signal.demographics.audiences`. Interest and lifestyle
audiences are on by default; identity-based audiences are opt-in and off by default.

**Blind Map.** The city × domain heatmap of cell scores. The main visual of the UI.

**Blindspot Score.** Combined 0 to 100 score for a target. Not computed on thin or empty cells.

**Blindspot.** A cell where a target agent's recommendations diverge from the answer key, confirmed
by extra probes and a confidence interval.

**Bridge probe.** Taste stated in one domain, recommendations asked in another (music taste, ask for
restaurants). Tests cross-domain knowledge, which is Qloo's strength.

**Cell.** One city × domain × probe-type combination.

**Confirmed blindspot.** A cell whose bootstrap 90% confidence interval lies entirely below the
score threshold, after enough probes.

**Coverage verdict.** `rich`, `thin` or `empty` for a cell, from result counts and score presence.

**Degraded.** A result returned without live Qloo data because of budget, breaker, deadline or a
disabled request. Always carries a human-readable reason.

**Entity.** A Qloo object (artist, movie, TV show, place, brand and so on) with a Qloo ID.

**`entity_index`.** Local table of every Qloo entity seen in ground truth, with normalized names and
aliases. The resolver matches agent output against it before calling `/search`.

**Famous-vs-local gap.** How much a target prefers globally popular picks over picks with high local
affinity for the audience.

**Famousness.** Share of a target's picks that are in Qloo's global top-k but not the local top-k.

**Held-out set.** Scenarios kept out of the fix loop's tuning, used to check the fix generalizes.

**Hub entity.** An entity that appears in many answer keys; down-weighted so it doesn't inflate
overlap everywhere.

**Kill switch.** Per-request switch that disables Qloo for that request only. With it on, scores
gray out, showing the app can't score without Qloo.

**Local hit share.** Share of a target's picks that are local favorites (in the local top-k, not the
global top-k).

**Persona.** A config-defined user: neutral descriptor, age band, audience IDs, interest tags and seed
entities. Never a stereotype.

**Phantom.** A recommendation that matches no Qloo entity and has no existence evidence.

**Probe type.** `core`, `swap`, `bridge`, `audience`, `freshness`. Each generates scenarios.

**Scenario.** One exam question: persona × city × domain × probe type, with prompt variants and Qloo
query specs. Deterministic and content-hashed.

**`served_from`.** Provenance label on every Qloo-derived result: `live`, `cache`, `snapshot`,
`disabled` or `degraded`. Synthetic snapshot data also carries `synthetic=true`.

**Silent ignore.** Qloo's behavior of accepting unknown parameters without error and ignoring them.
The param guard exists to catch it.

**Swap sameness.** Overlap between a target's answers for the same persona in two cities. High
overlap means the agent ignores place.

**Target.** The AI agent or model being audited, reached through an OpenAI-compatible endpoint.

**Taste signal.** An entity, tag or audience passed to Qloo to describe what the persona likes.

**Truth Receipt.** The record behind a score: the Qloo calls, parameters and returned entities,
drawn from the call log.

**Truth Table.** Side-by-side view: the agent alone, the agent with Qloo, and Qloo's answer key.
