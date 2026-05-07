#!/usr/bin/env python3
"""Reset DB and dashboard to Phase C canonical state.

Removes any runs not in the Phase C 6-run set (e.g., demo practice
runs from res_007 / res_008). On-chain anchors are untouched — they
are immutable. Idempotent: running a second time on a clean DB prints
"Already canonical." and exits 0.

Use case: between live-demo dry runs, restore the canonical 6-run
dataset so the next dry run starts from the same baseline.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

PHASE_C_RUN_IDS = {
    "run-3345a152239a",  # Alice
    "run-cab8a47f7500",  # Bob
    "run-b9fe36205c07",  # Carol
    "run-8393bd7ec3c6",  # David
    "run-12b5cf079f14",  # Emma
    "run-788d2d359738",  # Frank
}


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    db_path = project_root / "promptseal.sqlite"
    public_dir = project_root / "dashboard" / "public"

    if not venv_python.exists():
        print(f"✗ venv python not found at {venv_python}", file=sys.stderr)
        return 1
    if not db_path.exists():
        print(f"✗ DB not found at {db_path}", file=sys.stderr)
        return 1

    # Find non-Phase-C runs and their subject_ref (from final_decision payload).
    conn = sqlite3.connect(db_path)
    all_runs = conn.execute("SELECT run_id FROM runs").fetchall()
    extra: list[tuple[str, str]] = []
    for (run_id,) in all_runs:
        if run_id in PHASE_C_RUN_IDS:
            continue
        row = conn.execute(
            "SELECT payload_excerpt FROM receipts "
            "WHERE run_id = ? AND event_type = 'final_decision' LIMIT 1",
            (run_id,),
        ).fetchone()
        subject_ref = "?"
        if row:
            try:
                payload = json.loads(row[0])
                subject_ref = payload.get("candidate_id", "?")
            except (json.JSONDecodeError, TypeError):
                pass
        extra.append((run_id, subject_ref))

    if not extra:
        conn.close()
        print("Already canonical. DB at Phase C 6 runs.")
        return 0

    print(f"Found {len(extra)} non-Phase-C run(s) to remove:")
    for rid, sid in extra:
        print(f"  · {rid}  (subject: {sid})")
    print()

    for rid, _ in extra:
        conn.execute("DELETE FROM receipts WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM anchors WHERE run_id = ?", (rid,))
    conn.commit()

    after_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    after_anchors = conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
    after_receipts = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    conn.close()

    print(
        f"DB after cleanup: {after_runs} runs / "
        f"{after_anchors} anchors / {after_receipts} receipts"
    )

    # Regenerate dashboard JSONs from current DB state.
    print("\nRegenerating dashboard JSONs...")
    result = subprocess.run(
        [str(venv_python), "scripts/07_runs_list.py"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"\n✗ Re-export failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    # Clean orphan per-run files left behind in dashboard/public/ — 07
    # only writes; it never deletes, so deleted runs leave stale JSON
    # packs and HTML bundles behind. Cleanup walks both extensions.
    canonical_pack_files = {f"sample-pack-{rid}.json" for rid in PHASE_C_RUN_IDS}
    canonical_bundle_files = {f"evidence-bundle-{rid}.html" for rid in PHASE_C_RUN_IDS}
    orphans: list[str] = []
    for f in public_dir.glob("sample-pack-run-*.json"):
        if f.name not in canonical_pack_files:
            f.unlink()
            orphans.append(f.name)
    for f in public_dir.glob("evidence-bundle-run-*.html"):
        if f.name not in canonical_bundle_files:
            f.unlink()
            orphans.append(f.name)

    if orphans:
        print(f"\nRemoved {len(orphans)} orphan dashboard file(s):")
        for o in orphans:
            print(f"  · {o}")

    print("\n✓ Reset complete. DB at Phase C 6 runs canonical state.")
    print("  You can now ./scripts/demo_live.py res_007 (or res_008) again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
