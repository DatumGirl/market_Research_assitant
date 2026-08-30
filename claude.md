# CLAUDE.md — Project 3A: Market Research Agent (Competitor Analysis)

Guidance for Claude Code when working in this repository.

---

## 1. Project Overview

A **multi-agent competitor analysis system**. Given a company name, the system
autonomously discovers its top competitors, researches each one across the live
web, extracts structured intelligence, and compiles formatted competitor
analysis briefings.

**Primary search source:** you.com Search API (all web/news retrieval flows
through it — do not substitute another search provider as the primary source).

**Interface:** Streamlit.

**Framework:** LangGraph (stateful orchestration) + LangChain (tools, LLM
bindings). An n8n low-code equivalent is an accepted alternative track, but this
repo is the code track unless stated otherwise.

**Target users:** PMs (competitive intelligence), consultants (client research),
executives (market monitoring), analysts (industry tracking), founders
(landscape analysis), students (learning research workflows).

**Deliverable:** A working research agent producing formatted competitor
briefings, plus sample output covering 2–3 competitors. Submitted as a demo
recording + GitHub link or zip.

**Difficulty:** Beginner to Intermediate. Favor clarity over cleverness — this
code is meant to be read and learned from.

---

## 2. Agent Architecture

Five agents. The Orchestrator owns control flow; the others are stateless
workers invoked as graph nodes.

### Orchestrator Agent
Coordinates the full pipeline, owns the shared state object, handles retries and
partial failures, and assembles the final briefing set. Implemented as the
LangGraph `StateGraph` itself plus a small planning node — not as a
free-roaming ReAct agent. Deterministic control flow beats an LLM guessing the
next step.

### Agent 1 — Competitor Discovery
- **Input:** target company name (optionally: industry hint, geography)
- **Job:** identify the top 3 competitors via you.com search
- **Method:** issue several complementary queries (`"<company> competitors"`,
  `"<company> alternatives"`, `"companies like <company>"`, `"<industry> market
  leaders"`), then have the LLM reconcile and rank candidates by frequency of
  mention and source quality
- **Output:** `List[Competitor]` — name, one-line description, why it competes,
  confidence score, source URLs
- **Guardrails:** never return the target company itself; deduplicate
  aliases/subsidiaries (e.g. "Meta" vs "Facebook"); if fewer than 3 credible
  competitors are found, return what exists and flag it — do not invent names

### Agent 2 — Data Gathering
- **Input:** one competitor
- **Job:** collect fresh web + news evidence
- **Method:** parallel you.com queries across fixed facets — pricing, product
  features, positioning/customers, recent news (last 6–12 months), funding &
  scale
- **Output:** `List[Evidence]` — snippet, url, title, published date, facet tag
- **Guardrails:** always retain the source URL and date with every snippet;
  evidence with no URL is discarded. Prefer primary sources (company site, docs,
  pricing page) over aggregators.

### Agent 3 — Extraction & Analysis
- **Input:** one competitor + its evidence bundle
- **Job:** extract **pricing**, **core features**, **market positioning**, and
  **recent news** into a typed schema
- **Method:** structured output (Pydantic model bound to the LLM), grounded
  strictly in the supplied evidence
- **Output:** `CompetitorProfile`
- **Guardrails:** every non-obvious claim carries a citation index into the
  evidence list. Unknown fields are `null` with a stated reason — never a
  plausible guess. This is the single most important rule in the project.

### Agent 4 — Synthesis (supporting)
Runs once after all profiles exist. Produces the cross-competitor comparison:
feature matrix, pricing spread, positioning map, whitespace/differentiation
opportunities, and a short strategic-implications section for the target
company.

### Agent 5 — Briefing Writer (supporting)
Renders profiles + synthesis into the final Markdown briefing using a fixed
template. Formatting only — it must not introduce facts that are absent from the
profiles.

---

## 3. Pipeline Flow

```
User enters company name (Streamlit)
        │
        ▼
┌───────────────────────────────────────────────┐
│ ORCHESTRATOR (LangGraph StateGraph)           │
└───────────────────────────────────────────────┘
        │
        ▼
  Agent 1: Discover competitors ──► [C1, C2, C3]
        │
        ▼  (fan out, run per competitor)
  Agent 2: Gather data     ──► evidence[Ci]
        │
        ▼
  Agent 3: Extract & analyze ──► profile[Ci]
        │
        ▼  (fan in)
  Agent 4: Synthesize comparison
        │
        ▼
  Agent 5: Write briefings
        │
        ▼
  Streamlit renders + export (Markdown / JSON / PDF)
```

Per-competitor branches are independent — run them concurrently. One
competitor failing must not sink the run; mark it `status: failed` and continue.

---

## 4. State Schema

The graph state is the contract between agents. Keep it in one place.

```python
class ResearchState(TypedDict):
    # inputs
    target_company: str
    industry_hint: str | None
    max_competitors: int            # default 3

    # agent 1
    competitors: list[Competitor]

    # agent 2 — keyed by competitor name
    evidence: dict[str, list[Evidence]]

    # agent 3
    profiles: dict[str, CompetitorProfile]

    # agent 4 / 5
    comparison: ComparisonMatrix | None
    briefings: dict[str, str]       # markdown per competitor
    executive_summary: str | None

    # bookkeeping
    errors: list[AgentError]
    search_call_count: int
    started_at: datetime
```

State is append-only where practical. Nodes return partial dicts for LangGraph
to merge; they never mutate state in place.

---

## 5. Repository Layout

