"""Tests for promptseal.summarizer + the run_summaries Merkle-leaf helper.

LLM is mocked everywhere — no real API calls. The mock substitutes
`agent.llm.make_chat_llm` with a fake that returns a fixed string per test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from promptseal import summarizer
from promptseal.anchor import build_run_leaves
from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.merkle import build_merkle
from promptseal.receipt import build_signed_receipt
from promptseal.run_summary import (
    insert_run_summary,
    load_run_summary,
    update_summary_merkle_flag,
)


# --- helpers ---------------------------------------------------------------


class _FakeLLM:
    """Stand-in for whatever make_chat_llm returns. .invoke() yields a
    fixed message with `.content` matching what each test wants."""

    def __init__(self, response_text: str) -> None:
        self._text = response_text

    def invoke(self, _messages: Any) -> Any:  # langchain accepts list[dict] / list[Message]
        class _Msg:
            content = self._text
        return _Msg()


def _patch_llm(monkeypatch: pytest.MonkeyPatch, response_text: str) -> None:
    """Make summarizer.make_chat_llm(...) return a FakeLLM that replies with
    `response_text` regardless of input."""
    monkeypatch.setattr(
        summarizer,
        "make_chat_llm",
        lambda *, model, temperature: _FakeLLM(response_text),  # noqa: ARG005
    )


def _seed_run(
    db_path: Path,
    *,
    run_id: str = "run-test",
    n_receipts: int = 4,
    token_id: int | None = 633,
    final_decision_candidate: str = "res_002",
) -> tuple[str, list[dict[str, Any]]]:
    """Build a run with N+1 signed receipts: N standard llm_start/llm_end pairs
    plus one final_decision carrying `candidate_id: <res_NNN>`. Mirrors the
    real demo run's PII surface."""
    chain = ReceiptChain(db_path)
    chain.open_run(run_id, "hr-screener-v1")
    sk = generate_keypair()
    parent: str | None = None
    receipts: list[dict[str, Any]] = []

    for i in range(n_receipts):
        r = build_signed_receipt(
            sk=sk,
            agent_id="hr-screener-v1",
            agent_erc8004_token_id=token_id,
            event_type="llm_start" if i % 2 == 0 else "llm_end",
            payload_excerpt={"i": i, "model": "gpt-4o-mini"},
            parent_hash=parent,
        )
        chain.append(run_id, r)
        receipts.append(r)
        parent = r["event_hash"]

    fd = build_signed_receipt(
        sk=sk,
        agent_id="hr-screener-v1",
        agent_erc8004_token_id=token_id,
        event_type="final_decision",
        payload_excerpt={
            "candidate_id": final_decision_candidate,
            "decision": "reject",
            "reasoning_hash": "sha256:" + "0" * 64,
        },
        parent_hash=parent,
    )
    chain.append(run_id, fd)
    receipts.append(fd)
    chain.close()
    return run_id, receipts


# --- 1. summarize_run stores via insert_run_summary ------------------------


def test_summarize_run_calls_llm_and_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)
    _patch_llm(
        monkeypatch,
        "Agent screened the candidate, called resume parsing and scoring "
        "tools, then issued a hire/reject decision.",
    )

    stored = summarizer.summarize_run(run_id, db_path=db)

    assert stored["run_id"] == run_id
    assert stored["summary_text"].startswith("Agent screened")
    assert stored["llm_provider"] == "openai"
    assert stored["llm_model"] == "gpt-4o-mini"
    assert stored["included_in_merkle"] is False  # D2 default
    # Round-trips via load_run_summary.
    assert load_run_summary(run_id, db_path=db) == stored


# --- 2. summary_hash deterministic per text --------------------------------


