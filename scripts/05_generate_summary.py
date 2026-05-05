"""Generate + store a natural-language summary of a run.

By default, the summary is stored with `included_in_merkle=False` per D2 —
it's convenience text, not law-grade evidence. Pass `--include-in-merkle`
to flip the flag for Tier 3-style anchoring (the next anchor pass will
include the summary's hash as an extra Merkle leaf via
`promptseal/anchor.build_run_leaves`).

Usage:
    python scripts/05_generate_summary.py <run_id>
    python scripts/05_generate_summary.py <run_id> --llm-model gpt-4o-mini
    python scripts/05_generate_summary.py <run_id> --include-in-merkle
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint

from promptseal.run_summary import update_summary_merkle_flag
from promptseal.summarizer import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    PromptSealPiiError,
    summarize_run,
)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Generate an LLM summary of a run and store it in the DB.",
    )
    parser.add_argument("run_id", help="Run id, e.g. run-e8b202cfc898")
    parser.add_argument(
        "--llm-provider",
        default=DEFAULT_LLM_PROVIDER,
        help=f"Provider tag stored in the DB row (default: {DEFAULT_LLM_PROVIDER}).",
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_LLM_MODEL,
        help=f"Model name (default: {DEFAULT_LLM_MODEL}).",
    )
    parser.add_argument(
        "--include-in-merkle",
        action="store_true",
        help="Flip included_in_merkle=True so the next anchor pass adds the "
             "summary hash as an extra Merkle leaf (D2 opt-in).",
    )
    args = parser.parse_args(argv)

    db_path = Path(os.getenv("PROMPTSEAL_DB_PATH", "./promptseal.sqlite"))

    try:
        stored = summarize_run(
            args.run_id,
            db_path=db_path,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
        )
    except PromptSealPiiError as exc:
        rprint(f"[red]PII check refused to store summary:[/red] {exc}")
        return 2
    except (ValueError, FileNotFoundError) as exc:
        rprint(f"[red]Summary failed:[/red] {exc}")
        return 1

    if args.include_in_merkle:
        update_summary_merkle_flag(args.run_id, True, db_path=db_path)
        rprint(
            "[yellow]included_in_merkle=True[/yellow] — next anchor pass via "
            "anchor.build_run_leaves will append summary_hash as an extra leaf."
        )

    rprint(f"\n[bold green]✓ stored summary for {args.run_id}[/bold green]")
    rprint(f"  hash:     [dim]{stored['summary_hash']}[/dim]")
    rprint(f"  provider: {stored['llm_provider']} · model: {stored['llm_model']}")
    rprint(f"  in_merkle: {stored['included_in_merkle']}")
    rprint(f"  generated_at: {stored['generated_at']}")
    rprint("\n[bold]Summary:[/bold]")
    rprint(stored["summary_text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
