# References

Sources this project relies on. Anything about Qloo's behavior that matters for code must also be
recorded, with evidence, in `docs/qloo_api_reality.md`.

## Hackathon

- Qloo Agentic Hackathon (Devpost): https://qloo.devpost.com/ (rules, judging criteria, deadline)
- 2025 Qloo LLM Hackathon (previous edition): https://qloo-hackathon.devpost.com/
- 2025 project gallery: https://qloo-hackathon.devpost.com/project-gallery
- Qloo Hackathon Discord, channel `#api-help` (quota increases, API questions)

Submission requirements: a hosted, working demo; a public repo with all source and run instructions;
a text description; an open-source license visible in the repo's About section.

## Qloo API

- Hackathon developer guide: https://docs.qloo.com/reference/qloo-llm-hackathon-developer-guide
- Qloo API docs home: https://docs.qloo.com/
- Pages to read from the docs site: Parameter Overview, Parameter Reference, **Entity Type Parameter
  Guide** (which parameters each `filter.type` supports), Tags Search API, and the FAQ listing valid
  audience URNs
- Qloo insights terminology (affinity, popularity, audiences, heatmaps):
  https://www.qloo.com/resources/insights-by-qloo-defining-essential-terms-and-functionality
- Qloo location and geographic analysis:
  https://www.qloo.com/solutions/location-geographic-analysis
- Qloo data science and privacy approach: https://qloo.com/data-science/
- Qloo Series C post ("reverting to the mean" framing): https://qloo.com/press/series-c-press-release/

### Machine-readable helpers (third party, unofficial)

- `@devma/qloo` TypeScript SDK (ships an `LLM.txt`): https://app.unpkg.com/@devma/qloo@1.0.1/files/README.md
- `@buildwithlayer/qloo-mcp` tool definitions:
  https://app.unpkg.com/@buildwithlayer/qloo-mcp@0.0.4/files/dist/tools.json

Treat third-party material as hints only. It disagreed with the live API at least once
(see [ADR 0003](adr/0003-videogame-entity-type.md)).

## Prior work and positioning

### Previous Qloo hackathon winners (2025)

| Project | Place | What it did |
|---|---|---|
| GeoTaste | 1st | Location-based business consultant agent with market analysis and interactive visualizations |
| Resonance AI | Honorable mention | Political campaign messaging using Qloo cultural mapping |
| Alloy | Honorable mention | Details not researched |
| Zesty | Honorable mention | Details not researched |

Resonance AI write-up: https://devpost.com/software/resonance-ai-1hjbf0

### Related entries in the current hackathon (seen while researching)

- qloo-mcp / Taste Match: https://glama.ai/mcp/servers/NoBanks/qloo-mcp
- Taste-Context Brief: https://glama.ai/mcp/servers/weioai/qloo-audience-brief
- Booker: https://glama.ai/mcp/servers/danielhagever/booker

None of these audits other agents. Re-check the gallery before submission.

### Research on cultural bias in LLM recommendations

- Cultural Authenticity: Comparing LLM Cultural Representations to Native Human Expectations
  (finds Western-centric calibration): https://arxiv.org/abs/2604.03493
- Auditing Preferences for Brands and Cultures in LLMs: https://arxiv.org/pdf/2603.18300
- Revealing Potential Biases in LLM-Based Recommender Systems in the Cold Start Setting:
  https://arxiv.org/abs/2508.20401
- DiSCo: measuring cultural preference bias in LLMs: https://arxiv.org/pdf/2609.10253

How Blindspot differs: it uses a live behavioral taste graph as ground truth, an agent that hunts for
weak cells and confirms them statistically, and a fix that is re-tested on the same exam.

## Tooling

- FastAPI: https://fastapi.tiangolo.com/
- HTTPX: https://www.python-httpx.org/
- Pydantic: https://docs.pydantic.dev/
- RESPX (HTTPX mocking): https://lundberg.github.io/respx/
- Ruff: https://docs.astral.sh/ruff/
- pip-audit: https://github.com/pypa/pip-audit
- Render Blueprints (`render.yaml`): https://render.com/docs/blueprint-spec
- AGENTS.md convention: https://agents.md/
