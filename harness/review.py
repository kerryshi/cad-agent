"""Human review surface for staged builds: page, verdict, send gate.

Every `harness.make` build stages a self-contained `review.html` next to its
artifacts — renders (embedded, the file works anywhere), the extracted spec,
backends, slice stats, and the build's review status. The human looks at the
page, then records a verdict from the CLI:

  python -m harness.review <build-dir>                      # (re)generate page
  python -m harness.review <build-dir> --approve
  python -m harness.review <build-dir> --reject --comment "arms too thin"

An approval is bound to sha256 hashes of the staged artifacts (.step files
and .gcode.3mf), so ANY rebuild or re-slice invalidates it — `send_gate` is
what `harness.send` consults; it refuses on a missing verdict, a rejection,
or stale hashes. Rejection requires a comment: the comment is the revision
request the human takes back to the agent.

This module is deliberately verdict-only — it never edits specs or re-runs
codegen. Revision happens through the normal conversational loop; the gate
just makes an unreviewed print mechanically impossible.

Exit codes: 0 ok, 1 not a staged build (no manifest.json), 2 bad verdict
input (reject without --comment).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import time
from pathlib import Path

from toolchain.families import get_family

REVIEW_JSON = "review.json"
REVIEW_HTML = "review.html"

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem;
       padding: 0 1rem; background: #14161a; color: #d7dae0; }
h1 { margin-bottom: 0.2rem; } h2 { margin-top: 2rem; border-bottom: 1px solid
#333; padding-bottom: 0.3rem; }
.badge { display: inline-block; padding: 0.25rem 0.8rem; border-radius: 1rem;
         font-weight: 700; letter-spacing: 0.05em; }
.pending { background: #4a3f13; color: #f2c744; }
.approved { background: #1d4023; color: #6fdc84; }
.rejected { background: #4a1a1a; color: #f27b6c; }
.stale { background: #3d2c4a; color: #c79bf2; }
.request { font-size: 1.1rem; font-style: italic; color: #aab2bf; }
table { border-collapse: collapse; } td, th { padding: 0.3rem 0.9rem 0.3rem 0;
        text-align: left; } th { color: #8a93a3; font-weight: 600; }
.grid { display: flex; flex-wrap: wrap; gap: 0.8rem; }
.grid figure { margin: 0; } .grid img { width: 21rem; max-width: 100%;
        border-radius: 0.4rem; background: #fff; }
figcaption { color: #8a93a3; font-size: 0.85rem; padding-top: 0.2rem; }
pre { background: #1d2026; padding: 0.8rem; border-radius: 0.4rem;
      overflow-x: auto; }
code { background: #1d2026; padding: 0.1rem 0.3rem; border-radius: 0.2rem; }
.cmd { margin: 0.4rem 0; }
"""


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_hashes(build_dir: Path, family) -> dict[str, str]:
    """sha256 per staged artifact the verdict must cover: the verified STEP
    files plus every staged .gcode.3mf (what actually reaches the printer)."""
    build_dir = Path(build_dir)
    hashes: dict[str, str] = {}
    for filename in family.output_files:
        part = filename.split(".")[0]
        for rel in (Path(filename), Path(part) / f"{part}.gcode.3mf"):
            path = build_dir / rel
            if path.is_file():
                hashes[rel.as_posix()] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
    return hashes


def review_state(build_dir: Path, family) -> tuple[str, str]:
    """(state, detail) with state in pending|approved|rejected|stale."""
    rj = _load_json(Path(build_dir) / REVIEW_JSON)
    if rj is None:
        return "pending", "no verdict recorded"
    if rj.get("verdict") == "rejected":
        return "rejected", rj.get("comment", "")
    if rj.get("hashes") != artifact_hashes(build_dir, family):
        return "stale", "artifacts changed since the verdict"
    return "approved", rj.get("comment", "")


def send_gate(build_dir: Path, family) -> tuple[bool, str]:
    """The gate harness.send consults; only a current approval passes."""
    state, detail = review_state(build_dir, family)
    if state == "pending":
        return False, (
            "no verdict recorded — open review.html, then: "
            f"python -m harness.review {build_dir} --approve")
    if state == "rejected":
        return False, f"verdict is REJECTED: {detail}"
    if state == "stale":
        return False, (
            "approval is STALE — artifacts changed since the verdict; "
            "re-review review.html and approve again")
    return True, "approved"


def _render_figures(build_dir: Path) -> str:
    renders = sorted((Path(build_dir) / "renders").glob("*.png"))
    if not renders:
        return "<p>No renders staged.</p>"
    by_part: dict[str, list[Path]] = {}
    for p in renders:
        by_part.setdefault(p.stem.split("_")[0], []).append(p)
    blocks = []
    for part, paths in by_part.items():
        figs = []
        for p in paths:
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            view = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
            figs.append(
                f'<figure><img src="data:image/png;base64,{data}" '
                f'alt="{html.escape(p.stem)}">'
                f"<figcaption>{html.escape(view)}</figcaption></figure>")
        blocks.append(f"<h3>{html.escape(part)}</h3>"
                      f'<div class="grid">{"".join(figs)}</div>')
    return "".join(blocks)


