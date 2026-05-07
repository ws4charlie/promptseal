#!/usr/bin/env python3
"""Live demo wrapper: run agent + anchor + export in one command.

Thin shell over scripts/02_run_demo.py + scripts/03_anchor_run.py +
scripts/07_runs_list.py — does not duplicate any business logic. Each
underlying script remains independently invokable; this wrapper exists
purely so the on-stage operator types one command instead of three.

Any step's non-zero exit halts the wrapper immediately. On anchor
failure the run remains in the DB unanchored — the wrapper prints the
exact retry command so the operator can resume from where it broke.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: demo_live.py <candidate_id>", file=sys.stderr)
        print("Example: demo_live.py res_008", file=sys.stderr)
        return 1

    candidate = sys.argv[1]
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    db_path = project_root / "promptseal.sqlite"

    if not venv_python.exists():
        print(f"✗ venv python not found at {venv_python}", file=sys.stderr)
        return 1

    # Step 1: Run agent
    print(f"\n=== [1/3] Running agent for {candidate} ===")
    result = subprocess.run(
        [str(venv_python), "scripts/02_run_demo.py", candidate],
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"\n✗ Agent run failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    # Get latest run_id from DB (silent — internal lookup, not a user-visible step)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("\n✗ No run found in DB after agent execution", file=sys.stderr)
        return 1
    new_run = row[0]
    print(f"\nNew run: {new_run}")

    # Step 2: Anchor on chain
    print(f"\n=== [2/3] Anchoring on Base Sepolia ===")
    result = subprocess.run(
        [str(venv_python), "scripts/03_anchor_run.py", new_run],
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"\n✗ Anchor failed (exit {result.returncode})", file=sys.stderr)
        print("  Run is in DB but not anchored. To retry:", file=sys.stderr)
        print(
            f"    .venv/bin/python scripts/03_anchor_run.py {new_run}",
            file=sys.stderr,
        )
        return result.returncode

    # Step 3: Export evidence packs (regenerates runs-index.json + sample-packs)
    print(f"\n=== [3/3] Exporting evidence packs ===")
    result = subprocess.run(
        [str(venv_python), "scripts/07_runs_list.py"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"\n✗ Export failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    print("\n✓ Done. Refresh your dashboard (Cmd+Shift+R).")
    print(f"  Run ID: {new_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
