"""Training pipeline for the fault-class predictor.

Production path (when scikit-learn/xgboost are installed):
  - stratified train/test split + stratified K-fold cross-validation
  - full metrics (precision/recall/F1 per class, macro avg) saved to JSON
  - XGBoost when available, scikit-learn GradientBoosting otherwise
  - optional cross-seed evaluation on a second independently generated dataset
    (--eval-seed) so reported metrics reflect generalisation across generation
    runs, not just a stratified split of the same synthetic run.

Offline fallback (no sklearn/xgboost): a NumPy-only softmax classifier
(ml.baseline_model), so the whole system trains + runs at an air-gapped venue
before wheels are set up. Either way the same artifacts are written:
  models/predictor.pkl, models/meta.json, and a versioned models/v_<ts>/.

Usage:
    # Standard training (same-seed stratified split):
    python -m ml.train --data data/telemetry.parquet --out models/

    # With cross-seed held-out evaluation (recommended for reporting):
    python -m ml.train --data data/telemetry.parquet --out models/ --eval-seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import time

import numpy as np
import pandas as pd

from ml.feature_engineering import build_features, feature_columns
from sim.fault_injector import LABELS

N_CLASSES = len(LABELS)


def _load(path: str) -> pd.DataFrame:
    if path.endswith(".csv"):
        return pd.read_csv(path, parse_dates=["ts"])
    return pd.read_parquet(path)


def _save_artifacts(out: str, model, kind: str, cols: list, metrics: dict) -> str:
    os.makedirs(out, exist_ok=True)
    version = time.strftime("%Y%m%d-%H%M%S")
    vdir = os.path.join(out, f"v_{version}")
    os.makedirs(vdir, exist_ok=True)
    meta = {"version": version, "kind": kind, "features": cols, "metrics": metrics}
    for d in (vdir, out):  # versioned + 'latest'
        with open(os.path.join(d, "predictor.pkl"), "wb") as f:
            pickle.dump(model, f)
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
    return version


def _cross_seed_eval(model, cols: list, eval_seed: int, minutes: int) -> dict:
    """Evaluate a trained model on a fresh synthetic dataset generated with a
    different RNG seed and therefore different fault-window timing and noise
    realisations than the training data.

    Why this matters: generate() places fault windows at positions controlled by
    the seed's RNG. A same-seed stratified split shares timing structure across
    train/test; a different seed guarantees the model has never seen these
    exact window start times or noise sequences. This is the closest proxy for
    generalisation available without a live containerlab deployment.

    Args:
        model:     The fitted classifier (must expose .predict()).
        cols:      Feature column names from feature_columns().
        eval_seed: RNG seed for the held-out generation run.
        minutes:   Length of the evaluation dataset (matches training data length).

    Returns:
        dict with cross_seed_macro_f1, per-class metrics, and eval_seed.
    """
    # Lazy import to avoid pulling in the synthetic data generator at module
    # load time (keeps unit-test imports fast when train.py is imported).
    from ml.synthetic_data import generate

    print(f"[cross-seed eval] generating {minutes}-minute held-out set "
          f"with seed={eval_seed} ...")
    df_eval = generate(minutes, seed=eval_seed)
    feats_eval = build_features(df_eval)
    X_eval = feats_eval[cols].fillna(0).to_numpy()
    y_eval = feats_eval["label"].astype(int).to_numpy()

    preds = model.predict(X_eval)

    # Compute macro-F1 and per-class stats using whichever metrics library
    # is available (sklearn preferred; NumPy fallback via baseline_model).
    try:
        from sklearn.metrics import f1_score, classification_report
        cross_macro_f1 = float(f1_score(y_eval, preds, average="macro",
                                         zero_division=0))
        report = classification_report(y_eval, preds, output_dict=True,
                                        zero_division=0)
    except ImportError:
        from ml.baseline_model import macro_f1_report
        rep = macro_f1_report(y_eval, preds, N_CLASSES)
        cross_macro_f1 = rep["macro_f1"]
        report = rep["per_class"]

    # Class balance in the held-out set (useful for spotting skewed generation)
    label_counts = {int(k): int(v)
                    for k, v in zip(*np.unique(y_eval, return_counts=True))}

    print(f"[cross-seed eval] macro-F1 on seed-{eval_seed} held-out set: "
          f"{cross_macro_f1:.3f}  (label dist: {label_counts})")

    return {
        "cross_seed_macro_f1": cross_macro_f1,
        "eval_seed": eval_seed,
        "eval_minutes": minutes,
        "eval_label_distribution": label_counts,
        "eval_report": report,
    }


def _train_sklearn(X, y, cols, out, folds):
    from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
    from sklearn.metrics import classification_report, confusion_matrix, f1_score
    try:
        from xgboost import XGBClassifier
        model = XGBClassifier(n_estimators=400, max_depth=6, learning_rate=0.08,
                              subsample=0.9, colsample_bytree=0.9,
                              eval_metric="mlogloss", n_jobs=4)
        kind = "xgboost"
    except Exception:
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(random_state=0)
        kind = "sklearn-gbdt"

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)
    cv_macro_f1 = None
    try:
        min_class = int(np.bincount(y).min())
        nf = max(2, min(folds, min_class))
        skf = StratifiedKFold(n_splits=nf, shuffle=True, random_state=0)
        cv_macro_f1 = float(np.mean(cross_val_score(model, X, y, cv=skf, scoring="f1_macro")))
        print(f"CV macro-F1 ({nf}-fold): {cv_macro_f1:.3f}")
    except Exception as e:  # pragma: no cover
        print(f"[warn] CV skipped: {e}")
    model.fit(Xtr, ytr)
    preds = model.predict(Xte)
    test_macro_f1 = float(f1_score(yte, preds, average="macro"))
    print(f"trained {kind} | same-seed test macro-F1: {test_macro_f1:.3f}")
    print(classification_report(yte, preds, zero_division=0))
    metrics = {"cv_macro_f1": cv_macro_f1, "test_macro_f1": test_macro_f1,
               "report": classification_report(yte, preds, output_dict=True, zero_division=0),
               "confusion_matrix": confusion_matrix(yte, preds).tolist()}
    return model, kind, metrics


def _train_baseline(X, y, cols, out):
    from ml.baseline_model import train_softmax, macro_f1_report, stratified_split
    tr, te = stratified_split(y, test_frac=0.25, seed=0)
    model = train_softmax(X[tr], y[tr], N_CLASSES)
    preds = model.predict(X[te])
    rep = macro_f1_report(y[te], preds, N_CLASSES)
    print(f"trained softmax-baseline (NumPy) | same-seed test macro-F1: {rep['macro_f1']:.3f}")
    for c, m in rep["per_class"].items():
        print(f"  {LABELS[c]:18s} P={m['precision']:.2f} R={m['recall']:.2f} "
              f"F1={m['f1']:.2f} (n={m['support']})")
    return model, "softmax-baseline", {"test_macro_f1": rep["macro_f1"], "report": rep["per_class"]}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Train the fault-class predictor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Cross-seed evaluation example:\n"
            "  python -m ml.train --data data/telemetry.parquet --out models/ \\\n"
            "    --eval-seed 42 --eval-minutes 240\n\n"
            "This generates a second, independent synthetic dataset with seed=42\n"
            "and evaluates the trained model on it. The metric is stored in\n"
            "meta.json as cross_seed_macro_f1 and is the honest generalisation\n"
            "estimate across generation runs."
        ),
    )
    ap.add_argument("--data", default="data/train.parquet",
                    help="Training data (parquet or csv).")
    ap.add_argument("--out", default="models/",
                    help="Output directory for model artifacts.")
    ap.add_argument("--folds", type=int, default=5,
                    help="Number of CV folds (sklearn path only).")
    ap.add_argument("--baseline", action="store_true",
                    help="Force the NumPy-only softmax baseline (no sklearn/xgboost).")
    ap.add_argument(
        "--eval-seed", type=int, default=None, metavar="SEED",
        help=(
            "If set, generate an independent held-out dataset with this RNG seed "
            "(different fault-window timing + noise realisation than the training "
            "data) and report cross_seed_macro_f1 alongside the same-seed split "
            "metric. Use a seed different from the one used to generate --data "
            "(default training seed is 7). Recommended: --eval-seed 42."
        ),
    )
    ap.add_argument(
        "--eval-minutes", type=int, default=None, metavar="N",
        help=(
            "Minutes of synthetic data to generate for cross-seed evaluation. "
            "Defaults to the same length as the training dataset (inferred from "
            "its row count). Only used when --eval-seed is set."
        ),
    )
    args = ap.parse_args()

    df = _load(args.data)
    feats = build_features(df)
    cols = feature_columns()
    X = feats[cols].fillna(0).to_numpy()
    y = feats["label"].astype(int).to_numpy()

    if args.baseline:
        model, kind, metrics = _train_baseline(X, y, cols, args.out)
    else:
        try:
            model, kind, metrics = _train_sklearn(X, y, cols, args.out, args.folds)
        except ImportError:
            print("[info] scikit-learn not installed -> using NumPy softmax baseline.")
            model, kind, metrics = _train_baseline(X, y, cols, args.out)

    # -----------------------------------------------------------------------
    # Cross-seed held-out evaluation
    # -----------------------------------------------------------------------
    if args.eval_seed is not None:
        # Infer training dataset length from row count.
        # generate() produces (minutes * n_nodes) rows; n_nodes = 5.
        n_nodes = 5
        inferred_minutes = len(df) // n_nodes
        eval_minutes = args.eval_minutes if args.eval_minutes is not None else inferred_minutes
        cross_metrics = _cross_seed_eval(model, cols, args.eval_seed, eval_minutes)
        metrics.update(cross_metrics)
        print(
            f"\n[summary] same-seed test macro-F1 : {metrics['test_macro_f1']:.3f}\n"
            f"[summary] cross-seed macro-F1      : {cross_metrics['cross_seed_macro_f1']:.3f}"
            f"  (eval seed={args.eval_seed}, n={eval_minutes} min)"
        )
    # -----------------------------------------------------------------------

    version = _save_artifacts(args.out, model, kind, cols, metrics)
    print(f"saved model v_{version} ({kind}) and updated 'latest' -> {args.out}")


if __name__ == "__main__":
    main()
