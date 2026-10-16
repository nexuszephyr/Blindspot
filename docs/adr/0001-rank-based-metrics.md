# 0001: Primary metrics are rank-based

- Status: Accepted
- Date: 2026-10-16

## Context
Recon experiment E11 found that place results get affinity scores only when a taste signal (entity,
tag or audience) is present. Location-only place queries return no scores. Artists, film/TV and
brands always return scores, but scores from different queries are not on a shared scale. Result
order was stable across repeated calls. Scoring a fixed candidate list with location alone returned
only 1 of N candidates.

## Decision
- Primary metrics use set overlap and rank position (overlap@k, rank-weighted hit rate, rank of
  matched items, Jaccard for swap tests).
- Affinity is used only to compare items within a single query.
- Every scoring query for places pairs location with a persona taste signal.

## Consequences
- Metrics work for every domain, with or without scores.
- Candidate scoring for recommendations outside Qloo's top list needs separate evidence (Phase 2,
  E14). Until then, items outside the answer key count as misses, not as negative scores.
- Hub entities that appear in many lists can inflate overlap and must be down-weighted.

## Alternatives considered
- Affinity-weighted scores everywhere: impossible for location-only places, and not comparable
  across queries.
- LLM-as-judge scoring: rejected. The answer key must come from Qloo, not from another model.
