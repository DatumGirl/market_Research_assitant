---
name: market-competitive-intelligence
description: Research a company’s competitive landscape using current web evidence, produce sourced competitor profiles and a comparison, and clearly separate verified facts from unknowns. Use when asked for competitor research, market positioning, pricing comparisons, or strategic landscape analysis.
---

# Market Research Assistant

## Purpose

Turn a target company name into a concise, evidence-grounded competitor analysis. The product’s workflow is: discover competitors → gather web and news evidence → extract structured profiles → synthesize comparisons → render briefings.

Use progressive exposure: begin with the minimum needed to get a useful result, then reveal detail when the user asks for it. Do not overwhelm a first response with implementation architecture or every source snippet.

## Level 1 — Quick start

When the user asks for market or competitor research:

1. Identify the target company. If it is missing, ask for it.
2. Use any supplied industry or geography context; ask only when the missing context would materially change the findings.
3. Confirm the scope briefly: competitor count, market/geography, and desired depth. Use the product defaults when the user does not specify: 2–5 competitors, with the configured default count.
4. Return a short executive overview and the most useful findings first.
5. Make evidence gaps explicit. Never fill them with plausible-sounding guesses.

A minimal request can be as simple as: “Analyze competitors for Notion.”

## Level 2 — Standard research

For each requested run, follow the product’s consistent research facets:

- Pricing
- Product features
- Positioning and customers
- Funding and scale
- Recent news

### Workflow

1. **Discover:** Find and rank credible competitors using multiple complementary searches. Exclude the target company, deduplicate aliases, and return fewer candidates rather than inventing competitors.
2. **Gather:** Collect web and news evidence for each competitor across the same facets so comparisons are fair. Prefer primary sources, such as company websites, product documentation, and pricing pages.
3. **Extract:** Convert evidence into structured profiles. Keep claims tied to source evidence and preserve source URLs, titles, and dates.
4. **Synthesize:** Compare competitors, identify meaningful differences and potential whitespace, and distinguish observed facts from strategic interpretation.
5. **Write:** Present the executive summary and competitor briefings in a clear, repeatable format.

The repository’s implementation uses you.com Search API as its primary retrieval source, LangGraph for deterministic orchestration, LangChain for model/tool integration, and Streamlit for the interface. Do not silently substitute another primary search provider when changing or describing the product.

### Standard deliverable

- Executive summary
- Competitor comparison, including the most relevant feature, pricing, and positioning differences
- One concise profile per competitor
- Source list or citations that let the reader verify material claims
- A short implications section for the target company, labeled as analysis rather than fact

## Level 3 — Evidence and quality rules

These rules apply at every depth:

- **Unknown stays unknown.** Represent unsupported or unavailable data as null, “not found,” or “not publicly disclosed,” with context when helpful. Do not estimate prices, features, customer counts, funding, or market share without evidence.
- **Cite material claims.** Keep claims traceable to evidence. Never create citations or URLs. If citation validation fails, omit the invalid citation and do not imply the claim has been verified.
- **Preserve recency.** Include publication dates when available. Do not present old news as recent; label older material as historical context.
- **Use balanced coverage.** Research the same facets for each competitor. A missing result is a research gap, not proof that the company lacks that capability.
- **Handle noisy search results carefully.** Confirm that an item actually concerns the competitor before attributing it. Be especially cautious with news headlines and similarly named organizations.
- **Report partial failures honestly.** If one competitor has weak evidence or a research step fails, mark that profile as partial or failed and continue with the supported findings for others. Do not let a polished format hide incomplete research.
- **Respect configured search limits and caching.** The application has a per-run search budget and optional disk cache. Do not bypass these controls to force completeness.
- **Protect credentials.** Read API keys from environment configuration. Never include secret values in reports, logs, examples, or committed files.

## Level 4 — Deep-dive mode

Only expand to this level when the user requests detail, asks to inspect evidence, or needs a decision-ready analysis.

For each competitor, organize the deep dive as:

1. **Snapshot:** what the company does, who it serves, and supported scale indicators.
2. **Pricing:** public tiers and terms, or clearly state that pricing was not found or is not public.
3. **Products and features:** evidence-backed capabilities and relevant differentiators.
4. **Positioning:** customer segment, stated value proposition, and go-to-market observations.
5. **Recent developments:** dated, on-topic news; distinguish company announcements from third-party reporting.
6. **Implications:** possible threats, opportunities, and open questions for the target company.
7. **Sources and gaps:** links, dates, and the important information that could not be verified.

When presenting strategic implications, explain the evidence behind the interpretation and avoid making a recommendation sound certain when the evidence is limited.

## Interaction and output behavior

- Start with the requested result, not a long explanation of the underlying multi-agent system.
- Ask a concise follow-up only for information needed to proceed or materially improve the analysis.
- Scale the answer to the request: quick overview first; tables, citations, methodology, and per-source evidence on request or when needed for trust.
- In the Streamlit product, the user supplies a company, optional industry hint, and competitor count. The interface exposes progress, per-competitor results, source evidence, a run log, and Markdown/JSON downloads.
- The CLI supports headless research runs and writes outputs under `outputs/`.
- Do not claim that a live search or research run occurred unless it actually did. When answering from existing repository documentation or sample output, label it accordingly.

## Repository map for maintainers

- `app.py` — Streamlit interface and report exports
- `src/graph.py` — LangGraph workflow and CLI
- `src/state.py` — shared state and typed data models
- `src/agents/` — discovery, gathering, extraction, synthesis, and writing
- `src/tools/youcom.py` — search client, normalization, caching, and query accounting
- `src/prompts/` — agent prompts
- `src/config.py` — runtime settings and limits
- `tests/` — offline tests and fixtures

For code changes, preserve deterministic graph control flow, evidence-grounded structured extraction, fail-soft competitor branches, and offline tests. Run `pytest tests/ -q` and `ruff check src/ app.py tests/` after relevant changes.
