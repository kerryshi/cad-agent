"""VLM gross-error critique of rendered parts.

Role boundary (settled 2026-07-02, don't relitigate): a vision model cannot
measure dimensions — dimensional truth is verify.py's job. The VLM answers one
question: does the rendered part *grossly* match the spec (features present,
plausible proportions, nothing wildly wrong)? Its verdict is advisory signal
for the codegen loop, never a gate that overrides the probes.

NEGATIVE RESULT (2026-07-17, two prompt shapes tried, stopped per 2-strikes
rule): qwen2.5vl:7b has no discriminative signal on this task — with 6 views
it hallucinates issues on a known-good part; with 2 views it passes a
missing-post sabotage. The module is mechanically sound (calls the model,
parses the verdict) but is NOT wired into the loop. Revisit with a frontier
vision model (Claude via the Anthropic backend) once API credentials exist.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from harness.prompts import strip_code_fences
from toolchain.spec import EnclosureSpec

DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_URL = "http://localhost:11434"

SYSTEM = """You review 3D renders of a printed-enclosure design against its spec. \
You CANNOT measure dimensions from images — do not report dimensional issues. \
Report only GROSS mismatches: a feature that is missing or extra (standoffs, \
cutouts, corner posts, screw holes), a wildly wrong shape, or a part that is \
obviously not the described object. Reply with ONLY a JSON object: \
{"looks_consistent": true|false, "issues": ["..."]} — empty issues list if consistent."""


@dataclass
class Critique:
    looks_consistent: bool
    issues: list[str] = field(default_factory=list)
    raw: str = ""


def _b64(path: Path) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def critique_renders(spec: EnclosureSpec, render_paths: list[Path],
                     model: str = DEFAULT_MODEL, base_url: str = DEFAULT_URL,
                     timeout: float = 300.0) -> Critique:
    body_views = [p for p in render_paths if Path(p).name.startswith("body_")]
    lid_views = [p for p in render_paths if Path(p).name.startswith("lid_")]
    user = (
        f"Spec:\n{spec.model_dump_json(indent=2)}\n\n"
        f"The first {len(body_views)} image(s) show the BODY from several angles; "
        f"the next {len(lid_views)} show the LID. The body should show 4 corner "
        f"posts, {len(spec.standoffs)} standoff(s), {len(spec.cutouts)} wall "
        "cutout(s); the lid is a flat plate with 4 screw holes. Verdict?"
    )
    payload = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user,
             "images": [_b64(p) for p in [*body_views, *lid_views]]},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = json.loads(resp.read().decode("utf-8"))["message"]["content"]

    try:
        raw = strip_code_fences(text)
        data = json.loads(raw[raw.find("{"):])
        return Critique(bool(data.get("looks_consistent", False)),
                        [str(i) for i in data.get("issues", [])], raw=text)
    except (ValueError, KeyError):
        # unparseable verdict = no signal, not a failure — advisory only
        return Critique(looks_consistent=True,
                        issues=[f"unparseable VLM reply: {text[:200]}"], raw=text)
