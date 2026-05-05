"""Publish a run's evidence pack — JSON, optional self-contained HTML, and
optional upload to a GitHub Release.

This script REUSES (does not re-implement) two earlier scripts:
  - scripts/04_export_evidence_pack.py for the JSON pack
  - scripts/build_self_contained.py for the single-file HTML bundle

Usage:
    python scripts/06_publish_evidence.py <run_id>
    python scripts/06_publish_evidence.py <run_id> --build-html
    python scripts/06_publish_evidence.py <run_id> --build-html \\
        --upload-github-release v0.2-evidence-bob
    python scripts/06_publish_evidence.py <run_id> --output-dir /tmp/pub
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich import print as rprint

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_script(filename: str, module_name: str) -> Any:
    """Load a sibling script as a module. Names with leading digits aren't
    valid Python identifiers; importlib.util.spec_from_file_location is the
    portable workaround we've used since A1 / B6."""
    path = _REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, mod)
    spec.loader.exec_module(mod)
    return mod


_export_mod = _import_script(
    "04_export_evidence_pack.py", "promptseal_export_evidence_pack",
)
_build_mod = _import_script(
    "build_self_contained.py", "promptseal_build_self_contained",
)


class PublishError(Exception):
    """Raised when a publish step fails (gh missing, no remote, upload failed)."""


# --- gh / git helpers ------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """subprocess.run wrapper that captures stdout/stderr as text. Tests
    monkeypatch subprocess.run inside this module — keeping a single
    indirection point makes that clean."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _check_gh_installed() -> str:
    """Return gh's first version line, or raise PublishError."""
    res = _run(["gh", "--version"])
    if res.returncode != 0:
        raise PublishError(
            "`gh` CLI not installed or not on PATH. "
            "Install: https://cli.github.com/. Then `gh auth login`."
        )
    return res.stdout.splitlines()[0] if res.stdout else "gh"


def _git_remote_url() -> str | None:
    res = _run(["git", "remote", "get-url", "origin"], cwd=_REPO_ROOT)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


# Match both https://github.com/owner/repo(.git) and git@github.com:owner/repo(.git).
_REMOTE_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?$"
)


