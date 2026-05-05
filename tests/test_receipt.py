"""Tests for promptseal.receipt.

Spec (BRIEF §5):
- Canonical receipt body has 9 keys (sorted alphabetically by canonical_json).
- event_hash = "sha256:" + sha256(canonical_json(body))  where body excludes
  event_hash and signature themselves.
- signature signs the SAME canonical bytes that produced event_hash.
- Tampering ANY field after the fact must break verification.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re

import pytest

import promptseal.receipt as receipt_module
from promptseal.crypto import generate_keypair
from promptseal.receipt import (
    HASH_PREFIX,
    KEY_PREFIX,
    SCHEMA_VERSION,
    build_signed_receipt,
    load_erc8004_token_id,
    receipt_body_bytes,
    verify_receipt,
)


@pytest.fixture(autouse=True)
def _reset_erc8004_cache():
    """Reset the module-level cache before AND after each test.

    Critical: the repo root contains a real agent_id.json (token_id 633) from
    the live milestone-5 register TX. Without isolation, the first test to
    fire load_erc8004_token_id() would prime the cache with 633 and bleed
    into every subsequent test in this process.
    """
    receipt_module._ERC8004_TOKEN_ID_CACHE = None
    receipt_module._ERC8004_CACHE_LOADED = False
    yield
    receipt_module._ERC8004_TOKEN_ID_CACHE = None
    receipt_module._ERC8004_CACHE_LOADED = False


def _make(parent_hash=None, paired=None, **overrides):
    sk = overrides.pop("sk", None) or generate_keypair()
    base = dict(
        sk=sk,
        agent_id="hr-screener-v1",
        agent_erc8004_token_id=398,
        event_type="llm_start",
        payload_excerpt={"model": "claude-haiku-4-5-20251001", "temperature": 0.0},
        parent_hash=parent_hash,
        paired_event_hash=paired,
    )
    base.update(overrides)
    return sk, build_signed_receipt(**base)


def test_signed_receipt_has_full_schema():
    _, r = _make()
    assert set(r.keys()) == {
        "agent_erc8004_token_id",
        "agent_id",
        "event_hash",
        "event_type",
        "paired_event_hash",
        "parent_hash",
        "payload_excerpt",
        "public_key",
        "schema_version",
        "signature",
        "timestamp",
    }


def test_event_hash_format():
    _, r = _make()
    assert r["event_hash"].startswith(HASH_PREFIX)
    hex_part = r["event_hash"].removeprefix(HASH_PREFIX)
    assert len(hex_part) == 64
    int(hex_part, 16)


def test_signature_format():
    _, r = _make()
    assert r["signature"].startswith(KEY_PREFIX)


def test_public_key_format():
    _, r = _make()
    assert r["public_key"].startswith(KEY_PREFIX)


def test_event_hash_is_sha256_of_canonical_body_minus_signature_and_hash():
    _, r = _make()
    body_bytes = receipt_body_bytes(r)
    expected = HASH_PREFIX + hashlib.sha256(body_bytes).hexdigest()
    assert r["event_hash"] == expected


def test_receipt_body_bytes_excludes_signature_and_event_hash():
    _, r = _make()
    body_bytes = receipt_body_bytes(r)
    assert b'"event_hash"' not in body_bytes
    assert b'"signature"' not in body_bytes


def test_verify_round_trip():
    _, r = _make()
    assert verify_receipt(r) is True


def test_verify_rejects_tampered_payload():
    _, r = _make()
    r["payload_excerpt"]["model"] = "claude-evil"
    assert verify_receipt(r) is False


def test_verify_rejects_tampered_event_hash():
    _, r = _make()
    r["event_hash"] = HASH_PREFIX + "0" * 64
    assert verify_receipt(r) is False


def test_verify_rejects_tampered_signature():
    _, r = _make()
    sig_b64 = r["signature"].removeprefix(KEY_PREFIX)
    sig_bytes = bytearray(base64.b64decode(sig_b64))
    sig_bytes[0] ^= 0xFF
    r["signature"] = KEY_PREFIX + base64.b64encode(bytes(sig_bytes)).decode()
    assert verify_receipt(r) is False


def test_verify_rejects_tampered_timestamp():
    _, r = _make()
    r["timestamp"] = "1970-01-01T00:00:00.000Z"
    assert verify_receipt(r) is False


def test_verify_rejects_tampered_parent_hash():
    _, r = _make(parent_hash="sha256:" + "a" * 64)
    r["parent_hash"] = "sha256:" + "b" * 64
    assert verify_receipt(r) is False


def test_verify_rejects_swapped_public_key():
    _, r = _make()
    other_sk = generate_keypair()
    from promptseal.crypto import public_key_bytes
    other_pk = base64.b64encode(public_key_bytes(other_sk)).decode()
    r["public_key"] = KEY_PREFIX + other_pk
    assert verify_receipt(r) is False


def test_timestamp_is_iso8601_utc_z():
    _, r = _make()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", r["timestamp"])


def test_schema_version_constant():
    _, r = _make()
    assert r["schema_version"] == SCHEMA_VERSION
    assert SCHEMA_VERSION == "0.1"


def test_first_event_can_have_null_parent():
    _, r = _make(parent_hash=None)
    assert r["parent_hash"] is None
    assert verify_receipt(r) is True


def test_paired_event_hash_propagates():
    _, r = _make(paired="sha256:" + "c" * 64)
    assert r["paired_event_hash"] == "sha256:" + "c" * 64
    assert verify_receipt(r) is True


def test_explicit_timestamp_is_used():
    sk = generate_keypair()
    r = build_signed_receipt(
        sk=sk,
        agent_id="x",
        agent_erc8004_token_id=1,
        event_type="llm_start",
        payload_excerpt={},
        parent_hash=None,
        timestamp="2026-04-30T18:22:01.123Z",
    )
    assert r["timestamp"] == "2026-04-30T18:22:01.123Z"
    assert verify_receipt(r) is True


def test_two_receipts_with_same_payload_have_different_hashes_via_timestamp():
    """Same payload + same key → timestamps differ (or do they? at least signature deterministic).

    Ed25519 signatures are deterministic, so identical input → identical signature.
    What changes between two receipts at slightly different times is the timestamp,
    which feeds into the canonical body and therefore the event_hash.
    """
    sk = generate_keypair()
    r1 = build_signed_receipt(
        sk=sk, agent_id="a", agent_erc8004_token_id=1, event_type="t",
        payload_excerpt={}, parent_hash=None,
        timestamp="2026-01-01T00:00:00.000Z",
    )
    r2 = build_signed_receipt(
        sk=sk, agent_id="a", agent_erc8004_token_id=1, event_type="t",
        payload_excerpt={}, parent_hash=None,
        timestamp="2026-01-01T00:00:00.001Z",
    )
    assert r1["event_hash"] != r2["event_hash"]


# -- load_erc8004_token_id integration (milestone 5 step 3) ------------------

def _build_no_token(sk):
    """Construct a receipt without passing agent_erc8004_token_id — exercises
    the helper path."""
    return build_signed_receipt(
        sk=sk,
        agent_id="hr-screener-v1",
        event_type="llm_start",
        payload_excerpt={"model": "x"},
        parent_hash=None,
    )


def test_token_id_populated_from_agent_id_json(tmp_path, monkeypatch):
    """File present + valid → receipt's agent_erc8004_token_id is set."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent_id.json").write_text(
        json.dumps({"agent_id": "hr-screener-v1", "erc8004_token_id": 633})
    )
    sk = generate_keypair()
    r = _build_no_token(sk)
    assert r["agent_erc8004_token_id"] == 633
    assert verify_receipt(r) is True


