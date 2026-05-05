"""Tamper with one receipt's payload to break the verifier — and restore it.

Usage:
    python scripts/99_tamper_demo.py <receipt_id>            # tamper
    python scripts/99_tamper_demo.py --restore <receipt_id>  # restore

When tampered, two things break independently:
    1. Single-receipt verify  → step 1 fails (event_hash recomputed ≠ stored)
    2. Run-wide verify_chain  → next receipt's parent_hash no longer matches

Backup of the original payload is saved to .tamper_backup_<id>.json (gitignored)
so --restore is non-destructive.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint
from rich.table import Table

TAMPERED_PAYLOAD = '{"i":99}'  # deliberately tiny; visibly breaks demo


def _backup_path(receipt_id: int) -> Path:
    return Path(f".tamper_backup_{receipt_id}.json")


def _show_diff(before: str, after: str) -> None:
    table = Table(title="payload_excerpt diff")
    table.add_column("state", style="bold")
    table.add_column("value", overflow="fold")
    table.add_row("before", before)
    table.add_row("after",  after)
    rprint(table)


def tamper(conn: sqlite3.Connection, receipt_id: int) -> int:
    cur = conn.execute(
        "SELECT id, run_id, event_type, payload_excerpt FROM receipts WHERE id = ?",
        (receipt_id,),
    )
    row = cur.fetchone()
    if row is None:
        rprint(f"[red]no receipt with id={receipt_id}[/red]")
        return 2

    backup = _backup_path(receipt_id)
    if backup.exists():
        rprint(f"[yellow]backup already exists at {backup} — was this receipt already tampered?[/yellow]")
        rprint("[yellow]run with --restore first if you want to re-tamper.[/yellow]")
        return 3

    backup.write_text(json.dumps({
        "id": row["id"],
        "run_id": row["run_id"],
        "event_type": row["event_type"],
        "payload_excerpt": row["payload_excerpt"],
    }, indent=2))
    rprint(f"[dim]backed up original payload → {backup}[/dim]")

    conn.execute(
        "UPDATE receipts SET payload_excerpt = ? WHERE id = ?",
        (TAMPERED_PAYLOAD, receipt_id),
    )
    conn.commit()

    _show_diff(row["payload_excerpt"], TAMPERED_PAYLOAD)

    rprint(f"\n[bold red]✗ tampered receipt id={receipt_id}[/bold red] (run={row['run_id']}, type={row['event_type']})")
    rprint("\nNow:")
    rprint("  1. Re-paste the receipt into [bold]verifier[/bold] → step 1 should fail (event_hash mismatch)")
    rprint("  2. Run [bold]chain.verify_chain[/bold] → should also fail (downstream parent_hash break)")
    rprint(f"  3. When done, restore with: [dim]python scripts/99_tamper_demo.py --restore {receipt_id}[/dim]")
    return 0


def restore(conn: sqlite3.Connection, receipt_id: int) -> int:
    backup = _backup_path(receipt_id)
    if not backup.exists():
        rprint(f"[red]no backup found at {backup} — nothing to restore[/red]")
        return 4
    data = json.loads(backup.read_text())
    conn.execute(
        "UPDATE receipts SET payload_excerpt = ? WHERE id = ?",
        (data["payload_excerpt"], receipt_id),
    )
    conn.commit()
    backup.unlink()
    rprint(f"[bold green]✓ restored receipt id={receipt_id} (backup file removed)[/bold green]")
    return 0


def main() -> int:
    load_dotenv()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    do_restore = False
    if args[0] == "--restore":
        do_restore = True
        args = args[1:]
    if len(args) != 1:
        print(__doc__)
        return 1

    try:
        receipt_id = int(args[0])
    except ValueError:
        rprint(f"[red]receipt_id must be an integer, got {args[0]!r}[/red]")
        return 1

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return restore(conn, receipt_id) if do_restore else tamper(conn, receipt_id)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
