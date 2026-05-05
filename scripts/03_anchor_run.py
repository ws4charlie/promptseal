"""Anchor a run's Merkle root to Base Sepolia.

Usage:
  python scripts/03_anchor_run.py                       # latest run with receipts
  python scripts/03_anchor_run.py run-3e732839c923      # specific run
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint

from promptseal.anchor import anchor_root
from promptseal.chain import ReceiptChain
from promptseal.merkle import build_merkle


def _pick_run_id(chain: ReceiptChain) -> str | None:
    """Most recent run that has at least one receipt."""
    runs = chain.list_run_ids()
    for rid in reversed(runs):  # most recent first
        if chain.get_receipts(rid):
            return rid
    return None


def main() -> int:
    load_dotenv()

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    rpc_url = os.environ["BASE_SEPOLIA_RPC_URL"]
    chain_id = int(os.environ["BASE_SEPOLIA_CHAIN_ID"])
    pk = os.environ["DEPLOYER_PRIVATE_KEY"]

    chain = ReceiptChain(db_path)

    run_id = sys.argv[1] if len(sys.argv) > 1 else _pick_run_id(chain)
    if not run_id:
        rprint("[red]No run with receipts found in DB.[/red]")
        return 1

    receipts = chain.get_receipts(run_id)
    if not receipts:
        rprint(f"[red]Run {run_id} has no receipts to anchor.[/red]")
        return 1

    leaves = [r["event_hash"] for r in receipts]
    tree = build_merkle(leaves)
    root = tree["root"]

    rprint(f"[bold blue]→ Anchoring run {run_id}[/bold blue]")
    rprint(f"  receipts:    {len(leaves)}")
    rprint(f"  merkle root: {root}")
    rprint(f"  rpc:         {rpc_url}")
    rprint(f"  chain_id:    {chain_id}")
    rprint("[dim]submitting self-send TX, waiting 1 confirmation…[/dim]")

    try:
        result = anchor_root(
            root_hex=root,
            rpc_url=rpc_url,
            chain_id=chain_id,
            private_key=pk,
        )
    except Exception as exc:  # noqa: BLE001
        rprint(f"[red]Anchor failed:[/red] {type(exc).__name__}: {exc}")
        return 2

    chain.record_anchor(
        run_id=run_id,
        merkle_root=result.merkle_root,
        tx_hash=result.tx_hash,
        block_number=result.block_number,
        chain_id=result.chain_id,
    )

    rprint("[bold green]✓ anchored[/bold green]")
    rprint(f"  tx_hash:     {result.tx_hash}")
    rprint(f"  block:       {result.block_number}")
    rprint(f"  gas_used:    {result.gas_used}")
    rprint(f"  sender:      {result.sender}")
    rprint(f"  basescan:    https://sepolia.basescan.org/tx/{result.tx_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
