# Test fixtures

All files here are **synthetic**. They copy the *shape* of Qloo responses as documented in
docs.qloo.com and the `@devma/qloo` SDK, but every id, name and score is made up.

Real Qloo response bodies are never committed: Qloo's hackathon developer guide says
"Don't store Qloo response data in a public repository." Real recordings stay in `data/`
(gitignored). Once recon has run with a key, `scripts/recon.py --shape-check` compares these
fixtures' field names against the recorded responses, so the shapes stay honest.
