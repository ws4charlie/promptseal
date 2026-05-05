"""ERC-8004 agent identity registration on Base Sepolia.

Spec (BRIEF §10):
- Registry: 0x7177a6867296406881E20d6647232314736Dd09A on Base Sepolia.
- register(string agentCardURI) external returns (uint256 tokenId)
- The registry implements ERC-721; mint emits Transfer(from=0x0, to=msg.sender, tokenId).

Gotcha #1 (RESUMPTION-NOTES): EVM external return values are NOT in tx receipts.
The minted tokenId must be decoded from the Transfer event's third indexed topic.

Gotcha #3: register() with an inline `data:application/json;base64,...` agent
card uses 150-250K gas — far above intrinsic. We estimate, apply a 30% buffer,
and fail loud if estimation reverts (a revert here means semantic rejection,
not a gas issue).

This module deliberately keeps `register_agent()` pure: it accepts a built
`Web3` and an `eth_account.Account`-shaped object. `scripts/01_register_agent.py`
wires real RPC + key.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from eth_abi import decode as abi_decode
from web3 import Web3

from promptseal.canonical import canonical_json

logger = logging.getLogger(__name__)


class ERC8004RegistrationError(Exception):
    """Raised when ERC-8004 registration fails or returns an unexpected shape."""


# Minimal ABI: register() + the Transfer event for tokenId decoding. We do NOT
# include tokenURI(uint256) — this registry doesn't implement the ERC-721
# Metadata extension; the URI is read from the AGENT_CARD_EVENT log instead.
IDENTITY_REGISTRY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "register",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "agentCardURI", "type": "string"}],
        "outputs": [{"name": "tokenId", "type": "uint256"}],
    },
    {
        "type": "event",
        "name": "Transfer",
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"},
        ],
    },
]


# keccak256("Transfer(address,address,uint256)") — ERC-721 mint signature topic[0].
TRANSFER_EVENT_TOPIC: bytes = Web3.keccak(text="Transfer(address,address,uint256)")

# Custom event the registry emits alongside Transfer on register(); topic[0] is
# the event signature hash. The registry does NOT implement ERC-721 Metadata's
# tokenURI(uint256) — instead the agent card URI lives in this event's data
# field as an ABI-encoded string. We discovered this empirically when the live
# register TX (token 633) reverted on tokenURI() but the URI was byte-equal in
# this log's data. Topic captured directly from the on-chain receipt; the
# event's solidity signature is not specified in BRIEF §10.
AGENT_CARD_EVENT_TOPIC: bytes = bytes.fromhex(
    "ca52e62c367d81bb2e328eb795f7c7ba24afb478408a26c0e201d155c449bc4a"
)

_GAS_BUFFER_BPS = 13_000  # 1.30x → integer-math safe (val * 13000 // 10000)
_PRIORITY_FEE_GWEI = 1


# -- agent card construction -------------------------------------------------

def build_agent_card(
    public_key_b64: str,
    agent_id: str,
    *,
    version: str = "0.1",
) -> dict[str, Any]:
    """Build the BRIEF §10 agent card JSON for *agent_id*.

    *public_key_b64* must be the base64 of the raw 32-byte Ed25519 public key
    (BRIEF §13: same bytes the JS verifier consumes via @noble/ed25519).
    """
    return {
        "name": agent_id,
        "description": (
            "PromptSeal demo: hiring agent screening senior full-stack engineers"
        ),
        "endpoints": {"http": "https://example.com/agent"},
        "publicKey": f"ed25519:{public_key_b64}",
        "version": version,
    }


def agent_card_to_data_uri(card: dict[str, Any]) -> str:
    """Encode *card* as a `data:application/json;base64,...` URI.

    Canonicalizes (sorted keys, compact, UTF-8) before base64 so the bytes
    a third party retrieves via `tokenURI(...)` are reproducible from the
    same dict — regardless of dict insertion order.
    """
    payload = canonical_json(card)
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:application/json;base64,{encoded}"


# -- registration ------------------------------------------------------------

def _decode_token_id_from_logs(logs: list[Any]) -> int:
    """Find the Transfer log and pull tokenId from its 3rd indexed topic."""
    for log in logs:
        topics = list(getattr(log, "topics", []) or [])
        if not topics:
            continue
        topic0 = topics[0]
        topic0_bytes = bytes(topic0) if not isinstance(topic0, bytes) else topic0
        if topic0_bytes != TRANSFER_EVENT_TOPIC:
            continue
        if len(topics) < 4:
            raise ERC8004RegistrationError(
                "Transfer event present but missing indexed tokenId topic — "
                "wrong contract or non-ERC-721 event?"
            )
        token_topic = topics[3]
        token_bytes = (
            bytes(token_topic) if not isinstance(token_topic, bytes) else token_topic
        )
        return int.from_bytes(token_bytes, "big")
    raise ERC8004RegistrationError(
        "no Transfer event in tx receipt — register() did not mint an ERC-721 "
        "token (RESUMPTION-NOTES gotcha #1)"
    )


def register_agent(
    *,
    card_uri: str,
    w3: Web3,
    account: Any,
    registry_address: str,
    confirm_timeout: int = 180,
) -> dict[str, Any]:
    """Call `register(card_uri)` on the ERC-8004 registry; return the minted tokenId.

    Returns {"token_id": int, "tx_hash": "0x...", "block_number": int, "gas_used": int}.
    Raises ERC8004RegistrationError on revert, missing Transfer log, or estimate
    failure (we never auto-pad past a revert — gotcha #3).
    """
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address)
        if hasattr(Web3, "to_checksum_address")
        else registry_address,
        abi=IDENTITY_REGISTRY_ABI,
    )
    register_fn = contract.functions.register(card_uri)

    sender = account.address

    try:
        gas_estimate = register_fn.estimate_gas({"from": sender})
    except Exception as exc:  # noqa: BLE001 — RPC + revert errors come in many shapes
        raise ERC8004RegistrationError(
            f"estimate_gas reverted: {exc}. The registry rejected the call "
            f"(likely already registered, malformed URI, or wrong chain)."
        ) from exc

    gas_with_buffer = gas_estimate * _GAS_BUFFER_BPS // 10_000

    latest = w3.eth.get_block("latest")
    base_fee = (
        latest.get("baseFeePerGas", 0) if isinstance(latest, dict) else getattr(latest, "baseFeePerGas", 0)
    ) or 0
    priority_fee = w3.to_wei(_PRIORITY_FEE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority_fee
    chain_id = w3.eth.chain_id

    tx = register_fn.build_transaction(
        {
            "from": sender,
            "nonce": w3.eth.get_transaction_count(sender),
            "chainId": chain_id,
            "gas": gas_with_buffer,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
            "type": 2,
        }
    )
    tx["gas"] = gas_with_buffer  # build_transaction may overwrite; pin it

    signed = account.sign_transaction(tx)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(
        tx_hash_bytes, timeout=confirm_timeout
    )

    if int(getattr(receipt, "status", 1)) != 1:
        raise ERC8004RegistrationError(
            f"register() transaction reverted on-chain: tx_hash=0x{tx_hash_bytes.hex()}"
        )

    token_id = _decode_token_id_from_logs(list(receipt.logs))

    return {
        "token_id": token_id,
        "tx_hash": "0x" + tx_hash_bytes.hex(),
        "block_number": int(receipt.blockNumber),
        "gas_used": int(getattr(receipt, "gasUsed", 0)),
    }


# -- read-back ---------------------------------------------------------------

def get_agent_card_from_register_tx(
    w3: Web3,
    tx_hash: str,
) -> str:
    """Recover the submitted agent card URI from a register TX's event logs.

    The registry doesn't implement ERC-721 Metadata's `tokenURI(uint256)`;
    instead it emits a custom event (topic[0] = AGENT_CARD_EVENT_TOPIC) whose
    `data` field is the ABI-encoded URI string. We read that here. tokenId is
    not required — the URI is in the receipt regardless.

    Raises ERC8004RegistrationError if no matching log is found in the receipt.
    """
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    for log in getattr(receipt, "logs", []) or []:
        topics = list(getattr(log, "topics", []) or [])
        if not topics:
            continue
        topic0 = topics[0]
        topic0_bytes = bytes(topic0) if not isinstance(topic0, bytes) else topic0
        if topic0_bytes != AGENT_CARD_EVENT_TOPIC:
            continue
        data = log.data
        data_bytes = bytes(data) if not isinstance(data, bytes) else data
        (uri,) = abi_decode(["string"], data_bytes)
        return uri
    raise ERC8004RegistrationError(
        f"no agent-card event (topic 0x{AGENT_CARD_EVENT_TOPIC.hex()}) in "
        f"register TX {tx_hash} — wrong tx, wrong registry, or contract changed"
    )
