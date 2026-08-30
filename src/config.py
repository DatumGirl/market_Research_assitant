"""Central configuration. Every tunable lives here — nothing hardcoded in agents."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, replace

from dotenv import load_dotenv

load_dotenv()


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # --- credentials -------------------------------------------------
    ydc_api_key: str = os.getenv("YDC_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # --- models ------------------------------------------------------
    # Sonnet for the per-competitor workers (they run N times), Opus for the
    # single synthesis pass where reasoning quality matters most.
    model_worker: str = os.getenv("MODEL_WORKER", "claude-sonnet-5")
    model_synthesis: str = os.getenv("MODEL_SYNTHESIS", "claude-opus-5")
    # Claude 5 removed `temperature`; effort is the depth/cost dial.
    effort_worker: str = os.getenv("EFFORT_WORKER", "medium")
    effort_synthesis: str = os.getenv("EFFORT_SYNTHESIS", "high")
    max_tokens: int = 8192

    # --- limits ------------------------------------------------------
    max_searches_per_run: int = _int_env("MAX_SEARCHES_PER_RUN", 40)
    default_competitor_count: int = 3
    results_per_query: int = 6
    news_recency_months: int = 12
    request_timeout_s: int = 20
    max_retries: int = 3

    # --- caching -----------------------------------------------------
    cache_enabled: bool = _bool_env("YOUCOM_CACHE_ENABLED", True)
    cache_dir: str = os.getenv("YOUCOM_CACHE_DIR", ".cache/youcom")

    # --- io ----------------------------------------------------------
    output_dir: str = os.getenv("OUTPUT_DIR", "outputs")

    def validate(self) -> list[str]:
        """Return a list of human-readable problems. Empty list means good to go."""
        problems = []
        if not self.ydc_api_key:
            problems.append("YDC_API_KEY is not set — you.com search will fail.")
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set — the agents cannot run.")
        return problems


config = Config()


@contextmanager
def override(**changes):
    """Temporarily swap fields on the module-level config.

    `Config` is frozen on purpose — nothing should mutate settings mid-run. Tests
    (and the occasional notebook) still need to vary them, so this swaps in a new
    frozen instance and restores the original afterwards.
    """
    global config
    original = config
    config = replace(original, **changes)
    try:
        yield config
    finally:
        config = original
