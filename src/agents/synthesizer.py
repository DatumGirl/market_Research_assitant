"""Agent 4 — Synthesis (supporting).

Runs once, after every profile exists. Produces the cross-competitor comparison
matrix, whitespace analysis, strategic implications, and executive summary.
Uses the synthesis-tier model: this is the one call where reasoning quality
justifies the cost.
"""

from __future__ import annotations

import logging

from src.llm import structured
from src.prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER
from src.state import AgentError, ComparisonMatrix, CompetitorProfile

logger = logging.getLogger(__name__)
AGENT = "synthesizer"


def _render_profile(p: CompetitorProfile) -> str:
    lines = [f"### {p.name}  (status: {p.status}, evidence: {p.evidence_count})",
             f"Snapshot: {p.snapshot}"]

    if p.pricing_tiers:
        tiers = "; ".join(f"{t.tier_name}: {t.price} ({t.includes})" for t in p.pricing_tiers)
        lines.append(f"Pricing: {tiers}")
    else:
        lines.append(f"Pricing: {p.pricing_note or 'Unknown'}")

    if p.core_features:
        lines.append("Features: " + "; ".join(f"{f.name} — {f.why_it_matters}" for f in p.core_features))

    pos = p.positioning
    lines.append(
        "Positioning: segment={}, ICP={}, differentiation={}, geography={}, GTM={}".format(
            pos.segment or "Unknown",
            pos.ideal_customer or "Unknown",
            pos.differentiation or "Unknown",
            pos.geography or "Unknown",
            pos.go_to_market or "Unknown",
        )
    )

    if p.recent_news:
        lines.append(
            "Recent news: "
            + "; ".join(f"{n.date or 'undated'} {n.headline} ({n.so_what})" for n in p.recent_news)
        )

    if p.threats:
        lines.append("Threats: " + "; ".join(p.threats))
    if p.opportunities:
        lines.append("Opportunities: " + "; ".join(p.opportunities))

    return "\n".join(lines)


def synthesize(
    target_company: str, profiles: dict[str, CompetitorProfile]
) -> tuple[ComparisonMatrix | None, list[AgentError]]:
    usable = {k: v for k, v in profiles.items() if v.status != "failed"}

    if not usable:
        return None, [
            AgentError(
                agent=AGENT,
                message="No usable profiles — synthesis skipped.",
                recoverable=False,
            )
        ]

    rendered = "\n\n".join(_render_profile(p) for p in usable.values())

    try:
        matrix: ComparisonMatrix = structured(ComparisonMatrix, role="synthesis").invoke(
            [
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {
                    "role": "user",
                    "content": SYNTHESIS_USER.format(
                        target_company=target_company, profiles=rendered
                    ),
                },
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesis failed")
        return None, [AgentError(agent=AGENT, message=f"Synthesis failed: {exc}")]

    logger.info("synthesized comparison across %d competitors", len(usable))
    return matrix, []
