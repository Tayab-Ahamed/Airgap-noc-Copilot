"""Risk scoring + time-to-impact estimation.

This is the bridge between the ML prediction and the copilot. It produces the
structured object the LLM will NARRATE (never invent):

    {
      node, predicted_issue, confidence, risk_score,
      time_to_impact_s, contributing_features
    }

Time-to-impact is defined precisely (defensible to judges): we forecast the
leading degradation feature for the predicted class and report when it crosses
its degradation threshold.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from ml.feature_engineering import build_features, feature_columns
from sim.fault_injector import LABELS

# Leading feature + degradation threshold per fault class (define explicitly!).
LEADING_FEATURE = {
    1: ("if_utilization", 85.0),
    2: ("bgp_flaps", 5.0),
    3: ("tunnel_loss", 2.0),
    4: ("qos_violations", 10.0),
}


@dataclass
class RiskAssessment:
    node: str
    predicted_issue: str
    label: int
    confidence: float
    risk_score: float
    time_to_impact_s: float | None
    contributing_features: dict

    def to_dict(self) -> dict:
        return asdict(self)


class RiskScorer:
    def __init__(self, model_dir: str = "models/"):
        with open(f"{model_dir}/predictor.pkl", "rb") as f:
            self.model = pickle.load(f)
        with open(f"{model_dir}/meta.json") as f:
            self.cols = json.load(f)["features"]

    def _ttimpact(self, g: pd.DataFrame, label: int) -> float | None:
        if label not in LEADING_FEATURE:
            return None
        feat, thresh = LEADING_FEATURE[label]
        recent = g[feat].tail(5)
        if len(recent) < 2:
            return None
        slope = (recent.iloc[-1] - recent.iloc[0]) / max(len(recent) - 1, 1)  # per minute
        cur = recent.iloc[-1]
        if slope <= 0 or cur >= thresh:
            return 0.0 if cur >= thresh else None
        minutes = (thresh - cur) / slope
        return float(max(minutes, 0.0) * 60.0)

    def assess(self, df: pd.DataFrame) -> list[RiskAssessment]:
        feats = build_features(df)
        results = []
        for node, g in feats.groupby("node"):
            latest = g.iloc[[-1]][self.cols].fillna(0)
            proba = getattr(self.model, "predict_proba", None)
            if proba is not None:
                p = self.model.predict_proba(latest)[0]
                label = int(np.argmax(p))
                conf = float(np.max(p))
            else:
                label = int(self.model.predict(latest)[0])
                conf = 1.0
            top = (
                g.iloc[-1][[c for c in self.cols if c.endswith("_slope")]]
                .sort_values(ascending=False).head(3).to_dict()
            )
            results.append(RiskAssessment(
                node=node,
                predicted_issue=LABELS.get(label, "unknown"),
                label=label,
                confidence=round(conf, 3),
                risk_score=round(conf * (1.0 if label else 0.1), 3),
                time_to_impact_s=self._ttimpact(g, label),
                contributing_features={k: round(float(v), 3) for k, v in top.items()},
            ))
        return results
