# What only you can do

These steps need your accounts. Nothing here is automated, and no step needs the key to appear in a file, chat or command line.

- [ ] **1. Merge the import PR, then make the repo public.** The code is pushed to https://github.com/nexuszephyr/Blindspot on branch
  `chore/import-phase1`; this step is done once you merge that PR. The `<repo-url>` placeholders are
  replaced (F8 closed). Making the repository public is your call (CodeQL starts once it is).

- [x] **2. Deploy on Render (one step).** Done 2026-10-16: https://blindspot-tva4.onrender.com, `BLINDSPOT_URL` set.
  Done 2026-10-19: UptimeRobot monitor on `/healthz` every 5 minutes (the real keep-alive; GitHub's scheduled runs are 2 to 4.5 hours apart), and the cold start measured at about 60 s from a phone.
  Original steps, kept for a redeploy: In Render, choose **New → Blueprint** and pick the repo. Render reads `render.yaml`: a free web service, Docker, region `virginia`, health check `/healthz`.
  - When it asks, set these environment variables in the Render dashboard:

    | Variable | Value |
    |---|---|
    | `QLOO_API_KEY` | your hackathon key (secret) |
    | `ALLOWED_ORIGINS` | empty for now (the UI comes in Phase 2) |

  - Already set by `render.yaml`, so leave them unless you want to change them: `IP_HASH_SALT` (generated), `QLOO_BASE_URL`, `QLOO_MODE=live`, `QLOO_ENABLED=true`, `TRUSTED_PROXY_HOPS=1`, `QLOO_RPS=4`, `REQUEST_DEADLINE_S=8`, `QLOO_DAILY_LIVE_CAP=150`, `QLOO_CLIENT_HOURLY_CAP=30`, `QLOO_RUN_CAP=150`, `QLOO_DEMO_RESERVE=50`, `QLOO_MAX_LIVE_CALLS=100000`, `QLOO_SNAPSHOT_PATH=/etc/secrets/snapshot.json`.
  - Then follow the checks in `docs/JUDGES.md` → "Deploying the hosted demo": `/healthz` from a phone hotspot, `/status` showing no secrets, the `X-Forwarded-For` check, and the cold-start timing.
  - Keep it warm: set the GitHub repo variable `BLINDSPOT_URL`, and add a 5-minute `HEAD /healthz` monitor.

- [ ] **3. Start Docker Desktop**, then run `make docker-scan`, or `python scripts/scan_secrets.py --docker blindspot:local` after `docker build -t blindspot:local .`. That closes the image-scan gate, which is pending because Docker was off.

- [ ] **4. Ask Qloo about committing a snapshot.** Post this in the Qloo Discord, `#api-help`:

  > Hi! For the Qloo hackathon I'm building Blindspot, an agent that audits other AI agents for cultural blind spots using Qloo insights as ground truth. Judges need to run it without a key, and keys are deactivated after judging. The developer guide says not to store Qloo response data in a public repository, so today my public repo ships no Qloo data, and the no-key mode only works through the hosted demo.
  >
  > Would it be acceptable to commit a **small, minimal snapshot** (about 50 cached query results with entity ids, names, types and affinity/popularity scores only, no descriptions, images or other properties), clearly labelled as Qloo data and used only for the offline demo? If not, is keeping it as a private secret file on the hosting server (never in git) fine?
  >
  > Also, is the 10,000 calls/month limit on hackathon keys final for the judging period, or can it be raised for a public demo? Thanks!

- [ ] **5. (Optional) Decide on the `video_game` spelling.** The live API accepts `urn:entity:videogame` and rejects `urn:entity:video_game`, the opposite of the guide. I updated `CLAUDE.md` to match the real API (decisions D29). Revert that line if you disagree.

- [ ] **#25: OpenAPI at `/docs`.** The issue's acceptance criteria want the new routes listed at `/docs`, but D19 keeps
  `/docs`, `/redoc` and the OpenAPI schema off (small attack surface, `CSP default-src 'none'` also blocks the Swagger UI assets).
  Options: (a) keep it off and document the routes in architecture Section 12 (done; my recommendation);
  (b) serve only `/api/openapi.json` (schema, no UI); (c) enable `/docs` and relax the CSP for that path.

- [ ] **#27: AGENTS.md repo map.** AGENTS.md is protected, so the build loop didn't edit it. Suggested change
  to the `targets/` line in Section 4: `targets/  target models (#27, D50): Target protocol, OpenAI-compatible
  client, tolerant parser, response cache, `blindspot target` CLI`; and add `targets.yaml` (target models,
  env variable names only) to the `config/` line. Also add `target` to the CLI groups on the `cli.py` line.

- [ ] **#27: two real targets.** Run locally with `GROQ_API_KEY` and `GEMINI_API_KEY` in `.env`:
  `blindspot scenarios build`, then `blindspot target ask --target gpt-oss-20b --scenario <a demo scenario id>`
  and the same with `--target gemini-flash`. Each should print 1 to k names; report the outcome and token lines
  (not the names) in the PR.
