"""Optional LangGraph orchestration (an UPGRADE, not a dependency).

The backend works with a single copilot call. This graph adds explicit
Root-Cause -> Prediction-Verify -> Explain stages for a clear architecture flow.
It degrades gracefully: if langgraph isn't installed, `run_pipeline` falls back to
a plain sequential call.
"""
from __future__ import annotations

from typing import TypedDict

from llm import copilot


class NocState(TypedDict, total=False):
    assessment: dict
    contexts: list
    question: str
    verified: bool
    response: dict


def _verify(state: NocState) -> NocState:
    a = state["assessment"]
    # Cheap sanity gate: low-confidence nominal predictions need no narrative.
    state["verified"] = bool(a.get("label", 0) != 0 and a.get("confidence", 0) >= 0.5)
    return state


def _explain(state: NocState) -> NocState:
    resp = copilot.explain(state["assessment"], state.get("contexts", []), state.get("question"))
    state["response"] = resp.to_dict()
    return state


def run_pipeline(assessment: dict, contexts: list, question: str | None = None) -> dict:
    state: NocState = {"assessment": assessment, "contexts": contexts, "question": question or ""}
    try:
        from langgraph.graph import StateGraph, END
        g = StateGraph(NocState)
        g.add_node("verify", _verify)
        g.add_node("explain", _explain)
        g.set_entry_point("verify")
        g.add_edge("verify", "explain")
        g.add_edge("explain", END)
        app = g.compile()
        out = app.invoke(state)
        return out["response"]
    except Exception:
        # Sequential fallback
        state = _verify(state)
        state = _explain(state)
        return state["response"]
