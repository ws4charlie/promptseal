"""Anchor a Merkle root to Base Sepolia via a self-send transaction.

The simplest publishable evidence: a transaction whose `data` field is the
32-byte Merkle root and whose `to == from`. Anyone with the tx_hash can read
back the data via a Base Sepolia explorer / RPC and compare against the root
the verifier reconstructs from a receipt's inclusion proof.

EIP-1559 fees. Single confirmation wait. We do NOT use a contract — `to` is
just the deployer's own EOA, so no contract code or ABI is needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from eth_account import Account
from web3 import Web3

logger = logging.getLogger(__name__)

# Base TX (21000) + max 32 bytes * 16 gas/byte for non-zero data ≈ 21512.
# Add headroom for any zero-byte savings miscount; below an actual block-fill.
_GAS_FALLBACK = 30_000
_PRIORITY_FEE_GWEI = 1


@dataclass(frozen=True)
class AnchorResult:
    tx_hash: str            # "0x..." (66-char hex)
    block_number: int
    merkle_root: str        # "sha256:<hex>" (input echoed back for caller convenience)
    chain_id: int
    sender: str             # checksum address of from/to (self-send)
    gas_used: int


def _root_to_bytes(root_hex: str) -> bytes:
    """Strip 'sha256:' prefix or '0x' prefix; assert exactly 32 bytes."""
    h = root_hex
    for prefix in ("sha256:", "0x"):
        if h.startswith(prefix):
            h = h[len(prefix):]
    out = bytes.fromhex(h)
    if len(out) != 32:
        raise ValueError(f"merkle root must be 32 bytes, got {len(out)}")
    return out


def anchor_root(
    *,
    root_hex: str,
    rpc_url: str,
    chain_id: int,
    private_key: str,
    confirm_timeout: int = 120,
) -> AnchorResult:
    """Send a self-TX with `data = 0x + root_hex` and wait for 1 confirmation.

    Raises if RPC unreachable, signature fails, or confirmation times out.
    """
    data = _root_to_bytes(root_hex)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"could not reach RPC at {rpc_url}")

    if w3.eth.chain_id != chain_id:
        raise ValueError(
            f"chain id mismatch: RPC reports {w3.eth.chain_id}, env has {chain_id}"
        )

    acct = Account.from_key(private_key)
    sender = acct.address

    # Estimate gas; on rate-limit / RPC quirks fall back to a hardcoded ceiling
    # comfortably above the intrinsic+data minimum (BRIEF §13).
    try:
        gas = w3.eth.estimate_gas(
            {"from": sender, "to": sender, "value": 0, "data": data}
        )
        gas = int(gas * 12 // 10)  # 20% headroom
    except Exception as exc:  # noqa: BLE001 — RPC errors come in many shapes
        logger.warning("estimate_gas failed (%s); falling back to %d", exc, _GAS_FALLBACK)
        gas = _GAS_FALLBACK

    # EIP-1559 fees (Base Sepolia is sub-gwei; we set a comfortable ceiling).
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", 0) or 0
    priority_fee = w3.to_wei(_PRIORITY_FEE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority_fee

    tx = {
        "type": 2,
        "chainId": chain_id,
        "from": sender,
        "to": sender,
        "value": 0,
        "nonce": w3.eth.get_transaction_count(sender),
        "data": data,
        "gas": gas,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority_fee,
    }

    signed = acct.sign_transaction(tx)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=confirm_timeout)
    if receipt.status != 1:
        raise RuntimeError(
            f"transaction reverted on-chain: tx_hash=0x{tx_hash_bytes.hex()}"
        )

    return AnchorResult(
        tx_hash="0x" + tx_hash_bytes.hex(),
        block_number=int(receipt.blockNumber),
        merkle_root=root_hex,
        chain_id=chain_id,
        sender=sender,
        gas_used=int(receipt.gasUsed),
    )
