# Contributing

Blindspot is a hackathon project with a hard deadline, so the process is light but strict about
secrets and judge-runnability. AI coding agents follow [AGENTS.md](AGENTS.md); so do humans.

## Setup

```bash
git clone https://github.com/nexuszephyr/blindspot.git
cd blindspot
make setup            # or see docs/JUDGES.md for the no-make path on Windows
cp .env.example .env  # add your own Qloo hackathon key to use live mode
make test
make demo             # works without a key, from the synthetic snapshot
```

Get a hackathon key through the form linked in the Qloo developer guide. Never share yours.

## Workflow

1. Pick or open an issue. Every change starts from one.
2. Branch from `main`: `p<N>/<topic>` for phase work, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`.
3. Commit in small steps with conventional commits that reference the issue:
   `feat(groundtruth): checkpointed build (#14)`, `fix(client): honor deadline in backoff (#21)`.
4. Before pushing: `make test`, `ruff check .`, `python scripts/scan_secrets.py`.
5. Open a PR that says `Closes #<issue>` and completes the template checklist.
6. CI must pass. `main` is protected: no direct pushes, no force pushes.

## What a good PR includes

- Tests, written to fail first for bugs and security fixes
- No secrets and no real Qloo data (fixtures tiny and trimmed or synthetic)
- All three run modes still working (hosted, own key, no key)
- Docs updated: the phase report, `docs/decisions.md`, or a new ADR in `docs/adr/` when a decision
  changes or other code will depend on it
- `docs/JUDGES.md` still accurate

## Issues and labels

| Label | Use |
|---|---|
| `phase-1` … `phase-7` | Which roadmap phase it belongs to |
| `bug` | Something broken |
| `security` | Secrets, abuse, privacy (report real vulnerabilities privately, see SECURITY.md) |
| `judge-experience` | Anything a judge would notice: speed, errors, confusing UI, setup friction |
| `qloo-api` | Qloo behavior, parameters, quota |
| `needs-user` | Only the maintainer can do it (accounts, dashboards, Discord) |
| `deferred` | Known and accepted for now |
| `docs` | Documentation only |

## Live Qloo calls

The shared key has a 10,000 calls/month quota at 5 requests/second. Use the cache, keep scripts
resumable, and state the expected live-call count in your PR if it makes any.
