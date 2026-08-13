"""Central configuration, sourced from environment (12-factor, air-gap safe).

No secrets are hardcoded. At an air-gapped venue, set these via a local .env
(see .env.example) or the process environment. Nothing here reaches the network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Service
    host: str = os.getenv("NOC_HOST", "0.0.0.0")
    port: int = int(os.getenv("NOC_PORT", "8000"))
    log_level: str = os.getenv("NOC_LOG_LEVEL", "INFO")
    log_json: bool = _bool("NOC_LOG_JSON", True)

    # Auth (API key). If empty, auth is disabled (dev only).
    api_key: str = os.getenv("NOC_API_KEY", "")

    # Paths
    model_dir: str = os.getenv("MODEL_DIR", "models/")
    index_dir: str = os.getenv("INDEX_DIR", "data/faiss_index")
    db_path: str = os.getenv("NOC_DB_PATH", "data/noc.db")

    # Stream
    stream_interval_s: int = int(os.getenv("STREAM_INTERVAL", "5"))
    window_minutes: int = int(os.getenv("NOC_WINDOW", "30"))

    # LLM
    use_ollama: bool = _bool("USE_OLLAMA", False)
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")

    # Safety: refuse to start if a cloud-looking endpoint is configured.
    def assert_airgap_safe(self) -> None:
        bad = ("http://", "https://")
        url = self.ollama_url
        if not (url.startswith("http://localhost") or url.startswith("http://127.0.0.1")
                or url.startswith("http://ollama")):
            raise RuntimeError(
                f"OLLAMA_URL={url} is not a local/internal endpoint; refusing for air-gap safety."
            )


settings = Settings()
