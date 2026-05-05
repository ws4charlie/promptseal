"""Tests for promptseal.merkle.

Convention (Bitcoin-style):
- leaves are SHA-256 digests, supplied as "sha256:<hex>" strings
- on each level, if odd count → duplicate the last node before pairing
- single leaf → root = leaf (empty proof)
- inclusion proof = list of {sibling, side} where side ∈ {"L","R"} tells the
  verifier whether the sibling goes on the left or right when re-hashing
"""
from __future__ import annotations

import hashlib

import pytest

from promptseal.merkle import (
    build_merkle,
    inclusion_proof,
    verify_proof,
)

HASH_PREFIX = "sha256:"


def _leaf(payload: bytes) -> str:
    """Make a 'sha256:<hex>' leaf string from arbitrary bytes."""
    return HASH_PREFIX + hashlib.sha256(payload).hexdigest()


def _make_leaves(n: int) -> list[str]:
    return [_leaf(f"item-{i}".encode()) for i in range(n)]


# ---------- build_merkle: structural cases ---------------------------------

def test_empty_leaves_raises():
    with pytest.raises(ValueError):
        build_merkle([])


def test_single_leaf_root_equals_leaf():
    leaves = _make_leaves(1)
    out = build_merkle(leaves)
    assert out["root"] == leaves[0]


def test_two_leaves():
    leaves = _make_leaves(2)
    out = build_merkle(leaves)
    expected = HASH_PREFIX + hashlib.sha256(
        bytes.fromhex(leaves[0][7:]) + bytes.fromhex(leaves[1][7:])
    ).hexdigest()
    assert out["root"] == expected


def test_four_leaves_balanced():
    leaves = _make_leaves(4)
    out = build_merkle(leaves)
    L01 = hashlib.sha256(bytes.fromhex(leaves[0][7:]) + bytes.fromhex(leaves[1][7:])).digest()
    L23 = hashlib.sha256(bytes.fromhex(leaves[2][7:]) + bytes.fromhex(leaves[3][7:])).digest()
    expected = HASH_PREFIX + hashlib.sha256(L01 + L23).hexdigest()
    assert out["root"] == expected


def test_three_leaves_duplicates_last():
    leaves = _make_leaves(3)
    out = build_merkle(leaves)
    L01 = hashlib.sha256(bytes.fromhex(leaves[0][7:]) + bytes.fromhex(leaves[1][7:])).digest()
    # Last node duplicated at level 0
    L22 = hashlib.sha256(bytes.fromhex(leaves[2][7:]) + bytes.fromhex(leaves[2][7:])).digest()
    expected = HASH_PREFIX + hashlib.sha256(L01 + L22).hexdigest()
    assert out["root"] == expected


def test_five_leaves_duplicates_propagate():
    """5 leaves: level 0 has 5 (odd → 6), level 1 has 3 (odd → 4), level 2 has 2, level 3 = root."""
    leaves = _make_leaves(5)
    out = build_merkle(leaves)
    # Hand-compute the expected root
    b = [bytes.fromhex(leaves[i][7:]) for i in range(5)]
    L01 = hashlib.sha256(b[0] + b[1]).digest()
    L23 = hashlib.sha256(b[2] + b[3]).digest()
    L44 = hashlib.sha256(b[4] + b[4]).digest()         # duplicate at level 0
    M0 = hashlib.sha256(L01 + L23).digest()
    M1 = hashlib.sha256(L44 + L44).digest()            # duplicate at level 1
    expected = HASH_PREFIX + hashlib.sha256(M0 + M1).hexdigest()
    assert out["root"] == expected


def test_root_is_deterministic():
    leaves = _make_leaves(7)
    a = build_merkle(leaves)["root"]
    b = build_merkle(leaves)["root"]
    assert a == b


# ---------- inclusion proofs -----------------------------------------------

def test_single_leaf_proof_is_empty():
    leaves = _make_leaves(1)
    proof = inclusion_proof(leaves, 0)
    assert proof == []
    assert verify_proof(leaves[0], proof, build_merkle(leaves)["root"]) is True


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9, 16, 17])
def test_every_leaf_verifies_for_n(n: int):
    leaves = _make_leaves(n)
    root = build_merkle(leaves)["root"]
    for i in range(n):
        proof = inclusion_proof(leaves, i)
        assert verify_proof(leaves[i], proof, root) is True, f"n={n} i={i} failed"


def test_seventeen_leaf_tree_matches_milestone_3_run():
    """Milestone 3 produced a 17-receipt run. Mirror that count and verify
    inclusion proofs round-trip for every leaf."""
    leaves = _make_leaves(17)
    root = build_merkle(leaves)["root"]
    for i, leaf in enumerate(leaves):
        proof = inclusion_proof(leaves, i)
        assert verify_proof(leaf, proof, root), f"leaf {i} did not verify"
        # Sanity: 17 leaves require 5 levels of proof (ceil(log2(17)) = 5)
        assert len(proof) == 5


def test_proof_for_first_and_last_leaf_have_correct_step_count():
    leaves = _make_leaves(8)
    p_first = inclusion_proof(leaves, 0)
    p_last = inclusion_proof(leaves, 7)
    assert len(p_first) == 3
    assert len(p_last) == 3


def test_proof_has_sibling_and_side_keys():
    leaves = _make_leaves(4)
    proof = inclusion_proof(leaves, 0)
    for step in proof:
        assert set(step.keys()) == {"sibling", "side"}
        assert step["side"] in ("L", "R")
        assert step["sibling"].startswith(HASH_PREFIX)


def test_index_out_of_range_raises():
    leaves = _make_leaves(4)
    with pytest.raises(IndexError):
        inclusion_proof(leaves, 4)
    with pytest.raises(IndexError):
        inclusion_proof(leaves, -1)


# ---------- tamper detection -----------------------------------------------

def test_verify_rejects_tampered_leaf():
    leaves = _make_leaves(8)
    root = build_merkle(leaves)["root"]
    proof = inclusion_proof(leaves, 3)
    fake_leaf = _leaf(b"i-was-never-here")
    assert verify_proof(fake_leaf, proof, root) is False


def test_verify_rejects_wrong_root():
    leaves = _make_leaves(8)
    proof = inclusion_proof(leaves, 3)
    bogus_root = _leaf(b"not-the-root")
    assert verify_proof(leaves[3], proof, bogus_root) is False


def test_verify_rejects_modified_proof_sibling():
    leaves = _make_leaves(8)
    root = build_merkle(leaves)["root"]
    proof = inclusion_proof(leaves, 3)
    proof[0]["sibling"] = _leaf(b"forged-sibling")
    assert verify_proof(leaves[3], proof, root) is False


def test_verify_rejects_modified_proof_side():
    leaves = _make_leaves(8)
    root = build_merkle(leaves)["root"]
    proof = inclusion_proof(leaves, 3)
    proof[0]["side"] = "L" if proof[0]["side"] == "R" else "R"
    assert verify_proof(leaves[3], proof, root) is False


def test_verify_rejects_truncated_proof():
    leaves = _make_leaves(8)
    root = build_merkle(leaves)["root"]
    proof = inclusion_proof(leaves, 3)
    short = proof[:-1]
    assert verify_proof(leaves[3], short, root) is False