def test_summary_hash_deterministic_per_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Hash field is sha256(summary_text). Same text → same hash."""
    monkeypatch.chdir(tmp_path)
    db_a = tmp_path / "a.sqlite"
    db_b = tmp_path / "b.sqlite"
    run_id_a, _ = _seed_run(db_a, run_id="run-a")
    run_id_b, _ = _seed_run(db_b, run_id="run-b")

    text = "Agent ran 4 LLM calls + 1 decision."
    _patch_llm(monkeypatch, text)

    a = summarizer.summarize_run(run_id_a, db_path=db_a)
    b = summarizer.summarize_run(run_id_b, db_path=db_b)

    assert a["summary_text"] == b["summary_text"]
    assert a["summary_hash"] == b["summary_hash"]
    assert a["summary_hash"].startswith("sha256:")


# --- 3. PII filter rejects res_NNN leak in summary -------------------------


def test_summary_no_pii_leak_clean_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A well-behaved LLM that doesn't include candidate_id stores normally."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)
    _patch_llm(
        monkeypatch,
        "The agent screened the candidate via resume parsing, scored them, "
        "and chose to reject. Bob Martinez was not in the response.",
    )

    stored = summarizer.summarize_run(run_id, db_path=db)
    # Nothing matching res_\d{3} in the text.
    assert "res_001" not in stored["summary_text"]
    assert "res_002" not in stored["summary_text"]
    # Smoke: name pattern (PLAN R6 calls out names too) — the prompt asks
    # the LLM not to leak ids; a clean-output mock obviously won't have them.


def test_summary_pii_filter_raises_on_candidate_id_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """If the LLM echoes res_002 from the final_decision payload, refuse to
    store and raise PromptSealPiiError. Real defense, not just a test smoke."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db, final_decision_candidate="res_002")
    _patch_llm(
        monkeypatch,
        "Agent screened res_002 and decided to reject.",
    )

    with pytest.raises(summarizer.PromptSealPiiError, match="res_002"):
        summarizer.summarize_run(run_id, db_path=db)
    # Importantly: nothing was stored.
    assert load_run_summary(run_id, db_path=db) is None


# --- 4. anchor includes summary_hash when flag is True ---------------------


def test_anchor_includes_summary_when_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, receipts = _seed_run(db)

    # Baseline tree (no summary).
    baseline_leaves = build_run_leaves(run_id, receipts, db_path=db)
    baseline_root = build_merkle(baseline_leaves)["root"]

    # Insert summary, opt into Merkle.
    insert_run_summary(
        run_id=run_id,
        summary_text="agent did stuff",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        included_in_merkle=True,
        db_path=db,
    )

    # New tree should include summary_hash as an extra leaf.
    new_leaves = build_run_leaves(run_id, receipts, db_path=db)
    new_root = build_merkle(new_leaves)["root"]

    summary = load_run_summary(run_id, db_path=db)
    assert summary is not None
    assert new_leaves == [*baseline_leaves, summary["summary_hash"]]
    assert new_root != baseline_root  # adding a leaf must change the root


# --- 5. anchor excludes summary_hash by default ----------------------------


def test_anchor_excludes_summary_when_flag_not_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """D2: included_in_merkle defaults to False. Inserting a summary with the
    flag off must NOT change the leaf set."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, receipts = _seed_run(db)

    baseline = build_run_leaves(run_id, receipts, db_path=db)

    insert_run_summary(
        run_id=run_id,
        summary_text="agent did stuff",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        included_in_merkle=False,  # D2 default
        db_path=db,
    )

    after = build_run_leaves(run_id, receipts, db_path=db)
    assert after == baseline

    # Flipping the flag later WOULD include it (smoke).
    update_summary_merkle_flag(run_id, True, db_path=db)
    after_flip = build_run_leaves(run_id, receipts, db_path=db)
    assert len(after_flip) == len(baseline) + 1


# --- 6. existing-run hash chain unaffected ---------------------------------


def test_existing_run_chain_verify_unaffected_by_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """chain.verify_chain operates on receipts only — adding a run_summaries
    row (with or without the Merkle flag) must not change its result. This
    is the backward-compat guarantee for run-3e732839c923 / run-e8b202cfc898."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "p.sqlite"
    run_id, _ = _seed_run(db)

    chain = ReceiptChain(db)
    try:
        before = chain.verify_chain(run_id)
        assert before == (True, None)

        insert_run_summary(
            run_id=run_id,
            summary_text="agent did stuff",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            included_in_merkle=True,
            db_path=db,
        )
        after = chain.verify_chain(run_id)
        assert after == (True, None)

        # And again after flipping the flag — same answer.
        update_summary_merkle_flag(run_id, False, db_path=db)
        toggled = chain.verify_chain(run_id)
        assert toggled == (True, None)
    finally:
        chain.close()
