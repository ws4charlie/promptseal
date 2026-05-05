"""Register the agent's Ed25519 public key to ERC-8004 on Base Sepolia.

Usage:
  python scripts/01_register_agent.py             # register
  python scripts/01_register_agent.py --dry-run   # validate env + estimate gas, no TX

Run ONCE before the demo. Persists the minted tokenId (and tx metadata) to
`agent_id.json` so subsequent receipts can populate `agent_erc8004_token_id`.

Gotchas guarded against (RESUMPTION-NOTES §"4 specific gotchas"):
  #1 tokenId — decoded from Transfer event log (handled in promptseal.erc8004)
  #2 keypair — loads existing `agent_key.pem`; aborts if missing
  #3 gas    — estimate × 1.30; estimate revert is fatal (no silent fallback)
  #4 .env   — writes to `agent_id.json` only; never touches `.env`
"""
from __future__ import annotations

import json
import os
import sys
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from rich import print as rprint
from web3 import Web3

from promptseal.crypto import load_private_key_pem, public_key_bytes
from promptseal.erc8004 import (
    ERC8004RegistrationError,
    agent_card_to_data_uri,
    build_agent_card,
    get_agent_card_from_register_tx,
    register_agent,
)


_AGENT_ID_FILE = Path("agent_id.json")


def _load_existing_keypair(key_path: Path):
    """Gotcha #2: must reuse the keypair that signed the existing receipts."""
    if not key_path.exists():
        rprint(
            f"[red]agent key not found at {key_path}. Refusing to generate a new "
            "one — that would orphan the existing 17 receipts in the database. "
            "Restore the file or restart from milestone 1.[/red]"
        )
        sys.exit(2)
    sk = load_private_key_pem(key_path.read_bytes())
    pk_b64 = b64encode(public_key_bytes(sk)).decode("ascii")
    return sk, pk_b64


def _refuse_overwrite(existing: Path) -> None:
    if existing.exists():
        try:
            payload = json.loads(existing.read_text())
            tid = payload.get("erc8004_token_id")
        except Exception:  # noqa: BLE001
            tid = "?"
        rprint(
            f"[yellow]{existing} already exists (token_id={tid}). "
            "Refusing to overwrite — delete the file manually if you want "
            "to re-register.[/yellow]"
        )
        sys.exit(3)


def main() -> int:
    load_dotenv()

    dry_run = "--dry-run" in sys.argv[1:]

    rpc_url = os.environ["BASE_SEPOLIA_RPC_URL"]
    chain_id = int(os.environ["BASE_SEPOLIA_CHAIN_ID"])
    pk_hex = os.environ["DEPLOYER_PRIVATE_KEY"]
    registry = os.environ["ERC8004_IDENTITY_REGISTRY"]
    agent_id = os.environ.get("PROMPTSEAL_AGENT_ID", "hr-screener-v1")
    key_path = Path(os.environ.get("PROMPTSEAL_KEY_PATH", "./agent_key.pem"))

    if not dry_run:
        _refuse_overwrite(_AGENT_ID_FILE)

    sk, pk_b64 = _load_existing_keypair(key_path)
    del sk  # not needed downstream — only the pubkey is registered

    card = build_agent_card(public_key_b64=pk_b64, agent_id=agent_id)
    card_uri = agent_card_to_data_uri(card)

    rprint("[bold blue]→ Registering agent on ERC-8004[/bold blue]")
    rprint(f"  agent_id:    {agent_id}")
    rprint(f"  pubkey:      ed25519:{pk_b64}")
    rprint(f"  registry:    {registry}")
    rprint(f"  rpc:         {rpc_url}")
    rprint(f"  chain_id:    {chain_id}")
    rprint(f"  card_size:   {len(card_uri)} bytes")

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        rprint(f"[red]could not reach RPC at {rpc_url}[/red]")
        return 4
    if w3.eth.chain_id != chain_id:
        rprint(
            f"[red]chain id mismatch: RPC reports {w3.eth.chain_id}, env has {chain_id}[/red]"
        )
        return 5

    account = Account.from_key(pk_hex)
    sender_balance_wei = w3.eth.get_balance(account.address)
    rprint(
        f"  sender:      {account.address} "
        f"({w3.from_wei(sender_balance_wei, 'ether')} ETH)"
    )

    if dry_run:
        rprint("[dim]--dry-run: skipping live TX. Estimating gas only…[/dim]")
        from promptseal.erc8004 import IDENTITY_REGISTRY_ABI

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(registry), abi=IDENTITY_REGISTRY_ABI
        )
        try:
            est = contract.functions.register(card_uri).estimate_gas(
                {"from": account.address}
            )
        except Exception as exc:  # noqa: BLE001
            rprint(f"[red]estimate_gas reverted:[/red] {type(exc).__name__}: {exc}")
            return 6
        rprint(f"[green]✓ estimate_gas ok:[/green] {est} (+30% buffer = {est * 13 // 10})")
        rprint("[dim]Re-run without --dry-run to send the live register TX.[/dim]")
        return 0

    rprint("[dim]submitting register() TX, waiting for confirmation…[/dim]")
    try:
        result = register_agent(
            card_uri=card_uri,
            w3=w3,
            account=account,
            registry_address=registry,
            confirm_timeout=60,
        )
    except ERC8004RegistrationError as exc:
        rprint(f"[red]registration failed:[/red] {exc}")
        return 7
    except Exception as exc:  # noqa: BLE001
        rprint(f"[red]registration failed:[/red] {type(exc).__name__}: {exc}")
        return 8

    # Step 5 verification: recover the URI from the register TX's custom
    # event log and assert byte-equality with what we submitted.
    rprint("[dim]recovering URI from register-tx event log for byte-level verification…[/dim]")
    try:
        on_chain_uri = get_agent_card_from_register_tx(w3, result["tx_hash"])
    except Exception as exc:  # noqa: BLE001
        rprint(
            f"[yellow]register succeeded but read-back failed:[/yellow] "
            f"{type(exc).__name__}: {exc}"
        )
        on_chain_uri = None

    uri_match = on_chain_uri == card_uri

    payload = {
        "agent_id": agent_id,
        "erc8004_token_id": result["token_id"],
        "register_tx_hash": result["tx_hash"],
        "register_block_number": result["block_number"],
        "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_address": registry,
        "chain_id": chain_id,
        "agent_card_uri": card_uri,
        "agent_card_uri_verified": uri_match,
    }
    _AGENT_ID_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    rprint("[bold green]✓ registered[/bold green]")
    rprint(f"  token_id:    {result['token_id']}")
    rprint(f"  tx_hash:     {result['tx_hash']}")
    rprint(f"  block:       {result['block_number']}")
    rprint(f"  gas_used:    {result['gas_used']}")
    rprint(f"  basescan:    https://sepolia.basescan.org/tx/{result['tx_hash']}")
    rprint(
        f"  read-back:   "
        f"{'[green]✓ matches submitted URI[/green]' if uri_match else '[red]✗ mismatch[/red]'}"
    )
    rprint(f"  saved →      {_AGENT_ID_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
