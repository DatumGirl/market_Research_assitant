"""The Orchestrator — a LangGraph StateGraph.

Control flow is deterministic by design. An LLM choosing the next step buys
nothing here (the pipeline is fixed) and costs reliability, latency, and
legibility during a demo. The "agentic" work lives inside the nodes.

Flow:
    discover -> research (fan out per competitor, concurrent) -> synthesize -> write

Fail-soft contract: any competitor may fail without sinking the run. Its profile
is marked `failed`, an AgentError is recorded, and the remaining branches finish.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from langgraph.graph import END, StateGraph

from src.agents.discovery import discover_competitors
from src.agents.extractor import extract_profile
from src.agents.gatherer import gather_for_competitor
from src.agents.synthesizer import synthesize
from src.agents.writer import write_briefing, write_executive_summary
from src.config import config
from src.state import AgentError, CompetitorProfile, ResearchState
from src.tools.youcom import SearchBudget, SearchBudgetExceeded

logger = logging.getLogger(__name__)

# Optional UI hook: called with (phase, detail) as the pipeline advances.
ProgressFn = Callable[[str, str], None]


def build_graph(budget: SearchBudget, progress: ProgressFn | None = None):
    """Compile the research pipeline. `budget` is per-run, so build per run."""

    def emit(phase: str, detail: str = "") -> None:
        if progress:
            progress(phase, detail)

    # --- node: discovery -------------------------------------------------
    def node_discover(state: ResearchState) -> dict:
        emit("discover", f"Finding competitors for {state['target_company']}")
        try:
            return discover_competitors(state, budget)
        except SearchBudgetExceeded as exc:
            return {"competitors": [], "errors": [AgentError(agent="discovery", message=str(exc), recoverable=False)]}

    # --- node: research (fan out) ---------------------------------------
    def research_one(competitor_name: str, target: str) -> dict:
        """Agent 2 then Agent 3 for a single competitor."""
        emit("research", f"Researching {competitor_name}")
        try:
            evidence, logs, gather_errors = gather_for_competitor(competitor_name, budget)
        except SearchBudgetExceeded as exc:
            return {
                "evidence": {competitor_name: []},
                "profiles": {
                    competitor_name: CompetitorProfile(
                        name=competitor_name,
                        snapshot="Research skipped — search budget exhausted.",
                        status="failed",
                    )
                },
                "search_log": [],
                "errors": [AgentError(agent="gatherer", competitor=competitor_name, message=str(exc))],
            }

        emit("extract", f"Extracting profile for {competitor_name}")
        profile, extract_errors = extract_profile(competitor_name, target, evidence)

        return {
            "evidence": {competitor_name: evidence},
            "profiles": {competitor_name: profile},
            "search_log": logs,
            "errors": gather_errors + extract_errors,
        }

    def node_research(state: ResearchState) -> dict:
        competitors = state.get("competitors", [])
        target = state["target_company"]

        if not competitors:
            return {"errors": [AgentError(agent="orchestrator", message="No competitors to research.", recoverable=False)]}

        merged: dict = {"evidence": {}, "profiles": {}, "search_log": [], "errors": []}

        # Branches are independent — run them concurrently.
        with ThreadPoolExecutor(max_workers=min(len(competitors), 4)) as pool:
            futures = {pool.submit(research_one, c.name, target): c.name for c in competitors}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    part = fut.result()
                except Exception as exc:  # noqa: BLE001 — one branch must not sink the run
                    logger.exception("research branch failed for %s", name)
                    part = {
                        "evidence": {name: []},
                        "profiles": {
                            name: CompetitorProfile(
                                name=name, snapshot="Research failed.", status="failed"
                            )
                        },
                        "search_log": [],
                        "errors": [AgentError(agent="orchestrator", competitor=name, message=str(exc))],
                    }
                merged["evidence"].update(part["evidence"])
                merged["profiles"].update(part["profiles"])
                merged["search_log"].extend(part["search_log"])
                merged["errors"].extend(part["errors"])

        return merged

    # --- node: synthesis -------------------------------------------------
    def node_synthesize(state: ResearchState) -> dict:
        emit("synthesize", "Building cross-competitor comparison")
        matrix, errors = synthesize(state["target_company"], state.get("profiles", {}))
        return {"comparison": matrix, "errors": errors}

    # --- node: write -----------------------------------------------------
    def node_write(state: ResearchState) -> dict:
        emit("write", "Composing briefings")
        target = state["target_company"]
        profiles = state.get("profiles", {})
        evidence = state.get("evidence", {})

        briefings = {
            name: write_briefing(profile, target, evidence.get(name, []))
            for name, profile in profiles.items()
        }
        summary = write_executive_summary(target, state.get("comparison"), profiles)
        emit("done", f"{len(briefings)} briefings ready")
        return {"briefings": briefings, "executive_summary": summary}

    # --- wiring ----------------------------------------------------------
    graph = StateGraph(ResearchState)
    graph.add_node("discover", node_discover)
    graph.add_node("research", node_research)
    graph.add_node("synthesize", node_synthesize)
    graph.add_node("write", node_write)

    graph.set_entry_point("discover")
    graph.add_conditional_edges(
        "discover",
        lambda s: "research" if s.get("competitors") else END,
        {"research": "research", END: END},
    )
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", "write")
    graph.add_edge("write", END)

    return graph.compile()


def run_research(
    target_company: str,
    industry_hint: str | None = None,
    max_competitors: int | None = None,
    progress: ProgressFn | None = None,
) -> ResearchState:
    """Convenience entry point used by both the CLI and the Streamlit app."""
    budget = SearchBudget()
    app = build_graph(budget, progress)

    initial: ResearchState = {
        "target_company": target_company,
        "industry_hint": industry_hint,
        "max_competitors": max_competitors or config.default_competitor_count,
        "competitors": [],
        "evidence": {},
        "profiles": {},
        "briefings": {},
        "comparison": None,
        "executive_summary": None,
        "errors": [],
        "search_log": [],
        "started_at": datetime.now(),
    }

    final = app.invoke(initial)
    logger.info("run complete — %d searches used of %d", budget.used, budget.limit)
    return final


def save_outputs(state: ResearchState, out_dir: str | None = None) -> Path:
    """Write briefings + a machine-readable JSON dump to disk."""
    target = state["target_company"]
    slug = "".join(ch if ch.isalnum() else "_" for ch in target.lower()).strip("_")
    base = Path(out_dir or config.output_dir) / slug
    base.mkdir(parents=True, exist_ok=True)

    if state.get("executive_summary"):
        (base / "00_executive_summary.md").write_text(state["executive_summary"])

    for i, (name, md) in enumerate(state.get("briefings", {}).items(), 1):
        name_slug = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
        (base / f"{i:02d}_{name_slug}.md").write_text(md)

    (base / "research.json").write_text(json.dumps(to_json(state), indent=2, default=str))
    return base


def to_json(state: ResearchState) -> dict:
    """Serializable view of a run — used for export and for the JSON download."""
    return {
        "target_company": state.get("target_company"),
        "industry_hint": state.get("industry_hint"),
        "generated_at": str(state.get("started_at")),
        "competitors": [c.model_dump() for c in state.get("competitors", [])],
        "profiles": {k: v.model_dump() for k, v in state.get("profiles", {}).items()},
        "comparison": state["comparison"].model_dump() if state.get("comparison") else None,
        "executive_summary": state.get("executive_summary"),
        "evidence": {
            k: [e.model_dump() for e in v] for k, v in state.get("evidence", {}).items()
        },
        "search_log": [s.model_dump() for s in state.get("search_log", [])],
        "errors": [e.model_dump() for e in state.get("errors", [])],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless competitor research run.")
    parser.add_argument("--company", required=True, help="Target company to analyze.")
    parser.add_argument("--industry", default=None, help="Optional industry hint.")
    parser.add_argument("--count", type=int, default=None, help="Number of competitors (2-5).")
    parser.add_argument("--out", default=None, help="Output directory.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    problems = config.validate()
    if problems:
        for p in problems:
            print(f"  ! {p}")
        raise SystemExit(1)

    def progress(phase: str, detail: str) -> None:
        print(f"[{phase}] {detail}")

    state = run_research(args.company, args.industry, args.count, progress)
    path = save_outputs(state, args.out)

    print(f"\nWrote {len(state.get('briefings', {}))} briefings to {path}")
    for err in state.get("errors", []):
        print(f"  ! [{err.agent}] {err.competitor or ''} {err.message}")


if __name__ == "__main__":
    main()