def test_token_id_none_when_agent_id_json_missing(tmp_path, monkeypatch):
    """File absent → field stays None; no exception."""
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "agent_id.json").exists()
    sk = generate_keypair()
    r = _build_no_token(sk)
    assert r["agent_erc8004_token_id"] is None
    assert verify_receipt(r) is True


def test_token_id_none_when_agent_id_json_corrupt(tmp_path, monkeypatch):
    """Malformed JSON in file → field stays None; helper does NOT raise."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent_id.json").write_text("this is not { valid json")
    sk = generate_keypair()
    r = _build_no_token(sk)
    assert r["agent_erc8004_token_id"] is None
    assert verify_receipt(r) is True


def test_load_erc8004_token_id_caches_after_first_call(tmp_path, monkeypatch):
    """Second call must not re-read the file — proves the cache works.

    Strategy: prime the cache by calling once with the file present, then
    DELETE the file and call again. If caching works, the second call still
    returns 633. Without caching it would return None.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent_id.json").write_text(
        json.dumps({"erc8004_token_id": 633})
    )
    assert load_erc8004_token_id() == 633

    (tmp_path / "agent_id.json").unlink()
    assert not (tmp_path / "agent_id.json").exists()
    assert load_erc8004_token_id() == 633  # served from cache


def test_explicit_token_id_overrides_helper(tmp_path, monkeypatch):
    """Caller-supplied int wins over agent_id.json — preserves test fixtures
    (and the handler) that still pass explicit values."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent_id.json").write_text(
        json.dumps({"erc8004_token_id": 633})
    )
    sk = generate_keypair()
    r = build_signed_receipt(
        sk=sk,
        agent_id="x",
        agent_erc8004_token_id=42,
        event_type="t",
        payload_excerpt={},
        parent_hash=None,
    )
    assert r["agent_erc8004_token_id"] == 42
