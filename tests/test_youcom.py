"""you.com client tests. These must never hit the live API."""

import json
from pathlib import Path

import pytest

from src import config as config_module
from src.state import Evidence
from src.tools import youcom
from src.tools.youcom import SearchBudget, SearchBudgetExceeded

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def offline(monkeypatch):
    """Disable the disk cache so each test starts cold."""
    with config_module.override(cache_enabled=False, ydc_api_key="test-key"):
        yield


def _stub(monkeypatch, payload):
    monkeypatch.setattr(youcom, "_request", lambda url, params: payload)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_search_normalizes_hits(monkeypatch, offline):
    _stub(monkeypatch, _fixture("youcom_search.json"))
    evidence, log = youcom.search("acme pricing", facet="pricing", agent="test")

    assert all(isinstance(e, Evidence) for e in evidence)
    assert log.endpoint == "search"
    assert log.results == len(evidence)
    assert all(e.facet == "pricing" for e in evidence)


def test_evidence_without_url_or_snippet_is_discarded(monkeypatch, offline):
    _stub(monkeypatch, _fixture("youcom_search.json"))
    evidence, _ = youcom.search("acme pricing", facet="pricing")

    # Fixture has 4 hits; one lacks a URL and one has an empty snippet.
    assert len(evidence) == 2
    assert all(e.url for e in evidence)
    assert all(e.snippet for e in evidence)


def test_news_parses_nested_shape(monkeypatch, offline):
    _stub(monkeypatch, _fixture("youcom_news.json"))
    evidence, log = youcom.search_news("Acme", agent="test")

    assert log.endpoint == "news"
    assert len(evidence) == 1
    assert evidence[0].facet == "news"
    assert evidence[0].published_date == "2026-06-10"


def test_budget_blocks_overspend(monkeypatch, offline):
    _stub(monkeypatch, {"hits": []})
    budget = SearchBudget(limit=2)

    youcom.search("one", budget=budget)
    youcom.search("two", budget=budget)

    assert budget.remaining == 0
    with pytest.raises(SearchBudgetExceeded):
        youcom.search("three", budget=budget)


def test_cache_hit_does_not_consume_budget(monkeypatch, tmp_path):
    calls = {"n": 0}

    def counting_request(url, params):
        calls["n"] += 1
        return {"hits": []}

    monkeypatch.setattr(youcom, "_request", counting_request)

    with config_module.override(
        cache_enabled=True, cache_dir=str(tmp_path), ydc_api_key="test-key"
    ):
        budget = SearchBudget(limit=5)
        youcom.search("same query", budget=budget)
        youcom.search("same query", budget=budget)

    assert calls["n"] == 1, "second call should be served from cache"
    assert budget.used == 1, "cached calls must not consume quota"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    with config_module.override(ydc_api_key="", cache_enabled=False):
        with pytest.raises(youcom.YouComError, match="YDC_API_KEY"):
            youcom.search("anything")


def test_news_and_web_buckets_are_kept_separate(monkeypatch, offline):
    """One v1 response carries both buckets; each helper must read only its own."""
    payload = _fixture("youcom_news.json")
    _stub(monkeypatch, payload)

    web, _ = youcom.search("Acme", facet="features")
    news, _ = youcom.search_news("Acme")

    assert [e.url for e in web] == ["https://acme.example.com"]
    assert [e.url for e in news] == ["https://news.example.com/acme-series-c"]


def test_iso_timestamps_are_trimmed_to_dates(monkeypatch, offline):
    _stub(monkeypatch, _fixture("youcom_search.json"))
    evidence, _ = youcom.search("acme pricing", facet="pricing")

    assert evidence[0].published_date == "2026-03-01", "T-suffix should be stripped"


def test_news_query_is_framed_so_the_api_returns_a_news_bucket(monkeypatch, offline):
    """A bare company name yields no news bucket; the client adds the framing."""
    seen = {}

    def capture(url, params):
        seen["query"] = params["query"]
        return _fixture("youcom_news.json")

    monkeypatch.setattr(youcom, "_request", capture)
    youcom.search_news("Acme")

    assert seen["query"] != "Acme", "a bare name yields no news bucket"
    assert seen["query"].startswith("Acme"), "the subject must lead the query"
    assert any(w in seen["query"] for w in youcom._NEWS_WORDS)


def test_already_news_shaped_queries_are_not_reframed(monkeypatch, offline):
    seen = {}

    def capture(url, params):
        seen["query"] = params["query"]
        return _fixture("youcom_news.json")

    monkeypatch.setattr(youcom, "_request", capture)
    youcom.search_news("Acme funding round")

    assert seen["query"] == "Acme funding round"


def test_empty_news_bucket_falls_back_to_web(monkeypatch, offline):
    _stub(monkeypatch, _fixture("youcom_search.json"))  # has web hits, empty news
    evidence, log = youcom.search_news("Acme")

    assert evidence, "news facet must not silently return nothing"
    assert log.endpoint == "news"


def test_off_topic_news_is_filtered_out(monkeypatch, offline):
    """The news bucket is noisy; unrelated headlines must not reach the extractor."""
    monkeypatch.setattr(
        youcom,
        "_request",
        lambda url, params: {
            "results": {
                "news": [
                    {
                        "title": "Acme raises $50M",
                        "url": "https://n/1",
                        "description": "Acme funding round.",
                        "page_age": "2026-06-01T00:00:00",
                    },
                    {
                        "title": "Dolly Parton reflects on her career",
                        "url": "https://n/2",
                        "description": "Unrelated celebrity news.",
                        "page_age": "2026-06-02T00:00:00",
                    },
                ]
            }
        },
    )

    evidence, _ = youcom.search_news("Acme")

    assert [e.url for e in evidence] == ["https://n/1"]
