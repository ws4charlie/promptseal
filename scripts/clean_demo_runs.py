"""Prune debug-period runs from the demo SQLite, keep the two happy paths.

Two runs are protected (the demo's evidence-of-record):
- run-3e732839c923 — milestone 3 happy path (17 receipts, anchored)
- run-e8b202cfc898 — milestone 5 happy path with ERC-8004 token #633
                     (15 receipts, anchored, the URL in README.md)

Everything else in the `runs` table is treated as stale debug iteration and
is removed along with its receipts. Orphan rows in `anchors` (rows whose
run_id no longer exists in `runs`) are also swept.

Usage:
    python scripts/clean_demo_runs.py                # default: --dry-run
    python scripts/clean_demo_runs.py --execute      # actually delete
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint
from rich.table import Table


# Runs that the README + demo cheat sheet point at — never delete these.
KEEPER_RUN_IDS: frozenset[str] = frozenset({
    "run-3e732839c923",
    "run-e8b202cfc898",
})


@dataclass
class CleanPlan:
    """What clean() will do (or did, after --execute)."""

    stale_runs: list[tuple[str, int]] = field(default_factory=list)  # (run_id, receipt_count)
    keepers_present: list[str] = field(default_factory=list)
    keepers_missing: list[str] = field(default_factory=list)
    orphan_anchor_run_ids: list[str] = field(default_factory=list)
    executed: bool = False


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def survey(conn: sqlite3.Connection) -> CleanPlan:
    """Compute the deletion plan without mutating anything."""
    plan = CleanPlan()

    # All runs + receipt counts.
    if _table_exists(conn, "runs"):
        rows = conn.execute(
            """SELECT r.run_id, COUNT(rc.id) AS n
               FROM runs r LEFT JOIN receipts rc ON rc.run_id = r.run_id
               GROUP BY r.run_id ORDER BY r.started_at ASC"""
        ).fetchall()
        for run_id, n in rows:
            if run_id in KEEPER_RUN_IDS:
                plan.keepers_present.append(run_id)
            else:
                plan.stale_runs.append((run_id, n))
        plan.keepers_missing = sorted(KEEPER_RUN_IDS - set(plan.keepers_present))

    # Orphan anchors: anchor rows whose run_id isn't in `runs`. Includes
    # anchors that will become orphans after we delete stale runs.
    if _table_exists(conn, "anchors"):
        all_anchor_run_ids = {
            row[0] for row in conn.execute("SELECT run_id FROM anchors")
        }
        run_ids_after_clean = {r[0] for r in conn.execute("SELECT run_id FROM runs")}
        run_ids_after_clean -= {rid for rid, _ in plan.stale_runs}
        plan.orphan_anchor_run_ids = sorted(all_anchor_run_ids - run_ids_after_clean)

    return plan


def execute(conn: sqlite3.Connection, plan: CleanPlan) -> None:
    """Apply the plan in a single transaction."""
    stale_ids = [rid for rid, _ in plan.stale_runs]
    if stale_ids:
        # delete child rows first to satisfy FK constraints
        conn.executemany(
            "DELETE FROM receipts WHERE run_id = ?", [(rid,) for rid in stale_ids]
        )
        conn.executemany(
            "DELETE FROM anchors WHERE run_id = ?", [(rid,) for rid in stale_ids]
        )
        conn.executemany(
            "DELETE FROM runs WHERE run_id = ?", [(rid,) for rid in stale_ids]
        )
    if plan.orphan_anchor_run_ids:
        conn.executemany(
            "DELETE FROM anchors WHERE run_id = ?",
            [(rid,) for rid in plan.orphan_anchor_run_ids],
        )
    conn.commit()
    plan.executed = True


def clean(db_path: Path, *, execute_flag: bool = False) -> CleanPlan:
    """Compute the plan and optionally apply it. Returns the plan."""
    if not db_path.exists():
        # Fresh checkout, nothing to do.
        return CleanPlan()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        plan = survey(conn)
        if execute_flag:
            execute(conn, plan)
    finally:
        conn.close()
    return plan


def _print_plan(plan: CleanPlan, db_path: Path, *, dry_run: bool) -> None:
    header = "DRY RUN — nothing deleted" if dry_run else "EXECUTED"
    rprint(f"[bold]{header}[/bold]  ({db_path})")

    if plan.stale_runs:
        t = Table(title="Stale runs (will be deleted)" if dry_run else "Stale runs (deleted)")
        t.add_column("run_id", style="red")
        t.add_column("receipts", justify="right")
        for run_id, n in plan.stale_runs:
            t.add_row(run_id, str(n))
        rprint(t)
    else:
        rprint("[dim]No stale runs found.[/dim]")

    if plan.keepers_present:
        rprint(f"[green]Keepers (preserved):[/green] {', '.join(plan.keepers_present)}")
    if plan.keepers_missing:
        rprint(
            f"[yellow]Keepers missing from DB:[/yellow] "
            f"{', '.join(plan.keepers_missing)}"
        )

    if plan.orphan_anchor_run_ids:
        rprint(
            f"[red]Orphan anchor rows ({len(plan.orphan_anchor_run_ids)}):[/red] "
            f"{', '.join(plan.orphan_anchor_run_ids)}"
        )
    else:
        rprint("[dim]No orphan anchors.[/dim]")

    if dry_run and (plan.stale_runs or plan.orphan_anchor_run_ids):
        rprint("\nRe-run with [bold]--execute[/bold] to apply.")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Prune stale debug runs from the demo SQLite.",
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) Show what would be deleted without changing the DB.",
    )
    g.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the stale runs and orphan anchors.",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    plan = clean(db_path, execute_flag=args.execute)
    _print_plan(plan, db_path, dry_run=not args.execute)
    return 0


if __name__ == "__main__":
    sys.exit(main())
