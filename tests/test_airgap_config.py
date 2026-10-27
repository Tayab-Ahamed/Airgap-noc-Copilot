from dataclasses import replace

import pytest

from core.config import settings


@pytest.mark.parametrize("url", [
    "http://localhost.attacker.example:11434",
    "http://127.0.0.1.attacker.example:11434",
    "http://ollama.attacker.example:11434",
    "http://localhost@attacker.example:11434",
    "http://ollama:bad-port",
    "file://localhost/model",
])
def test_airgap_rejects_spoofed_or_invalid_endpoints(url):
    with pytest.raises(RuntimeError):
        replace(settings, ollama_url=url).assert_airgap_safe()


@pytest.mark.parametrize("url", [
    "http://localhost:11434", "http://127.0.0.1:11434", "http://ollama:11434",
    "http://[::1]:11434", "https://localhost:11434",
])
def test_airgap_accepts_exact_local_endpoints(url):
    replace(settings, ollama_url=url).assert_airgap_safe()
