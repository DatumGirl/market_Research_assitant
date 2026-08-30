"""Streamlit interface for the Market Research Agent.

One input screen plus tabbed results. The live progress panel — showing which
agent is running and each you.com query as it is issued — is the demo: the
visible agent hand-off is what makes the multi-agent design legible.
"""

from __future__ import annotations

import json
import logging

import streamlit as st

from src.config import config
from src.graph import run_research, to_json

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Market Research Agent", page_icon="🔍", layout="wide")

PHASE_LABELS = {
    "discover": "🔎 Agent 1 — Discovering competitors",
    "research": "🌐 Agent 2 — Gathering web & news data",
    "extract": "🧪 Agent 3 — Extracting structured intelligence",
    "synthesize": "📊 Agent 4 — Synthesizing comparison",
    "write": "✍️ Agent 5 — Writing briefings",
    "done": "✅ Complete",
}


def slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Research Brief")
    company = st.text_input("Company to analyze", placeholder="e.g. Notion")
    industry = st.text_input("Industry hint (optional)", placeholder="e.g. productivity software")
    count = st.slider("Competitors to analyze", 2, 5, config.default_competitor_count)

    problems = config.validate()
    for p in problems:
        st.error(p)

    run = st.button("Run research", type="primary", disabled=bool(problems) or not company.strip())

    st.divider()
    st.caption(
        f"Search source: **you.com** · Budget: {config.max_searches_per_run} queries/run · "
        f"Cache: {'on' if config.cache_enabled else 'off'}"
    )

st.title("🔍 Market Research Agent")
st.caption("Multi-agent competitor analysis — discovery, research, extraction, synthesis.")

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

if run:
    status_box = st.status("Starting research pipeline…", expanded=True)
    seen: list[str] = []

    def progress(phase: str, detail: str) -> None:
        label = PHASE_LABELS.get(phase, phase)
        line = f"{label} · {detail}" if detail else label
        if line not in seen:
            seen.append(line)
            status_box.write(line)
        status_box.update(label=label)

    try:
        # Cached in session_state so switching tabs never re-triggers research.
        st.session_state["state"] = run_research(company.strip(), industry.strip() or None, count, progress)
        status_box.update(label="✅ Research complete", state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001
        status_box.update(label="❌ Research failed", state="error")
        st.exception(exc)

# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

state = st.session_state.get("state")

if not state:
    st.info("Enter a company in the sidebar and click **Run research** to begin.")
    st.markdown(
        """
        **How it works**

        1. **Agent 1 — Discovery** runs several complementary you.com searches and reconciles them into a ranked competitor list.
        2. **Agent 2 — Gathering** collects web + news evidence per competitor across fixed facets: pricing, features, positioning, funding, news.
        3. **Agent 3 — Extraction** turns that evidence into a typed profile. Unknown fields stay blank — never guessed.
        4. **Agent 4 — Synthesis** builds the comparison matrix and whitespace analysis.
        5. **Agent 5 — Writer** renders the briefings.
        """
    )
    st.stop()

target = state["target_company"]
profiles = state.get("profiles", {})
briefings = state.get("briefings", {})
search_log = state.get("search_log", [])
errors = state.get("errors", [])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Competitors", len(profiles))
c2.metric("Evidence items", sum(len(v) for v in state.get("evidence", {}).values()))
c3.metric("Searches run", len(search_log))
c4.metric("Issues", len(errors))

tabs = st.tabs(["📋 Executive Summary"] + [f"🏢 {n}" for n in briefings] + ["🔧 Run Log"])

with tabs[0]:
    st.markdown(state.get("executive_summary") or "_No summary generated._")

for i, (name, md) in enumerate(briefings.items(), start=1):
    with tabs[i]:
        profile = profiles.get(name)
        if profile and profile.status != "ok":
            st.warning(f"Profile status: **{profile.status}** — limited evidence available.")

        st.markdown(md)

        ev = state.get("evidence", {}).get(name, [])
        if ev:
            with st.expander(f"Sources & Evidence ({len(ev)} items)"):
                for j, e in enumerate(ev, 1):
                    date_str = f" · {e.published_date}" if e.published_date else ""
                    st.markdown(f"**[{j}]** `{e.facet}`{date_str} — [{e.title}]({e.url})")
                    st.caption(e.snippet[:300])

        st.download_button(
            "⬇️ Download briefing (Markdown)",
            md,
            file_name=f"{slug(name)}_briefing.md",
            mime="text/markdown",
            key=f"dl_{slug(name)}",
        )

with tabs[-1]:
    st.subheader("you.com queries issued")
    if search_log:
        st.dataframe(
            [
                {
                    "Agent": s.agent,
                    "Endpoint": s.endpoint,
                    "Query": s.query,
                    "Results": s.results,
                    "Cached": "yes" if s.cached else "no",
                }
                for s in search_log
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No searches recorded.")

    if errors:
        st.subheader("Issues")
        for e in errors:
            line = f"**{e.agent}**" + (f" · {e.competitor}" if e.competitor else "") + f" — {e.message}"
            (st.warning if e.recoverable else st.error)(line)

st.divider()
d1, d2 = st.columns(2)
full_md = (state.get("executive_summary") or "") + "\n\n---\n\n" + "\n\n---\n\n".join(briefings.values())
d1.download_button(
    "⬇️ Download full report (Markdown)",
    full_md,
    file_name=f"{slug(target)}_competitive_analysis.md",
    mime="text/markdown",
)
d2.download_button(
    "⬇️ Download raw data (JSON)",
    json.dumps(to_json(state), indent=2, default=str),
    file_name=f"{slug(target)}_research.json",
    mime="application/json",
)
