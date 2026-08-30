"""LLM factory. Agents ask for a model by role, not by id.

Claude 5 models removed `temperature` (a 400 error if sent) — thinking depth and
token spend are controlled by `output_config.effort` instead. Extraction wants
determinism-adjacent behavior, which now comes from a tight schema plus the
grounding rule in the prompt rather than from a sampling parameter.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from src.config import config


def get_llm(role: str = "worker"):
    """`worker` = per-competitor agents (run N times). `synthesis` = single deep pass."""
    if role == "synthesis":
        model, effort = config.model_synthesis, config.effort_synthesis
    else:
        model, effort = config.model_worker, config.effort_worker

    return ChatAnthropic(
        model=model,
        max_tokens=config.max_tokens,
        api_key=config.anthropic_api_key or None,
        output_config={"effort": effort},
    )


def structured(schema, role: str = "worker"):
    """An LLM bound to a Pydantic schema. Every data-producing call uses this."""
    return get_llm(role).with_structured_output(schema)
