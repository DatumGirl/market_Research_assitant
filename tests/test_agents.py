"""Agent-level tests: dedupe, grading, citation validation, rendering."""

from src.agents.discovery import _norm, _same_company
from src.agents.extractor import _drop_bad_citations, _grade
from src.agents.gatherer import _dedupe
from src.agents.writer import write_briefing, write_executive_summary
from src.state import (
    ComparisonMatrix,
    CompetitorProfile,
    Evidence,
    Feature,
    NewsItem,
    Positioning,
    PricingTier,
)


def ev(url: str, facet="features") -> Evidence:
    return Evidence(title="t", url=url, snippet="s", facet=facet)


def test_dedupe_ignores_query_string_and_trailing_slash():
    items = [
        ev("https://a.com/page"),
        ev("https://a.com/page/"),
        ev("https://a.com/page?utm=x"),
        ev("https://b.com/other"),
    ]
    assert len(_dedupe(items)) == 2


def test_alias_detection_catches_suffixes():
    assert _same_company("Meta", "Meta Platforms")
    assert _same_company("Acme Inc.", "acme")
    assert not _same_company("Acme", "Globex")


def test_norm_strips_legal_suffixes():
    assert _norm("Globex Corporation") == "globex"


def test_grade_marks_thin_evidence_partial():
    p = CompetitorProfile(name="X", snapshot="s")
    assert _grade(p, n_evidence=1) == "partial"


def test_grade_ok_when_sections_filled():
    p = CompetitorProfile(
        name="X",
        snapshot="s",
        pricing_note="Not disclosed",
        core_features=[Feature(name="f", why_it_matters="w")],
        positioning=Positioning(segment="SMB"),
    )
    assert _grade(p, n_evidence=10) == "ok"


def test_out_of_range_citations_are_dropped():
    p = CompetitorProfile(
        name="X",
        snapshot="s",
        pricing_tiers=[PricingTier(tier_name="Pro", price="$1", includes="x", citation=99)],
        core_features=[Feature(name="f", why_it_matters="w", citation=2)],
    )
    _drop_bad_citations(p, n_evidence=3)
    assert p.pricing_tiers[0].citation is None
    assert p.core_features[0].citation == 2


def test_briefing_reports_unknown_pricing_rather_than_inventing():
    p = CompetitorProfile(
        name="Globex",
        snapshot="Enterprise vendor.",
        pricing_note="Not publicly disclosed; sales-led.",
    )
    md = write_briefing(p, "Acme", [ev("https://x.com/a")])

    assert "Not publicly disclosed" in md
    assert "| Tier |" not in md
    assert "## Sources" in md


def test_partial_status_surfaces_a_banner():
    p = CompetitorProfile(name="Globex", snapshot="s", status="partial")
    md = write_briefing(p, "Acme", [])
    assert "Partial profile" in md


def test_briefing_separates_recent_from_historical_news():
    p = CompetitorProfile(
        name="Globex",
        snapshot="s",
        recent_news=[
            NewsItem(date="2026-06-01", headline="New round", so_what="scale", url="https://n/1"),
            NewsItem(
                date="2019-01-01",
                headline="Old launch",
                so_what="context",
                url="https://n/2",
                is_recent=False,
            ),
        ],
    )
    md = write_briefing(p, "Acme", [])
    assert "Historical context" in md
    assert md.index("New round") < md.index("Historical context")


def test_comparison_matrix_accepts_unknown_values():
    m = ComparisonMatrix(
        rows=[{"dimension": "Entry price", "values": {"Globex": "Unknown", "Initech": "$10"}}]
    )
    assert m.rows[0].values["Globex"] == "Unknown"


def test_stray_inline_citation_markers_are_stripped():
    """The renderer owns footnotes; a model's inline [10] must not double up."""
    p = CompetitorProfile(
        name="Globex",
        snapshot="A vendor [3] with scale [4][5].",
        core_features=[
            Feature(name="API", why_it_matters="Integration depth [10].", citation=2)
        ],
        threats=["Undercuts on price [7]"],
    )
    md = write_briefing(p, "Acme", [ev("https://x.com/a"), ev("https://x.com/b")])

    assert "[3]" not in md and "[10]" not in md and "[7]" not in md
    assert "A vendor with scale." in md
    assert "Integration depth. [^2]" in md, "the renderer's own footnote survives"


def test_clean_preserves_text_without_markers():
    p = CompetitorProfile(name="Globex", snapshot="Plain snapshot, no markers.")
    md = write_briefing(p, "Acme", [])
    assert "Plain snapshot, no markers." in md


def test_stripping_a_marker_does_not_leave_doubled_punctuation():
    """'etc. [11][12].' must not collapse to 'etc..'."""
    p = CompetitorProfile(
        name="Globex",
        snapshot="Integrates with Slack etc. [11][12]. Used across the U.S. [3].",
    )
    md = write_briefing(p, "Acme", [])

    assert ".." not in md
    assert "Slack etc. Used across the U.S." in md


def test_sentence_terminator_wins_over_a_stranded_period():
    """'$9/mo. [4]!' keeps the '!', not the period that preceded the marker."""
    from src.agents.writer import _clean

    assert _clean("Costs $9/mo. [4]!") == "Costs $9/mo!"
    assert _clean("Ends here [5]?") == "Ends here?"
    # decimals and ratios must survive untouched
    assert _clean("Priced at $9.99, up from $7.99.") == "Priced at $9.99, up from $7.99."
    assert _clean("Ratio was 1.5:1 per the filing.") == "Ratio was 1.5:1 per the filing."


def test_executive_summary_strips_inline_markers():
    """The summary and matrix render model text too — they need the same cleaning."""
    m = ComparisonMatrix(
        rows=[{"dimension": "Entry price [1]", "values": {"Globex": "$20/mo [2]"}}],
        whitespace=["Nobody owns unified files [4]"],
        strategic_implications=["Attack on price [5]"],
        executive_summary="Rivals converged on AI [9].",
    )
    md = write_executive_summary("Acme", m, {"Globex": CompetitorProfile(name="Globex", snapshot="s")})

    for marker in ("[1]", "[2]", "[4]", "[5]", "[9]"):
        assert marker not in md, f"{marker} leaked into the summary"
    assert "Rivals converged on AI." in md
    assert "$20/mo" in md
