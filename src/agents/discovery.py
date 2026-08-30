"""Agent 1 — Competitor Discovery.

Issues several complementary you.com queries, then has the LLM reconcile the
results into a ranked competitor list. Multiple query angles matter: a single
"X competitors" search skews heavily toward whoever won that SEO page.
"""

from __future__ import annotations

import logging

from src.llm import structured
from src.prompts import DISCOVERY_SYSTEM, DISCOVERY_USER
from src.state import AgentError, Competitor, CompetitorList, Evidence, ResearchState
from src.tools.youcom import SearchBudget, YouComError, search

logger = logging.getLogger(__name__)
AGENT = "discovery"


def _queries(company: str, industry: str | None) -> list[str]:
    qs = [
        f"{company} competitors",
        f"{company} alternatives comparison",
        f"companies like {company}",
    ]
    if industry:
        qs.append(f"{industry} market leaders {company}")
    else:
        qs.append(f"{company} vs competitors market share")
    return qs


def _same_company(a: str, b: str) -> bool:
    """Loose alias check — catches 'Meta' vs 'Meta Platforms, Inc.'."""
    na, nb = _norm(a), _norm(b)
    return na == nb or na in nb or nb in na


def _norm(name: str) -> str:
    out = name.lower().strip()
    for suffix in (" inc", " inc.", " llc", " ltd", " corp", " corporation", " co.", " platforms"):
        if out.endswith(suffix):
            out = out[: -len(suffix)]
    return out.replace(",", "").replace(".", "").strip()


def discover_competitors(state: ResearchState, budget: SearchBudget) -> dict:
    target = state["target_company"]
    industry = state.get("industry_hint")
    want = state.get("max_competitors", 3)

    evidence: list[Evidence] = []
    logs = []
    errors: list[AgentError] = []

    for q in _queries(target, industry):
        try:
            hits, log = search(q, facet="positioning", budget=budget, agent=AGENT)
            evidence.extend(hits)
            logs.append(log)
        except YouComError as exc:
            errors.append(AgentError(agent=AGENT, message=f"search failed for {q!r}: {exc}"))

    if not evidence:
        return {
            "competitors": [],
            "search_log": logs,
            "errors": errors
            + [
                AgentError(
                    agent=AGENT,
                    message="No search evidence retrieved — cannot discover competitors.",
                    recoverable=False,
                )
            ],
        }

    rendered = "\n\n".join(e.as_prompt_line(i) for i, e in enumerate(evidence, 1))
    industry_line = f"Industry hint: {industry}" if industry else ""

    try:
        result: CompetitorList = structured(CompetitorList).invoke(
            [
                {"role": "system", "content": DISCOVERY_SYSTEM},
                {
                    "role": "user",
                    "content": DISCOVERY_USER.format(
                        target_company=target,
                        industry_line=industry_line,
                        max_competitors=want,
                        evidence=rendered,
                    ),
                },
            ]
        )
    except Exception as exc:  # noqa: BLE001 — fail soft, the run continues
        logger.exception("discovery LLM call failed")
        return {
            "competitors": [],
            "search_log": logs,
            "errors": errors
            + [AgentError(agent=AGENT, message=f"LLM failed: {exc}", recoverable=False)],
        }

    # Post-filter: the prompt forbids returning the target, but never trust that alone.
    kept: list[Competitor] = []
    for c in result.competitors:
        if _same_company(c.name, target):
            continue
        if any(_same_company(c.name, k.name) for k in kept):
            continue
        kept.append(c)

    kept.sort(key=lambda c: c.confidence, reverse=True)
    kept = kept[:want]

    if len(kept) < want:
        errors.append(
            AgentError(
                agent=AGENT,
                message=f"Found {len(kept)} credible competitors, requested {want}. "
                f"Proceeding with what the evidence supports.",
            )
        )

    logger.info("discovered %d competitors: %s", len(kept), [c.name for c in kept])
    return {"competitors": kept, "search_log": logs, "errors": errors}
