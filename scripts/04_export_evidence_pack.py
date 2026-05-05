"""Export an evidence pack — single JSON (or ZIP) usable by the dashboard.

Per PLAN §7 (canonical schema) + D4 (one shape, both URL-load and self-
contained HTML use the same parser):

    {
      "version": "0.2",
      "agent_id": "...",
      "agent_erc8004_token_id": 633 | null,
      "run_id": "...",
      "receipts": [...],
      "merkle_root": "sha256:...",
      "anchor": {"tx_hash": "0x...", "block_number": ..., "chain_id": 84532},
      "proofs": {"<receipt_id>": [...]},
      "summary": {...}    // optional
    }

Usage:
    python scripts/04_export_evidence_pack.py <run_id>
    python scripts/04_export_evidence_pack.py <run_id> --output /tmp/p.json
    python scripts/04_export_evidence_pack.py <run_id> --zip
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich import print as rprint

from promptseal.merkle import build_merkle, inclusion_proof
from promptseal.run_summary import load_run_summary

EVIDENCE_PACK_VERSION = "0.2"
DEFAULT_CHAIN_ID = 84532  # Base Sepolia
README_TEXT = """\
PromptSeal evidence pack
========================

This ZIP contains:
  evidence-pack.json   — the canonical evidence pack (PLAN §7 schema)
  README.txt           — this file

To verify:
  1. Open the dashboard and load evidence-pack.json by URL or drag-drop, OR
  2. Use the vanilla verifier at promptseal/verifier/index.html and paste
     individual receipts.

The pack is self-contained data — anyone can verify the receipts against
Base Sepolia (chain_id 84532) without trusting the sender.
"""


class EvidencePackError(Exception):
    """Raised when an evidence pack cannot be built (missing run / anchor)."""


def _fetch_receipts(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Receipts for `run_id`, ordered by id, with `id` and parsed payload."""
    rows = conn.execute(
        "SELECT * FROM receipts WHERE run_id = ? ORDER BY id ASC", (run_id,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "id": row["id"],
            "agent_erc8004_token_id": row["agent_erc8004_token_id"],
            "agent_id": row["agent_id"],
            "event_hash": row["event_hash"],
            "event_type": row["event_type"],
            "paired_event_hash": row["paired_event_hash"],
            "parent_hash": row["parent_hash"],
            "payload_excerpt": json.loads(row["payload_excerpt"]),
            "public_key": row["public_key"],
            "schema_version": row["schema_version"],
            "signature": row["signature"],
            "timestamp": row["timestamp"],
        })
    return out


def _fetch_anchor(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT tx_hash, block_number, chain_id FROM anchors WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "tx_hash": row["tx_hash"],
        "block_number": row["block_number"],
        "chain_id": row["chain_id"] if row["chain_id"] is not None else DEFAULT_CHAIN_ID,
    }


def build_evidence_pack(run_id: str, db_path: Path) -> dict[str, Any]:
    """Pure function — builds the dict per PLAN §7. Does not write to disk."""
    if not db_path.exists():
        raise EvidencePackError(f"DB not found at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        receipts = _fetch_receipts(conn, run_id)
        if not receipts:
            raise EvidencePackError(
                f"run {run_id!r} has no receipts (or doesn't exist)"
            )
        anchor = _fetch_anchor(conn, run_id)
        if anchor is None:
            raise EvidencePackError(
                f"run {run_id!r} is not anchored — run scripts/03_anchor_run.py first"
            )
    finally:
        conn.close()

    leaves = [r["event_hash"] for r in receipts]
    tree = build_merkle(leaves)
    proofs = {
        str(r["id"]): inclusion_proof(leaves, i)
        for i, r in enumerate(receipts)
    }

    pack: dict[str, Any] = {
        "version": EVIDENCE_PACK_VERSION,
        "agent_id": receipts[0]["agent_id"],
        "agent_erc8004_token_id": receipts[0]["agent_erc8004_token_id"],
        "run_id": run_id,
        "receipts": receipts,
        "merkle_root": tree["root"],
        "anchor": anchor,
        "proofs": proofs,
    }

    summary = load_run_summary(run_id, db_path=db_path)
    if summary is not None:
        # Map run_summary's flat shape into PLAN §7's nested "summary" object.
        pack["summary"] = {
            "text": summary["summary_text"],
            "hash": summary["summary_hash"],
            "generated_at": summary["generated_at"],
            "llm_provider": summary["llm_provider"],
            "llm_model": summary["llm_model"],
            "included_in_merkle": summary["included_in_merkle"],
        }

    return pack


def write_evidence_pack(pack: dict[str, Any], output_path: Path, *, as_zip: bool) -> Path:
    """Serialize `pack` to disk. Returns the actual path written."""
    body = json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False)
    if not as_zip:
        output_path.write_text(body, encoding="utf-8")
        return output_path

    zip_path = output_path.with_suffix(".zip") if output_path.suffix != ".zip" else output_path
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("evidence-pack.json", body)
        zf.writestr("README.txt", README_TEXT)
    return zip_path


def export_evidence_pack(
    run_id: str,
    db_path: Path,
    output_path: Path | None = None,
    *,
    as_zip: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Build + write. Returns (path, pack)."""
    pack = build_evidence_pack(run_id, db_path)
    if output_path is None:
        suffix = ".zip" if as_zip else ".json"
        output_path = Path(f"./evidence-pack-{run_id}{suffix}")
    written = write_evidence_pack(pack, output_path, as_zip=as_zip)
    return written, pack


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Export a PromptSeal evidence pack for a run.",
    )
    parser.add_argument("run_id", help="Run id to export, e.g. run-e8b202cfc898")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: ./evidence-pack-<run_id>.{json,zip}).",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Wrap the JSON + README.txt in a ZIP file.",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))

    try:
        path, pack = export_evidence_pack(
            args.run_id, db_path, output_path=args.output, as_zip=args.zip,
        )
    except EvidencePackError as exc:
        rprint(f"[red]Export failed:[/red] {exc}")
        return 1

    rprint(f"[bold green]✓ wrote {path}[/bold green]")
    rprint(f"  receipts:    {len(pack['receipts'])}")
    rprint(f"  merkle_root: {pack['merkle_root']}")
    rprint(f"  anchor:      {pack['anchor']['tx_hash']} (block {pack['anchor']['block_number']})")
    if "summary" in pack:
        rprint(f"  summary:     present, included_in_merkle={pack['summary']['included_in_merkle']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
