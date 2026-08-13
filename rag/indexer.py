"""Build a FAISS index over internal runbooks/topology docs.

Fully offline once the embedding model is pre-downloaded:
    huggingface-cli download BAAI/bge-small-en-v1.5

Falls back to a hashing vectorizer if sentence-transformers/faiss are missing,
so indexing + retrieval still work for development.

Usage:
    python -m rag.indexer --docs rag/runbooks --out data/faiss_index
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle

EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _chunks(text: str, size: int = 600, overlap: int = 80):
    words = text.split()
    i = 0
    while i < len(words):
        yield " ".join(words[i:i + size])
        i += size - overlap


def _load_docs(docs_dir: str):
    items = []
    for path in sorted(glob.glob(os.path.join(docs_dir, "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for j, ch in enumerate(_chunks(text)):
            items.append({"id": f"{os.path.basename(path)}#{j}", "source": path, "text": ch})
    return items


def build_index(docs_dir: str, out_dir: str) -> None:
    items = _load_docs(docs_dir)
    os.makedirs(out_dir, exist_ok=True)
    texts = [it["text"] for it in items]

    try:
        import faiss  # type: ignore
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        emb = model.encode(texts, normalize_embeddings=True)
        index = faiss.IndexFlatIP(emb.shape[1])
        index.add(emb)
        faiss.write_index(index, os.path.join(out_dir, "index.faiss"))
        backend = "faiss+bge"
    except Exception:
        # Pure-python fallback: no embeddings needed at index time. Retrieval uses
        # a bag-of-words cosine over the stored chunk texts (see retriever.py).
        backend = "bow-fallback"

    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"backend": backend, "items": items}, f)
    print(f"indexed {len(items)} chunks using {backend} -> {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="rag/runbooks")
    ap.add_argument("--out", default="data/faiss_index")
    args = ap.parse_args()
    build_index(args.docs, args.out)
