"""Tests for promptseal.run_summary — pure CRUD over the run_summaries table.

Spec: PLAN §4 A3 + decision D2 (summaries not in Merkle by default).

These tests do NOT exercise any LLM call — that's Phase C1's territory. Here
we only verify schema migration, the four CRUD functions, hash determinism,
and the FK constraint.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from promptseal.chain import ReceiptChain
from promptseal.run_summary import (
    _compute_summary_hash,
    delete_run_summary,
    insert_run_summary,
    list_run_summaries,
    load_run_summary,
)


# --- helpers ----------------------------------------------------------------


def _seed_run(db_path: Path, run_id: str = "run-x") -> None:
    """Create a `runs` row so FK-referencing inserts can succeed."""
    chain = ReceiptChain(db_path)
    try:
        chain.open_run(run_id, "hr-screener-v1")
    finally:
        chain.close()


# --- 1. schema migration is idempotent --------------------------------------


def test_schema_migration_idempotent(tmp_path: Path):
    """Re-instantiating ReceiptChain on the same DB must not error.

    Guards against `CREATE TABLE` (without IF NOT EXISTS) sneaking in.
    """
    db = tmp_path / "p.sqlite"
    ReceiptChain(db).close()
    ReceiptChain(db).close()  # second init must be a no-op
    ReceiptChain(db).close()  # third for good measure

    # And the new table is actually present.
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='run_summaries'"
    ).fetchone()
    conn.close()
    assert row is not None


# --- 2. insert + load round-trip --------------------------------------------


def test_insert_then_load_round_trip(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _seed_run(db, "run-x")

    inserted = insert_run_summary(
        run_id="run-x",
        summary_text="The agent screened Carol Singh and decided to hire.",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        db_path=db,
    )
    loaded = load_run_summary("run-x", db_path=db)
    assert loaded == inserted

    # Field-by-field sanity:
    assert loaded["run_id"] == "run-x"
    assert loaded["summary_text"].startswith("The agent screened")
    assert loaded["llm_provider"] == "openai"
    assert loaded["llm_model"] == "gpt-4o-mini"
    assert loaded["summary_hash"].startswith("sha256:")
    assert loaded["included_in_merkle"] is False  # D2 default
    assert "T" in loaded["generated_at"] and loaded["generated_at"].endswith("Z")


# --- 3. summary_hash deterministic ------------------------------------------


def test_summary_hash_deterministic(tmp_path: Path):
    """Same text → identical hash, every time. This hash is what becomes a
    Merkle leaf when Tier 3 opts in, so determinism is load-bearing."""
    text = "Agent screened candidate; tools used: parse, score, decide."
    h1 = _compute_summary_hash(text)
    h2 = _compute_summary_hash(text)
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64  # 64 hex chars = 32 bytes
    # Different text → different hash (sanity).
    assert _compute_summary_hash(text + "!") != h1

    # And the same hash flows through insert.
    db = tmp_path / "p.sqlite"
    _seed_run(db, "run-x")
    inserted = insert_run_summary(
        run_id="run-x", summary_text=text,
        llm_provider="openai", llm_model="gpt-4o-mini",
        db_path=db,
    )
    assert inserted["summary_hash"] == h1


# --- 4. included_in_merkle round-trip (default 0, set 1) --------------------


def test_included_in_merkle_round_trip(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _seed_run(db, "run-default")
    _seed_run(db, "run-included")

    default_row = insert_run_summary(
        run_id="run-default", summary_text="x",
        llm_provider="openai", llm_model="gpt-4o-mini",
        db_path=db,
    )
    included_row = insert_run_summary(
        run_id="run-included", summary_text="y",
        llm_provider="openai", llm_model="gpt-4o-mini",
        included_in_merkle=True,
        db_path=db,
    )

    # D2: default is False. Tier 3 opt-in makes it True. Both round-trip.
    assert default_row["included_in_merkle"] is False
    assert included_row["included_in_merkle"] is True
    assert load_run_summary("run-default", db_path=db)["included_in_merkle"] is False
    assert load_run_summary("run-included", db_path=db)["included_in_merkle"] is True


# --- 5. list_run_summaries sorted newest-first ------------------------------


def test_list_sorted_by_generated_at_desc(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    for rid in ("run-a", "run-b", "run-c"):
        _seed_run(db, rid)

    # Use deterministic timestamps to guarantee ordering.
    insert_run_summary(
        "run-a", "first", "openai", "gpt-4o-mini",
        db_path=db, generated_at="2026-05-05T10:00:00.000Z",
    )
    insert_run_summary(
        "run-b", "second", "openai", "gpt-4o-mini",
        db_path=db, generated_at="2026-05-05T11:00:00.000Z",
    )
    insert_run_summary(
        "run-c", "third", "openai", "gpt-4o-mini",
        db_path=db, generated_at="2026-05-05T12:00:00.000Z",
    )

    listed = list_run_summaries(db_path=db)
    assert [s["run_id"] for s in listed] == ["run-c", "run-b", "run-a"]


# --- 6. delete + load returns None ------------------------------------------


def test_delete_then_load_returns_none(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _seed_run(db, "run-x")
    insert_run_summary(
        "run-x", "summary", "openai", "gpt-4o-mini", db_path=db,
    )
    assert load_run_summary("run-x", db_path=db) is not None

    removed = delete_run_summary("run-x", db_path=db)
    assert removed is True
    assert load_run_summary("run-x", db_path=db) is None

    # Re-deleting a non-existent row reports False, doesn't raise.
    assert delete_run_summary("run-x", db_path=db) is False


# --- 7. FK constraint rejects unknown run_id --------------------------------


def test_fk_constraint_rejects_unknown_run_id(tmp_path: Path):
    """Schema declares FOREIGN KEY (run_id) REFERENCES runs(run_id) and
    `_open` enables PRAGMA foreign_keys=ON, so an insert against a run_id
    that doesn't exist must raise IntegrityError."""
    db = tmp_path / "p.sqlite"
    ReceiptChain(db).close()  # init schema, but do NOT seed any run

    with pytest.raises(sqlite3.IntegrityError):
        insert_run_summary(
            run_id="run-does-not-exist",
            summary_text="orphan",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            db_path=db,
        )


# --- 8. dict shape is JSON-serializable -------------------------------------


def test_dict_shape_is_json_serializable(tmp_path: Path):
    """Evidence packs (Phase B2) embed summaries as JSON. The shape returned
    by load/insert must round-trip through json.dumps without custom encoders.
    """
    db = tmp_path / "p.sqlite"
    _seed_run(db, "run-x")
    inserted = insert_run_summary(
        "run-x", "summary text", "openai", "gpt-4o-mini",
        included_in_merkle=True, db_path=db,
    )

    serialized = json.dumps(inserted, sort_keys=True)
    restored = json.loads(serialized)
    assert restored == inserted
    # All seven canonical keys present, no surprise extras.
    assert set(restored.keys()) == {
        "run_id", "summary_text", "summary_hash", "generated_at",
        "llm_provider", "llm_model", "included_in_merkle",
    }
