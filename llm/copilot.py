"""The NOC Copilot: NARRATES an ML prediction over retrieved runbooks.

CRITICAL DESIGN RULE: the LLM never invents the prediction. It receives the
structured RiskAssessment from the ML engine plus retrieved runbook context,
and only explains/justifies it in natural language. If Ollama is unavailable,
a deterministic template fallback produces the same structured answer so the
demo and dev loop never break.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from llm import ollama_client

SYSTEM_PROMPT = (
    "You are an air-gapped NOC assistant. You are given a MACHINE-GENERATED "
    "prediction and internal runbook excerpts. Explain the prediction clearly "
    "for a network operator. NEVER change the predicted issue, confidence, or "
    "time-to-impact \u2014 only explain them and recommend actions grounded in the "
    "provided runbooks. If the runbooks do not cover something, say so."
)


@dataclass
class CopilotResponse:
    predicted_issue: str
    confidence: float
    time_to_impact_s: float | None
    root_cause: str
    affected_scope: str
    recommended_actions: list
    explanation: str
    sources: list

    def to_dict(self):
        return self.__dict__


def _build_prompt(assessment: dict, contexts: list[dict], question: str | None) -> str:
    ctx = "\n\n".join(f"[{c['source']}]\n{c['text']}" for c in contexts)
    q = question or "Explain this prediction and recommend actions."
    return (
        f"PREDICTION (do not alter):\n{json.dumps(assessment, indent=2)}\n\n"
        f"RUNBOOK CONTEXT:\n{ctx}\n\n"
        f"OPERATOR QUESTION: {q}\n\n"
        "Respond with: root cause hypothesis, affected scope, and a numbered list "
        "of recommended actions grounded in the runbooks."
    )


def _fallback(assessment: dict, contexts: list[dict]) -> CopilotResponse:
    issue = assessment.get("predicted_issue", "unknown")
    top = contexts[0]["text"] if contexts else ""
    # Pull the first few action-looking lines from the top runbook.
    actions = [l.strip(" 0123456789.") for l in top.splitlines() if l.strip()[:1].isdigit()][:4]
    return CopilotResponse(
        predicted_issue=issue,
        confidence=assessment.get("confidence", 0.0),
        time_to_impact_s=assessment.get("time_to_impact_s"),
        root_cause=f"Precursor trend consistent with {issue} on {assessment.get('node')}.",
        affected_scope="VPN traffic on the affected path; high-priority classes first.",
        recommended_actions=actions or ["Consult the relevant runbook for remediation steps."],
        explanation=(
            f"The ML engine flagged {issue} on {assessment.get('node')} with "
            f"confidence {assessment.get('confidence')}. Contributing trends: "
            f"{assessment.get('contributing_features')}."
        ),
        sources=[c["source"] for c in contexts],
    )


def explain(assessment: dict, contexts: list[dict], question: str | None = None) -> CopilotResponse:
    if not ollama_client.is_available():
        return _fallback(assessment, contexts)
    text = ollama_client.generate(_build_prompt(assessment, contexts, question), system=SYSTEM_PROMPT)
    resp = _fallback(assessment, contexts)
    resp.explanation = text.strip() or resp.explanation
    return resp
