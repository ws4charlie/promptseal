"""Generate dashboard/public/runs-index.json — the operator's runs list.

PLAN §6 (E2 backend) + D17. Each entry is one anchored run with the metadata
the dashboard's RunsListPage renders. Newest-first by started_at. Excludes
runs without a confirmed anchor (no anchor row, or anchor.block_number IS
NULL — i.e. an in-flight TX).

E6a (v0.3): also exports per-run evidence packs to
`dashboard/public/sample-pack-<run_id>.json` by default, so the dashboard
can resolve the click-row → /run/<id>?evidence=/sample-pack-<id>.json
flow with a single command. Use --no-export-packs to skip.

Usage:
    python scripts/07_runs_list.py
    python scripts/07_runs_list.py --output /tmp/runs.json
    python scripts/07_runs_list.py --no-export-packs   # skip per-run exports
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
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


def _load_export_module() -> ModuleType:
    """Lazily load `scripts/04_export_evidence_pack.py`.

    The leading-digit filename prevents a regular `import`. Mirrors the
    spec_from_file_location pattern that tests/test_evidence_pack.py and
    tests/test_runs_list.py already use to load these scripts.
    """
    script = Path(__file__).resolve().parent / "04_export_evidence_pack.py"
    spec = importlib.util.spec_from_file_location(
        "promptseal_export_evidence_pack_lazy", script,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def export_sample_packs(
    runs: list[dict[str, Any]],
    db_path: Path,
    output_dir: Path,
) -> tuple[int, int]:
    """For each anchored run in `runs`, write a per-run evidence pack to
    `output_dir/sample-pack-<run_id>.json` via the canonical export path
    (scripts/04_export_evidence_pack.py).

    Per-run failures don't abort the loop — they're logged and counted.
    Returns (success_count, failure_count). Caller decides what to do
    with the failure count; for v0.3 we just print the totals and return
    exit code 0 either way (pack export is best-effort enrichment, not
    a fatal step).
    """
    if not runs:
        return (0, 0)
    mod = _load_export_module()
    output_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for run in runs:
        run_id = run["run_id"]
        out = output_dir / f"sample-pack-{run_id}.json"
        try:
            mod.export_evidence_pack(run_id, db_path, output_path=out, as_zip=False)
        except Exception as e:  # broad on purpose: any single-run failure is
            # non-fatal and we want to surface the message verbatim.
            rprint(f"  [yellow]✗ failed for {run_id}: {e}[/yellow]")
            fail += 1
            continue
        rprint(f"  [green]✓ exported {out.name}[/green]")
        ok += 1
    return (ok, fail)


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
    parser.add_argument(
        "--export-packs",
        dest="export_packs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Also export per-run evidence packs to "
            "<output_dir>/sample-pack-<run_id>.json. "
            "Use --no-export-packs to skip. Default: enabled."
        ),
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

    if args.export_packs and index["runs"]:
        # Co-locate sample packs with runs-index.json (same dashboard/public/
        # directory by default; honors --output's parent if redirected).
        sample_pack_dir = output_path.parent
        rprint("[bold]exporting per-run evidence packs[/bold]")
        ok, fail = export_sample_packs(index["runs"], db_path, sample_pack_dir)
        if fail > 0:
            rprint(
                f"  packs: [green]{ok} ok[/green], "
                f"[yellow]{fail} failed[/yellow] (non-fatal — index still written)",
            )
        else:
            rprint(f"  packs: [green]{ok} ok[/green]")

    # Per E6a contract: even single-run export failures don't change exit code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
