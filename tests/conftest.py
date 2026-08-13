"""Test fixtures. Builds a tiny stand-in model + RAG index so tests run with no
heavy ML deps (sklearn/xgboost/faiss) installed.
"""
import json
import os
import pickle
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml.feature_engineering import feature_columns  # noqa: E402


class DummyModel:
    classes_ = [0, 1, 2, 3, 4]

    def predict_proba(self, X):
        X = np.asarray(X)
        return np.tile(np.array([0.55, 0.15, 0.1, 0.1, 0.1]), (len(X), 1))

    def predict(self, X):
        return np.argmax(self.predict_proba(X), axis=1)


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    pickle.dump(DummyModel(), open(d / "predictor.pkl", "wb"))
    json.dump({"kind": "dummy", "features": feature_columns()}, open(d / "meta.json", "w"))
    return str(d)


@pytest.fixture(scope="session")
def index_dir(tmp_path_factory):
    from rag.indexer import build_index
    d = tmp_path_factory.mktemp("index")
    build_index("rag/runbooks", str(d))
    return str(d)
