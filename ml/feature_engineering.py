"""Turn raw per-minute telemetry into model features.

Key idea for *precursor* detection: rolling deltas and slopes matter more than
absolute values. We compute short rolling windows per node so the model learns
'this is trending toward failure', not 'this crossed a threshold'.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RAW_FEATURES = [
    "if_utilization", "latency_ms", "jitter_ms", "queue_drops", "tunnel_loss",
    "bgp_flaps", "route_churn", "path_asymmetry", "rekey_anomaly",
    "qos_violations", "acl_mismatch",
]


def build_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    df = df.sort_values(["node", "ts"]).copy()
    out = []
    for node, g in df.groupby("node"):
        g = g.copy()
        for f in RAW_FEATURES:
            g[f"{f}_roll_mean"] = g[f].rolling(window, min_periods=1).mean()
            g[f"{f}_roll_std"] = g[f].rolling(window, min_periods=1).std().fillna(0)
            g[f"{f}_delta"] = g[f].diff().fillna(0)
            g[f"{f}_slope"] = g[f].diff().rolling(window, min_periods=1).mean().fillna(0)
        out.append(g)
    res = pd.concat(out).sort_values(["ts", "node"]).reset_index(drop=True)
    return res


def feature_columns() -> list[str]:
    cols = list(RAW_FEATURES)
    for f in RAW_FEATURES:
        cols += [f"{f}_roll_mean", f"{f}_roll_std", f"{f}_delta", f"{f}_slope"]
    return cols
