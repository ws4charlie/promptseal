"""SQLite-backed hash-chained storage for receipts.

Design notes:
- Receipts are stored in *individual columns* (not as one opaque JSON blob)
  so the demo's tamper command (BRIEF §13) — `UPDATE receipts SET
  payload_excerpt = '...' WHERE id = N` — actually mutates the canonical body
  and is detected by `verify_chain`.
- A `runs` table groups receipts; each run's events form their own hash chain
  (parent_hash is run-local).
- `append` is the only insertion path and enforces both the chain link
  (parent_hash == latest in run) and self-consistency (signature verifies).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import canonical_json
from .receipt import verify_receipt


class ChainIntegrityError(Exception):
    """Raised when an append violates hash-chain or signature invariants."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS receipts (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                   TEXT NOT NULL,
  schema_version           TEXT NOT NULL,
  agent_id                 TEXT NOT NULL,
  agent_erc8004_token_id   INTEGER,
  event_type               TEXT NOT NULL,
  timestamp                TEXT NOT NULL,
  parent_hash              TEXT,
  paired_event_hash        TEXT,
  payload_excerpt          TEXT NOT NULL,
  public_key               TEXT NOT NULL,
  signature                TEXT NOT NULL,
  event_hash               TEXT NOT NULL UNIQUE,
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_receipts_run_id ON receipts(run_id, id);

CREATE TABLE IF NOT EXISTS anchors (
  run_id        TEXT PRIMARY KEY,
  merkle_root   TEXT NOT NULL,
  tx_hash       TEXT NOT NULL,
  block_number  INTEGER,
  chain_id      INTEGER NOT NULL,
  anchored_at   TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ReceiptChain:
    """SQLite-backed receipt store with append-only hash-chain semantics."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- runs ---------------------------------------------------------------

    def open_run(self, run_id: str, agent_id: str) -> None:
        """Register a new run. parent_hash for its first receipt must be None."""
        self._conn.execute(
            "INSERT INTO runs (run_id, agent_id, started_at) VALUES (?, ?, ?)",
            (run_id, agent_id, _now_iso()),
        )
        self._conn.commit()

    def close_run(self, run_id: str) -> None:
        """Mark a run ended. Does not prevent further appends — Merkle batching
        downstream is what closes the run for anchoring."""
        self._conn.execute(
            "UPDATE runs SET ended_at = ? WHERE run_id = ?", (_now_iso(), run_id)
        )
        self._conn.commit()

    # -- receipts -----------------------------------------------------------

    def latest_event_hash(self, run_id: str) -> str | None:
        """event_hash of the most recently appended receipt in *run_id*, or None."""
        row = self._conn.execute(
            "SELECT event_hash FROM receipts WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return row["event_hash"] if row else None

    def append(self, run_id: str, receipt: dict[str, Any]) -> int:
        """Append *receipt* to *run_id*'s chain.

        Raises ChainIntegrityError if:
        - parent_hash does not equal the previous receipt's event_hash, or
        - the receipt fails signature/hash verification (already tampered).
        """
        last = self.latest_event_hash(run_id)
        if receipt.get("parent_hash") != last:
            raise ChainIntegrityError(
                f"parent_hash {receipt.get('parent_hash')!r} does not match "
                f"latest event_hash {last!r} for run {run_id!r}"
            )
        if not verify_receipt(receipt):
            raise ChainIntegrityError(
                "receipt failed signature/hash verification before insert"
            )
        cur = self._conn.execute(
            """INSERT INTO receipts
               (run_id, schema_version, agent_id, agent_erc8004_token_id,
                event_type, timestamp, parent_hash, paired_event_hash,
                payload_excerpt, public_key, signature, event_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                receipt["schema_version"],
                receipt["agent_id"],
                receipt["agent_erc8004_token_id"],
                receipt["event_type"],
                receipt["timestamp"],
                receipt["parent_hash"],
                receipt["paired_event_hash"],
                canonical_json(receipt["payload_excerpt"]).decode("utf-8"),
                receipt["public_key"],
                receipt["signature"],
                receipt["event_hash"],
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    @staticmethod
    def _row_to_receipt(row: sqlite3.Row) -> dict[str, Any]:
        return {
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
        }

    def get_receipts(self, run_id: str) -> list[dict[str, Any]]:
        """All receipts for *run_id* in insertion order."""
        cur = self._conn.execute(
            "SELECT * FROM receipts WHERE run_id = ? ORDER BY id ASC", (run_id,)
        )
        return [self._row_to_receipt(r) for r in cur.fetchall()]

    # -- anchors ------------------------------------------------------------

    def record_anchor(
        self,
        run_id: str,
        merkle_root: str,
        tx_hash: str,
        block_number: int | None,
        chain_id: int,
    ) -> None:
        """Persist anchor metadata for *run_id*. One row per run."""
        self._conn.execute(
            """INSERT OR REPLACE INTO anchors
               (run_id, merkle_root, tx_hash, block_number, chain_id, anchored_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, merkle_root, tx_hash, block_number, chain_id, _now_iso()),
        )
        self._conn.commit()

    def get_anchor(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM anchors WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_run_ids(self) -> list[str]:
        """All run_ids in the runs table, oldest first."""
        return [r["run_id"] for r in self._conn.execute(
            "SELECT run_id FROM runs ORDER BY started_at ASC"
        )]

    # -- chain integrity ----------------------------------------------------

    def verify_chain(self, run_id: str) -> tuple[bool, str | None]:
        """Walk *run_id*'s chain and verify each link + signature.

        Returns (True, None) if every receipt verifies and parent_hash links
        are intact. Returns (False, "<reason>") on first failure.
        """
        receipts = self.get_receipts(run_id)
        previous_hash: str | None = None
        for i, r in enumerate(receipts):
            if r.get("parent_hash") != previous_hash:
                return False, (
                    f"receipt #{i} (id={r['event_hash'][:14]}...) parent_hash "
                    f"mismatch: expected {previous_hash!r}, got {r.get('parent_hash')!r}"
                )
            if not verify_receipt(r):
                return False, (
                    f"receipt #{i} (id={r['event_hash'][:14]}...) failed "
                    f"signature/hash verification"
                )
            previous_hash = r["event_hash"]
        return True, None
