"""Shared state and data models. This is the contract between agents.

Rule that governs every model here: an unknown value is `None` with a stated
reason. It is never a plausible-sounding guess.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Facet = Literal["pricing", "features", "positioning", "news", "funding"]


# ---------------------------------------------------------------------------
# Agent 1 output
# ---------------------------------------------------------------------------


class Competitor(BaseModel):
    name: str = Field(description="Company name, as commonly written.")
    description: str = Field(description="One line: what they do.")
    why_competes: str = Field(description="Why this company competes with the target.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence this is a real competitor.")
    source_urls: list[str] = Field(default_factory=list)


class CompetitorList(BaseModel):
    """Structured-output wrapper for Agent 1."""

    competitors: list[Competitor]
    reasoning: str = Field(description="Short note on how the ranking was decided.")


# ---------------------------------------------------------------------------
# Agent 2 output
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """One retrieved snippet. Evidence without a URL is discarded upstream."""

    title: str
    url: str
    snippet: str
    facet: Facet
    published_date: str | None = None
    query: str = Field(default="", description="The query that surfaced this.")

    def as_prompt_line(self, index: int) -> str:
        date = f" ({self.published_date})" if self.published_date else ""
        return f"[{index}] ({self.facet}){date} {self.title}\n    {self.snippet}\n    URL: {self.url}"


# ---------------------------------------------------------------------------
# Agent 3 output
# ---------------------------------------------------------------------------


class PricingTier(BaseModel):
    tier_name: str
    price: str = Field(description="Verbatim from source, e.g. '$12/user/mo'.")
    includes: str
    citation: int | None = Field(default=None, description="Index into the evidence list.")


class Feature(BaseModel):
    name: str
    why_it_matters: str
    citation: int | None = None


class NewsItem(BaseModel):
    date: str | None
    headline: str
    so_what: str = Field(description="One line on why this matters competitively.")
    url: str
    is_recent: bool = Field(default=True, description="False = historical context, not recent news.")


class Positioning(BaseModel):
    segment: str | None = None
    ideal_customer: str | None = None
    differentiation: str | None = None
    geography: str | None = None
    go_to_market: str | None = None
    unknown_reason: str | None = Field(
        default=None, description="Why fields are null, if they are."
    )


class CompetitorProfile(BaseModel):
    name: str
    snapshot: str = Field(description="One paragraph: what they do, who for, scale.")

    pricing_tiers: list[PricingTier] = Field(default_factory=list)
    pricing_note: str | None = Field(
        default=None,
        description="Set when pricing is not public, e.g. 'Not publicly disclosed; sales-led.'",
    )

    core_features: list[Feature] = Field(default_factory=list)
    positioning: Positioning = Field(default_factory=Positioning)
    recent_news: list[NewsItem] = Field(default_factory=list)

    threats: list[str] = Field(default_factory=list, description="Threats to the target company.")
    opportunities: list[str] = Field(default_factory=list)

    evidence_count: int = 0
    status: Literal["ok", "partial", "failed"] = "ok"


# ---------------------------------------------------------------------------
# Agent 4 output
# ---------------------------------------------------------------------------


class ComparisonRow(BaseModel):
    dimension: str = Field(description="e.g. 'Entry price', 'Target segment'.")
    values: dict[str, str] = Field(description="competitor name -> value ('Unknown' allowed).")


class ComparisonMatrix(BaseModel):
    rows: list[ComparisonRow]
    whitespace: list[str] = Field(
        default_factory=list, description="Gaps no competitor currently serves well."
    )
    strategic_implications: list[str] = Field(default_factory=list)
    executive_summary: str = ""


# ---------------------------------------------------------------------------
# Errors + graph state
# ---------------------------------------------------------------------------


class AgentError(BaseModel):
    agent: str
    competitor: str | None = None
    message: str
    recoverable: bool = True


class SearchLogEntry(BaseModel):
    query: str
    endpoint: Literal["search", "news"]
    results: int
    cached: bool = False
    agent: str = ""


class ResearchState(TypedDict, total=False):
    """LangGraph state. Nodes return partial dicts; they never mutate in place."""

    # inputs
    target_company: str
    industry_hint: str | None
    max_competitors: int

    # agent 1
    competitors: list[Competitor]

    # agent 2 — keyed by competitor name
    evidence: Annotated[dict[str, list[Evidence]], operator.or_]

    # agent 3 — keyed by competitor name
    profiles: Annotated[dict[str, CompetitorProfile], operator.or_]

    # agent 4 / 5
    comparison: ComparisonMatrix | None
    briefings: Annotated[dict[str, str], operator.or_]
    executive_summary: str | None

    # bookkeeping
    errors: Annotated[list[AgentError], operator.add]
    search_log: Annotated[list[SearchLogEntry], operator.add]
    started_at: datetime
