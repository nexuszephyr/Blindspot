# Running Blindspot (for judges)

> Repository: https://github.com/nexuszephyr/Blindspot

There are three ways to run it. The first needs no setup at all.

| Path | You need | First run | What you get |
|---|---|---|---|
| 1. Hosted demo | A browser | Up to about a minute if the free server was asleep, then instant | Everything, with live Qloo data within daily limits |
| 2. Local, your own Qloo key | Python 3.11+, git, a hackathon key | About 80 s (dependency install), then about 3 s | Everything, live, on your quota |
| 3. Local, no key | Python 3.11+, git | About 80 s, then about 3 s | Runs offline; anything not cached comes back `degraded` |

## 1. Hosted demo

URL: **https://blindspot-tva4.onrender.com**. Open [https://blindspot-tva4.onrender.com/healthz](https://blindspot-tva4.onrender.com/healthz) first. If the server was asleep, give it up to a minute.

The hosted daily live-call caps are deliberately low (150 a day, 100 of them for ad-hoc queries), because the
key's 10,000-calls/month quota is shared with development ([decisions D33](decisions.md)). When a cap is hit,
a query answers `served_from: "degraded"` with a `reason` that names the limit; the app keeps working and
cached answers still come back as `cache`.

## 2. Local with your own key

```bash
git clone https://github.com/nexuszephyr/Blindspot.git blindspot
cd blindspot
cp .env.example .env        # then edit .env and set QLOO_API_KEY=<your key>
make demo                   # Windows: mingw32-make demo, or: python scripts/dev.py demo
```

Then open http://127.0.0.1:8000/api/qloo/status. It should show `"mode": "live"` and `"key_configured": true`. The key is only read on the server side and is never printed or logged.

## 3. Local with no key

```bash
git clone https://github.com/nexuszephyr/Blindspot.git blindspot
cd blindspot
make demo                   # Windows: mingw32-make demo, or: python scripts/dev.py demo
```

With no key, the app starts in **replay mode** and never contacts Qloo. It serves only what is in the local cache or snapshot. The public repo ships **no Qloo data**, because Qloo's terms say not to store response data in a public repository, so a fresh clone answers most queries with `served_from: "degraded"` and a message pointing you to the hosted demo or your own key. That's expected behavior, not a crash.

### Try it

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/api/qloo/status
curl "http://127.0.0.1:8000/api/qloo/search?q=Radiohead"
curl "http://127.0.0.1:8000/api/qloo/search?q=Radiohead&qloo=false"
```

### Is port 8000 already in use?

```bash
PORT=8010 make demo                              # Linux/macOS
```
```powershell
$env:PORT = "8010"; python scripts/dev.py demo   # Windows PowerShell
```

### Docker instead of Python

```bash
docker compose up --build                        # http://127.0.0.1:8000
BLINDSPOT_PORT=8010 docker compose up --build    # if 8000 is busy
```

### Run the tests (offline, no key)

```bash
make test                                        # or: python scripts/dev.py test
```

## What the labels mean

Every result says where its data came from in `served_from`:

| `served_from` | Meaning |
|---|---|
| `live` | Fetched from Qloo just now |
| `cache` | An earlier live answer for the same query, stored on this server (default TTL 7 days) |
| `snapshot` | Precomputed demo data shipped privately with the server. It's real Qloo data, but not fresh |
| `disabled` | Qloo was switched off for this request (`qloo=false`, the UI's grounding toggle) or for the whole server. No Qloo data is returned |
| `degraded` | No answer could be produced safely. `reason` says why (offline replay mode, a budget limit, Qloo rate-limiting or down, an invalid query). The app keeps working |

Cached data is never labelled `live`.

### Why you might see `degraded`

The hosted server protects a shared Qloo quota. Limits are per visitor per hour and per run, with a global daily cap. Part of the daily quota is reserved so the precomputed demo keeps working even during a traffic spike. If Qloo starts rate-limiting, a circuit breaker pauses live calls for about 30 s and then recovers on its own.

## Platform notes

- Linux and macOS: `make` and `python3` (3.11+) are needed.
- Windows: `make` isn't standard. Use `python scripts/dev.py demo` (tested in Git Bash and PowerShell) or `mingw32-make demo`. WSL also works.

---

## Deploying the hosted demo (owner only)

Status: **DONE 2026-10-16** (#8). What was actually done:
a Render Blueprint from `render.yaml` (free Docker web service, region `virginia`, health check `/healthz`,
auto-deploy on every commit to `main`), `QLOO_API_KEY` set in the Render dashboard, and the GitHub repo variable
`BLINDSPOT_URL` set for the keepalive workflow. The steps below are kept for a redeploy.

1. The code is on GitHub at https://github.com/nexuszephyr/Blindspot (private until the maintainer makes it public). Render needs
   access to it: connect your GitHub account in Render and grant it this repository.
2. In Render: **New → Blueprint**, pick the repo, and Render reads `render.yaml` (a free web service, Docker, region `virginia`, health check `/healthz`).
3. When it asks for env vars, set **`QLOO_API_KEY`** to the hackathon key. Leave `ALLOWED_ORIGINS` empty until the UI exists. `IP_HASH_SALT` is generated for you. The rest come from `render.yaml`.
4. Optional, but recommended once Phase 2 has a demo set:
   - Locally, run `python scripts/warm_cache.py` and then `python scripts/export_snapshot.py`.
   - In Render, go to **Environment → Secret Files**, add a file named `snapshot.json`, and paste the contents of `data/snapshot/snapshot.json`.
   - Never commit that file.
5. Verify from a **different network** (for example a phone hotspot):
   - `curl https://<app>.onrender.com/healthz` returns `{"ok":true,"mode":"live"}`.
   - `curl https://<app>.onrender.com/api/qloo/status` shows `"key_configured": true` and no key or salt.
   - Its `your_client_id` differs between the phone and the laptop, and doesn't change when you add `-H "X-Forwarded-For: 1.2.3.4"` or between repeated requests. Render sits behind Cloudflare, so `TRUSTED_PROXY_HOPS=2` (#80).
   - Or run all checks at once: `python scripts/verify_deploy.py https://<app>.onrender.com` (about 1 live Qloo call).
