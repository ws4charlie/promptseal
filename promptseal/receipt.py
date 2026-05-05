"""Receipt construction and verification.

A PromptSeal receipt is a self-describing, self-verifying JSON object. The
schema (BRIEF §5) is fixed: any change to the on-disk fields after signing
must invalidate the receipt.

Pipeline:
    body = {schema_version, agent_id, agent_erc8004_token_id, event_type,
            timestamp, parent_hash, paired_event_hash, payload_excerpt,
            public_key}
    canonical_bytes = canonical_json(body)        # sorted, compact, UTF-8
    event_hash      = "sha256:" + sha256(canonical_bytes).hexdigest()
    signature       = "ed25519:" + b64(Ed25519.sign(canonical_bytes))
    receipt         = body | {event_hash, signature}

Verification reverses: strip event_hash and signature, re-derive canonical
bytes, recompute hash, verify Ed25519 signature against same bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_json
from .crypto import public_key_bytes, sign, verify

SCHEMA_VERSION = "0.1"
HASH_PREFIX = "sha256:"
KEY_PREFIX = "ed25519:"

_AGENT_ID_JSON_PATH = Path("agent_id.json")

# Process-level cache for the ERC-8004 token id. Populated on first
# load_erc8004_token_id() call so subsequent calls don't re-read disk.
# Tests reset these directly (see tests/test_receipt.py fixture).
_ERC8004_TOKEN_ID_CACHE: int | None = None
_ERC8004_CACHE_LOADED: bool = False


def load_erc8004_token_id() -> int | None:
    """Read the agent's ERC-8004 token id from `agent_id.json`, once per process.

    `scripts/01_register_agent.py` writes this file after a successful
    on-chain registration. Reading it here lets every receipt carry the
    binding to the on-chain agent identity without plumbing the token id
    through every constructor.

    Missing file, malformed JSON, or missing/non-int `erc8004_token_id`
    field all return None — preserves backward compatibility with receipts
    written before milestone 5 (e.g. the 28 already in the SQLite DB).
    """
    global _ERC8004_TOKEN_ID_CACHE, _ERC8004_CACHE_LOADED
    if _ERC8004_CACHE_LOADED:
        return _ERC8004_TOKEN_ID_CACHE

    try:
        if not _AGENT_ID_JSON_PATH.exists():
            _ERC8004_CACHE_LOADED = True
            return None
        data = json.loads(_AGENT_ID_JSON_PATH.read_text())
        token_id = data.get("erc8004_token_id")
        if isinstance(token_id, int) and not isinstance(token_id, bool):
            _ERC8004_TOKEN_ID_CACHE = token_id
    except (json.JSONDecodeError, OSError):
        pass  # silently treat as missing — never raise from receipt construction

    _ERC8004_CACHE_LOADED = True
    return _ERC8004_TOKEN_ID_CACHE


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _encode_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _decode_prefixed(s: str, prefix: str) -> bytes:
    if not isinstance(s, str) or not s.startswith(prefix):
        raise ValueError(f"expected {prefix!r}-prefixed string, got {s!r}")
    return base64.b64decode(s[len(prefix):])


def _receipt_body(
    *,
    schema_version: str,
    agent_id: str,
    agent_erc8004_token_id: int | None,
    event_type: str,
    timestamp: str,
    parent_hash: str | None,
    paired_event_hash: str | None,
    payload_excerpt: dict[str, Any],
    public_key: str,
) -> dict[str, Any]:
    """Assemble the body dict that gets canonicalized → hashed → signed."""
    return {
        "agent_erc8004_token_id": agent_erc8004_token_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "paired_event_hash": paired_event_hash,
        "parent_hash": parent_hash,
        "payload_excerpt": payload_excerpt,
        "public_key": public_key,
        "schema_version": schema_version,
        "timestamp": timestamp,
    }


def build_signed_receipt(
    *,
    sk: Ed25519PrivateKey,
    agent_id: str,
    agent_erc8004_token_id: int | None = None,
    event_type: str,
    payload_excerpt: dict[str, Any],
    parent_hash: str | None,
    paired_event_hash: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a fully signed receipt dict (BRIEF §5).

    `agent_erc8004_token_id` semantics:
      - explicit int  → use that value (caller override, e.g. tests)
      - None / omitted → read once from `agent_id.json` via
        load_erc8004_token_id(); falls back to None if the file is absent
    """
    if agent_erc8004_token_id is None:
        agent_erc8004_token_id = load_erc8004_token_id()
    public_key_str = KEY_PREFIX + _encode_b64(public_key_bytes(sk))
    body = _receipt_body(
        schema_version=SCHEMA_VERSION,
        agent_id=agent_id,
        agent_erc8004_token_id=agent_erc8004_token_id,
        event_type=event_type,
        timestamp=timestamp or _now_iso(),
        parent_hash=parent_hash,
        paired_event_hash=paired_event_hash,
        payload_excerpt=payload_excerpt,
        public_key=public_key_str,
    )
    body_bytes = canonical_json(body)
    body["event_hash"] = HASH_PREFIX + hashlib.sha256(body_bytes).hexdigest()
    body["signature"] = KEY_PREFIX + _encode_b64(sign(sk, body_bytes))
    return body


def receipt_body_bytes(receipt: dict[str, Any]) -> bytes:
    """Return the canonical bytes that were hashed and signed.

    Strips the two derived fields (event_hash, signature) so we get the
    original body shape.
    """
    body = {k: v for k, v in receipt.items() if k not in ("event_hash", "signature")}
    return canonical_json(body)


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify event_hash matches body bytes and signature is valid for them.

    Returns False on any malformed input. Never raises.
    """
    try:
        body_bytes = receipt_body_bytes(receipt)
        expected_hash = HASH_PREFIX + hashlib.sha256(body_bytes).hexdigest()
        if receipt.get("event_hash") != expected_hash:
            return False
        sig_bytes = _decode_prefixed(receipt["signature"], KEY_PREFIX)
        pk_bytes = _decode_prefixed(receipt["public_key"], KEY_PREFIX)
        return verify(pk_bytes, body_bytes, sig_bytes)
    except (KeyError, ValueError, TypeError):
        return False