```
market_research_assistant/
├── app.py                     # Streamlit entry point
├── src/
│   ├── graph.py               # StateGraph wiring — the Orchestrator
│   ├── state.py               # ResearchState + all Pydantic models
│   ├── agents/
│   │   ├── discovery.py       # Agent 1
│   │   ├── gatherer.py        # Agent 2
│   │   ├── extractor.py       # Agent 3
│   │   ├── synthesizer.py     # Agent 4
│   │   └── writer.py          # Agent 5
│   ├── tools/
│   │   └── youcom.py          # you.com Search + News API client
│   ├── prompts/               # one .py or .md per agent, versioned
│   └── config.py              # env, model ids, limits
├── outputs/                   # generated briefings (sample output lives here)
├── tests/
└── requirements.txt
```

---

## 6. you.com Integration

All retrieval goes through `src/tools/youcom.py`. No agent calls the HTTP API
directly.

- **Auth:** `YDC_API_KEY` from the environment. Never hardcode; never log it.
- **Endpoint:** `GET https://api.you.com/v1/search` (you.platform), header
  `X-API-Key`, params `query` / `count` / `freshness`. One response carries both
  a `web` and a `news` bucket — there is no separate news endpoint.
- **News quirk:** the news bucket only populates for news-shaped queries, and it
  is noisy. The client frames the query and filters off-topic results; do not
  push either job into a prompt.
- **Client requirements:**
  - retry with exponential backoff on 429/5xx (3 attempts)
  - request timeout (~20s) with graceful degradation
  - an on-disk or in-memory cache keyed by the normalized query — reruns during
    development must not burn quota
  - normalize every provider response into a single `Evidence` shape so agents
    never see raw API JSON
  - a hard per-run search budget from config; the Orchestrator enforces it
- **Query hygiene:** short, keyword-dense queries. One intent per query. Log
  every query issued — query transparency is a demo feature, not just debugging.

---

## 7. Coding Conventions

- Python 3.10+, type hints throughout, `ruff` clean.
- **Every LLM call that produces data uses structured output** (Pydantic +
  `with_structured_output`). Free-text parsing is reserved for the final
  briefing prose.
- Prompts live in `src/prompts/`, never inline in agent logic.
- Agents are pure-ish functions: `state -> partial state`. No hidden globals.
- Model ids, effort levels, and limits come from `config.py`. Use the latest
  Claude models (`claude-opus-5`, `claude-sonnet-5`); default to Sonnet for
  per-competitor worker agents and Opus for synthesis if cost allows.
  **Claude 5 removed `temperature`** — sending it is a 400. Control depth with
  `output_config={"effort": ...}` instead.
- Log at agent boundaries: agent name, input summary, duration, token/search
  cost. The Streamlit UI surfaces these.
- Fail soft. Any agent may return a partial result plus an `AgentError`; the
  pipeline continues.

---

## 8. Output Format

Each competitor briefing follows this template:

```markdown
# Competitor Briefing: {name}
_Prepared for: {target_company} · Generated: {date} · Sources: {n}_

## Snapshot
{one-paragraph overview: what they do, who for, scale}

## Pricing
| Tier | Price | Includes |
|---|---|---|
{rows, or "Not publicly disclosed" with what is known}

## Core Features
- {feature} — {why it matters} [^1]

## Market Positioning
{segment, ICP, differentiation claim, geography, go-to-market motion}

## Recent News (last 12 months)
- **{date}** — {headline}. {so-what in one line} [^2]

## Strategic Implications for {target_company}
- **Threat:** …
- **Opportunity:** …

## Sources
[^1]: {title} — {url}
```

The run also produces a cross-competitor comparison table and a short executive
summary. Every briefing is exportable as Markdown and JSON; PDF is a nice-to-have.

Ship at least one committed sample run under `outputs/` covering 2–3
competitors — it is part of the deliverable, not an afterthought.

---

## 9. Streamlit Interface

Keep it to one screen plus results.

- Input: company name, optional industry hint, competitor count (2–5)
- A **live progress view** showing which agent is running and each you.com query
  as it is issued — the visible agent hand-off is the demo
- Tabbed results: Executive Summary · Comparison Table · one tab per competitor
- Expandable "Sources & Evidence" panel per section
- Download buttons: Markdown, JSON
- Cache the last run in `st.session_state` so tab switching does not re-trigger
  research

---

## 10. Non-Negotiables

1. **No fabrication.** Unknown means `null` plus a reason. A briefing with an
   invented price is worse than one with a blank price field.
2. **Everything is cited.** Claims trace back to a URL in the evidence bundle.
3. **you.com is the primary search source.**
4. **Never commit or log `YDC_API_KEY` or any LLM key.** `.env` stays
   gitignored; ship `.env.example`.
5. **Respect the search budget.** Uncapped fan-out is the fastest way to burn
   quota on a demo day.
6. **Recency matters.** News older than ~12 months is labeled as historical
   context, not "recent news".
7. **Partial results ship.** Two solid profiles beat three where one is
   hallucinated.

---

## 11. Common Commands

```bash
pip install -r requirements.txt
cp .env.example .env            # then add YDC_API_KEY, ANTHROPIC_API_KEY

streamlit run app.py            # launch the UI

python -m src.graph --company "Notion"   # headless run, writes to outputs/

pytest tests/ -v
ruff check src/ app.py
```

---

## 12. Working Notes for Claude

- When adding an agent, wire it into `src/graph.py` **and** extend
  `ResearchState` — a node reading a key no producer writes is the most common
  bug here.
- Prefer editing prompts over adding parsing logic. If extraction is unreliable,
  tighten the Pydantic schema and the prompt's evidence-grounding instruction
  before writing a regex.
- Do not swap the search provider, add a vector store, or introduce a database
  without being asked. Scope creep on a beginner-to-intermediate project costs
  more than it adds.
- Use cached fixtures in `tests/` — the test suite must not hit the live
  you.com API.
