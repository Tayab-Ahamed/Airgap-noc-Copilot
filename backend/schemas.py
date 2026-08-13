from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class RiskItem(BaseModel):
    node: str
    predicted_issue: str
    label: int
    confidence: float
    risk_score: float
    time_to_impact_s: Optional[float] = None
    contributing_features: dict


class RiskResponse(BaseModel):
    generated_at: str
    items: list[RiskItem]


class CopilotQuery(BaseModel):
    node: Optional[str] = None
    question: Optional[str] = None


class CopilotAnswer(BaseModel):
    predicted_issue: str
    confidence: float
    time_to_impact_s: Optional[float] = None
    root_cause: str
    affected_scope: str
    recommended_actions: list
    explanation: str
    sources: list


class ScenarioRequest(BaseModel):
    scenario_name: str
    target: Optional[str] = None
