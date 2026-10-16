# 0002: No real Qloo data in git

- Status: Accepted (pending Qloo's answer on snapshots)
- Date: 2026-10-16

## Context
The repo is public. Qloo's terms on redistributing API results are unknown, and we have asked on the
hackathon Discord. Hackathon keys are deactivated after judging, so a keyless clone must still run.
Render's free tier has an ephemeral disk, so the hosted cache can disappear on restart.

## Decision
- Real Qloo responses live only in `data/` (gitignored) and in private storage.
- The public repo ships a synthetic snapshot with the real schema, labeled `synthetic=true`.
- The hosted demo loads a real snapshot at startup from `SNAPSHOT_URL` (+ optional `SNAPSHOT_TOKEN`),
  validated by schema version and checksum, falling back to the synthetic snapshot with a warning.
- `config/resolved_ids.lock.json` keeps only Qloo IDs and our own query text; returned names stay
  in gitignored `data/resolved_ids.detail.json`.
- Committed test fixtures are tiny and trimmed or synthetic.

## Consequences
- Judges get real data on the hosted demo and synthetic data from a fresh clone. The UI must say
  which one they're seeing.
- If Qloo allows a minimal snapshot, revisit and commit the demo subset.

## Alternatives considered
- Commit the real snapshot: unknown legal status.
- Rebuild ground truth on every boot: costs about 100 calls per cold start on a host that sleeps.
- Paid persistent disk: simplest, about $7/month; still an option.
