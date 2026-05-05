"""Tests for scripts/reset.py — clean DB iteration without losing keypair.

Spec: PLAN §4 A1.
Key invariants:
- default mode clears 3 known tables (receipts, anchors, runs) and preserves
  agent_key.pem + agent_id.json
- forward-compat: if run_summaries (A3) exists, also clear it; if absent,
  reset must NOT error
- --full mode also deletes the keypair + agent_id.json
- without --yes the confirmation prompt blocks; "n" aborts cleanly, "y" proceeds
- if any anchor row has NULL block_number (in-flight TX), refuse to reset
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# scripts/ is not a package — load reset.py via a file-based import so tests
# don't depend on PYTHONPATH gymnastics.
_RESET_PATH = Path(__file__).resolve().parent.parent / "scripts" / "reset.py"
_spec = importlib.util.spec_from_file_location("promptseal_reset", _RESET_PATH)
assert _spec and _spec.loader
reset_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_reset"] = reset_mod
_spec.loader.exec_module(reset_mod)


# --- helpers ----------------------------------------------------------------


def _make_seeded_db(db_path: Path, *, with_run_summaries: bool = False) -> None:
    """Create a SQLite DB with the v0.1 schema + one row per table.

    If `with_run_summaries=True`, also create the A3 table with one row,
    simulating "A3 migration has already run".
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE anchors (
            run_id TEXT PRIMARY KEY,
            merkle_root TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            block_number INTEGER,
            chain_id INTEGER NOT NULL,
            anchored_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO runs VALUES ('run-x', 'hr-screener-v1', '2026-05-05T00:00:00Z', NULL)"
    )
    conn.execute(
        "INSERT INTO receipts (run_id, event_hash) VALUES ('run-x', 'sha256:aa')"
    )
    conn.execute(
        "INSERT INTO anchors VALUES ('run-x', 'sha256:bb', '0xdead', 41000000, 84532, '2026-05-05T00:00:01Z')"
    )
    if with_run_summaries:
        conn.executescript(
            """
            CREATE TABLE run_summaries (
                run_id TEXT PRIMARY KEY,
                summary_text TEXT NOT NULL,
                summary_hash TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                llm_provider TEXT NOT NULL,
                llm_model TEXT NOT NULL,
                included_in_merkle INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO run_summaries VALUES ('run-x', 'summary text', 'sha256:cc', "
            "'2026-05-05T00:00:02Z', 'openai', 'gpt-4o-mini', 0)"
        )
    conn.commit()
    conn.close()


def _row_counts(db_path: Path, tables: list[str]) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    out = {}
    for t in tables:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        if row is None:
            out[t] = -1  # sentinel for "table missing"
        else:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()
    return out


@pytest.fixture
def env(tmp_path: Path):
    """Standard layout: a seeded DB + keypair + agent_id.json under tmp_path."""
    db = tmp_path / "promptseal.sqlite"
    key = tmp_path / "agent_key.pem"
    agent_id = tmp_path / "agent_id.json"
    _make_seeded_db(db)
    key.write_bytes(b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    agent_id.write_text('{"agent_id":"hr-screener-v1","erc8004_token_id":633}')
    return {"db": db, "key": key, "agent_id": agent_id, "tmp": tmp_path}


# --- default mode (the 99% path) -------------------------------------------


def test_default_clears_three_core_tables(env):
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        assume_yes=True,
    )
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 0, "anchors": 0, "runs": 0}
    assert set(summary.tables_cleared) == {"receipts", "anchors", "runs"}
    assert summary.aborted is False


def test_default_preserves_keypair_and_agent_id(env):
    reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        assume_yes=True,
    )
    assert env["key"].exists(), "default mode must NOT delete agent_key.pem"
    assert env["agent_id"].exists(), "default mode must NOT delete agent_id.json"


def test_default_skips_run_summaries_when_table_absent(env):
    """A3 not yet landed: reset must succeed without erroring on missing table."""
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        assume_yes=True,
    )
    assert "run_summaries" not in summary.tables_cleared
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 0, "anchors": 0, "runs": 0}


def test_default_clears_run_summaries_when_table_present(tmp_path: Path):
    """A3 landed: reset clears all 4 tables, including run_summaries."""
    db = tmp_path / "promptseal.sqlite"
    key = tmp_path / "agent_key.pem"
    agent_id = tmp_path / "agent_id.json"
    _make_seeded_db(db, with_run_summaries=True)
    key.write_bytes(b"fake-key")
    agent_id.write_text("{}")

    summary = reset_mod.reset(
        db_path=db, key_path=key, agent_id_path=agent_id, assume_yes=True,
    )
    assert "run_summaries" in summary.tables_cleared
    counts = _row_counts(db, ["receipts", "anchors", "runs", "run_summaries"])
    assert counts == {"receipts": 0, "anchors": 0, "runs": 0, "run_summaries": 0}


# --- --full mode -----------------------------------------------------------


def test_full_removes_keypair_and_agent_id(env):
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        full=True, assume_yes=True,
    )
    assert not env["key"].exists()
    assert not env["agent_id"].exists()
    assert env["key"] in summary.files_removed
    assert env["agent_id"] in summary.files_removed


def test_full_clears_run_summaries_when_present(tmp_path: Path):
    db = tmp_path / "promptseal.sqlite"
    key = tmp_path / "agent_key.pem"
    agent_id = tmp_path / "agent_id.json"
    _make_seeded_db(db, with_run_summaries=True)
    key.write_bytes(b"fake")
    agent_id.write_text("{}")

    summary = reset_mod.reset(
        db_path=db, key_path=key, agent_id_path=agent_id,
        full=True, assume_yes=True,
    )
    assert "run_summaries" in summary.tables_cleared
    counts = _row_counts(db, ["run_summaries"])
    assert counts == {"run_summaries": 0}
    assert not key.exists() and not agent_id.exists()


# --- confirmation prompt ----------------------------------------------------


def test_confirmation_prompt_aborts_on_no(env):
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        prompt_fn=lambda _msg: "n",
    )
    assert summary.aborted is True
    # Nothing changed.
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 1, "anchors": 1, "runs": 1}
    assert env["key"].exists() and env["agent_id"].exists()


def test_confirmation_prompt_proceeds_on_yes(env):
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        prompt_fn=lambda _msg: "y",
    )
    assert summary.aborted is False
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 0, "anchors": 0, "runs": 0}


def test_confirmation_prompt_empty_input_aborts(env):
    """Empty answer == default No. Belt-and-braces against accidental enter."""
    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        prompt_fn=lambda _msg: "",
    )
    assert summary.aborted is True
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 1, "anchors": 1, "runs": 1}


# --- in-flight anchor refusal ----------------------------------------------


def test_refuses_when_in_flight_anchor_present(env):
    """An anchor row with NULL block_number means a TX was sent but not yet
    confirmed — resetting now would lose the run↔TX link forever."""
    conn = sqlite3.connect(env["db"])
    conn.execute(
        "INSERT INTO anchors VALUES ('run-y', 'sha256:dd', '0xpending', NULL, 84532, '2026-05-05T00:00:03Z')"
    )
    conn.commit()
    conn.close()

    summary = reset_mod.reset(
        db_path=env["db"], key_path=env["key"], agent_id_path=env["agent_id"],
        assume_yes=True,
    )
    assert summary.aborted is True
    assert "in-flight" in (summary.abort_reason or "")
    # Nothing changed.
    counts = _row_counts(env["db"], ["receipts", "anchors", "runs"])
    assert counts == {"receipts": 1, "anchors": 2, "runs": 1}


# --- edge cases -------------------------------------------------------------


def test_missing_db_does_not_crash(tmp_path: Path):
    """Fresh checkout, no DB yet: reset should be a no-op, not an error."""
    db = tmp_path / "no-such.sqlite"
    key = tmp_path / "agent_key.pem"
    agent_id = tmp_path / "agent_id.json"
    key.write_bytes(b"fake")
    agent_id.write_text("{}")

    summary = reset_mod.reset(
        db_path=db, key_path=key, agent_id_path=agent_id, assume_yes=True,
    )
    assert summary.aborted is False
    assert summary.tables_cleared == []
    assert key.exists() and agent_id.exists()


def test_full_handles_missing_files_gracefully(tmp_path: Path):
    """--full with already-absent keypair files should not crash."""
    db = tmp_path / "promptseal.sqlite"
    key = tmp_path / "agent_key.pem"  # never created
    agent_id = tmp_path / "agent_id.json"  # never created
    _make_seeded_db(db)

    summary = reset_mod.reset(
        db_path=db, key_path=key, agent_id_path=agent_id,
        full=True, assume_yes=True,
    )
    assert summary.aborted is False
    assert summary.files_removed == []  # nothing to remove
