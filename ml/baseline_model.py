"""Dependency-free (NumPy-only) multinomial logistic-regression classifier.

This is the OFFLINE fallback model so the whole system runs end-to-end with no
sklearn/xgboost installed (handy at an air-gapped venue before wheels are set up,
and for instant demos). The production path is still XGBoost via ml/train.py;
this class is pickle-compatible with RiskScorer (exposes `predict_proba`,
`predict`, and `classes_`).
"""
from __future__ import annotations

import numpy as np


class SoftmaxClassifier:
    def __init__(self, n_classes, mean=None, std=None, W=None, b=None, classes_=None):
        self.n_classes = n_classes
        self.mean = mean
        self.std = std
        self.W = W
        self.b = b
        self.classes_ = classes_ if classes_ is not None else list(range(n_classes))

    def _norm(self, X):
        return (np.asarray(X, dtype=float) - self.mean) / self.std

    def predict_proba(self, X):
        Z = self._norm(X) @ self.W + self.b
        Z -= Z.max(axis=1, keepdims=True)
        E = np.exp(Z)
        return E / E.sum(axis=1, keepdims=True)

    def predict(self, X):
        return np.asarray(self.classes_)[np.argmax(self.predict_proba(X), axis=1)]


def train_softmax(X, y, n_classes, epochs=1200, lr=0.3, l2=1e-4,
                  class_weight=True, seed=0) -> SoftmaxClassifier:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Xn = (X - mean) / std
    n, d = Xn.shape
    W = np.zeros((d, n_classes))
    b = np.zeros(n_classes)
    Y = np.eye(n_classes)[y]
    if class_weight:
        # Soften with sqrt so minority classes are helped without flooding the
        # model with false positives (which tanks nominal recall).
        counts = np.bincount(y, minlength=n_classes).astype(float)
        w = np.sqrt(n / (n_classes * np.maximum(counts, 1)))
        sw = w[y][:, None]
    else:
        sw = np.ones((n, 1))
    for _ in range(epochs):
        Z = Xn @ W + b
        Z -= Z.max(axis=1, keepdims=True)
        E = np.exp(Z)
        P = E / E.sum(axis=1, keepdims=True)
        G = (P - Y) * sw
        W -= lr * (Xn.T @ G / n + l2 * W)
        b -= lr * (G.sum(axis=0) / n)
    return SoftmaxClassifier(n_classes, mean, std, W, b, list(range(n_classes)))


def macro_f1_report(y_true, y_pred, n_classes):
    """Pure-numpy per-class precision/recall/F1 + macro-F1."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    per_class = {}
    f1s = []
    for c in range(n_classes):
        tp = int(((y_pred == c) & (y_true == c)).sum())
        fp = int(((y_pred == c) & (y_true != c)).sum())
        fn = int(((y_pred != c) & (y_true == c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[c] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
        f1s.append(f1)
    return {"per_class": per_class, "macro_f1": float(np.mean(f1s))}


def stratified_split(y, test_frac=0.25, seed=0):
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    test_idx = []
    train_idx = []
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        rng.shuffle(idx)
        k = max(1, int(len(idx) * test_frac))
        test_idx += list(idx[:k])
        train_idx += list(idx[k:])
    return np.array(train_idx), np.array(test_idx)
