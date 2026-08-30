"""End-to-end pipeline test with the LLM and search layers stubbed.

Proves the graph wiring, fan-out, state merging, and fail-soft contract without
touching a live API.
"""

import pytest

from src import config as config_module
from src.agents import discovery, extractor, synthesizer
from src.graph import run_research, to_json
from src.state import (
    ComparisonMatrix,
    Competitor,
    CompetitorList,
    CompetitorProfile,
    Feature,
    Positioning,
)
from src.tools import youcom


class FakeStructured:
    """Stands in for `structured(Schema)` — returns a canned object."""

    def __init__(self, result):
        self.result = result

    def invoke(self, _messages):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Stub search + all three LLM call sites."""

    def fake_request(url, params):
        if "news" in url:
            return {
                "news": {
                    "results": [
                        {
                            "title": "Globex raises $50M",
                            "url": "https://news.example.com/globex",
                            "description": "Globex announced a Series C.",
                            "page_age": "2026-06-10",
                        }
                    ]
                }
            }
        return {
            "hits": [
                {
                    "title": f"Result for {params['query']}",
                    "url": f"https://example.com/{abs(hash(params['query'])) % 1000}",
                    "snippets": ["Globex and Initech compete with Acme in mid-market."],
                    "page_age": "2026-05-01",
                }
            ]
        }

    monkeypatch.setattr(youcom, "_request", fake_request)

    monkeypatch.setattr(
        discovery,
        "structured",
        lambda schema, role="worker": FakeStructured(
            CompetitorList(
                competitors=[
                    Competitor(name="Globex", description="d", why_competes="w", confidence=0.9),
                    Competitor(name="Initech", description="d", why_competes="w", confidence=0.8),
                    # The target itself and a duplicate — both must be filtered out.
                    Competitor(name="Acme Inc.", description="d", why_competes="w", confidence=0.95),
                    Competitor(name="globex", description="d", why_competes="w", confidence=0.5),
                ],
                reasoning="r",
            )
        ),
    )

    def fake_profile(schema, role="worker"):
        return FakeStructured(
            CompetitorProfile(
                name="PLACEHOLDER",
                snapshot="A competitor.",
                pricing_note="Not publicly disclosed.",
                core_features=[Feature(name="API", why_it_matters="integration", citation=1)],
                positioning=Positioning(segment="Mid-market"),
                threats=["Undercuts on price"],
            )
        )

    monkeypatch.setattr(extractor, "structured", fake_profile)
    monkeypatch.setattr(
        synthesizer,
        "structured",
        lambda schema, role="worker": FakeStructured(
            ComparisonMatrix(
                rows=[
                    {
                        "dimension": "Entry price",
                        "values": {"Globex": "Unknown", "Initech": "$10"},
                    }
                ],
                whitespace=["No strong SMB offering"],
                strategic_implications=["Move downmarket"],
                executive_summary="Two credible competitors.",
            )
        ),
    )

    with config_module.override(cache_enabled=False, ydc_api_key="k", anthropic_api_key="k"):
        yield


def test_full_pipeline_produces_briefings(stub_pipeline):
    state = run_research("Acme", industry_hint="productivity", max_competitors=2)

    assert [c.name for c in state["competitors"]] == ["Globex", "Initech"]
    assert set(state["profiles"]) == {"Globex", "Initech"}
    assert set(state["briefings"]) == {"Globex", "Initech"}
    assert "Competitor Briefing: Globex" in state["briefings"]["Globex"]
    assert state["executive_summary"].startswith("# Competitive Landscape: Acme")
    assert state["search_log"], "queries should be logged for the UI"


def test_target_company_and_duplicates_are_filtered(stub_pipeline):
    state = run_research("Acme", max_competitors=4)
    names = [c.name for c in state["competitors"]]

    assert not any("acme" in n.lower() for n in names)
    assert len({n.lower() for n in names}) == len(names)


def test_profile_name_is_never_overwritten_by_the_model(stub_pipeline):
    # The stub returns name="PLACEHOLDER"; the extractor must restore the real name.
    state = run_research("Acme", max_competitors=2)
    for name, profile in state["profiles"].items():
        assert profile.name == name


def test_one_failing_competitor_does_not_sink_the_run(stub_pipeline, monkeypatch):
    original = extractor.extract_profile
    calls = {"n": 0}

    def flaky(competitor_name, target_company, evidence):
        calls["n"] += 1
        if competitor_name == "Globex":
            raise RuntimeError("simulated extraction crash")
        return original(competitor_name, target_company, evidence)

    monkeypatch.setattr("src.graph.extract_profile", flaky)

    state = run_research("Acme", max_competitors=2)

    assert state["profiles"]["Globex"].status == "failed"
    assert state["profiles"]["Initech"].status in {"ok", "partial"}
    assert any(e.competitor == "Globex" for e in state["errors"])
    assert "Initech" in state["briefings"], "the healthy branch still ships"


def test_json_export_is_serializable(stub_pipeline):
    import json

    state = run_research("Acme", max_competitors=2)
    payload = json.dumps(to_json(state), default=str)

    assert "Globex" in payload
    assert json.loads(payload)["target_company"] == "Acme"
