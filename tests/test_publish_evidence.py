"""Tests for scripts/06_publish_evidence.py.

All gh CLI invocations are mocked — the test suite must never reach out
to GitHub. Vite build is also mocked when --build-html is exercised
(reusing the same pattern from test_build_self_contained).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.merkle import build_merkle
from promptseal.receipt import build_signed_receipt

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "06_publish_evidence.py"
_spec = importlib.util.spec_from_file_location("promptseal_publish_evidence", _SCRIPT)
assert _spec and _spec.loader
publish_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_publish_evidence"] = publish_mod
_spec.loader.exec_module(publish_mod)


# Mirror the stub used in test_build_self_contained — keeps fake vite output
# in the same shape vite-plugin-singlefile produces.
STUB_HTML = """\
<!doctype html>
<html lang="en"><head>
<meta charset="UTF-8" />
<title>PromptSeal Dashboard</title>
</head><body>
<div id="root"></div>
<script type="module">/* inlined main.tsx */</script>
</body></html>
"""


# --- helpers ---------------------------------------------------------------


def _seed_run_with_anchor(
    db_path: Path,
    *,
    run_id: str = "run-test",
    n_receipts: int = 3,
) -> str:
    chain = ReceiptChain(db_path)
    chain.open_run(run_id, "hr-screener-v1")
    sk = generate_keypair()
    parent: str | None = None
    leaves: list[str] = []
    for i in range(n_receipts):
        r = build_signed_receipt(
            sk=sk,
            agent_id="hr-screener-v1",
            agent_erc8004_token_id=633,
            event_type="llm_start" if i % 2 == 0 else "llm_end",
            payload_excerpt={"i": i, "model": "gpt-4o-mini"},
            parent_hash=parent,
        )
        chain.append(run_id, r)
        leaves.append(r["event_hash"])
        parent = r["event_hash"]
    chain.record_anchor(
        run_id=run_id,
        merkle_root=build_merkle(leaves)["root"],
        tx_hash="0x" + "ab" * 32,
        block_number=41115306,
        chain_id=84532,
    )
    chain.close()
    return run_id


def _patch_vite(monkeypatch: pytest.MonkeyPatch, dashboard_dir: Path) -> Path:
    """Pre-bake a fake dist/index.html so build_self_contained doesn't try
    to run npm. Returns the template path (mostly for clarity)."""
    dist = dashboard_dir / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    template_path = dist / "index.html"
    template_path.write_text(STUB_HTML, encoding="utf-8")
    monkeypatch.setattr(
        publish_mod._build_mod, "run_vite_build", lambda d: template_path,  # type: ignore[attr-defined]
    )
    return template_path


def _patch_subprocess_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[str]]:
    """Replace publish_mod._run with a recorder. Returns a list of all the
    argv lists that flowed through. Tests assert on this list to verify
    *which* subprocess calls happened."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path | None = None):
        calls.append(list(cmd))
        # Default: return success with empty stdout. Specific tests can
        # override this in-place by patching again with a smarter fake.
        class _R:
            returncode = 0
            stdout = "gh version 2.50.0\n" if cmd[:2] == ["gh", "--version"] else ""
            stderr = ""
        return _R()

    monkeypatch.setattr(publish_mod, "_run", fake_run)
    return calls


# --- 1. JSON-only path (no html, no gh) ------------------------------------


def test_publish_creates_json_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Default flags: just an evidence-pack JSON + share-info.md. No HTML,
    no subprocess calls (no gh, no npm)."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    out = tmp_path / "out"
    calls = _patch_subprocess_recorder(monkeypatch)

    result = publish_mod.publish_evidence(
        run_id,
        db_path=db,
        dashboard_dir=tmp_path / "dashboard",  # never touched
        output_dir=out,
        build_html=False,
        upload_release_tag=None,
    )

    assert result["json_path"].exists()
    assert result["html_path"] is None
    assert result["share_info_path"].exists()
    assert result["release_urls"] == {}
    # No subprocess.run anywhere — no gh, no npm.
    assert calls == []
    # share-info markdown sits next to the JSON.
    assert result["json_path"].parent == out
    assert result["share_info_path"].parent == out


# --- 2. JSON + HTML --------------------------------------------------------


def test_publish_with_html_creates_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)
    out = tmp_path / "out"
    calls = _patch_subprocess_recorder(monkeypatch)

    result = publish_mod.publish_evidence(
        run_id,
        db_path=db,
        dashboard_dir=dashboard,
        output_dir=out,
        build_html=True,
        upload_release_tag=None,
    )

    assert result["json_path"].exists()
    assert result["html_path"] is not None
    assert result["html_path"].exists()
    # Self-contained HTML should have the embedded script (B6 contract).
    assert "window.__PROMPTSEAL_EVIDENCE__" in result["html_path"].read_text()
    # build_self_contained's vite mock was used; no gh subprocess called.
    assert calls == []


# --- 3. unknown run --------------------------------------------------------


def test_publish_unknown_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    ReceiptChain(db).close()  # init schema, no runs

    with pytest.raises(
        publish_mod._export_mod.EvidencePackError, match="no receipts",  # type: ignore[attr-defined]
    ):
        publish_mod.publish_evidence(
            "run-does-not-exist",
            db_path=db,
            dashboard_dir=tmp_path / "dashboard",
            output_dir=tmp_path / "out",
        )


