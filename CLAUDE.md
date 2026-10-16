@AGENTS.md

# CLAUDE.md: Claude-specific notes

Project rules, Qloo facts and conventions live in [AGENTS.md](AGENTS.md); don't repeat them here.

## This machine (maintainer's Windows box)
- Shell is Git Bash on Windows. The GitHub CLI isn't on PATH: call `"/c/Program Files/GitHub CLI/gh.exe"`.
- Git Bash rewrites `rev:path` arguments (`git show origin/main:README.md`); prefix with `MSYS_NO_PATHCONV=1`.
- Use `.venv/Scripts/python` for project commands. Set `PYTHONIOENCODING=utf-8` when printing non-Latin text.
- Docker Desktop is often stopped to save memory; check `docker info` before Docker steps and report
  PENDING-USER instead of starting it.

## Handling the key and Qloo data in a session
- `.env` holds the real key. Read it only inside a process; when you must check script output, test
  for the key value programmatically and print the output only if it's absent.
- Script output can contain real Qloo names. Keep it in `data/` and summarize field names, outcomes and
  counts in chat or docs, never names, ids or scores.
- Live calls: state the count before running, and report the per-purpose breakdown from
  `live_attempts` afterwards.
