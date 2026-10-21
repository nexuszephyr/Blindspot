# The Claude loop

Claude Code builds ready issues unattended in GitHub Actions, reviews its own PRs in a separate run,
and turns on auto-merge when a review round finds nothing. GitHub merges only once every required
check is green. The maintainer chose to allow this (AGENTS.md section 12).

## Workflows

| Workflow | When | What it does |
|---|---|---|
| `claude-next.yml` | Every 3 hours, when a `claude` PR merges, or by hand (optionally with an issue number) | Picks the next ready issue, builds it with tests and docs, and opens a PR labelled `claude`. Waits while any `claude` PR is still open |
| `claude-review.yml` | When a `claude` PR is opened, updated or labelled | Reviews it as a fresh reviewer, fixes and pushes, and posts a verdict: `READY` (turns on auto-merge), `FIXED` (the push triggers the next round) or `NEEDS-HUMAN`. After 4 rounds without a clean one, it hands the PR to the maintainer |
| `protected-paths.yml` | Every PR, from anyone | A required check. It fails if a PR touches `data/`, or touches a sensitive file without the `human-approved` label |

**Ready issue:** labelled `claude-ready`, with no open "Blocked by" links, and none of `needs-live`,
`needs-user`, `needs-human` or `claude-working`. Order: `critical-path` first, then P0, P1, P2, then
the lowest number.

**Sensitive files:** `.github/`, `AGENTS.md`, `CLAUDE.md`, `docs/adr/`, `render.yaml`, `Dockerfile`,
`pyproject.toml`, `config/personas.yaml`, `config/resolved_ids.lock.json`, the Qloo client, params,
budget and redaction modules, `service/security.py` and `scripts/scan_secrets.py`. The loop is also
told never to edit `.github/`.

## Labels

| Label | Meaning |
|---|---|
| `claude-ready` | The maintainer has cleared the issue for the loop |
| `claude` | The loop opened this PR; only these get reviewed and auto-merged |
| `claude-working` | The loop is building the issue now |
| `needs-live` | The issue needs live Qloo calls. The loop comments with the prompt to run locally and skips it |
| `needs-human` | A maintainer decision is needed (AGENTS.md section 13, unclear criteria, a failed run, or too many review rounds) |
| `human-approved` | A human has read the sensitive files in this PR; Protected paths passes |

## Secrets and settings

- `CLAUDE_CODE_OAUTH_TOKEN`: Claude Code's token for `anthropics/claude-code-action`.
- `BOT_PAT`: a personal access token used to push, open PRs and edit labels. Pushes made with a PAT
  start CI; ones made with the default token don't.
- No Qloo key is stored in GitHub. Every loop job runs with `QLOO_MODE=replay` and `QLOO_ENABLED=false`.
- Auto-merge needs "Allow auto-merge" turned on in the repository settings, and branch protection on
  `main` that requires the CI checks and **Protected paths**.

## Off switch

Set the repository variable `CLAUDE_LOOP` to `off` (Settings → Secrets and variables → Actions →
Variables). Both loop workflows skip while it is `off`; Protected paths keeps running.

## What stays with the maintainer

Issues that need live Qloo calls (labelled `needs-live`), anything in AGENTS.md section 13, every PR
that touches a sensitive file, and every merge outside the loop's own `claude` PRs.
