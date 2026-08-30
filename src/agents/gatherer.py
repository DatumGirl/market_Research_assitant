"""Agent 2 — Data Gathering.

Collects fresh web + news evidence for ONE competitor across fixed facets.
Facets are fixed rather than LLM-chosen so that every competitor is researched
along the same axes — otherwise the comparison matrix has holes that look like
findings but are really just uneven research.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from src.state import AgentError, Evidence, Facet, SearchLogEntry
from src.tools.youcom import SearchBudget, YouComError, search, search_news

logger = logging.getLogger(__name__)
AGENT = "gatherer"

# (facet, query template, use_news_endpoint)
FACET_QUERIES: list[tuple[Facet, str, bool]] = [
    ("pricing", "{name} pricing plans cost per month", False),
    ("features", "{name} product features platform capabilities", False),
    ("positioning", "{name} target customers market positioning who it is for", False),
    ("funding", "{name} funding valuation employees revenue", False),
    ("news", "{name}", True),
]


def gather_for_competitor(
    competitor_name: str, budget: SearchBudget
) -> tuple[list[Evidence], list[SearchLogEntry], list[AgentError]]:
    evidence: list[Evidence] = []
    logs: list[SearchLogEntry] = []
    errors: list[AgentError] = []

    def run_one(spec: tuple[Facet, str, bool]):
        facet, template, is_news = spec
        query = template.format(name=competitor_name)
        if is_news:
            return search_news(query, budget=budget, agent=AGENT)
        return search(query, facet=facet, budget=budget, agent=AGENT)

    # Facets are independent — fan them out.
    with ThreadPoolExecutor(max_workers=len(FACET_QUERIES)) as pool:
        futures = {pool.submit(run_one, spec): spec for spec in FACET_QUERIES}
        for fut, spec in futures.items():
            facet = spec[0]
            try:
                hits, log = fut.result()
                evidence.extend(hits)
                logs.append(log)
            except YouComError as exc:
                errors.append(
                    AgentError(
                        agent=AGENT,
                        competitor=competitor_name,
                        message=f"{facet} search failed: {exc}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    AgentError(
                        agent=AGENT,
                        competitor=competitor_name,
                        message=f"{facet} gather error: {exc}",
                    )
                )

    evidence = _dedupe(evidence)
    logger.info("gathered %d evidence items for %s", len(evidence), competitor_name)
    return evidence, logs, errors


def _dedupe(items: list[Evidence]) -> list[Evidence]:
    """Same URL often surfaces under several facets. Keep the first occurrence."""
    seen: set[str] = set()
    out: list[Evidence] = []
    for e in items:
        key = e.url.split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
