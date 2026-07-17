"""Ollama backend (local models via localhost:11434, stdlib HTTP only).

Local 4B/8B models will score low on CadQuery codegen — this backend exists to
prove the peer-agent contract (any model can drive the same toolchain), not to
claim quality parity.
"""

from __future__ import annotations

import json
import urllib.request

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_URL = "http://localhost:11434"


class OllamaBackend:
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_URL,
                 timeout: float = 300.0):
        self.name = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["message"]["content"]
