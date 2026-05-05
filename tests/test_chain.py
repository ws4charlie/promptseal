"""Tests for promptseal.chain.

Spec (BRIEF §12 milestone 2 verification):
- Insert 10 receipts, query back, assert hash chain integrity.
- The tamper demo (BRIEF §13): direct SQL UPDATE on payload_excerpt must
  cause verify_chain → False.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from promptseal.chain import ChainIntegrityError, ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.receipt import build_signed_receipt


def _build(sk, parent_hash=None, event_type="llm_start", payload=None, timestamp=None):
    return build_signed_receipt(
        sk=sk,
        agent_id="hr-screener-v1",
        agent_erc8004_token_id=398,
        event_type=event_type,
        payload_excerpt=payload if payload is not None else {"foo": "bar"},
        parent_hash=parent_hash,
        timestamp=timestamp,
    )


def test_open_run_creates_row(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    rows = list(chain._conn.execute("SELECT * FROM runs"))
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"


def test_append_first_receipt_must_have_null_parent(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()
    r = _build(sk, parent_hash="sha256:" + "0" * 64)
    with pytest.raises(ChainIntegrityError):
        chain.append("run-1", r)


def test_append_subsequent_must_link_to_last(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()

    r1 = _build(sk, parent_hash=None, payload={"i": 1})
    chain.append("run-1", r1)

    r2 = _build(sk, parent_hash=r1["event_hash"], payload={"i": 2})
    chain.append("run-1", r2)

    r3_bad = _build(sk, parent_hash="sha256:" + "f" * 64, payload={"i": 3})
    with pytest.raises(ChainIntegrityError):
        chain.append("run-1", r3_bad)


def test_append_rejects_self_inconsistent_receipt(tmp_path: Path):
    """If signature has been tampered before storage, append refuses it."""
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()
    r = _build(sk, parent_hash=None)
    r["payload_excerpt"] = {"changed": "after_signing"}
    with pytest.raises(ChainIntegrityError):
        chain.append("run-1", r)


def test_insert_10_receipts_and_verify_chain(tmp_path: Path):
    """BRIEF §12 milestone-2 verification step."""
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()

    parent = None
    inserted = []
    for i in range(10):
        # Force unique timestamps so each event_hash differs even if payload were same.
        ts = f"2026-04-30T18:22:{i:02d}.000Z"
        r = _build(sk, parent_hash=parent, payload={"i": i}, timestamp=ts)
        chain.append("run-1", r)
        inserted.append(r)
        parent = r["event_hash"]

    receipts = chain.get_receipts("run-1")
    assert len(receipts) == 10
    for stored, original in zip(receipts, inserted):
        assert stored["event_hash"] == original["event_hash"]
        assert stored["signature"] == original["signature"]
        assert stored["parent_hash"] == original["parent_hash"]

    ok, err = chain.verify_chain("run-1")
    assert ok, err


def test_verify_chain_detects_payload_tamper(tmp_path: Path):
    """The 'wow' demo path — flip a byte via SQL UPDATE → verify_chain RED."""
    db = tmp_path / "x.sqlite"
    chain = ReceiptChain(db)
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()
    parent = None
    for i in range(5):
        r = _build(sk, parent_hash=parent, payload={"i": i},
                   timestamp=f"2026-04-30T18:22:{i:02d}.000Z")
        chain.append("run-1", r)
        parent = r["event_hash"]
    chain.close()

    # Tamper exactly as BRIEF §13's demo command does.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE receipts SET payload_excerpt = ? WHERE id = 3",
                 ('{"i":99}',))
    conn.commit()
    conn.close()

    chain2 = ReceiptChain(db)
    ok, err = chain2.verify_chain("run-1")
    assert ok is False
    assert err is not None


def test_verify_chain_detects_parent_hash_tamper(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    chain = ReceiptChain(db)
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()
    parent = None
    for i in range(3):
        r = _build(sk, parent_hash=parent, payload={"i": i},
                   timestamp=f"2026-04-30T18:22:{i:02d}.000Z")
        chain.append("run-1", r)
        parent = r["event_hash"]
    chain.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE receipts SET parent_hash = ? WHERE id = 2",
        ("sha256:" + "0" * 64,),
    )
    conn.commit()
    conn.close()

    chain2 = ReceiptChain(db)
    ok, err = chain2.verify_chain("run-1")
    assert ok is False
    assert err is not None


def test_event_hash_unique_constraint(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    chain.open_run("run-2", "hr-screener-v1")
    sk = generate_keypair()
    r = _build(sk, parent_hash=None)
    chain.append("run-1", r)

    with pytest.raises(sqlite3.IntegrityError):
        chain._conn.execute(
            """INSERT INTO receipts
            (run_id, schema_version, agent_id, agent_erc8004_token_id, event_type,
             timestamp, parent_hash, paired_event_hash, payload_excerpt,
             public_key, signature, event_hash)
            VALUES ('run-2', '0.1', 'a', 1, 't', '2026-01-01T00:00:00.000Z',
                    NULL, NULL, '{}', 'ed25519:x', 'ed25519:y', ?)""",
            (r["event_hash"],),
        )
        chain._conn.commit()


def test_latest_event_hash_returns_most_recent(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    assert chain.latest_event_hash("run-1") is None

    sk = generate_keypair()
    r1 = _build(sk, parent_hash=None, timestamp="2026-04-30T18:22:00.000Z")
    chain.append("run-1", r1)
    assert chain.latest_event_hash("run-1") == r1["event_hash"]

    r2 = _build(sk, parent_hash=r1["event_hash"], timestamp="2026-04-30T18:22:01.000Z")
    chain.append("run-1", r2)
    assert chain.latest_event_hash("run-1") == r2["event_hash"]


def test_runs_are_independent(tmp_path: Path):
    """Each run has its own chain — run-B's first receipt has parent=None
    even though run-A already has receipts."""
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-A", "hr-screener-v1")
    chain.open_run("run-B", "hr-screener-v1")
    sk = generate_keypair()

    rA = _build(sk, parent_hash=None, payload={"r": "A"},
                timestamp="2026-04-30T18:22:00.000Z")
    chain.append("run-A", rA)

    rB = _build(sk, parent_hash=None, payload={"r": "B"},
                timestamp="2026-04-30T18:22:01.000Z")
    chain.append("run-B", rB)

    okA, _ = chain.verify_chain("run-A")
    okB, _ = chain.verify_chain("run-B")
    assert okA and okB


def test_close_run_records_ended_at(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    chain.close_run("run-1")
    row = chain._conn.execute(
        "SELECT ended_at FROM runs WHERE run_id = 'run-1'"
    ).fetchone()
    assert row["ended_at"] is not None


def test_get_receipts_returns_in_insertion_order(tmp_path: Path):
    chain = ReceiptChain(tmp_path / "x.sqlite")
    chain.open_run("run-1", "hr-screener-v1")
    sk = generate_keypair()
    parent = None
    for i in range(4):
        r = _build(sk, parent_hash=parent, payload={"i": i},
                   timestamp=f"2026-04-30T18:22:{i:02d}.000Z")
        chain.append("run-1", r)
        parent = r["event_hash"]
    receipts = chain.get_receipts("run-1")
    assert [r["payload_excerpt"]["i"] for r in receipts] == [0, 1, 2, 3]