def _slice_table(manifest: dict) -> str:
    slices = manifest.get("slices") or []
    if not slices:
        return "<p>Not sliced (--no-slice build) — slice before sending.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(str(s['part']))}</td><td>{s['minutes']} min"
        f"</td><td>{s['layers']}</td><td>{s['cm3']} cm3</td></tr>"
        for s in slices)
    return ("<table><tr><th>part</th><th>time</th><th>layers</th>"
            f"<th>filament</th></tr>{rows}</table>")


_BADGES = {
    "pending": ("pending", "PENDING REVIEW"),
    "approved": ("approved", "APPROVED"),
    "rejected": ("rejected", "REJECTED"),
    "stale": ("stale", "APPROVAL STALE"),
}


def write_review_page(build_dir: Path) -> Path:
    """Generate review.html from the staged build. Family comes from the
    manifest — the page needs no flags to regenerate."""
    build_dir = Path(build_dir)
    manifest = _load_json(build_dir / "manifest.json")
    if manifest is None:
        raise FileNotFoundError(f"not a staged build (no manifest.json): {build_dir}")
    family = get_family(manifest["family"])
    state, detail = review_state(build_dir, family)
    css_class, label = _BADGES[state]
    detail_html = f" — {html.escape(detail)}" if state in (
        "rejected", "stale") and detail else ""

    d = html.escape(str(build_dir))
    py = "python"
    verdict_help = (
        f'<div class="cmd">approve: <code>{py} -m harness.review "{d}" '
        "--approve</code></div>"
        f'<div class="cmd">reject: <code>{py} -m harness.review "{d}" '
        '--reject --comment "what to change"</code></div>'
        f'<div class="cmd">send (after approval): <code>{py} -m harness.send '
        f'"{d}" --plate-clear</code></div>')

    page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{html.escape(build_dir.name)} — review</title>
<style>{_CSS}</style></head><body>
<h1>{html.escape(build_dir.name)}</h1>
<p><span class="badge {css_class}">{label}</span>{detail_html}</p>
<p class="request">&ldquo;{html.escape(manifest.get("request", ""))}&rdquo;</p>
<table>
<tr><th>family</th><td>{html.escape(manifest.get("family", ""))}</td></tr>
<tr><th>extraction</th><td>{html.escape(manifest.get("extract_backend", ""))}</td></tr>
<tr><th>codegen</th><td>{html.escape(manifest.get("codegen_backend", ""))}
 ({manifest.get("iterations", "?")} iteration(s))</td></tr>
<tr><th>created</th><td>{html.escape(manifest.get("created", ""))}</td></tr>
</table>
<h2>Renders</h2>
{_render_figures(build_dir)}
<h2>Extracted spec — check these numbers</h2>
<pre>{html.escape(json.dumps(manifest.get("spec", {}), indent=2))}</pre>
<h2>Print files</h2>
{_slice_table(manifest)}
<h2>Verdict</h2>
{verdict_help}
<p>Generated {html.escape(time.strftime("%Y-%m-%d %H:%M:%S"))}. Approval is
hash-bound: any rebuild or re-slice makes it stale.</p>
</body></html>
"""
    out = build_dir / REVIEW_HTML
    out.write_text(page, encoding="utf-8")
    return out


def record_verdict(build_dir: Path, verdict: str, comment: str) -> None:
    build_dir = Path(build_dir)
    manifest = _load_json(build_dir / "manifest.json")
    family = get_family(manifest["family"])
    record = {
        "verdict": verdict,
        "comment": comment,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hashes": artifact_hashes(build_dir, family),
    }
    (build_dir / REVIEW_JSON).write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("build_dir", help="staged build directory (has manifest.json)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true",
                   help="record approval, hash-bound to the current artifacts")
    g.add_argument("--reject", action="store_true",
                   help="record rejection (--comment required)")
    p.add_argument("--comment", default="",
                   help="reviewer note; the revision request on a rejection")
    args = p.parse_args(argv)

    build_dir = Path(args.build_dir)
    if not (build_dir / "manifest.json").is_file():
        print(f"REFUSED: not a staged build (no manifest.json): {build_dir}")
        return 1
    if args.reject and not args.comment:
        print("REFUSED: --reject requires --comment — the comment is the "
              "revision request; a bare rejection helps no one")
        return 2

    if args.approve or args.reject:
        verdict = "approved" if args.approve else "rejected"
        record_verdict(build_dir, verdict, args.comment)
        print(f"recorded: {verdict}" + (f" ({args.comment})" if args.comment else ""))
    page = write_review_page(build_dir)
    print(f"review page: {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
