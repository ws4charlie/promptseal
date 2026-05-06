"""Tests for scripts/07_runs_list.py — generates dashboard/public/runs-index.json.

PLAN §6 (E2 backend) + D17. The runs-index is a static JSON the operator
regenerates on demand; the dashboard reads it at startup.

Schema invariants verified here:
  - Only anchored runs appear (in-flight TX with NULL block_number is excluded).
  - Sorted started_at desc.
  - subject_ref + final_decision come from final_decision payload's candidate_id /
    decision; both null when no final_decision event.
  - Round-trip via write_runs_index() preserves every field.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.merkle import build_merkle
from promptseal.receipt import build_signed_receipt
from promptseal.run_summary import insert_run_summary

# scripts/07_runs_list.py is not importable as a normal module name (leading
# digit). Load via spec_from_file_location — same trick test_evidence_pack uses.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "07_runs_list.py"
_spec = importlib.util.spec_from_file_location("promptseal_runs_list", _SCRIPT)
assert _spec and _spec.loader
runs_list_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_runs_list"] = runs_list_mod
_spec.loader.exec_module(runs_list_mod)


# --- helpers ---------------------------------------------------------------


def _override_run_times(
    db_path: Path,
    run_id: str,
    *,
    started_at: str,
    ended_at: str | None,
) -> None:
    """ReceiptChain.open_run/close_run stamp _now_iso(); override for tests."""
    conn = sqlite3.connect(db_path)
    try:
        if ended_at is None:
            conn.execute(
                "UPDATE runs SET started_at = ?, ended_at = NULL WHERE run_id = ?",
                (started_at, run_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET started_at = ?, ended_at = ? WHERE run_id = ?",
                (started_at, ended_at, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_run(
    db_path: Path,
    *,
    run_id: str,
    started_at: str,
    ended_at: str | None,
    n_other_events: int = 2,
    final_decision: dict[str, Any] | None = None,
    with_anchor: bool = True,
    anchor_block_number: int | None = 41115306,
    summary: bool = False,
    token_id: int | None = 633,
) -> None:
    """Seed one run + its receipts + optional anchor + optional summary.

    `final_decision` is the payload dict for a final_decision receipt; pass
    None to skip emitting one (simulates a run without a decision).
    `anchor_block_number=None` simulates an in-flight TX (anchor row exists
    but block isn't confirmed yet).
    """
    chain = ReceiptChain(db_path)
    chain.open_run(run_id, "hr-screener-v1")

    sk = generate_keypair()
    receipts: list[dict[str, Any]] = []
    parent_hash: str | None = None
    for i in range(n_other_events):
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

    if final_decision is not None:
        r = build_signed_receipt(
            sk=sk,
            agent_id="hr-screener-v1",
            agent_erc8004_token_id=token_id,
            event_type="final_decision",
            payload_excerpt=final_decision,
            parent_hash=parent_hash,
        )
        chain.append(run_id, r)
        receipts.append(r)

    if ended_at is not None:
        chain.close_run(run_id)

    if with_anchor:
        leaves = [r["event_hash"] for r in receipts]
        root = build_merkle(leaves)["root"]
        chain.record_anchor(
            run_id=run_id,
            merkle_root=root,
            tx_hash="0x" + run_id.replace("-", "").ljust(64, "0")[:64],
            block_number=anchor_block_number,
            chain_id=84532,
        )
    chain.close()

    # Override started_at / ended_at after open_run + close_run, since both
    # stamp wall-clock time. _override needs a settled state, so do it last.
    _override_run_times(db_path, run_id, started_at=started_at, ended_at=ended_at)

    if summary:
        insert_run_summary(
            run_id=run_id,
            summary_text="Sample summary.",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            included_in_merkle=False,
            db_path=db_path,
        )


# --- 1. all anchored runs land in the index --------------------------------


def test_runs_index_includes_all_anchored_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    _seed_run(
        db, run_id="run-a",
        started_at="2026-05-05T10:00:00Z", ended_at="2026-05-05T10:00:05Z",
    )
    _seed_run(
        db, run_id="run-b",
        started_at="2026-05-05T11:00:00Z", ended_at="2026-05-05T11:00:05Z",
    )
    _seed_run(
        db, run_id="run-c",
        started_at="2026-05-05T12:00:00Z", ended_at="2026-05-05T12:00:05Z",
    )

    index = runs_list_mod.build_runs_index(db)

    assert index["version"] == "0.3"
    assert "generated_at" in index and index["generated_at"].endswith("Z")
    assert {r["run_id"] for r in index["runs"]} == {"run-a", "run-b", "run-c"}


# --- 2. unanchored / in-flight runs are excluded ---------------------------


def test_runs_index_excludes_unanchored_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Two exclusion paths: no anchor row, and anchor with NULL block_number."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    _seed_run(
        db, run_id="run-anchored",
        started_at="2026-05-05T10:00:00Z", ended_at="2026-05-05T10:00:05Z",
    )
    _seed_run(
        db, run_id="run-inflight",
        started_at="2026-05-05T11:00:00Z", ended_at="2026-05-05T11:00:05Z",
        anchor_block_number=None,  # anchor row exists but block not confirmed
    )
    _seed_run(
        db, run_id="run-no-anchor",
        started_at="2026-05-05T12:00:00Z", ended_at="2026-05-05T12:00:05Z",
        with_anchor=False,
    )

    index = runs_list_mod.build_runs_index(db)
    assert {r["run_id"] for r in index["runs"]} == {"run-anchored"}


# --- 3. ordered newest started_at first ------------------------------------


def test_runs_index_sorted_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    # Seed in reverse chronological order to confirm sort isn't insertion-order.
    _seed_run(
        db, run_id="run-old",
        started_at="2026-05-01T10:00:00Z", ended_at="2026-05-01T10:00:05Z",
    )
    _seed_run(
        db, run_id="run-new",
        started_at="2026-05-05T10:00:00Z", ended_at="2026-05-05T10:00:05Z",
    )
    _seed_run(
        db, run_id="run-mid",
        started_at="2026-05-03T10:00:00Z", ended_at="2026-05-03T10:00:05Z",
    )

    index = runs_list_mod.build_runs_index(db)
    assert [r["run_id"] for r in index["runs"]] == ["run-new", "run-mid", "run-old"]


# --- 4. subject_ref + final_decision null without final_decision event -----


def test_runs_index_omits_subject_ref_if_no_final_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    _seed_run(
        db, run_id="run-no-fd",
        started_at="2026-05-05T10:00:00Z", ended_at="2026-05-05T10:00:05Z",
        final_decision=None,
    )
    _seed_run(
        db, run_id="run-with-fd",
        started_at="2026-05-05T11:00:00Z", ended_at="2026-05-05T11:00:05Z",
        final_decision={"candidate_id": "res_002", "decision": "reject"},
    )

    index = runs_list_mod.build_runs_index(db)
    by_id = {r["run_id"]: r for r in index["runs"]}

    # No final_decision event → both subject_ref and final_decision are null.
    assert by_id["run-no-fd"]["subject_ref"] is None
    assert by_id["run-no-fd"]["final_decision"] is None
    # With final_decision → both populated from payload.
    assert by_id["run-with-fd"]["subject_ref"] == "res_002"
    assert by_id["run-with-fd"]["final_decision"] == "reject"


# --- 5. default --output writes dashboard/public/runs-index.json -----------


def test_runs_index_default_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "promptseal.sqlite"
    monkeypatch.setenv("PROMPTSEAL_DB_PATH", str(db))
    _seed_run(
        db, run_id="run-x",
        started_at="2026-05-05T10:00:00Z", ended_at="2026-05-05T10:00:05Z",
        final_decision={"candidate_id": "res_001", "decision": "hire"},
    )

    rc = runs_list_mod.main([])
    assert rc == 0

    expected = tmp_path / "dashboard" / "public" / "runs-index.json"
    assert expected.exists(), "default --output should be dashboard/public/runs-index.json"
    data = json.loads(expected.read_text())
    assert data["version"] == "0.3"
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == "run-x"
    assert data["runs"][0]["final_decision"] == "hire"


# --- 6. round-trip: write → read, every field preserved -------------------


def test_runs_index_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    _seed_run(
        db, run_id="run-rt",
        started_at="2026-05-05T16:34:20.979Z",
        ended_at="2026-05-05T16:34:27.881Z",
        final_decision={"candidate_id": "res_002", "decision": "reject"},
        summary=True,
    )

    index = runs_list_mod.build_runs_index(db)
    out = tmp_path / "out.json"
    runs_list_mod.write_runs_index(index, out)
    reloaded = json.loads(out.read_text())

    # Top-level shape.
    assert reloaded["version"] == "0.3"
    assert "generated_at" in reloaded
    assert isinstance(reloaded["runs"], list) and len(reloaded["runs"]) == 1

    # Every field per PLAN §6 schema is present + correctly typed.
    run = reloaded["runs"][0]
    assert set(run.keys()) == {
        "run_id", "agent_id", "subject_ref", "started_at", "ended_at",
        "duration_ms", "event_count", "final_decision", "anchor_tx",
        "anchor_block", "has_summary",
    }
    assert run["run_id"] == "run-rt"
    assert run["agent_id"] == "hr-screener-v1"
    assert run["subject_ref"] == "res_002"
    assert run["started_at"] == "2026-05-05T16:34:20.979Z"
    assert run["ended_at"] == "2026-05-05T16:34:27.881Z"
    assert run["duration_ms"] == 6902  # 27.881 - 20.979 = 6.902s
    assert isinstance(run["event_count"], int)
    assert run["event_count"] >= 3  # 2 other + 1 final_decision
    assert run["final_decision"] == "reject"
    assert run["anchor_tx"].startswith("0x")
    assert isinstance(run["anchor_block"], int)
    assert run["has_summary"] is True
