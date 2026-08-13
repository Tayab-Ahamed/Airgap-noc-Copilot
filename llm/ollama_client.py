"""Thin client for a locally running Ollama server (air-gapped).

No network egress beyond localhost. If Ollama isn't reachable, callers should
use the mock fallback in copilot.py so development never blocks.

Pre-download (while online):
    ollama pull llama3.2
    ollama pull qwen2.5:3b   # fallback
"""
from __future__ import annotations

import json
import os
import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")


def is_available(timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def generate(prompt: str, system: str = "", temperature: float = 0.2) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": temperature},
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
    r.raise_for_status()
    return r.json().get("response", "")
