# Planning scripts

The GitHub issues, milestones and labels, and `docs/issues-guide.md`, all come from `issues_spec.py`.

```bash
# create or update labels, milestones, issues and "blocked by" links (idempotent by title; needs gh auth)
python scripts/planning/create_issues.py scripts/planning
# preview rendered bodies without touching GitHub
python scripts/planning/create_issues.py scripts/planning --dry P2-7 U7
# regenerate the guide from the spec and issue numbers
python scripts/planning/make_guide.py scripts/planning docs/issues-guide.md
```

Issues are matched by the `**Key:**` marker in their body, so never rename or reuse a key. The
`critical-path` label is computed (zero slack to submission), not set by hand. Change the spec first, run both, and commit the spec, `issue_numbers.json` and the guide together.
These scripts never touch Qloo or any secret.
