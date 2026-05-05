"""Build a self-contained evidence-bundle HTML for a run.

Output: a single file that, when double-clicked, opens the dashboard with
the run's full evidence pack already loaded — no host, no extra files. The
recipient verifies on Base Sepolia (one RPC call per run) without trusting
the sender. Per D7, this is the *default* share mode.

Pipeline:
  1. Build the pack via promptseal/scripts/04_export_evidence_pack.py logic.
  2. Run `SELF_CONTAINED=1 npm run build` in dashboard/ to produce a
     single-file dist/index.html (vite-plugin-singlefile inlines all JS+CSS).
  3. Inject `<script>window.__PROMPTSEAL_EVIDENCE__ = "<base64>";</script>`
     before the first <script tag. main.tsx detects this and switches the
     router to HashRouter (file:// can't do BrowserRouter).
  4. Write evidence-bundle-<run_id>.html.

Usage:
    python scripts/build_self_contained.py <run_id>
    python scripts/build_self_contained.py <run_id> --output /tmp/x.html
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich import print as rprint

# Reuse build_evidence_pack from scripts/04_export_evidence_pack.py without
# making that file an import target by name (leading digit).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXPORT_SCRIPT = _REPO_ROOT / "scripts" / "04_export_evidence_pack.py"
_spec = importlib.util.spec_from_file_location(
    "promptseal_export_evidence_pack", _EXPORT_SCRIPT,
)
assert _spec and _spec.loader
_export_mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("promptseal_export_evidence_pack", _export_mod)
_spec.loader.exec_module(_export_mod)


SELF_CONTAINED_SIZE_WARN_BYTES = 5 * 1024 * 1024  # PLAN R3


class BuildSelfContainedError(Exception):
    """Raised when the bundle can't be produced."""


def run_vite_build(dashboard_dir: Path) -> Path:
    """Run `npm run build:single` in `dashboard_dir`. Returns dist/index.html."""
    rprint(f"[dim]→ running SELF_CONTAINED=1 npm run build in {dashboard_dir}…[/dim]")
    result = subprocess.run(
        ["npm", "run", "build:single"],
        cwd=dashboard_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildSelfContainedError(
            "vite build failed:\n"
            f"  stdout: {result.stdout[-1000:]}\n"
            f"  stderr: {result.stderr[-1000:]}"
        )
    out = dashboard_dir / "dist" / "index.html"
    if not out.exists():
        raise BuildSelfContainedError(
            f"vite build reported success but {out} is missing"
        )
    return out


def inject_evidence(html_template: str, pack: dict[str, Any]) -> str:
    """Inline `pack` as a base64 JS string assigned to window.__PROMPTSEAL_EVIDENCE__.

    The injected `<script>` is placed immediately BEFORE the first existing
    `<script` tag, so the global is set before main.tsx runs.
    """
    pack_json = json.dumps(
        pack, separators=(",", ":"), sort_keys=True, ensure_ascii=False,
    )
    b64 = base64.b64encode(pack_json.encode("utf-8")).decode("ascii")
    snippet = f'<script>window.__PROMPTSEAL_EVIDENCE__ = "{b64}";</script>'
    if "<script" not in html_template:
        raise BuildSelfContainedError(
            "no <script tag found in vite output — the singlefile plugin "
            "may not have inlined the bundle"
        )
    return html_template.replace("<script", snippet + "<script", 1)


def build_self_contained(
    run_id: str,
    *,
    db_path: Path,
    dashboard_dir: Path,
    output_path: Path | None = None,
) -> Path:
    """Build the bundle. Returns the path written.

    Raises BuildSelfContainedError on any failure (no run, no anchor, vite
    failure, missing template).
    """
    pack = _export_mod.build_evidence_pack(run_id, db_path)

    template_path = run_vite_build(dashboard_dir)
    template = template_path.read_text(encoding="utf-8")

    final_html = inject_evidence(template, pack)

    if output_path is None:
        output_path = Path(f"./evidence-bundle-{run_id}.html")
    output_path.write_text(final_html, encoding="utf-8")

    size = output_path.stat().st_size
    if size > SELF_CONTAINED_SIZE_WARN_BYTES:
        rprint(
            f"[yellow]warning:[/yellow] bundle is "
            f"{size / 1024 / 1024:.1f} MB (PLAN R3 caps at "
            f"{SELF_CONTAINED_SIZE_WARN_BYTES / 1024 / 1024:.0f} MB)"
        )
    return output_path


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Build a single-file HTML evidence bundle for a run.",
    )
    parser.add_argument("run_id", help="Run id, e.g. run-e8b202cfc898")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML path (default: ./evidence-bundle-<run_id>.html).",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    dashboard_dir = _REPO_ROOT / "dashboard"

    try:
        out = build_self_contained(
            args.run_id,
            db_path=db_path,
            dashboard_dir=dashboard_dir,
            output_path=args.output,
        )
    except _export_mod.EvidencePackError as exc:
        rprint(f"[red]Build failed:[/red] {exc}")
        return 1
    except BuildSelfContainedError as exc:
        rprint(f"[red]Build failed:[/red] {exc}")
        return 2

    size_kb = out.stat().st_size / 1024
    rprint(f"[bold green]✓ wrote {out}[/bold green] ({size_kb:.1f} KB)")
    rprint(
        "  Open it directly with your browser "
        f"([dim]file://{out.resolve()}[/dim]) — no server needed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