def _parse_owner_repo(remote_url: str) -> tuple[str, str] | None:
    m = _REMOTE_RE.match(remote_url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _release_exists(tag: str) -> bool:
    res = _run(["gh", "release", "view", tag])
    return res.returncode == 0


def _create_release(tag: str, notes: str) -> None:
    res = _run(["gh", "release", "create", tag, "--notes", notes])
    if res.returncode != 0:
        raise PublishError(
            f"`gh release create {tag}` failed: {res.stderr.strip() or res.stdout.strip()}"
        )


def _upload_assets(tag: str, files: list[Path]) -> None:
    cmd = ["gh", "release", "upload", tag, "--clobber"]
    cmd.extend(str(f) for f in files)
    res = _run(cmd)
    if res.returncode != 0:
        raise PublishError(
            f"`gh release upload {tag}` failed: {res.stderr.strip() or res.stdout.strip()}"
        )


def _release_download_url(owner: str, repo: str, tag: str, filename: str) -> str:
    return f"https://github.com/{owner}/{repo}/releases/download/{tag}/{filename}"


# --- share-info.md template ------------------------------------------------


def _format_share_info(
    *,
    run_id: str,
    pack: dict[str, Any],
    json_path: Path,
    html_path: Path | None,
    release_urls: dict[str, str],
) -> str:
    """Render a markdown sheet a sender can paste into Slack / email / a
    PR description. Includes a 'Share message' template at the bottom
    written in plain colleague-to-colleague tone."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    anchor = pack["anchor"]
    tx = anchor["tx_hash"]
    block = anchor["block_number"]
    n_receipts = len(pack["receipts"])
    token_id = pack.get("agent_erc8004_token_id")
    json_size_kb = json_path.stat().st_size / 1024
    html_line = ""
    if html_path is not None and html_path.exists():
        html_line = f"- `{html_path.name}` ({html_path.stat().st_size / 1024:.1f} KB) — self-contained HTML, double-click to open"
    lines: list[str] = []
    lines.append(f"# Evidence pack for {run_id}")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Run: `{run_id}`")
    lines.append(f"- Receipts: {n_receipts}")
    lines.append(
        f"- Anchor TX: [{tx}](https://sepolia.basescan.org/tx/{tx}) "
        f"(Base Sepolia, block {block})"
    )
    if token_id is not None:
        lines.append(
            f"- Agent ERC-8004 token: "
            f"[#{token_id}](https://sepolia.basescan.org/token/0x7177a6867296406881E20d6647232314736Dd09A?a={token_id})"
        )
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(f"- `{json_path.name}` ({json_size_kb:.1f} KB) — canonical PLAN §7 evidence pack")
    if html_line:
        lines.append(html_line)
    lines.append("")
    lines.append("## Verify")
    lines.append("")
    lines.append("**Option 1 — self-contained HTML (no host required):**")
    if html_path is not None:
        lines.append(
            f"  Double-click `{html_path.name}`. The dashboard auto-verifies all "
            "receipts against Base Sepolia (1 RPC call)."
        )
    else:
        lines.append(
            "  Generate one with `--build-html`, then double-click. The "
            "dashboard auto-verifies all receipts against Base Sepolia."
        )
    lines.append("")
    lines.append("**Option 2 — hosted JSON + dashboard:**")
    if release_urls.get("json"):
        lines.append(
            f"  Open `<your dashboard host>/#/run/{run_id}?evidence={release_urls['json']}`"
        )
    else:
        lines.append(
            "  Host the JSON at any HTTPS URL, then open "
            f"`<dashboard host>/#/run/{run_id}?evidence=<JSON URL>`."
        )
    lines.append("")
    lines.append("**Option 3 — vanilla verifier (no JS framework):**")
    lines.append(
        "  Open `verifier/index.html` from this repo. Paste any receipt + "
        "proof + tx_hash. Same 5-step pipeline, ~300 lines of vanilla JS."
    )
    lines.append("")
    if release_urls:
        lines.append("## Direct download links (GitHub Release)")
        lines.append("")
        for name, url in release_urls.items():
            lines.append(f"- {name}: {url}")
        lines.append("")
    lines.append("## Share message")
    lines.append("")
    lines.append("```")
    lines.append("Hi —")
    lines.append("")
    lines.append(
        f"Attached is the PromptSeal evidence pack for run {run_id}: "
        f"{n_receipts} signed receipts, anchored on Base Sepolia (block {block})."
    )
    lines.append("")
    if html_path is not None:
        lines.append(
            f"Easiest path: download {html_path.name} and open it in any "
            "browser — no host required, nothing to trust on our side. The "
            "page auto-verifies all receipts against the on-chain anchor:"
        )
    else:
        lines.append(
            f"Open the JSON in the PromptSeal dashboard "
            f"(your-host/#/run/{run_id}?evidence=<URL>) — it auto-verifies "
            "all receipts against the on-chain anchor:"
        )
    lines.append(f"  https://sepolia.basescan.org/tx/{tx}")
    lines.append("")
    lines.append("Source: https://github.com/<owner>/<repo>")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# --- orchestrator ----------------------------------------------------------


def publish_evidence(
    run_id: str,
    *,
    db_path: Path,
    dashboard_dir: Path,
    output_dir: Path,
    build_html: bool = False,
    upload_release_tag: str | None = None,
) -> dict[str, Any]:
    """Build artifacts under `output_dir`, optionally upload to a Release.
    Returns a dict with paths and any release URLs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 — JSON pack (always).
    json_path = output_dir / f"evidence-pack-{run_id}.json"
    _, pack = _export_mod.export_evidence_pack(
        run_id, db_path, output_path=json_path, as_zip=False,
    )

    # Step 2 — optional self-contained HTML (B6).
    html_path: Path | None = None
    if build_html:
        html_path = output_dir / f"evidence-bundle-{run_id}.html"
        _build_mod.build_self_contained(
            run_id,
            db_path=db_path,
            dashboard_dir=dashboard_dir,
            output_path=html_path,
        )

    # Step 3 — optional GitHub Release upload.
    release_urls: dict[str, str] = {}
    if upload_release_tag is not None:
        _check_gh_installed()  # raises if missing
        remote = _git_remote_url()
        if not remote:
            raise PublishError(
                "no `origin` remote on this repo — set one before publishing"
            )
        parsed = _parse_owner_repo(remote)
        if not parsed:
            raise PublishError(
                f"could not parse owner/repo from remote URL: {remote}"
            )
        owner, repo = parsed

        if not _release_exists(upload_release_tag):
            _create_release(
                upload_release_tag,
                f"PromptSeal evidence pack for {run_id}",
            )

        files = [json_path] + ([html_path] if html_path is not None else [])
        _upload_assets(upload_release_tag, files)

        release_urls["json"] = _release_download_url(
            owner, repo, upload_release_tag, json_path.name,
        )
        if html_path is not None:
            release_urls["html"] = _release_download_url(
                owner, repo, upload_release_tag, html_path.name,
            )

    # Step 4 — share-info.md (always).
    share_info_path = output_dir / f"share-info-{run_id}.md"
    share_info_path.write_text(
        _format_share_info(
            run_id=run_id,
            pack=pack,
            json_path=json_path,
            html_path=html_path,
            release_urls=release_urls,
        ),
        encoding="utf-8",
    )

    return {
        "run_id": run_id,
        "json_path": json_path,
        "html_path": html_path,
        "share_info_path": share_info_path,
        "release_urls": release_urls,
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Publish a run's evidence pack (JSON + optional HTML + optional GitHub Release).",
    )
    parser.add_argument("run_id")
    parser.add_argument(
        "--build-html",
        action="store_true",
        help="Also build the self-contained HTML bundle (recommended for "
             "non-technical recipients).",
    )
    parser.add_argument(
        "--upload-github-release",
        metavar="TAG",
        default=None,
        help="Create / append-to a GitHub Release with this tag and upload "
             "the artifacts as assets (requires `gh` CLI authenticated).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./published"),
        help="Where to write artifacts (default: ./published/).",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    dashboard_dir = _REPO_ROOT / "dashboard"

    try:
        result = publish_evidence(
            args.run_id,
            db_path=db_path,
            dashboard_dir=dashboard_dir,
            output_dir=args.output_dir,
            build_html=args.build_html,
            upload_release_tag=args.upload_github_release,
        )
    except _export_mod.EvidencePackError as exc:
        rprint(f"[red]Publish failed:[/red] {exc}")
        return 1
    except _build_mod.BuildSelfContainedError as exc:
        rprint(f"[red]HTML build failed:[/red] {exc}")
        return 2
    except PublishError as exc:
        rprint(f"[red]Publish failed:[/red] {exc}")
        return 3

    rprint("[bold green]✓ published[/bold green]")
    rprint(f"  JSON:        {result['json_path']}")
    if result["html_path"] is not None:
        rprint(f"  HTML:        {result['html_path']}")
    rprint(f"  share info:  {result['share_info_path']}")
    if result["release_urls"]:
        rprint("[bold]GitHub Release URLs:[/bold]")
        for name, url in result["release_urls"].items():
            rprint(f"  {name}: [cyan]{url}[/cyan]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
