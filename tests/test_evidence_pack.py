"""Tests for scripts/04_export_evidence_pack.py — PLAN §7 schema.

Covers the build_evidence_pack() pure function (testable without writing
files) plus a quick round-trip sanity check.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from promptseal.anchor import AnchorResult  # noqa: F401  (kept for type clarity)
from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.merkle import build_merkle, verify_proof
from promptseal.receipt import build_signed_receipt
from promptseal.run_summary import insert_run_summary

# scripts/04_export_evidence_pack.py is not importable as a module name
# (leading digit). Load it via spec_from_file_location.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "04_export_evidence_pack.py"
_spec = importlib.util.spec_from_file_location("promptseal_export_evidence_pack", _SCRIPT)
assert _spec and _spec.loader
export_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_export_evidence_pack"] = export_mod
_spec.loader.exec_module(export_mod)


# --- helpers ---------------------------------------------------------------


def _seed_run(
    db_path: Path,
    *,
    run_id: str = "run-test",
    n_receipts: int = 3,
    token_id: int | None = 633,
    with_anchor: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Create a run with N signed receipts and an anchor row.

    Returns (run_id, receipts). Receipts are real (signed via Ed25519); the
    anchor's tx_hash is fake (we don't hit a real chain in tests).
    """
    chain = ReceiptChain(db_path)
    chain.open_run(run_id, "hr-screener-v1")
    sk = generate_keypair()
    receipts: list[dict[str, Any]] = []
    parent_hash: str | None = None
    for i in range(n_receipts):
        r = build_signed_receipt(
            sk=sk,
            agent_id="hr-screener-v1",
            agent_erc8004_token_id=token_id,
            event_type="llm_start" if i % 2 == 0 else "llm_end",
            payload_excerpt={"i": i, "model": "gpt-4o-mini"},
            parent_hash=parent_hash,
        )
        chain.append(run_id, r)
        receipts.append(r)
        parent_hash = r["event_hash"]

    if with_anchor:
        leaves = [r["event_hash"] for r in receipts]
        root = build_merkle(leaves)["root"]
        chain.record_anchor(
            run_id=run_id,
            merkle_root=root,
            tx_hash="0xdeadbeef" + "00" * 28,
            block_number=41115306,
            chain_id=84532,
        )
    chain.close()
    return run_id, receipts


# --- 1. with token id ------------------------------------------------------


def test_export_run_with_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, n_receipts=4, token_id=633)

    pack = export_mod.build_evidence_pack(run_id, db)

    assert pack["version"] == "0.2"
    assert pack["agent_id"] == "hr-screener-v1"
    assert pack["agent_erc8004_token_id"] == 633
    assert pack["run_id"] == run_id
    assert len(pack["receipts"]) == 4
    assert pack["merkle_root"].startswith("sha256:")
    assert pack["anchor"]["chain_id"] == 84532
    assert pack["anchor"]["block_number"] == 41115306
    assert pack["anchor"]["tx_hash"].startswith("0x")
    assert "summary" not in pack  # nothing inserted


# --- 2. without token id ---------------------------------------------------


def test_export_run_without_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mirrors run-3e732839c923 (anchored before ERC-8004 registration)."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, token_id=None)

    pack = export_mod.build_evidence_pack(run_id, db)

    assert pack["agent_erc8004_token_id"] is None
    # All receipts also carry None for the token id, individually.
    for r in pack["receipts"]:
        assert r["agent_erc8004_token_id"] is None


# --- 3. summary absent → key omitted ---------------------------------------


def test_export_omits_summary_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)

    pack = export_mod.build_evidence_pack(run_id, db)
    assert "summary" not in pack


# --- 4. summary present → nested per PLAN §7 -------------------------------


def test_export_includes_summary_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)
    insert_run_summary(
        run_id=run_id,
        summary_text="Agent screened candidate, decided hire.",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        included_in_merkle=False,  # D2: default off
        db_path=db,
    )

    pack = export_mod.build_evidence_pack(run_id, db)

    assert "summary" in pack
    s = pack["summary"]
    assert s["text"].startswith("Agent screened")
    assert s["hash"].startswith("sha256:")
    assert s["llm_provider"] == "openai"
    assert s["llm_model"] == "gpt-4o-mini"
    assert s["included_in_merkle"] is False
    assert "T" in s["generated_at"]


# --- 5. proofs: per-receipt, keyed by id, walk-verifies to root ------------


def test_proofs_per_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, n_receipts=5)

    pack = export_mod.build_evidence_pack(run_id, db)

    # Every receipt has a proof; keys are the receipt's DB id as a string.
    receipt_ids = [str(r["id"]) for r in pack["receipts"]]
    assert set(pack["proofs"].keys()) == set(receipt_ids)

    # Each proof actually walks back to the merkle root — meaningful, not
    # just a structural smoke test.
    for r in pack["receipts"]:
        proof = pack["proofs"][str(r["id"])]
        assert verify_proof(r["event_hash"], proof, pack["merkle_root"]) is True


# --- 6. round-trip: write to JSON, reload, fields preserved ----------------


def test_round_trip_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, n_receipts=3)

    out = tmp_path / "pack.json"
    written, pack = export_mod.export_evidence_pack(
        run_id, db, output_path=out, as_zip=False,
    )
    assert written == out
    assert out.exists()

    reloaded = json.loads(out.read_text())
    # Top-level keys exactly match PLAN §7 (no extras, no omissions besides
    # the optional 'summary' which isn't seeded in this test).
    assert set(reloaded.keys()) == {
        "version", "agent_id", "agent_erc8004_token_id", "run_id",
        "receipts", "merkle_root", "anchor", "proofs",
    }
    assert reloaded == pack  # round-trip equality


# --- bonus: ZIP mode wraps json + README.txt -------------------------------


def test_zip_mode_contains_json_and_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)

    out = tmp_path / "pack.zip"
    written, _ = export_mod.export_evidence_pack(
        run_id, db, output_path=out, as_zip=True,
    )
    assert written == out
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert names == {"evidence-pack.json", "README.txt"}
        # The JSON inside is the canonical pack.
        inner = json.loads(zf.read("evidence-pack.json"))
        assert inner["run_id"] == run_id


# --- bonus: errors are loud, not silent ------------------------------------


def test_unanchored_run_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, with_anchor=False)
    with pytest.raises(export_mod.EvidencePackError, match="not anchored"):
        export_mod.build_evidence_pack(run_id, db)


def test_unknown_run_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    ReceiptChain(db).close()  # init schema, no rows
    with pytest.raises(export_mod.EvidencePackError, match="no receipts"):
        export_mod.build_evidence_pack("run-does-not-exist", db)
