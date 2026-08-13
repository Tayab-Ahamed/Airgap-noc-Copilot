"""Build a LARGE, CLASS-BALANCED labeled training dataset.

Why this exists: the random scheduler in `synthetic_data.generate` under-
represents some fault classes (and may miss one entirely on a short run), which
hurts the classifier. This generator instead creates independent, balanced
*episodes* per fault class plus nominal episodes.

Each episode is emitted under a UNIQUE synthetic node id (e.g. ``mpls__017``)
so per-node rolling features never bleed across episodes. The label space and
precursor shapes come straight from sim/fault_injector.py, so a model trained
here transfers to the real containerlab telemetry (same columns, same labels).

Usage:
    python -m ml.make_dataset --episodes 80 --out data/train.parquet
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from sim.fault_injector import SCENARIOS, apply_scenario
from ml.synthetic_data import BASE_FEATURES


def _nominal_row(rng: np.random.Generator) -> dict:
    return {f: max(0.0, b * (1.0 + rng.normal(0, 0.06))) for f, b in BASE_FEATURES.items()}


def _episode(rng: np.random.Generator, node_id: str, start: pd.Timestamp,
             scenario: str | None, warmup: int) -> list[dict]:
    """One clean time series: `warmup` nominal minutes, then (if scenario set) a
    precursor ramp + short post-impact tail."""
    rows: list[dict] = []
    minute = 0
    # nominal warmup so rolling deltas/slopes have realistic history
    for _ in range(warmup):
        r = _nominal_row(rng)
        r.update({"ts": start + pd.Timedelta(minutes=minute), "node": node_id,
                  "label": 0, "time_to_impact_s": np.nan})
        rows.append(r)
        minute += 1
    if scenario is None:
        # extra nominal minutes for a pure-nominal episode
        for _ in range(rng.integers(6, 12)):
            r = _nominal_row(rng)
            r.update({"ts": start + pd.Timedelta(minutes=minute), "node": node_id,
                      "label": 0, "time_to_impact_s": np.nan})
            rows.append(r)
            minute += 1
        return rows
    sc = SCENARIOS[scenario]
    ramp_min = sc.ramp_seconds // 60
    fault_len = ramp_min + int(rng.integers(3, 8))  # ramp + post-impact tail
    for k in range(fault_len):
        base = _nominal_row(rng)
        base.update({"ts": start + pd.Timedelta(minutes=minute), "node": node_id})
        elapsed_s = (k + 1) * 60.0
        rows.append(apply_scenario(base, scenario, elapsed_s) | {
            "ts": base["ts"], "node": node_id})
        minute += 1
    return rows


def build(episodes_per_class: int = 80, warmup: int = 8, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    all_rows: list[dict] = []
    start = pd.Timestamp("2026-06-01T00:00:00")
    classes = list(SCENARIOS)
    # fault episodes (balanced across the 4 classes)
    for sc in classes:
        for ep in range(episodes_per_class):
            node_id = f"{sc}__{ep:04d}"
            all_rows += _episode(rng, node_id, start, sc, warmup)
    # nominal episodes ~ 1.5x a single class so negatives are well represented
    for ep in range(int(episodes_per_class * 1.5)):
        node_id = f"nominal__{ep:04d}"
        all_rows += _episode(rng, node_id, start, None, warmup)
    df = pd.DataFrame(all_rows)
    # tidy column order
    cols = ["ts", "node"] + list(BASE_FEATURES) + ["label", "time_to_impact_s"]
    return df[cols]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=80, help="episodes per fault class")
    ap.add_argument("--warmup", type=int, default=8, help="nominal warmup minutes per episode")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="data/train.parquet")
    args = ap.parse_args()
    df = build(args.episodes, args.warmup, args.seed)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out = args.out
    try:
        df.to_parquet(out)
    except Exception:
        out = out.rsplit(".", 1)[0] + ".csv"
        df.to_csv(out, index=False)
    dist = df["label"].value_counts().sort_index().to_dict()
    from sim.fault_injector import LABELS
    named = {LABELS[k]: v for k, v in dist.items()}
    print(f"wrote {len(df)} rows ({df['node'].nunique()} episodes) -> {out}")
    print("label distribution:", named)


if __name__ == "__main__":
    main()
