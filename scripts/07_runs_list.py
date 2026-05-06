"""Generate dashboard/public/runs-index.json — the operator's runs list.

PLAN §6 (E2 backend) + D17. Each entry is one anchored run with the metadata
the dashboard's RunsListPage renders. Newest-first by started_at. Excludes
runs without a confirmed anchor (no anchor row, or anchor.block_number IS
NULL — i.e. an in-flight TX).

Usage:
    python scripts/07_runs_list.py
    python scripts/07_runs_list.py --output /tmp/runs.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich import print as rprint

RUNS_INDEX_VERSION = "0.3"
DEFAULT_OUTPUT = Path("dashboard/public/runs-index.json")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration_ms(started_at: str, ended_at: str) -> int | None:
    """ISO-8601 ms-resolution diff. Returns None if either input is malformed."""
    try:
        d_start = datetime.fromisoformat(started_at)
        d_end = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return int((d_end - d_start).total_seconds() * 1000)


def _final_decision_payload(
    conn: sqlite3.Connection, run_id: str,
) -> dict[str, Any] | None:
    """Most recent final_decision receipt's payload, or None if absent."""
    row = conn.execute(
        "SELECT payload_excerpt FROM receipts "
        "WHERE run_id = ? AND event_type = 'final_decision' "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload_excerpt"])
    except json.JSONDecodeError:
        return None


def _event_count(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM receipts WHERE run_id = ?", (run_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _has_summary(conn: sqlite3.Connection, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM run_summaries WHERE run_id = ? LIMIT 1", (run_id,),
    ).fetchone()
    return row is not None


def _build_run_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Project one joined runs⨯anchors row plus a few aux queries into the
    PLAN §6 schema entry."""
    run_id = row["run_id"]
    started_at = row["started_at"]
    ended_at = row["ended_at"]
    duration = _duration_ms(started_at, ended_at) if ended_at else None
    fd = _final_decision_payload(conn, run_id)
    return {
        "run_id": run_id,
        "agent_id": row["agent_id"],
        "subject_ref": (fd or {}).get("candidate_id"),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration,
        "event_count": _event_count(conn, run_id),
        "final_decision": (fd or {}).get("decision"),
        "anchor_tx": row["tx_hash"],
        "anchor_block": row["block_number"],
        "has_summary": _has_summary(conn, run_id),
    }


def build_runs_index(db_path: Path) -> dict[str, Any]:
    """Read DB, return PLAN §6 runs-index dict. Pure function — no disk I/O."""
    if not db_path.exists():
        return {
            "version": RUNS_INDEX_VERSION,
            "generated_at": _now_iso_utc(),
            "runs": [],
        }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT r.run_id, r.agent_id, r.started_at, r.ended_at,
                   a.tx_hash, a.block_number
            FROM runs r
            JOIN anchors a ON a.run_id = r.run_id
            WHERE a.block_number IS NOT NULL
            ORDER BY r.started_at DESC
            """,
        ).fetchall()
        runs = [_build_run_entry(conn, row) for row in rows]
    finally:
        conn.close()
    return {
        "version": RUNS_INDEX_VERSION,
        "generated_at": _now_iso_utc(),
        "runs": runs,
    }


def write_runs_index(index: dict[str, Any], output_path: Path) -> Path:
    """Serialize to disk; create parent directories on demand."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False)
    output_path.write_text(body, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Generate dashboard/public/runs-index.json (PLAN §6 / D17).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output path (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    output_path = args.output if args.output is not None else DEFAULT_OUTPUT

    index = build_runs_index(db_path)
    written = write_runs_index(index, output_path)
    rprint(f"[bold green]✓ wrote {written}[/bold green]")
    rprint(f"  runs:     {len(index['runs'])}")
    for run in index["runs"]:
        decision = run["final_decision"] or "—"
        subj = run["subject_ref"] or "—"
        rprint(
            f"    · {run['run_id']}  {run['agent_id']}  "
            f"{subj}  {decision}  {run['event_count']} events",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
