"""Tests for scripts/clean_demo_runs.py — keep happy paths, prune debug runs.

Spec: PLAN §4 A2.
Key invariants:
- --dry-run does not mutate the DB
- --execute deletes only stale runs + their receipts + their anchor rows
- the two keeper run_ids are preserved (with their receipts and anchors)
- orphan anchor rows (run_id missing from `runs`) are swept
- non-existent / fresh DB does not crash
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "clean_demo_runs.py"
_spec = importlib.util.spec_from_file_location("promptseal_clean_demo_runs", _SCRIPT)
assert _spec and _spec.loader
clean_mod = importlib.util.module_from_spec(_spec)
sys.modules["promptseal_clean_demo_runs"] = clean_mod
_spec.loader.exec_module(clean_mod)


# --- helpers ----------------------------------------------------------------


def _build_db(db_path: Path) -> None:
    """Mirror the seven runs the real demo DB has today: 5 stale + 2 keepers."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
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
            event_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE anchors (
            run_id TEXT PRIMARY KEY,
            merkle_root TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            block_number INTEGER,
            chain_id INTEGER NOT NULL,
            anchored_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        """
    )
    layout = [
        # (run_id, started_at, receipt_count, anchored?)
        ("run-cda33fba8b8e", "2026-05-05T03:42:25Z", 1, False),
        ("run-6ac6130553b6", "2026-05-05T03:46:50Z", 1, False),
        ("run-c081198de511", "2026-05-05T04:14:47Z", 1, False),
        ("run-b0bab969f09c", "2026-05-05T04:15:18Z", 1, False),
        ("run-97d9fe124897", "2026-05-05T04:20:53Z", 7, False),
        ("run-3e732839c923", "2026-05-05T04:26:20Z", 17, True),  # keeper
        ("run-e8b202cfc898", "2026-05-05T16:34:20Z", 15, True),  # keeper
    ]
    seq = 0
    for run_id, started_at, n, anchored in layout:
        conn.execute(
            "INSERT INTO runs VALUES (?, 'hr-screener-v1', ?, NULL)",
            (run_id, started_at),
        )
        for _ in range(n):
            seq += 1
            conn.execute(
                "INSERT INTO receipts (run_id, event_hash) VALUES (?, ?)",
                (run_id, f"sha256:{seq:064x}"),
            )
        if anchored:
            conn.execute(
                "INSERT INTO anchors VALUES (?, 'sha256:root', '0xtx', 41000000, 84532, ?)",
                (run_id, started_at),
            )
    conn.commit()
    conn.close()


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    out = {}
    for t in ("runs", "receipts", "anchors"):
        out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()
    return out


def _run_ids(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    rows = [r[0] for r in conn.execute("SELECT run_id FROM runs ORDER BY started_at")]
    conn.close()
    return rows


# --- tests ------------------------------------------------------------------


def test_dry_run_does_not_mutate(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _build_db(db)
    before = _counts(db)

    plan = clean_mod.clean(db, execute_flag=False)

    assert _counts(db) == before, "dry run must not delete anything"
    assert plan.executed is False
    # Still surfaces what would be deleted.
    assert len(plan.stale_runs) == 5


def test_dry_run_lists_correct_stale_and_keepers(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _build_db(db)

    plan = clean_mod.clean(db, execute_flag=False)

    stale_ids = {rid for rid, _ in plan.stale_runs}
    assert stale_ids == {
        "run-cda33fba8b8e",
        "run-6ac6130553b6",
        "run-c081198de511",
        "run-b0bab969f09c",
        "run-97d9fe124897",
    }
    assert set(plan.keepers_present) == {"run-3e732839c923", "run-e8b202cfc898"}
    assert plan.keepers_missing == []
    # 7-receipt stale run is recorded with its actual count.
    counts_by_id = dict(plan.stale_runs)
    assert counts_by_id["run-97d9fe124897"] == 7


def test_execute_deletes_stale_and_their_receipts(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _build_db(db)

    clean_mod.clean(db, execute_flag=True)

    # 7 - 5 = 2 keeper runs; receipts 1+1+1+1+7 = 11 stale, total 43-11 = 32.
    counts = _counts(db)
    assert counts["runs"] == 2
    assert counts["receipts"] == 17 + 15  # only keepers' receipts remain
    assert set(_run_ids(db)) == {"run-3e732839c923", "run-e8b202cfc898"}


def test_execute_preserves_keeper_anchors(tmp_path: Path):
    db = tmp_path / "p.sqlite"
    _build_db(db)

    clean_mod.clean(db, execute_flag=True)

    conn = sqlite3.connect(db)
    anchors = sorted(r[0] for r in conn.execute("SELECT run_id FROM anchors"))
    conn.close()
    assert anchors == ["run-3e732839c923", "run-e8b202cfc898"]


def test_execute_sweeps_orphan_anchors(tmp_path: Path):
    """Anchor rows whose run_id is missing from `runs` (e.g. a manual half-cleanup)
    should be removed even though no run exists to drive their deletion."""
    db = tmp_path / "p.sqlite"
    _build_db(db)
    # Inject a dangling anchor without a corresponding run row.
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")  # let us insert an orphan on purpose
    conn.execute(
        "INSERT INTO anchors VALUES ('run-orphan-zzzz', 'sha256:x', '0xx', 41000000, 84532, '2026-05-05T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    plan = clean_mod.clean(db, execute_flag=False)
    assert "run-orphan-zzzz" in plan.orphan_anchor_run_ids

    clean_mod.clean(db, execute_flag=True)

    conn = sqlite3.connect(db)
    remaining = {r[0] for r in conn.execute("SELECT run_id FROM anchors")}
    conn.close()
    assert "run-orphan-zzzz" not in remaining
    # Keeper anchors still there.
    assert remaining == {"run-3e732839c923", "run-e8b202cfc898"}


def test_missing_db_does_not_crash(tmp_path: Path):
    """Fresh checkout: clean is a no-op."""
    plan = clean_mod.clean(tmp_path / "no-such.sqlite", execute_flag=True)
    assert plan.stale_runs == []
    assert plan.keepers_present == []


def test_only_keepers_present_is_a_noop(tmp_path: Path):
    """Re-running after a successful cleanup should report nothing to do."""
    db = tmp_path / "p.sqlite"
    _build_db(db)
    clean_mod.clean(db, execute_flag=True)  # first pass cleans
    before = _counts(db)

    plan = clean_mod.clean(db, execute_flag=True)  # second pass

    assert _counts(db) == before
    assert plan.stale_runs == []
    assert plan.orphan_anchor_run_ids == []
