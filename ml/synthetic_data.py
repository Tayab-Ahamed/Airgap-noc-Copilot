"""Generate a labeled, time-series telemetry dataset.

Lets you build and demo the whole pipeline before the containerlab sim exists,
using the SAME label space + scenario shapes as sim/fault_injector.py so the
model trained on synthetic data also works on real telemetry.

Usage:
    python -m ml.synthetic_data --minutes 240 --out data/telemetry.parquet
"""
from __future__ import annotations

import argparse
import os
import numpy as np
import pandas as pd

from sim.fault_injector import SCENARIOS, LABEL_IDS, apply_scenario

NODES = ["CE1", "PE1", "P1", "PE2", "CE2"]

BASE_FEATURES = {
    "if_utilization": 35.0,   # percent
    "latency_ms": 12.0,
    "jitter_ms": 1.5,
    "queue_drops": 0.5,
    "tunnel_loss": 0.1,       # percent
    "bgp_flaps": 0.0,
    "route_churn": 1.0,
    "path_asymmetry": 0.2,
    "rekey_anomaly": 0.0,
    "qos_violations": 0.0,
    "acl_mismatch": 0.0,
}


def _noisy(rng: np.random.Generator, base: float) -> float:
    return max(0.0, base * (1.0 + rng.normal(0, 0.05)))


def generate(minutes: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2026-06-18T00:00:00")
    # Schedule a few fault windows across the timeline.
    scenario_names = list(SCENARIOS)
    windows = []
    t = 20
    while t < minutes - 10:
        sc = scenario_names[rng.integers(0, len(scenario_names))]
        dur = SCENARIOS[sc].ramp_seconds // 60 + rng.integers(3, 8)
        windows.append((t, t + dur, sc))
        t += dur + rng.integers(15, 30)  # quiet gap

    for minute in range(minutes):
        ts = start + pd.Timedelta(minutes=minute)
        active = next(((s, sc) for (s, e, sc) in windows if s <= minute < e), None)
        for node in NODES:
            row = {"ts": ts, "node": node}
            for f, base in BASE_FEATURES.items():
                row[f] = _noisy(rng, base)
            row["label"] = 0
            row["time_to_impact_s"] = np.nan
            if active is not None:
                start_min, sc = active
                target = SCENARIOS[sc].target
                if node == target:
                    elapsed_s = (minute - start_min) * 60.0
                    row = apply_scenario(row, sc, elapsed_s)
            rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=240)
    ap.add_argument("--out", default="data/telemetry.parquet")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    df = generate(args.minutes, args.seed)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    if args.out.endswith(".csv"):
        df.to_csv(args.out, index=False)
    else:
        df.to_parquet(args.out)
    print(f"wrote {len(df)} rows -> {args.out}")
    print(df["label"].value_counts().to_dict())