6. Measure the cold start: leave the app idle for at least 20 minutes, then run `curl -o /dev/null -w "%{time_total}\n" https://<app>.onrender.com/healthz`.
7. Keep it warm during judging: in GitHub, go to **Settings → Secrets and variables → Actions → Variables** and set `BLINDSPOT_URL=https://<app>.onrender.com`. That turns on `.github/workflows/keepalive.yml`, but GitHub runs scheduled workflows **best-effort**: observed gaps of 2 to 4.5 hours between runs, while the free instance sleeps after about 15 minutes. The real keep-alive is an external 5-minute `HEAD /healthz` monitor (for example UptimeRobot).

### Hosted verification log (2026-10-17)

`python scripts/verify_deploy.py https://blindspot-tva4.onrender.com` from the maintainer's network:

| Check | Result |
|---|---|
| `/healthz` 200, `ok`, `mode: live` | PASS (821 ms warm) |
| `/api/qloo/status`: key configured, breaker closed | PASS |
| `/api/qloo/status` caps match `render.yaml` | PASS (daily 150, hourly 30, run 150, reserve 50) |
| `/api/qloo/status` leaks nothing (key shapes, paths, hostnames, raw IPs) | PASS |
| search: `live` then `cache` | PASS (5 results both times; 1 live Qloo call) |
| forged `X-Forwarded-For` ignored | **FAIL**: identity followed the Cloudflare edge address, see #80; passes once `TRUSTED_PROXY_HOPS=2` is deployed |
| unknown route: JSON 404, no traceback | PASS |
| oversized `q` (10 KB): 4xx not 500 | PASS (422) |
| keepalive workflow, manual run | PASS, [run 39057710749](https://github.com/nexuszephyr/Blindspot/actions/runs/39057710749) |
| keepalive scheduled runs | best-effort: 9 runs in 28 hours, 2 to 4.5 hours apart |
| cold start after 20+ min idle | pending, measuring 2026-10-18 from a phone on mobile data |
| disk | ephemeral: budget counters reset to 0 on the #79 redeploy (D33) |

---

## Verification log (2026-10-13, Phase 1)

Every command above was run in a fresh clone that had no `.env`:

| Command | Where | Result |
|---|---|---|
| `git clone …`, `cd blindspot`, `make demo`, the four `curl` lines | Linux (`python:3.12` container) | healthy, `mode: replay`; search → `degraded` with guidance; `qloo=false` → `disabled` |
| `PORT=8010 make demo` | Linux container | healthy on 8010 |
| `cp .env.example .env` then `make demo` (key left empty) | Linux container | replay mode, `key_configured: false` |
| `make test`, `python scripts/dev.py test` | Linux container | 50 passed, 1 skipped (the live test) |
| `mingw32-make demo` | Windows 11, Git Bash | healthy after about 78 s on first run |
| `$env:PORT = "8010"; python scripts/dev.py demo` | Windows 11, PowerShell | healthy on 8010 |
| `BLINDSPOT_PORT=8010 docker compose up --build` | Windows 11, Docker Desktop | healthy on 8010 |
| `docker compose up --build` | Windows 11 | failed here only because port 8000 was taken by another app on the dev machine; the line above is the documented workaround |

Not run yet:
- Path 2 with a **real** key: no key was available. A fake key degrades correctly to "Qloo rejected the server's API key".
- The owner deploy steps.
- macOS: no Mac was available. CI includes `macos-latest`, but the repo hasn't been pushed yet.

---

## Developer commands (moved here from the pre-import README)

With a key in `.env`, these scripts make live calls. All of them share the client's budget, cache and call log, so a rerun of cached queries is free.

```bash
python scripts/smoke.py                 # about 4 live calls
python scripts/recon.py                 # E1-E11, about 200 live calls cold (cap: --max-calls 250)
python scripts/coverage_matrix.py       # about 96 live calls (cap: --max-calls 150)
python scripts/warm_cache.py            # fill the cache from config/demo_queries.json
python scripts/export_snapshot.py       # cache -> data/snapshot/snapshot.json (private, never commit)
python scripts/export_snapshot.py --synthetic   # synthetic snapshot for offline dev (no Qloo data)
```

CLI, no service needed:

```bash
python -m blindspot.qloo.cli budget     # remaining budgets, cache size, breaker state
python -m blindspot.qloo.cli search "Burna Boy" --types urn:entity:artist
python -m blindspot.qloo.cli calls --last 10
```

Windows without `make`: `python scripts/dev.py setup|demo|test|lint|check|audit`.

Cities, domains and demo queries are data in `config/`. Steps only the owner can do are in [NEEDS_FROM_USER.md](NEEDS_FROM_USER.md).

**Data note:** no Qloo response data is committed, because Qloo's hackathon guide says not to store it in a public repository. Test fixtures are synthetic. The cache, recordings and snapshots live in `data/`, which is gitignored.
