"""Agent 3 — Extraction & Analysis.

Turns one competitor's evidence bundle into a typed CompetitorProfile via
structured output. The grounding rule in the prompt is the single most
important constraint in this project: unknown means null, never a guess.
"""

from __future__ import annotations

import logging
from datetime import date

from src.llm import structured
from src.prompts import EXTRACTION_SYSTEM, EXTRACTION_USER
from src.state import AgentError, CompetitorProfile, Evidence

logger = logging.getLogger(__name__)
AGENT = "extractor"

MIN_EVIDENCE = 3


def extract_profile(
    competitor_name: str, target_company: str, evidence: list[Evidence]
) -> tuple[CompetitorProfile, list[AgentError]]:
    errors: list[AgentError] = []

    if not evidence:
        return (
            CompetitorProfile(
                name=competitor_name,
                snapshot="No evidence could be retrieved for this competitor.",
                status="failed",
            ),
            [
                AgentError(
                    agent=AGENT,
                    competitor=competitor_name,
                    message="No evidence to extract from.",
                    recoverable=False,
                )
            ],
        )

    rendered = "\n\n".join(e.as_prompt_line(i) for i, e in enumerate(evidence, 1))

    try:
        profile: CompetitorProfile = structured(CompetitorProfile).invoke(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {
                    "role": "user",
                    "content": EXTRACTION_USER.format(
                        competitor_name=competitor_name,
                        target_company=target_company,
                        today=date.today().isoformat(),
                        evidence=rendered,
                    ),
                },
            ]
        )
    except Exception as exc:  # noqa: BLE001 — one competitor failing must not sink the run
        logger.exception("extraction failed for %s", competitor_name)
        return (
            CompetitorProfile(
                name=competitor_name,
                snapshot="Extraction failed for this competitor.",
                status="failed",
                evidence_count=len(evidence),
            ),
            [
                AgentError(
                    agent=AGENT,
                    competitor=competitor_name,
                    message=f"LLM extraction failed: {exc}",
                    recoverable=False,
                )
            ],
        )

    profile.name = competitor_name  # never let the model rename the subject
    profile.evidence_count = len(evidence)
    profile.status = _grade(profile, len(evidence))

    if profile.status == "partial":
        errors.append(
            AgentError(
                agent=AGENT,
                competitor=competitor_name,
                message="Thin evidence — profile marked partial.",
            )
        )

    _drop_bad_citations(profile, len(evidence))
    return profile, errors


def _grade(profile: CompetitorProfile, n_evidence: int) -> str:
    """Honest status beats an optimistic one — the briefing surfaces it to the reader."""
    if n_evidence < MIN_EVIDENCE:
        return "partial"
    filled = sum(
        [
            bool(profile.pricing_tiers or profile.pricing_note),
            bool(profile.core_features),
            bool(profile.positioning.segment or profile.positioning.differentiation),
            bool(profile.recent_news),
        ]
    )
    return "ok" if filled >= 3 else "partial"


def _drop_bad_citations(profile: CompetitorProfile, n_evidence: int) -> None:
    """A citation pointing outside the evidence list is worse than none at all."""

    def valid(c: int | None) -> int | None:
        return c if c is not None and 1 <= c <= n_evidence else None

    for tier in profile.pricing_tiers:
        tier.citation = valid(tier.citation)
    for feat in profile.core_features:
        feat.citation = valid(feat.citation)
