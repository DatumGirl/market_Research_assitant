"""you.com Search API client (you.platform / api.you.com v1).

The ONLY module in this project that speaks HTTP to a search provider. Agents
import `search()` / `search_news()` and never see raw API JSON.

API shape, verified against the live endpoint:
    GET https://api.you.com/v1/search
    header: X-API-Key
    params: query, count, freshness
    -> {"results": {"web": [...], "news": [...]}, "metadata": {...}}

    Each hit: url, title, description, snippets[], page_age (ISO8601)

Note that ONE call returns both a `web` and a `news` bucket, so news retrieval
does not need a second request — `search_news()` reads the news bucket from the
same endpoint and adds `freshness` to bias toward recent coverage.

Responsibilities:
  - auth from env (never logged)
  - retry with exponential backoff on 429/5xx
  - on-disk cache keyed by normalized query, so dev reruns do not burn quota
  - normalization into the single `Evidence` shape
  - a per-run search budget the orchestrator can enforce
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import requests

from src import config as config_module
from src.state import Evidence, Facet, SearchLogEntry

logger = logging.getLogger(__name__)


def _cfg():
    """Resolve config at call time, so `config.override()` in tests takes effect."""
    return config_module.config


SEARCH_URL = "https://api.you.com/v1/search"


class SearchBudgetExceeded(RuntimeError):
    """Raised when a run tries to exceed its configured search budget."""


class YouComError(RuntimeError):
    """Non-recoverable you.com API failure after retries."""


class SearchBudget:
    """Tracks search spend for one run. The orchestrator owns one instance."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit if limit is not None else _cfg().max_searches_per_run
        self.used = 0

    def consume(self, n: int = 1) -> None:
        if self.used + n > self.limit:
            raise SearchBudgetExceeded(
                f"Search budget exhausted ({self.used}/{self.limit}). "
                "Raise MAX_SEARCHES_PER_RUN or narrow the research scope."
            )
        self.used += n

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def _normalize(query: str) -> str:
    return " ".join(query.lower().split())


