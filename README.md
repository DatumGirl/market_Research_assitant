# 🔍 Market Research Agent — Competitor Analysis

A multi-agent system that takes a company name, discovers its top competitors,
researches each one across the live web via the **you.com Search API**, extracts
structured intelligence, and compiles formatted competitor analysis briefings.

Built with **LangGraph** (stateful orchestration) + **LangChain** (tools, model
binding), surfaced through **Streamlit**.

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # add YDC_API_KEY and ANTHROPIC_API_KEY

streamlit run app.py                       # UI
python -m src.graph --company "Notion"     # headless, writes to outputs/
```

Sample output is committed under [`outputs/sample_notion/`](outputs/sample_notion/) —
a real run against live you.com data covering Coda, ClickUp, and Confluence.

---

## Architecture

```
User enters company name (Streamlit)
        │
        ▼
┌───────────────────────────────────────────────┐
│ ORCHESTRATOR — LangGraph StateGraph           │
└───────────────────────────────────────────────┘
        │
        ▼
  Agent 1: Discover competitors ──► [C1, C2, C3]
        │
        ▼  fan out — competitors run concurrently
  Agent 2: Gather web + news evidence ──► evidence[Ci]
        │
        ▼
  Agent 3: Extract structured profile ──► profile[Ci]
        │
        ▼  fan in
  Agent 4: Synthesize comparison matrix
        │
        ▼
  Agent 5: Write briefings
        │
        ▼
  Streamlit renders + exports (Markdown / JSON)
```

| Agent | File | Role |
|---|---|---|
| **Orchestrator** | [`src/graph.py`](src/graph.py) | Owns state, control flow, concurrency, fail-soft recovery |
| **1 — Discovery** | [`src/agents/discovery.py`](src/agents/discovery.py) | Finds top competitors via multiple complementary you.com queries |
| **2 — Gathering** | [`src/agents/gatherer.py`](src/agents/gatherer.py) | Collects web + news evidence across five fixed facets |
| **3 — Extraction** | [`src/agents/extractor.py`](src/agents/extractor.py) | Pricing, features, positioning, news → typed schema |
| **4 — Synthesis** | [`src/agents/synthesizer.py`](src/agents/synthesizer.py) | Comparison matrix, whitespace, strategic implications |
| **5 — Writer** | [`src/agents/writer.py`](src/agents/writer.py) | Renders briefings (deterministic — cannot invent facts) |

### Why the orchestrator is a graph, not a ReAct agent

The pipeline is fixed: discover → research → synthesize → write. Letting an LLM
choose the next step would buy nothing and cost reliability, latency, and
legibility during a demo. The agentic reasoning lives *inside* the nodes; the
control flow around them is deterministic.

### Why Agent 5 is not an LLM

The writer renders a template. If it were a model, it could introduce a fact
absent from the profile — silently undoing the grounding work of Agent 3. Making
it deterministic makes that class of hallucination structurally impossible.

---

## Design decisions worth knowing

**Unknown is null, never a guess.** Every extraction prompt carries a grounding
rule, and the schema allows null everywhere. A briefing with a blank pricing
field is correct; one with an invented price is a project-level failure. The
sample output demonstrates this — ClickUp's profile is marked `partial` and says
so at the top.

**Fixed research facets.** Agent 2 always searches the same five facets (pricing,
features, positioning, funding, news) rather than letting the LLM choose. Uneven
research produces comparison-matrix holes that *look* like findings but are
really just gaps in coverage.

**Fail-soft.** Any competitor branch may fail without sinking the run. Its profile
is marked `failed`, an `AgentError` is recorded, and the remaining briefings
still ship. Two solid profiles beat three where one is hallucinated.

**Search budget + cache.** `SearchBudget` caps queries per run; the disk cache in
[`src/tools/youcom.py`](src/tools/youcom.py) means development reruns cost zero
quota. Cached calls do not consume budget — there is a test for that.

**Citations are validated.** A citation index pointing outside the evidence list
is dropped rather than rendered, because a wrong footnote is worse than none.

---

## Configuration

All tunables live in [`src/config.py`](src/config.py), overridable via `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `YDC_API_KEY` | — | you.com Search API key (required) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required) |
| `MODEL_WORKER` | `claude-sonnet-5` | Per-competitor agents (run N times) |
| `MODEL_SYNTHESIS` | `claude-opus-5` | Single synthesis pass |
| `EFFORT_WORKER` | `medium` | Reasoning depth for worker agents |
| `EFFORT_SYNTHESIS` | `high` | Reasoning depth for synthesis |
| `MAX_SEARCHES_PER_RUN` | `40` | Hard search budget |
| `YOUCOM_CACHE_ENABLED` | `true` | Disk cache for dev reruns |

---

## Project layout

```
├── app.py                     # Streamlit entry point
├── src/
│   ├── graph.py               # Orchestrator — StateGraph wiring + CLI
│   ├── state.py               # ResearchState + all Pydantic models
│   ├── config.py              # Settings (frozen) + test override()
│   ├── llm.py                 # Model factory by role
│   ├── agents/                # The five agents
│   ├── tools/youcom.py        # you.com client — the only HTTP caller
│   └── prompts/               # Versioned prompt templates
├── outputs/sample_notion/     # Committed sample deliverable
└── tests/                     # 21 tests, fully offline
```

---

## Tests

```bash
pytest tests/ -q      # 21 tests
ruff check src/ app.py tests/
```

The suite never touches the live you.com or Anthropic APIs — search responses
come from [`tests/fixtures/`](tests/fixtures/) and the LLM call sites are
stubbed. [`tests/test_graph.py`](tests/test_graph.py) exercises the full
pipeline end to end, including the fail-soft path where one competitor's
extraction crashes and the run still ships the healthy briefings.

---

## Notes on the you.com API

The client targets `https://api.you.com/v1/search` (you.platform) with an
`X-API-Key` header. Two behaviors are worth knowing, both handled in
[`src/tools/youcom.py`](src/tools/youcom.py):

- **One call returns both a `web` and a `news` bucket.** News retrieval needs no
  second request — `search_news()` reads the news bucket from the same response.
- **The news bucket only populates for news-shaped queries.** A bare company name
  returns web results only, so the client adds the framing itself. The bucket is
  also noisy (a query for a company reliably returns a few unrelated headlines),
  so results that never mention the subject are filtered out before they reach
  the extractor — otherwise the model may attribute someone else's acquisition to
  this competitor.
