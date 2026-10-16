# Planning scripts

The GitHub issues, milestones and labels, and `docs/issues-guide.md`, all come from `issues_spec.py`.

```bash
# create or update labels, milestones, issues and "blocked by" links (idempotent by title; needs gh auth)
python scripts/planning/create_issues.py scripts/planning
# regenerate the guide from the spec and issue numbers
python scripts/planning/make_guide.py scripts/planning docs/issues-guide.md
```

Change the spec first, run both, and commit the spec, `issue_numbers.json` and the guide together.
These scripts never touch Qloo or any secret.
