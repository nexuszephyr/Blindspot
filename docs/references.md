# References: what to use, what to take, what to avoid

Every source the project relies on, with a verdict. Anything about Qloo behavior that code depends on
must also be recorded with evidence in `docs/qloo_api_reality.md`. When a source and the live API
disagree, **the live API wins** ([ADR 0003](adr/0003-videogame-entity-type.md) is the precedent).

Legend: ✅ use · 🟡 use with care · ⛔ avoid

**Contents:** [Ground rules](#ground-rules) · [Hackathon](#1-hackathon) · [Qloo official](#2-qloo-official) ·
[Unofficial Qloo helpers](#3-unofficial-qloo-helpers) · [Prior and competing projects](#4-prior-and-competing-projects) ·
[Research](#5-research-on-cultural-bias-and-llm-recommenders) · [Metrics and statistics](#6-metrics-and-statistics) ·
[Tooling](#7-tooling) · [Hosting and CI](#8-hosting-and-ci) · [Avoid list](#9-the-avoid-list)

---

## Ground rules

1. **Read official docs by hand.** `docs.qloo.com` disallows automated access (robots.txt). Don't
   scrape it with scripts or agents; read it in a browser and summarize in our own words.
2. **Don't copy code from other projects.** Learn patterns, write our own. Check a project's license
   before reusing even small snippets, and credit it in the README if we do.
3. **Don't copy text.** Summarize sources in our own words; quote at most a short phrase with a link.
4. **Third-party claims are hypotheses** until a recorded live response confirms them.
5. **Cite in the Devpost write-up** any source a judge might ask about (Qloo docs, research framing).

---

## 1. Hackathon

| Source | Verdict | Take | Avoid |
|---|---|---|---|
| [Qloo Agentic Hackathon on Devpost](https://qloo.devpost.com/) | ✅ | Official rules, judging criteria, deadline (2026-10-30), prizes, the Calacanis investment bonus. Re-read the rules page before submitting. | Assuming the rules match last year's. Read this year's. |
| Submission requirements (from the challenge page) | ✅ | Hosted working demo; public repo with all source, assets and run instructions; text description; open-source license visible in the repo's About section. | Submitting a local-only or login-gated demo; it disqualifies. |
| Judging criterion "Technological Implementation" (how thoroughly and skillfully Qloo is used) | ✅ | Use many Qloo features with intent: search, tags, insights with location, audiences, taste signals, trends, candidate scoring. Show it in Truth Receipts. | Feature-stuffing calls that don't change the result. |
| Judges (Qloo CTO, investors, media figures) | 🟡 | Write the description for both a technical and a business reader; have a clear B2B story. | Jargon-only write-ups. |
| Qloo Hackathon Discord (linked from the developer guide's Resources section), channel `#api-help` | ✅ | Quota increases, API questions, the snapshot and `videogame` questions. | Posting keys, logs with keys, or real data dumps. |
| [2025 Qloo LLM Hackathon](https://qloo-hackathon.devpost.com/) | 🟡 | Context on what won and what was common. | Treating its rules or prizes as this year's. |

---

## 2. Qloo official

| Source | Verdict | Take | Avoid |
|---|---|---|---|
| [Hackathon developer guide](https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide) | ✅ | Base URL `https://hackathon.api.qloo.com` only; `X-Api-Key` header; insights is GET with query string; no `/recommendations` or `/recs`; tags and audiences need real IDs; unknown params are silently ignored; keys deactivate after judging; quota raised via Discord. | Its `urn:entity:video_game` spelling: the live API rejects it (ADR 0003). |
| Entity Type Parameter Guide (docs site) | ✅ | Which parameters each `filter.type` supports. Seed `params.py` from it, then promote with evidence. | Assuming a documented parameter changes results; verify first (ADR 0005). |
| Parameter Reference and Parameter Overview (docs site) | ✅ | Formats and meanings of parameters. | Undocumented or deprecated parameters. |
| Tags Search API (docs site) | ✅ | How to find tag IDs for cuisines, genres and interests. | Passing free-text tags. |
| FAQ: valid audience URNs (docs site) | ✅ | The audience list for `signal.demographics.audiences`. | Identity-based audiences by default; they're opt-in only. |
| [Qloo insights terminology](https://www.qloo.com/resources/insights-by-qloo-defining-essential-terms-and-functionality) | ✅ | Meaning of affinity, popularity, audiences, heatmaps. Use Qloo's own words in the UI ("affinity"). | Comparing affinity across different queries (ADR 0001). |
| [Location and geographic analysis](https://www.qloo.com/solutions/location-geographic-analysis) | 🟡 | Framing for the global-coverage story; Qloo's claim that it covers areas large and small. | Treating marketing coverage claims as verified; our coverage probe decides. |
| [Data science and privacy](https://qloo.com/data-science/) | ✅ | The privacy story: anonymized signals, no personal data. Mirror it: Blindspot needs no personal data either. | Overstating Qloo's data sources beyond what the page says. |
| [Series C post](https://qloo.com/press/series-c-press-release/) | ✅ | The "generative AI reverts to the mean" framing, which is exactly what the Sameness metric measures. | Long quotes; paraphrase and link. |
| [Qloo API product page](https://www.qloo.com/products/api) | 🟡 | General positioning. | Any technical detail; use the docs instead. |

---

## 3. Unofficial Qloo helpers

| Source | Verdict | Take | Avoid |
|---|---|---|---|
| [`@devma/qloo` SDK README and `LLM.txt`](https://app.unpkg.com/@devma/qloo@1.0.1/files/README.md) | 🟡 | Parameter names and endpoint shapes as hints; confirmation that the hackathon base URL exists. | Installing it as a dependency (we're Python); trusting its defaults. |
| [`@buildwithlayer/qloo-mcp` tools.json](https://app.unpkg.com/@buildwithlayer/qloo-mcp@0.0.4/files/dist/tools.json) | 🟡 | Enumerated insights parameters and entity types, as a checklist for recon. | It points at a staging URL; never use staging or production with a hackathon key. Its `videogame` vs `video_game` listing isn't evidence either way. |

---

## 4. Prior and competing projects

Learn from them; don't copy them. Differentiation is part of the judging.

### 4.1 2025 winners

| Project | What it did | Take | Avoid |
|---|---|---|---|
| GeoTaste (1st) | Location-based business consultant agent: market analysis, competitive intelligence, interactive visuals | Win pattern: a real enterprise use of Qloo packaged as an agent, with a strong visual and a 10-second explanation. Our Blind Map plays that role. | Building another location-analysis consultant. |
| [Resonance AI](https://devpost.com/software/resonance-ai-1hjbf0) (honorable mention) | Political campaign messaging using Qloo cultural mapping; a political agent and a content agent | A clear vertical and a business story; multi-agent split. | Political or persuasion use cases; sensitive and crowded. |
| Alloy, Zesty (honorable mentions) | Not researched | Look them up in the gallery before writing the Devpost text. | Assuming what they did. |
| [2025 project gallery](https://qloo-hackathon.devpost.com/project-gallery) | Mostly travel, dining, mood and chat assistants | Proof that consumer recommenders were the crowded space. | Any "taste-based recommender chatbot" positioning. |

### 4.2 Current-hackathon projects seen while researching

| Project | Take | Avoid |
|---|---|---|
| [Booker](https://glama.ai/mcp/servers/danielhagever/booker) | Its "How we know" panel (every Qloo call with parameters, status, count and time, plus its own rules labeled separately) validates our Truth Receipts. Its use of `filter.results.entities` to rank a fixed set is the idea behind E14. | Copying its UI or code. |
| [qloo-mcp / Taste Match](https://glama.ai/mcp/servers/NoBanks/qloo-mcp) | Parallel fan-out across domains; resolving seeds to IDs before insights; exposing Qloo as MCP tools for other agents. | Being "another Qloo MCP wrapper"; our MCP export is a by-product, not the product. |
| [Taste-Context Brief](https://glama.ai/mcp/servers/weioai/qloo-audience-brief) | Honesty pattern: labeling fixture vs live output, rate-limit binding required before any spend. It lists an LLM-only vs Qloo comparison as not built, which is our core. | Fixture-only demos; judges must see real data on the hosted demo. |

**Before submission:** browse the full current gallery for any audit or evaluation entry and sharpen
the differentiation paragraph.

---

## 5. Research on cultural bias and LLM recommenders

Use for framing and related-work credibility. Don't overclaim: we measure gaps against Qloo, not
against human ground truth.

| Paper | Verdict | Take | Avoid |
|---|---|---|---|
| [Cultural Authenticity: Comparing LLM Cultural Representations to Native Human Expectations](https://arxiv.org/abs/2604.03493) | ✅ | Evidence of Western-centric calibration that worsens with cultural distance from the US. Motivates testing Lagos, Bengaluru and São Paulo, not just New York. | Claiming our results replicate theirs; different method. |
| [Auditing Preferences for Brands and Cultures in LLMs](https://arxiv.org/pdf/2603.18300) | ✅ | Open-ended recommendation auditing and "superstar" bias toward popular providers. Supports the famous-vs-local metric. | Reusing their benchmark data without checking its license. |
| [DiSCo: distribution-first cultural preference bias](https://arxiv.org/pdf/2609.10253) | ✅ | Measure the distribution of choices, not single right answers; prompt steering alone doesn't fix preference bias. Supports measuring across prompt variants and fixing with grounding, not prompts. | Forced-choice framing; our probes are open-ended. |
| [Biases in LLM-based recommenders in the cold-start setting](https://arxiv.org/abs/2508.20401) | ✅ | Zero-context recommendation audits find cultural stereotypes across music and movies. Supports our neutral persona descriptors. | Sensitive-attribute probing by default. |

How to cite in the write-up: one sentence of related work, then what's new: a live behavioral taste
graph as the answer key, an agent that confirms blindspots statistically, and a fix measured on the
same exam plus a held-out set.

---

## 6. Metrics and statistics

| Concept | Verdict | Take | Avoid |
|---|---|---|---|
| Overlap@k and Jaccard similarity | ✅ | Simple, explainable primary measures. | Reporting overlap without the match rate beside it. |
| nDCG-style rank weighting | ✅ | Credit for matching Qloo's higher-ranked items. | Using Qloo affinity as relevance grades across queries. |
| Inverse-frequency down-weighting of hub entities | ✅ | Stops one famous chain from earning credit everywhere. | Removing hubs entirely; they're real answers too. |
| Bootstrap confidence intervals | ✅ | Confirm a blindspot only when the interval sits below the threshold. | Declaring a blindspot from one or two probes. |
| Upper-confidence-bound cell selection | ✅ | Explore uncertain cells, exploit weak ones, within budget. | Complex bandit machinery we can't explain in one sentence. |
| LLM-as-judge | ⛔ | Nothing for scoring. | Any LLM in the answer key or the score. |

---

## 7. Tooling

| Tool | Verdict | Take | Avoid |
|---|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | ✅ | Service layer, OpenAPI docs, dependency injection for `RequestContext`. | Blocking calls inside async routes. |
| [HTTPX](https://www.python-httpx.org/) | ✅ | Async client with timeouts; the only HTTP library used for Qloo. | Calling Qloo with `requests` or raw httpx outside `qloo/`. |
| [Pydantic](https://docs.pydantic.dev/) and pydantic-settings | ✅ | Typed requests, responses, config files, settings. | `repr` of settings objects in logs (key exposure). |
| SQLite (stdlib) | ✅ | Cache, call log, ground truth; WAL mode and a busy timeout. | Sharing one connection across async tasks unsafely. |
| [RESPX](https://lundberg.github.io/respx/) | ✅ | Mock every Qloo behavior offline. | Live calls in the default test run. |
| [Ruff](https://docs.astral.sh/ruff/) | ✅ | Lint and format. | — |
| [pip-audit](https://github.com/pypa/pip-audit) | ✅ | Dependency vulnerabilities in CI. | Ignoring findings without a note. |
| [gitleaks](https://github.com/gitleaks/gitleaks) | ✅ | History secret scan in CI (run from its container). | The gitleaks GitHub Action; it failed in our CI. |
| [Mermaid](https://mermaid.js.org/) | ✅ | Diagrams in docs; GitHub renders them. | Diagram syntax GitHub's Mermaid version doesn't support; parse-check before committing. |
| [AGENTS.md convention](https://agents.md/) | ✅ | One instruction file for all coding agents; `CLAUDE.md` imports it. | Duplicating rules between `AGENTS.md` and `CLAUDE.md`. |
| [Model Context Protocol](https://modelcontextprotocol.io/) | 🟡 | Format for the exported fix patch. | Building a full MCP server before the core works. |

---

## 8. Hosting and CI

| Source | Verdict | Take | Avoid |
|---|---|---|---|
| [Render Blueprint spec](https://render.com/docs/blueprint-spec) | ✅ | One-step deploy from `render.yaml`; health check on `/healthz`. | Secrets in `render.yaml`; set `QLOO_API_KEY` in the dashboard only. |
| Render free tier behavior | 🟡 | Expect sleep on idle and an ephemeral disk; seed from the snapshot at startup. | Rebuilding ground truth on boot; it burns quota on every cold start. |
| [GitHub Actions](https://docs.github.com/actions) | ✅ | CI on Ubuntu and Windows, fresh-clone job, Docker scan. | Storing the Qloo key as a GitHub secret; CI never calls Qloo. |
| [CodeQL](https://codeql.github.com/) | ✅ | Static analysis once the repo is public. | Running it on the private repo (needs a paid plan). |
| [Dependabot](https://docs.github.com/code-security/dependabot) | ✅ | Weekly pip and Actions updates. | Auto-merging without CI and review. |

---

## 9. The avoid list

Things that look tempting and would hurt the project:

| Don't | Why |
|---|---|
| Use `/recommendations` or `/recs` | Legacy and unsupported for the hackathon |
| Use staging or production base URLs | Hackathon keys return 401 there |
| Send the key as a query parameter or Bearer token | Wrong auth; also leaks into logs and URLs |
| POST to `/v2/insights` | It's GET with a query string |
| Trust an empty 200 as "no data" | Unknown parameters are silently ignored; check params first |
| Pass free-text tags or audiences | They need real IDs or URNs |
| Use age or gender for dining personas | Recorded as ineffective for dining |
| Use the location **signal** for places | Timed out for restaurants in 4 of 6 cities; use the location filter |
| Compare affinity across queries | Not on a shared scale (ADR 0001) |
| Score with an LLM | The answer key must be Qloo |
| Commit real Qloo responses | Terms unknown (ADR 0002) |
| Scrape `docs.qloo.com` | Disallowed by robots.txt; read it by hand |
| Ship fixture-only demos | Judges must see real data on the hosted demo |
| Enable identity-based audiences by default | Risk of stereotyping; opt-in and flagged only |
| Claim "Qloo is the truth" | It's a strong proxy; say so in the write-up |
