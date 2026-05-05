"""Tests for scripts/build_self_contained.py.

Vite build is mocked — running the real `npm run build:single` would slow
the suite from <2s to >30s. Instead, we patch run_vite_build to return a
pre-baked stub HTML. That keeps the tests focused on what we actually own:
the evidence-injection logic + error contract.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.merkle import build_merkle
from promptseal.receipt import build_signed_receipt

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_self_contained.py"
_spec = importlib.util.spec_from_file_location("promptseal_build_self_contained", _SCRIPT)
assert _spec and _spec.loader
build_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_build_self_contained"] = build_mod
_spec.loader.exec_module(build_mod)


# A minimal HTML that looks like vite-plugin-singlefile output: one inlined
# <script type="module"> block + a #root div. Just enough for inject_evidence
# to find a <script tag to insert before.
STUB_HTML = """\
<!doctype html>
<html lang="en"><head>
<meta charset="UTF-8" />
<title>PromptSeal Dashboard</title>
<style>body{background:#0d1117}</style>
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
    token_id: int | None = 633,
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
            agent_erc8004_token_id=token_id,
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
    """Pre-bake dist/index.html with STUB_HTML, mock run_vite_build."""
    dist = dashboard_dir / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    template_path = dist / "index.html"
    template_path.write_text(STUB_HTML, encoding="utf-8")
    monkeypatch.setattr(build_mod, "run_vite_build", lambda d: template_path)
    return template_path


def _extract_embedded_b64(html: str) -> str:
    m = re.search(
        r'window\.__PROMPTSEAL_EVIDENCE__\s*=\s*"([A-Za-z0-9+/=]+)"', html,
    )
    assert m, "embedded evidence script not found in output HTML"
    return m.group(1)


# --- tests -----------------------------------------------------------------


def test_build_self_contained_for_anchored_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    out = build_mod.build_self_contained(
        run_id, db_path=db, dashboard_dir=dashboard,
    )

    assert out.exists()
    size = out.stat().st_size
    # Stub HTML (~280 B) + embedded base64 script (small for 3 receipts).
    # Loose bounds — tightening is brittle.
    assert 500 < size < 2_000_000
    # Sanity: the placeholder template is preserved alongside the injection.
    body = out.read_text(encoding="utf-8")
    assert '<div id="root"></div>' in body
    assert "window.__PROMPTSEAL_EVIDENCE__" in body


def test_embedded_evidence_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Decoding the base64 must yield the SAME pack build_evidence_pack
    produced — bytes-equal under canonical JSON serialization. This is the
    contract the dashboard's loadFromEmbedded relies on."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db, n_receipts=4, token_id=633)
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    out = build_mod.build_self_contained(
        run_id, db_path=db, dashboard_dir=dashboard,
    )

    # Build the same pack the script just embedded — comparing dict equality
    # would also work, but we assert the wire-format JSON matches byte-for-
    # byte since that's what the browser will see.
    expected_pack: dict[str, Any] = build_mod._export_mod.build_evidence_pack(  # type: ignore[attr-defined]
        run_id, db,
    )
    expected_json = json.dumps(
        expected_pack, separators=(",", ":"), sort_keys=True, ensure_ascii=False,
    )

    b64 = _extract_embedded_b64(out.read_text(encoding="utf-8"))
    decoded = base64.b64decode(b64).decode("utf-8")
    assert decoded == expected_json


def test_build_unknown_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    ReceiptChain(db).close()  # init schema, no rows
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    with pytest.raises(
        build_mod._export_mod.EvidencePackError, match="no receipts",  # type: ignore[attr-defined]
    ):
        build_mod.build_self_contained(
            "run-does-not-exist", db_path=db, dashboard_dir=dashboard,
        )


def test_build_unanchored_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    chain = ReceiptChain(db)
    chain.open_run("run-noanchor", "hr-screener-v1")
    sk = generate_keypair()
    r = build_signed_receipt(
        sk=sk, agent_id="hr-screener-v1", agent_erc8004_token_id=633,
        event_type="llm_start",
        payload_excerpt={"i": 0}, parent_hash=None,
    )
    chain.append("run-noanchor", r)
    chain.close()
    # No record_anchor → unanchored.
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    with pytest.raises(
        build_mod._export_mod.EvidencePackError, match="not anchored",  # type: ignore[attr-defined]
    ):
        build_mod.build_self_contained(
            "run-noanchor", db_path=db, dashboard_dir=dashboard,
        )


def test_output_path_default_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db, run_id="run-default-out")
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    out = build_mod.build_self_contained(
        run_id, db_path=db, dashboard_dir=dashboard,
    )

    expected = Path(f"./evidence-bundle-{run_id}.html")
    assert out == expected
    assert (tmp_path / f"evidence-bundle-{run_id}.html").exists()


def test_output_path_custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id = _seed_run_with_anchor(db)
    dashboard = tmp_path / "dashboard"
    _patch_vite(monkeypatch, dashboard)

    custom = tmp_path / "subdir" / "my-bundle.html"
    custom.parent.mkdir()

    out = build_mod.build_self_contained(
        run_id,
        db_path=db,
        dashboard_dir=dashboard,
        output_path=custom,
    )

    assert out == custom
    assert custom.exists()


# --- bonus: pure-function regression test ---------------------------------


def test_inject_evidence_places_script_before_first_existing_script():
    """Injection must happen before any existing <script>, so that
    window.__PROMPTSEAL_EVIDENCE__ is defined when main.tsx runs."""
    pack = {"run_id": "rx", "version": "0.2"}
    out = build_mod.inject_evidence(STUB_HTML, pack)

    # Our injection appears first, then the original script tag.
    pos_inject = out.index("window.__PROMPTSEAL_EVIDENCE__")
    pos_main = out.index("/* inlined main.tsx */")
    assert pos_inject < pos_main


def test_inject_evidence_raises_when_no_script_tag():
    """Defensive: if vite-plugin-singlefile failed to inline, we should
    refuse to write a broken bundle rather than emitting an HTML that
    silently won't run."""
    bare = "<!doctype html><html><body><div id='root'></div></body></html>"
    pack = {"run_id": "rx", "version": "0.2"}
    with pytest.raises(build_mod.BuildSelfContainedError, match="no <script tag"):
        build_mod.inject_evidence(bare, pack)
