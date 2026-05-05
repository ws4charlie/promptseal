"""Print the three textarea contents for the static verifier.

Usage:
    python scripts/generate_verifier_inputs.py <run_id> [<receipt_id>]
    python scripts/generate_verifier_inputs.py run-e8b202cfc898         # default to last receipt
    python scripts/generate_verifier_inputs.py run-e8b202cfc898 41      # final_decision

Emits, in this order:
    1. Receipt JSON (canonical bytes — paste into textarea 1)
    2. Merkle proof JSON (paste into textarea 2)
    3. Anchor tx hash (paste into input 3)

Pre-formatted for live demo: no further editing needed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from promptseal.canonical import canonical_json
from promptseal.chain import ReceiptChain
from promptseal.merkle import build_merkle, inclusion_proof


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    run_id = sys.argv[1]
    receipt_id_arg: int | None = int(sys.argv[2]) if len(sys.argv) > 2 else None

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    chain = ReceiptChain(db_path)

    receipts = chain.get_receipts(run_id)
    if not receipts:
        print(f"!! no receipts for run {run_id}", file=sys.stderr)
        return 2

    # get_receipts strips the autoincrement id; pull the id list separately so
    # we can map receipt_id_arg → list index.
    id_rows = chain._conn.execute(
        "SELECT id FROM receipts WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    receipt_ids = [r["id"] for r in id_rows]

    if receipt_id_arg is not None:
        if receipt_id_arg not in receipt_ids:
            print(
                f"!! receipt id {receipt_id_arg} not in run {run_id} "
                f"(valid ids: {receipt_ids[0]}..{receipt_ids[-1]})",
                file=sys.stderr,
            )
            return 3
        idx = receipt_ids.index(receipt_id_arg)
    else:
        idx = len(receipts) - 1

    target = receipts[idx]
    target_id = receipt_ids[idx]

    leaves = [r["event_hash"] for r in receipts]
    tree = build_merkle(leaves)
    proof = inclusion_proof(leaves, idx)

    anchor = chain.get_anchor(run_id) if hasattr(chain, "get_anchor") else None
    if not anchor:
        # ReceiptChain may not expose a getter; query directly.
        row = chain._conn.execute(
            "SELECT tx_hash, merkle_root FROM anchors WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            print(f"!! run {run_id} not anchored — run scripts/03_anchor_run.py first",
                  file=sys.stderr)
            return 4
        anchor = {"tx_hash": row["tx_hash"], "merkle_root": row["merkle_root"]}

    if anchor["merkle_root"] != tree["root"]:
        print("!! WARNING: anchored root does not match recomputed root for this run",
              file=sys.stderr)
        print(f"   anchored : {anchor['merkle_root']}", file=sys.stderr)
        print(f"   recomputed: {tree['root']}", file=sys.stderr)

    receipt_json = canonical_json(target).decode("utf-8")
    proof_json = json.dumps(proof, separators=(",", ":"))

    print(f"# run={run_id} receipt_id={target_id} event_type={target['event_type']} index={idx}")
    print(f"# leaf_count={len(leaves)} merkle_root={tree['root']}")
    print()
    print("# ─── 1. Receipt JSON ────────────────────────────────────────────")
    print(receipt_json)
    print()
    print("# ─── 2. Merkle proof ────────────────────────────────────────────")
    print(proof_json)
    print()
    print("# ─── 3. Anchor tx hash ──────────────────────────────────────────")
    print(anchor["tx_hash"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
