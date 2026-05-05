"""Reset PromptSeal local state for clean iteration.

Default mode clears the SQLite database (receipts, anchors, runs, and
run_summaries when present) but **preserves** the Ed25519 keypair and
`agent_id.json`. Regenerating the keypair requires re-registering the agent
on ERC-8004 (gas + a brand-new token id that breaks all historical receipts),
so 99% of resets just want a clean DB.

Usage:
    python scripts/reset.py                  # default: clear DB only
    python scripts/reset.py --full           # also delete keypair + agent_id.json
    python scripts/reset.py --yes            # skip confirmation prompt
    python scripts/reset.py --full --yes     # both

Refuses to run while any anchor row has a NULL block_number — that means
an anchor TX is still in flight and resetting now would lose the linkage
between an on-chain transaction and the run it anchored.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from rich import print as rprint


# Tables cleared in default mode. run_summaries is appended at runtime if
# the table exists (forward-compat with v0.2 A3).
_CORE_TABLES = ("receipts", "anchors", "runs")
_OPTIONAL_TABLES = ("run_summaries",)


@dataclass
class ResetSummary:
    """What `reset()` actually did. Returned for tests + caller display."""

    tables_cleared: list[str] = field(default_factory=list)
    files_removed: list[Path] = field(default_factory=list)
    files_preserved: list[Path] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _check_in_flight_anchors(conn: sqlite3.Connection) -> int:
    """Count anchor rows with NULL block_number (TX sent but not confirmed)."""
    if not _table_exists(conn, "anchors"):
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE block_number IS NULL"
    ).fetchone()[0]


def _confirm(prompt: str, prompt_fn: Callable[[str], str]) -> bool:
    """Return True iff user answers y/yes (case-insensitive)."""
    answer = prompt_fn(prompt).strip().lower()
    return answer in ("y", "yes")


def reset(
    db_path: Path,
    key_path: Path,
    agent_id_path: Path,
    *,
    full: bool = False,
    assume_yes: bool = False,
    prompt_fn: Callable[[str], str] = input,
) -> ResetSummary:
    """Clear local state. Returns a ResetSummary describing what changed.

    Default mode (`full=False`): clear DB tables, preserve keypair + agent_id.
    `full=True`: also delete `key_path` and `agent_id_path`.
    """
    summary = ResetSummary()

    # Compose the list of tables we'd clear, including run_summaries if
    # the A3 migration has already run.
    tables_to_clear: list[str] = []
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            if _check_in_flight_anchors(conn) > 0:
                summary.aborted = True
                summary.abort_reason = (
                    "anchors table has rows with NULL block_number "
                    "(in-flight TX) — wait for confirmation before resetting"
                )
                return summary
            for t in _CORE_TABLES:
                if _table_exists(conn, t):
                    tables_to_clear.append(t)
            for t in _OPTIONAL_TABLES:
                if _table_exists(conn, t):
                    tables_to_clear.append(t)

    files_to_remove: list[Path] = []
    files_to_preserve: list[Path] = [key_path, agent_id_path]
    if full:
        files_to_remove = [key_path, agent_id_path]
        files_to_preserve = []

    # Confirm with the user unless --yes.
    if not assume_yes:
        rprint("[bold]Reset plan:[/bold]")
        if tables_to_clear:
            rprint(f"  Clear DB tables: [cyan]{', '.join(tables_to_clear)}[/cyan] in {db_path}")
        else:
            rprint(f"  No DB at {db_path} (or no known tables) — nothing to clear")
        if files_to_remove:
            for f in files_to_remove:
                rprint(f"  [red]Delete file:[/red] {f}")
            rprint(
                "  [yellow]WARNING:[/yellow] --full removes the agent keypair. "
                "ERC-8004 token #633 (or whatever you've registered) will be "
                "orphaned — it still exists on-chain but local code can no "
                "longer use it. You'll need to re-register a new agent."
            )
        else:
            for f in files_to_preserve:
                rprint(f"  [green]Preserve file:[/green] {f}")
        if not _confirm("Proceed? [y/N] ", prompt_fn):
            summary.aborted = True
            summary.abort_reason = "user declined at confirmation prompt"
            return summary

    # Apply.
    if tables_to_clear:
        with sqlite3.connect(db_path) as conn:
            for t in tables_to_clear:
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        summary.tables_cleared = tables_to_clear

    for f in files_to_remove:
        if f.exists():
            f.unlink()
            summary.files_removed.append(f)
    summary.files_preserved = [f for f in files_to_preserve if f.exists()]

    return summary


def _print_result(summary: ResetSummary) -> None:
    if summary.aborted:
        rprint(f"[yellow]Aborted:[/yellow] {summary.abort_reason}")
        return
    if summary.tables_cleared:
        rprint(f"[green]Cleared tables:[/green] {', '.join(summary.tables_cleared)}")
    if summary.files_removed:
        for f in summary.files_removed:
            rprint(f"[green]Removed:[/green] {f}")
    if summary.files_preserved:
        for f in summary.files_preserved:
            rprint(f"[green]Preserved:[/green] {f}")
    rprint("[bold green]✓ reset complete[/bold green]")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Reset PromptSeal local state (DB + optionally keypair).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also delete the agent keypair and agent_id.json (rare).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    key_path = Path(os.getenv("PROMPTSEAL_KEY_PATH", "./agent_key.pem"))
    agent_id_path = Path("./agent_id.json")

    summary = reset(
        db_path=db_path,
        key_path=key_path,
        agent_id_path=agent_id_path,
        full=args.full,
        assume_yes=args.yes,
    )
    _print_result(summary)
    return 1 if summary.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
