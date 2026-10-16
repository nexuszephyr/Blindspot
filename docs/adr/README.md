# Architecture decision records

Short records of decisions that are hard to reverse or easy to forget. Day-to-day decisions go in
`docs/decisions.md`; promote one to an ADR when it shapes the design or other code depends on it.

Copy `0000-template.md`, take the next number, and link the ADR from the PR that implements it.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-rank-based-metrics.md) | Primary metrics are rank-based; affinity only within a query | Accepted |
| [0002](0002-no-real-qloo-data-in-git.md) | No real Qloo data in git; synthetic public snapshot, private real snapshot | Accepted |
| [0003](0003-videogame-entity-type.md) | Follow the live API's `urn:entity:videogame` | Accepted |
| [0004](0004-three-run-modes.md) | Three run modes, all tested, judge-runnable | Accepted |
| [0005](0005-strict-parameter-guard.md) | Strict parameter allowlist with evidence-based promotion | Accepted |
