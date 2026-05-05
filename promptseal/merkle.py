"""Merkle tree builder + inclusion proof generator/verifier.

Convention (Bitcoin-style, well-suited for browser-side verification):
- Leaves are 32-byte SHA-256 digests, exposed as "sha256:<hex>" strings at
  the API boundary.
- On each level, if there's an odd count, the last node is duplicated before
  pairing.
- Single-leaf tree: root = leaf (proof is empty).
- Inclusion proof: ordered list of {"sibling": "sha256:<hex>", "side": "L"|"R"}.
  "L" = sibling goes on the left when re-hashing; "R" = on the right.

The JS verifier walks the same proof format using sha256 from a CDN library —
no custom encoding to keep cross-language re-hash trivial.
"""
from __future__ import annotations

import hashlib
from typing import Any

HASH_PREFIX = "sha256:"


# -- internal byte-level helpers --------------------------------------------

def _h(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _strip(s: str) -> bytes:
    """'sha256:<hex>' or '<hex>' → 32 raw bytes."""
    h = s[len(HASH_PREFIX):] if s.startswith(HASH_PREFIX) else s
    out = bytes.fromhex(h)
    if len(out) != 32:
        raise ValueError(f"expected 32-byte sha256 digest, got {len(out)} bytes")
    return out


def _wrap(b: bytes) -> str:
    return HASH_PREFIX + b.hex()


def _level_up(level: list[bytes]) -> list[bytes]:
    """Reduce one level of the Merkle tree. Duplicates last node if odd."""
    items = list(level)
    if len(items) == 1:
        return items
    if len(items) % 2 == 1:
        items.append(items[-1])
    return [_h(items[i] + items[i + 1]) for i in range(0, len(items), 2)]


def _build_levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Bottom-up tree: levels[0] = leaves, levels[-1] = [root]."""
    if not leaves:
        raise ValueError("merkle tree requires at least 1 leaf")
    levels: list[list[bytes]] = [list(leaves)]
    while len(levels[-1]) > 1:
        levels.append(_level_up(levels[-1]))
    return levels


# -- public API -------------------------------------------------------------

def build_merkle(leaves_hex: list[str]) -> dict[str, Any]:
    """Build a Merkle tree from "sha256:<hex>" leaves.

    Returns {"root": "sha256:<hex>", "leaves": <input list>, "leaf_count": N}.
    """
    leaves = [_strip(s) for s in leaves_hex]
    levels = _build_levels(leaves)
    return {
        "root": _wrap(levels[-1][0]),
        "leaves": list(leaves_hex),
        "leaf_count": len(leaves),
    }


def inclusion_proof(leaves_hex: list[str], index: int) -> list[dict[str, str]]:
    """Generate an inclusion proof for `leaves_hex[index]`.

    Each step is {"sibling": "sha256:<hex>", "side": "L"|"R"}; "L" means the
    sibling is hashed on the left of the running value at that step.
    """
    if index < 0 or index >= len(leaves_hex):
        raise IndexError(f"index {index} out of range for {len(leaves_hex)} leaves")
    leaves = [_strip(s) for s in leaves_hex]
    if len(leaves) == 1:
        return []
    levels = _build_levels(leaves)
    proof: list[dict[str, str]] = []
    idx = index
    for level in levels[:-1]:  # skip root level
        items = list(level)
        if len(items) % 2 == 1:
            items.append(items[-1])  # mirror the duplicate-last rule used in build
        if idx % 2 == 0:
            sibling = items[idx + 1]
            side = "R"  # sibling goes on the right; current on the left
        else:
            sibling = items[idx - 1]
            side = "L"
        proof.append({"sibling": _wrap(sibling), "side": side})
        idx //= 2
    return proof


def verify_proof(leaf_hex: str, proof: list[dict[str, str]], root_hex: str) -> bool:
    """Verify that `leaf_hex` + `proof` reconstructs `root_hex`."""
    try:
        cur = _strip(leaf_hex)
        target = _strip(root_hex)
    except ValueError:
        return False
    for step in proof:
        try:
            sib = _strip(step["sibling"])
        except (KeyError, ValueError, TypeError):
            return False
        side = step.get("side")
        if side == "R":
            cur = _h(cur + sib)
        elif side == "L":
            cur = _h(sib + cur)
        else:
            return False
    return cur == target
