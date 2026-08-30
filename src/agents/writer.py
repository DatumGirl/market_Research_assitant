"""Agent 5 — Briefing Writer (supporting).

Deterministic rendering of profiles into the briefing template. Deliberately
NOT an LLM: the writer must not be able to introduce a fact that is absent from
the profile. Formatting is a template's job.
"""

from __future__ import annotations

import re
from datetime import date

from src.state import ComparisonMatrix, CompetitorProfile, Evidence

# The prompt forbids inline citation markers, but a model occasionally emits them
# anyway. The renderer owns footnotes, so strip stray markers rather than render
# "... [10]. [^10]".
_INLINE_CITATION = re.compile(r"\s*\[\^?\d+\](?:\[\^?\d+\])*")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    out = _INLINE_CITATION.sub("", text)
    out = re.sub(r"\s+([.,;:])", r"\1", out)
    # Stripping a marker can strand punctuation next to the punctuation that
    # preceded it — "etc. [11]." collapses to "etc..". Keep the stronger mark:
    # a trailing "!"/"?" is the real terminator, otherwise keep the first.
    out = re.sub(r"([.,;:!?])\s*([.,;:]+)(?![!?])", r"\1", out)
    out = re.sub(r"[.,;:]+\s*([!?])", r"\1", out)
    return out.strip()


STATUS_BANNER = {
    "partial": "> ⚠️ **Partial profile** — limited public evidence was available. "
    "Blank fields are unknown, not zero.",
    "failed": "> ❌ **Research failed** for this competitor. No reliable evidence was retrieved.",
}


def write_briefing(
    profile: CompetitorProfile,
    target_company: str,
    evidence: list[Evidence],
) -> str:
    p = profile
    out: list[str] = []

    out.append(f"# Competitor Briefing: {p.name}")
    out.append(
        f"_Prepared for: {target_company} · Generated: {date.today().isoformat()} "
        f"· Sources: {p.evidence_count}_"
    )
    if p.status in STATUS_BANNER:
        out.append("")
        out.append(STATUS_BANNER[p.status])

    out.append("\n## Snapshot")
    out.append(_clean(p.snapshot) or "_Not available._")

    # --- pricing ---------------------------------------------------------
    out.append("\n## Pricing")
    if p.pricing_tiers:
        out.append("| Tier | Price | Includes |")
        out.append("|---|---|---|")
        for t in p.pricing_tiers:
            cite = f" [^{t.citation}]" if t.citation else ""
            out.append(f"| {_clean(t.tier_name)} | {_clean(t.price)}{cite} | {_clean(t.includes)} |")
    else:
        out.append(_clean(p.pricing_note) or "_Not publicly disclosed._")

    # --- features --------------------------------------------------------
    out.append("\n## Core Features")
    if p.core_features:
        for f in p.core_features:
            cite = f" [^{f.citation}]" if f.citation else ""
            out.append(f"- **{_clean(f.name)}** — {_clean(f.why_it_matters)}{cite}")
    else:
        out.append("_No feature detail found in the retrieved evidence._")

    # --- positioning -----------------------------------------------------
    out.append("\n## Market Positioning")
    pos = p.positioning
    fields = [
        ("Segment", pos.segment),
        ("Ideal customer", pos.ideal_customer),
        ("Differentiation", pos.differentiation),
        ("Geography", pos.geography),
        ("Go-to-market", pos.go_to_market),
    ]
    known = [(label, value) for label, value in fields if value]
    if known:
        for label, value in known:
            out.append(f"- **{label}:** {_clean(value)}")
    else:
        out.append("_Positioning could not be determined from the evidence._")
    if pos.unknown_reason:
        out.append(f"\n_Gaps: {_clean(pos.unknown_reason)}_")

    # --- news ------------------------------------------------------------
    out.append("\n## Recent News (last 12 months)")
    recent = [n for n in p.recent_news if n.is_recent]
    historical = [n for n in p.recent_news if not n.is_recent]
    if recent:
        for n in recent:
            out.append(f"- **{n.date or 'Undated'}** — [{_clean(n.headline)}]({n.url}). {_clean(n.so_what)}")
    else:
        out.append("_No coverage from the last 12 months was retrieved._")
    if historical:
        out.append("\n**Historical context**")
        for n in historical:
            out.append(f"- **{n.date or 'Undated'}** — [{_clean(n.headline)}]({n.url}). {_clean(n.so_what)}")

    # --- implications ----------------------------------------------------
    out.append(f"\n## Strategic Implications for {target_company}")
    if p.threats or p.opportunities:
        for t in p.threats:
            out.append(f"- **Threat:** {_clean(t)}")
        for o in p.opportunities:
            out.append(f"- **Opportunity:** {_clean(o)}")
    else:
        out.append("_Insufficient evidence to draw strategic conclusions._")

    # --- sources ---------------------------------------------------------
    if evidence:
        out.append("\n## Sources")
        for i, e in enumerate(evidence, 1):
            date_str = f" ({e.published_date})" if e.published_date else ""
            out.append(f"[^{i}]: {e.title}{date_str} — {e.url}")

    return "\n".join(out)


def write_executive_summary(
    target_company: str, matrix: ComparisonMatrix | None, profiles: dict[str, CompetitorProfile]
) -> str:
    names = list(profiles)
    out = [
        f"# Competitive Landscape: {target_company}",
        f"_Generated: {date.today().isoformat()} · Competitors analyzed: {len(names)}_",
        "",
    ]

    if matrix is None:
        out.append("_Synthesis unavailable — see individual briefings._")
        return "\n".join(out)

    out.append("## Executive Summary")
    out.append(_clean(matrix.executive_summary) or "_Not available._")

    if matrix.rows:
        out.append("\n## Comparison Matrix")
        header = "| Dimension | " + " | ".join(names) + " |"
        out.append(header)
        out.append("|---" * (len(names) + 1) + "|")
        for row in matrix.rows:
            cells = " | ".join(_clean(row.values.get(n, "Unknown")) for n in names)
            out.append(f"| **{_clean(row.dimension)}** | {cells} |")

    if matrix.whitespace:
        out.append("\n## Whitespace & Differentiation Opportunities")
        for w in matrix.whitespace:
            out.append(f"- {_clean(w)}")

    if matrix.strategic_implications:
        out.append(f"\n## Strategic Implications for {target_company}")
        for s in matrix.strategic_implications:
            out.append(f"- {_clean(s)}")

    degraded = [p.name for p in profiles.values() if p.status != "ok"]
    if degraded:
        out.append("\n---")
        out.append(
            f"_Research quality note: {', '.join(degraded)} returned limited evidence. "
            "Treat those sections as directional._"
        )

    return "\n".join(out)
