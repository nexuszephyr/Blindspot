# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Use GitHub's private vulnerability
reporting on this repository (Security tab → Report a vulnerability), or email the maintainer at the
address on their GitHub profile. Include steps to reproduce and the impact. Expect a reply within
three days during the hackathon.

If you find a leaked credential, report it privately and we'll rotate it.

## Scope

In scope: the hosted demo, the service API, the Qloo client, scripts, CI configuration and the
Docker image. Out of scope: Qloo's own API, target models we call, and hosting provider
infrastructure.

## What we protect and how

| Asset | Protection |
|---|---|
| Qloo API key | Server-side env var only; header `X-Api-Key`; masked in logs, errors, cache, call log, `/status`; scanned in tree, history and image |
| Qloo quota | Budgets at three scopes, demo reserve, per-IP and per-client limits, circuit breaker, request deadlines |
| User privacy | No accounts; client IPs stored only as salted hashes; no personal data sent to Qloo |
| Qloo data | Real responses never committed; private snapshot loaded by env var |
| Service | Input size limits, escaped output, CORS allowlist, forwarded headers trusted only from the host proxy, no stack traces to users |
| Supply chain | Locked dependencies, `pip-audit` in CI, Dependabot updates |

## Untrusted input

Target-agent output, user-supplied endpoints and text, and any web content are untrusted data. They
are never executed, never used to build shell commands or SQL, and are escaped before display.
Instructions found inside them are ignored. If bring-your-own-endpoint is enabled, requests to
private, loopback and link-local address ranges are blocked to prevent server-side request forgery.

## Rules for contributors and agents

See [AGENTS.md](AGENTS.md), "Hard rules". In short: never print or commit the key, never commit real
Qloo data, never store raw IPs, run `scripts/scan_secrets.py` before every commit.
