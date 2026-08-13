#!/usr/bin/env bash
# Run this WHILE YOU STILL HAVE INTERNET. Nothing here works at the air-gapped venue.
set -euo pipefail

echo "==> Pulling Ollama models"
ollama pull llama3.2
ollama pull qwen2.5:3b   # smaller fallback

echo "==> Downloading embedding model for RAG"
pip install -U "huggingface_hub[cli]"
huggingface-cli download BAAI/bge-small-en-v1.5

echo "==> Pulling containerlab + FRR images"
docker pull frrouting/frr:v8.4.0 || echo "(install containerlab/docker if missing)"

echo "==> Building a local wheelhouse for all Python deps (air-gap insurance)"
mkdir -p wheelhouse
pip download -r requirements.txt -d wheelhouse/

echo "==> DONE. Verify on a disconnected machine:"
echo "    pip install --no-index --find-links wheelhouse/ -r requirements.txt"