def _cache_path(query: str, **params: Any) -> Path:
    key = json.dumps({"q": _normalize(query), **params}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    return Path(_cfg().cache_dir) / f"search_{digest}.json"


def _cache_read(path: Path) -> dict | None:
    if not _cfg().cache_enabled or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(path: Path, payload: dict) -> None:
    if not _cfg().cache_enabled:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError as exc:  # cache failures must never break a run
        logger.debug("cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _request(url: str, params: dict[str, Any]) -> dict:
    api_key = _cfg().ydc_api_key or os.getenv("YDC_API_KEY", "")
    if not api_key:
        raise YouComError("YDC_API_KEY is not set.")

    headers = {"X-API-Key": api_key}
    last_exc: Exception | None = None

    for attempt in range(_cfg().max_retries):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=_cfg().request_timeout_s
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                # Retryable. Back off with jitter so parallel branches desynchronize.
                delay = (2**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "you.com %s on attempt %d/%d, retrying in %.1fs",
                    resp.status_code,
                    attempt + 1,
                    _cfg().max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            if resp.status_code in (401, 403):
                raise YouComError(
                    f"you.com rejected the API key ({resp.status_code}). Check that "
                    f"YDC_API_KEY is a valid you.platform key: {resp.text[:120]}"
                )
            raise YouComError(f"you.com returned {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep((2**attempt) + random.uniform(0, 0.5))

    raise YouComError(f"you.com request failed after {_cfg().max_retries} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def _bucket(payload: dict, name: str) -> list[dict]:
    """Pull the `web` or `news` list out of a v1 response."""
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if isinstance(results, dict):
        items = results.get(name)
        return items if isinstance(items, list) else []
    # Tolerate a flat list response, and the legacy `hits` shape.
    if isinstance(results, list):
        return results
    return payload.get("hits") or []


def _to_evidence(hit: dict, facet: Facet, query: str) -> Evidence | None:
    url = hit.get("url") or hit.get("link")
    if not url:
        return None  # evidence without a source URL is discarded, per spec

    # `snippets` is the richest field; fall back to `description`.
    raw = hit.get("snippets") or hit.get("description") or ""
    snippet = " ".join(raw) if isinstance(raw, list) else str(raw)
    snippet = snippet.strip()
    if not snippet:
        return None

    page_age = hit.get("page_age") or hit.get("published_date") or hit.get("age")
    if isinstance(page_age, str) and "T" in page_age:
        page_age = page_age.split("T")[0]  # 2026-08-28T19:04:44 -> 2026-08-28

    return Evidence(
        title=(hit.get("title") or "Untitled").strip(),
        url=url,
        snippet=snippet[:1200],
        facet=facet,
        published_date=page_age,
        query=query,
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def search(
    query: str,
    facet: Facet = "features",
    *,
    budget: SearchBudget | None = None,
    agent: str = "",
    count: int | None = None,
) -> tuple[list[Evidence], SearchLogEntry]:
    """Run a you.com web search. Returns (evidence, log entry)."""
    return _run(query, facet, budget, agent, count, bucket="web", freshness=None)


def search_news(
    query: str,
    *,
    budget: SearchBudget | None = None,
    agent: str = "",
    count: int | None = None,
    freshness: str = "month",
) -> tuple[list[Evidence], SearchLogEntry]:
    """Read the news bucket, biased toward recent coverage.

    The API only populates a `news` bucket when the query itself is news-shaped —
    a bare company name returns web results only. Callers pass the plain company
    name, so the news framing is added here rather than duplicated at each call
    site. If the bucket still comes back empty, we fall back to the web bucket so
    a news facet never silently yields nothing.
    """
    framed = query if _looks_like_news_query(query) else f"{query} announces"
    evidence, log = _run(
        framed, "news", budget, agent, count, bucket="news", freshness=freshness
    )
    return _filter_on_topic(evidence, subject=query), log


_NEWS_WORDS = ("news", "announce", "launch", "funding", "raise", "acquisition", "report")


def _looks_like_news_query(query: str) -> bool:
    lowered = query.lower()
    return any(word in lowered for word in _NEWS_WORDS)


def _filter_on_topic(evidence: list[Evidence], subject: str) -> list[Evidence]:
    """Drop news items that never mention the subject.

    The news bucket is noisy — a query for a company reliably returns a few
    unrelated headlines. Passing those to the extractor invites the model to
    attribute someone else's acquisition to this competitor, so they are dropped
    here rather than filtered by prompt.
    """
    subject_l = subject.lower().strip()
    if not subject_l:
        return evidence
    on_topic = [
        e for e in evidence if subject_l in e.title.lower() or subject_l in e.snippet.lower()
    ]
    if not on_topic:
        logger.info("news: no on-topic items for %r, dropping %d unrelated", subject, len(evidence))
    return on_topic


def _run(
    query: str,
    facet: Facet,
    budget: SearchBudget | None,
    agent: str,
    count: int | None,
    *,
    bucket: str,
    freshness: str | None,
) -> tuple[list[Evidence], SearchLogEntry]:
    count = count or _cfg().results_per_query
    params: dict[str, Any] = {"query": query, "count": count}
    if freshness:
        params["freshness"] = freshness

    path = _cache_path(query, count=count, freshness=freshness)
    cached = _cache_read(path)

    if cached is not None:
        payload, was_cached = cached, True
    else:
        if budget is not None:
            budget.consume()  # only uncached calls cost quota
        payload = _request(SEARCH_URL, params)
        _cache_write(path, payload)
        was_cached = False

    hits = _bucket(payload, bucket)
    if not hits and bucket == "news":
        # Recency-framed web results still beat returning nothing for the news facet.
        hits = _bucket(payload, "web")
    evidence = [e for e in (_to_evidence(h, facet, query) for h in hits) if e]

    log = SearchLogEntry(
        query=query,
        endpoint="news" if bucket == "news" else "search",
        results=len(evidence),
        cached=was_cached,
        agent=agent,
    )
    logger.info(
        "[%s] %s %r -> %d results%s",
        agent,
        bucket,
        query,
        len(evidence),
        " (cached)" if was_cached else "",
    )
    return evidence, log
