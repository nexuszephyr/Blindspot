# 0005: Strict parameter allowlist with evidence-based promotion

- Status: Accepted
- Date: 2026-10-16

## Context
Qloo silently ignores unknown or unsupported parameters and returns 200. Verified live in the Phase 1
audit: a bogus parameter changed nothing and nothing flagged it. A silently ignored filter would make
the answer key wrong without any error, which would corrupt every score.

## Decision
- `src/blindspot/qloo/params.py` holds a per-`filter.type` allowlist. Each parameter is VERIFIED
  (observed to change results), INEFFECTIVE (observed not to), or UNKNOWN.
- App code runs in strict mode: anything not VERIFIED raises `QlooParamError` before a network call.
- `scripts/promote_param.py` promotes a parameter by running a baseline and a variant and recording
  the evidence in `docs/param_evidence.md`.
- Recon scripts may use `strict=False`; they must log every non-allowlisted parameter sent.

## Consequences
- New query shapes need a promotion step first. That's deliberate friction.
- Scenario design can't depend on INEFFECTIVE parameters (for example age/gender for dining).

## Update 2026-10-20 (#12)
The promotion tool exists: `scripts/promote_param.py`. VERIFIED parameters are added to `PROMOTED`
in `params.py` (merged into the allowlist at import, each entry citing its evidence row id), and
INEFFECTIVE ones to a commented do-not-retry list. The tool only adds entries; removing one needs the
maintainer. Method, thresholds and every run are in `docs/param_evidence.md`.

## Alternatives considered
- Trust the docs: the docs were already wrong once (ADR 0003).
- Detect only empty results: an ignored parameter often returns non-empty, unchanged results.