# --- 4. unanchored run -----------------------------------------------------


def test_publish_unanchored_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    chain = ReceiptChain(db)
    chain.open_run("run-noanchor", "hr-screener-v1")
    sk = generate_keypair()
    r = build_signed_receipt(
        sk=sk,
        agent_id="hr-screener-v1",
        agent_erc8004_token_id=633,
        event_type="llm_start",
        payload_excerpt={"i": 0},
        parent_hash=None,
    )
    chain.append("run-noanchor", r)
    chain.close()

    with pytest.raises(
        publish_mod._export_mod.EvidencePackError, match="not anchored",  # type: ignore[attr-defined]
    ):
        publish_mod.publish_evidence(
            "run-noanchor",
            db_path=db,
            dashboard_dir=tmp_path / "dashboard",
            output_dir=tmp_path / "out",
        )


# --- 5. share-info.md content ----------------------------------------------


def test_share_info_md_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The markdown sheet must include: run id, anchor TX link, files
    section, all three verify options, and the share message template."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)
    _patch_subprocess_recorder(monkeypatch)

    result = publish_mod.publish_evidence(
        run_id,
        db_path=db,
        dashboard_dir=dashboard,
        output_dir=tmp_path / "out",
        build_html=True,
    )

    md = result["share_info_path"].read_text(encoding="utf-8")
    assert f"# Evidence pack for {run_id}" in md
    assert "0x" + "ab" * 32 in md  # anchor tx
    assert "https://sepolia.basescan.org/tx/0x" in md
    assert "## Files" in md
    assert "evidence-pack-" in md
    assert "evidence-bundle-" in md  # because build_html=True
    assert "## Verify" in md
    assert "Option 1" in md and "Option 2" in md and "Option 3" in md
    assert "## Share message" in md
    # Plain-language tone check: should *say* "no host" or "no trust", not
    # "this script generates a self-authenticating artifact".
    assert "no host" in md.lower() or "double-click" in md.lower()


# --- 6. gh skipped when no upload tag --------------------------------------


def test_publish_skips_gh_when_not_specified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Belt-and-braces: even if gh were available, omitting
    --upload-github-release must not produce ANY gh subprocess call."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    calls = _patch_subprocess_recorder(monkeypatch)

    publish_mod.publish_evidence(
        run_id,
        db_path=db,
        dashboard_dir=tmp_path / "dashboard",
        output_dir=tmp_path / "out",
        build_html=False,
        upload_release_tag=None,
    )

    # Sweep: no command starting with "gh" or "git".
    for cmd in calls:
        assert cmd[0] != "gh", f"unexpected gh call: {cmd}"
        assert cmd[0] != "git", f"unexpected git call: {cmd}"


# --- bonus: gh CLI flow records expected commands --------------------------


def test_publish_with_release_runs_expected_gh_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When --upload-github-release is used, verify the exact subprocess
    sequence: gh --version → git remote → gh release view → gh release create
    (when missing) → gh release upload."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    out = tmp_path / "out"

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path | None = None):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[:2] == ["gh", "--version"]:
            _R.stdout = "gh version 2.50.0\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            _R.stdout = "https://github.com/example/promptseal-test.git\n"
        elif cmd[:3] == ["gh", "release", "view"]:
            # Simulate "tag does not exist yet" → triggers create.
            _R.returncode = 1
        return _R()

    monkeypatch.setattr(publish_mod, "_run", fake_run)

    result = publish_mod.publish_evidence(
        run_id,
        db_path=db,
        dashboard_dir=tmp_path / "dashboard",
        output_dir=out,
        build_html=False,
        upload_release_tag="v0.2-test",
    )

    sequence = [tuple(c[:3]) for c in calls]
    assert ("gh", "--version") in [tuple(c[:2]) for c in calls]
    assert ("git", "remote", "get-url") in sequence
    assert ("gh", "release", "view") in sequence
    assert ("gh", "release", "create") in sequence
    assert ("gh", "release", "upload") in sequence

    # And the URL we computed matches the constructor pattern.
    assert result["release_urls"]["json"] == (
        "https://github.com/example/promptseal-test/releases/download/v0.2-test/"
        f"evidence-pack-{run_id}.json"
    )


# --- bonus: missing gh raises clear error ---------------------------------


def test_upload_with_missing_gh_raises_publisherror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """If `gh --version` exits non-zero (not installed), surface a clean
    PublishError instead of letting subprocess noise leak through."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)

    def fake_run(cmd: list[str], *, cwd: Path | None = None):
        class _R:
            returncode = 127  # "command not found" exit code
            stdout = ""
            stderr = "gh: command not found"
        return _R()

    monkeypatch.setattr(publish_mod, "_run", fake_run)

    with pytest.raises(publish_mod.PublishError, match="gh"):
        publish_mod.publish_evidence(
            run_id,
            db_path=db,
            dashboard_dir=tmp_path / "dashboard",
            output_dir=tmp_path / "out",
            upload_release_tag="v0.2-test",
        )
