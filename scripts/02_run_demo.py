"""Run the hiring agent on one or more resumes and stream signed receipts.

Usage:
    python scripts/02_run_demo.py                  # screens all 5 resumes
    python scripts/02_run_demo.py res_001          # screens just Alice

Reads ANTHROPIC_API_KEY + PROMPTSEAL_* from .env (BRIEF §4).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint
from rich.table import Table

from agent.hiring_agent import build_agent_executor, screen_resume
from promptseal.chain import ReceiptChain
from promptseal.crypto import (
    generate_keypair,
    load_private_key_pem,
    private_key_to_pem,
)
from promptseal.handler import PromptSealCallbackHandler

ALL_RESUME_IDS = ["res_001", "res_002", "res_003", "res_004", "res_005", "res_006"]


def _load_or_create_key(path: Path):
    if path.exists():
        return load_private_key_pem(path.read_bytes())
    sk = generate_keypair()
    path.write_bytes(private_key_to_pem(sk))
    rprint(f"[yellow]Created new agent key at {path}[/yellow]")
    return sk


def main() -> int:
    load_dotenv()
    # Credential validation lives in agent.llm.make_chat_llm — it raises a clear
    # RuntimeError if neither BIFROST_* nor ANTHROPIC_API_KEY is set.

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))
    key_path = Path(os.getenv("PROMPTSEAL_KEY_PATH", "./agent_key.pem"))
    agent_id = os.getenv("PROMPTSEAL_AGENT_ID", "hr-screener-v1")
    token_id_str = os.getenv("PROMPTSEAL_AGENT_TOKEN_ID")
    token_id = int(token_id_str) if token_id_str else None

    sk = _load_or_create_key(key_path)
    chain = ReceiptChain(db_path)
    handler = PromptSealCallbackHandler(
        sk=sk,
        chain=chain,
        agent_id=agent_id,
        agent_erc8004_token_id=token_id,
    )
    executor = build_agent_executor()

    resume_ids = sys.argv[1:] or ALL_RESUME_IDS

    for rid in resume_ids:
        rprint(f"\n[bold blue]→ Screening {rid}[/bold blue]")
        try:
            # Pass callbacks at invoke time — see build_agent_executor docstring
            # for why constructor-level callbacks don't propagate in 0.3.x.
            result = screen_resume(rid, executor, callbacks=[handler])
        except Exception as exc:
            rprint(f"[red]Agent invocation failed: {exc}[/red]")
            continue
        rprint(f"[green]Agent output:[/green] {result.get('output')}")

        ps_run_id = handler.last_run_id
        if not ps_run_id:
            rprint("[yellow]No PromptSeal run was opened (callbacks may not have fired).[/yellow]")
            continue
        receipts = chain.get_receipts(ps_run_id)

        table = Table(title=f"Run {ps_run_id} — {len(receipts)} receipts")
        table.add_column("#", justify="right")
        table.add_column("event_type", style="cyan")
        table.add_column("event_hash")
        table.add_column("paired", style="dim")
        for i, r in enumerate(receipts):
            paired = (r["paired_event_hash"] or "—")[:16]
            table.add_row(str(i), r["event_type"], r["event_hash"][:24] + "...", paired)
        rprint(table)

        ok, err = chain.verify_chain(ps_run_id)
        if ok:
            rprint("[bold green]✓ chain integrity OK[/bold green]")
        else:
            rprint(f"[bold red]✗ chain integrity FAILED:[/bold red] {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
