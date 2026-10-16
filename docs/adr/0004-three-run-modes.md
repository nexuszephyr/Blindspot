# 0004: Three run modes, all judge-runnable

- Status: Accepted
- Date: 2026-10-16

## Context
Judges use the hosted demo and may clone the repo. They don't have our Qloo key, may click fast and
in parallel, and may hit a cold server. Keys are deactivated after judging.

## Decision
Support and test three modes on every change:
1. Hosted demo: no setup.
2. Local with own key: `.env` and one command.
3. Local with no key: replay/snapshot mode, offline.

Every result carries `served_from`. Every web request has an overall deadline and degrades instead of
hanging. Budgets at three scopes protect the shared quota, with a reserved slice for the demo.

## Consequences
- CI runs a fresh-clone, no-key demo job on every PR.
- `docs/JUDGES.md` must be accurate; every command in it is executed before release.
- Slightly more code in the client (context, budgets, breaker, deadline) for a much safer demo.

## Alternatives considered
- Hosted only: fails the "public repo with instructions" requirement once keys expire.
- Ask judges for their own key: they won't have one.
