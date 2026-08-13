"""Retrieve relevant runbook chunks for a query. Mirrors indexer's backend."""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")


def _tf(text: str) -> Counter:
    return Counter(_TOKEN.findall(text.lower()))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[t] * b[t] for t in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


class Retriever:
    def __init__(self, index_dir: str = "data/faiss_index"):
        self.index_dir = index_dir
        with open(os.path.join(index_dir, "meta.json")) as f:
            meta = json.load(f)
        self.backend = meta["backend"]
        self.items = meta["items"]
        self._faiss = None
        self._model = None
        if self.backend.startswith("faiss"):
            import faiss  # type: ignore
            from sentence_transformers import SentenceTransformer
            self._faiss = faiss.read_index(os.path.join(index_dir, "index.faiss"))
            self._model = SentenceTransformer("BAAI/bge-small-en-v1.5")
        else:
            # Pure-python bag-of-words cosine (no ML deps required).
            self._tfs = [_tf(it["text"]) for it in self.items]

    def search(self, query: str, k: int = 4) -> list[dict]:
        if self._faiss is not None:
            q = self._model.encode([query], normalize_embeddings=True)
            scores, idx = self._faiss.search(q, k)
            return [dict(self.items[i], score=float(scores[0][r])) for r, i in enumerate(idx[0])]
        # bag-of-words fallback
        qtf = _tf(query)
        scored = sorted(
            ((_cosine(qtf, t), i) for i, t in enumerate(self._tfs)),
            key=lambda x: x[0], reverse=True,
        )
        return [dict(self.items[i], score=float(s)) for s, i in scored[:k]]
