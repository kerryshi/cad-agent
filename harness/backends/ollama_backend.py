"""Ollama backend (local models via localhost:11434, stdlib HTTP only).

Division of labour (measured, not assumed): local models are a genuine peer
for EXTRACTION — with `schema` they decode under the spec's JSON schema
(Ollama `format`, temperature 0), which guarantees shape but not values —
while CadQuery codegen stays the honest local-vs-frontier gap column.
"""

from __future__ import annotations

import json
import urllib.request

DEFAULT_MODEL = "llama3.1:8b"  # best measured extractor (2026-07-21): 8/8 + 5/5 constrained
DEFAULT_URL = "http://localhost:11434"


class OllamaBackend:
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_URL,
                 timeout: float = 900.0):  # 14B on a 12GB card legitimately needs minutes
        self.name = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str, schema: dict | None = None) -> str:
        req_body: dict = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            req_body["format"] = schema
            req_body["options"] = {"temperature": 0}  # per Ollama structured-output docs
        payload = json.dumps(req_body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["message"]["content"]
