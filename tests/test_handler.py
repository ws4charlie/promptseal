"""Tests for promptseal.handler.

Spec (BRIEF §8, §13):
- Subclass of BaseCallbackHandler, sync hooks only.
- Outermost on_chain_start (parent_run_id is None) opens a new PromptSeal run.
- Nested calls (LLM inside tool) inherit the same PromptSeal run_id.
- _start receipt's event_hash is stored, popped on matching _end → embedded
  as paired_event_hash on the _end receipt.
- on_tool_end with tool_name="decide" → an extra final_decision receipt.
- Errors → an "error" receipt (single, not paired).
- Multiple sequential agent invocations produce independent runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from promptseal.chain import ReceiptChain
from promptseal.crypto import generate_keypair
from promptseal.handler import PromptSealCallbackHandler
from promptseal.receipt import HASH_PREFIX


def _make_handler(tmp_path: Path):
    sk = generate_keypair()
    chain = ReceiptChain(tmp_path / "x.sqlite")
    h = PromptSealCallbackHandler(
        sk=sk,
        chain=chain,
        agent_id="hr-screener-v1",
        agent_erc8004_token_id=398,
    )
    return h, chain


# -- happy path --------------------------------------------------------------

def test_chain_start_opens_run(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    rid = uuid4()
    h.on_chain_start({"name": "AgentExecutor"}, {"input": "x"}, run_id=rid)
    assert h.last_run_id is not None
    rows = list(chain._conn.execute("SELECT run_id FROM runs"))
    assert len(rows) == 1


def test_chain_end_closes_run(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    rid = uuid4()
    h.on_chain_start({"name": "X"}, {}, run_id=rid)
    h.on_chain_end({"output": "ok"}, run_id=rid)
    row = chain._conn.execute(
        "SELECT ended_at FROM runs WHERE run_id = ?", (h.last_run_id,)
    ).fetchone()
    assert row["ended_at"] is not None


def test_llm_start_end_pair(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    chain_id = uuid4()
    llm_id = uuid4()
    h.on_chain_start({}, {}, run_id=chain_id)
    h.on_llm_start({"kwargs": {"model": "claude-haiku-4-5-20251001", "temperature": 0.0}},
                   ["hello"], run_id=llm_id, parent_run_id=chain_id)
    response = LLMResult(generations=[[Generation(text="hi")]],
                         llm_output={"usage": {"input_tokens": 10, "output_tokens": 1}})
    h.on_llm_end(response, run_id=llm_id, parent_run_id=chain_id)

    receipts = chain.get_receipts(h.last_run_id)
    assert len(receipts) == 2
    assert receipts[0]["event_type"] == "llm_start"
    assert receipts[1]["event_type"] == "llm_end"
    # Pairing
    assert receipts[1]["paired_event_hash"] == receipts[0]["event_hash"]
    # Chain link
    assert receipts[1]["parent_hash"] == receipts[0]["event_hash"]
    # Payload sanity
    assert receipts[0]["payload_excerpt"]["model"] == "claude-haiku-4-5-20251001"
    assert receipts[0]["payload_excerpt"]["temperature"] == 0.0


def test_chat_model_start_extracts_system_prompt(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    cid = uuid4()
    lid = uuid4()
    h.on_chain_start({}, {}, run_id=cid)
    messages = [[
        SystemMessage(content="You are a recruiter."),
        HumanMessage(content="screen res_001"),
    ]]
    h.on_chat_model_start(
        {"kwargs": {"model": "claude-haiku-4-5-20251001", "temperature": 0.0}},
        messages, run_id=lid, parent_run_id=cid,
    )
    response = LLMResult(generations=[[ChatGeneration(message=HumanMessage(content="ok"))]])
    h.on_llm_end(response, run_id=lid, parent_run_id=cid)

    receipts = chain.get_receipts(h.last_run_id)
    start = receipts[0]
    assert start["event_type"] == "llm_start"
    assert start["payload_excerpt"]["system_prompt_hash"] is not None
    assert start["payload_excerpt"]["system_prompt_hash"].startswith(HASH_PREFIX)


def test_tool_start_end_pair(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    cid = uuid4()
    tid = uuid4()
    h.on_chain_start({}, {}, run_id=cid)
    h.on_tool_start({"name": "resume_parse"}, "res_001", run_id=tid, parent_run_id=cid)
    h.on_tool_end({"id": "res_001", "name": "Alice"}, run_id=tid, parent_run_id=cid)

    receipts = chain.get_receipts(h.last_run_id)
    assert [r["event_type"] for r in receipts] == ["tool_start", "tool_end"]
    assert receipts[1]["paired_event_hash"] == receipts[0]["event_hash"]
    assert receipts[0]["payload_excerpt"]["tool_name"] == "resume_parse"
    assert receipts[1]["payload_excerpt"]["tool_name"] == "resume_parse"


# -- the BRIEF §8 critical test ----------------------------------------------

def test_nested_llm_inside_tool_inherits_run_and_pairs_correctly(tmp_path: Path):
    """The score_candidate tool internally invokes the LLM. All 6 events
    must land in the same PromptSeal run with correct pairing.

    LangChain emits this nesting:
        chain_start (A, parent=None)
          chat_model_start (B, parent=A)        # outer reasoning LLM
          chat_model_end   (B)
          tool_start       (C, parent=A, name=score_candidate)
            chat_model_start (D, parent=C)      # inner LLM call inside tool
            chat_model_end   (D)
          tool_end         (C)
        chain_end   (A)
    """
    h, chain = _make_handler(tmp_path)
    A, B, C, D = uuid4(), uuid4(), uuid4(), uuid4()

    h.on_chain_start({}, {}, run_id=A)
    # Outer LLM
    h.on_chat_model_start({"kwargs": {"model": "m", "temperature": 0.0}},
                          [[HumanMessage(content="step")]],
                          run_id=B, parent_run_id=A)
    h.on_llm_end(LLMResult(generations=[[ChatGeneration(message=HumanMessage(content="thinking"))]]),
                 run_id=B, parent_run_id=A)
    # Tool
    h.on_tool_start({"name": "score_candidate"}, '{"resume":"x"}', run_id=C, parent_run_id=A)
    # Nested LLM inside tool
    h.on_chat_model_start({"kwargs": {"model": "m", "temperature": 0.0}},
                          [[HumanMessage(content="score it")]],
                          run_id=D, parent_run_id=C)
    h.on_llm_end(LLMResult(generations=[[ChatGeneration(message=HumanMessage(content='{"technical_score":8}'))]]),
                 run_id=D, parent_run_id=C)
    h.on_tool_end({"technical_score": 8}, run_id=C, parent_run_id=A)
    h.on_chain_end({}, run_id=A)

    ps_run_id = h.last_run_id
    receipts = chain.get_receipts(ps_run_id)
    types = [r["event_type"] for r in receipts]
    assert types == [
        "llm_start", "llm_end",
        "tool_start",
        "llm_start", "llm_end",
        "tool_end",
    ]

    # Pairings
    outer_llm_start, outer_llm_end = receipts[0], receipts[1]
    tool_start, inner_llm_start, inner_llm_end, tool_end = (
        receipts[2], receipts[3], receipts[4], receipts[5]
    )
    assert outer_llm_end["paired_event_hash"] == outer_llm_start["event_hash"]
    assert inner_llm_end["paired_event_hash"] == inner_llm_start["event_hash"]
    assert tool_end["paired_event_hash"] == tool_start["event_hash"]

    # parent_hash chain integrity
    ok, err = chain.verify_chain(ps_run_id)
    assert ok, err

    # All 6 receipts in ONE run
    rows = list(chain._conn.execute("SELECT run_id FROM runs"))
    assert len(rows) == 1


def test_parallel_tools_pair_correctly(tmp_path: Path):
    """If two tool_starts fire before either tool_end (parallel call), the
    pairing must remain correct: each _end pairs with its OWN _start."""
    h, chain = _make_handler(tmp_path)
    A, X, Y = uuid4(), uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_tool_start({"name": "t1"}, "in1", run_id=X, parent_run_id=A)
    h.on_tool_start({"name": "t2"}, "in2", run_id=Y, parent_run_id=A)
    h.on_tool_end("out1", run_id=X, parent_run_id=A)
    h.on_tool_end("out2", run_id=Y, parent_run_id=A)

    receipts = chain.get_receipts(h.last_run_id)
    types = [r["event_type"] for r in receipts]
    assert types == ["tool_start", "tool_start", "tool_end", "tool_end"]

    t1_start, t2_start, t1_end, t2_end = receipts
    assert t1_end["paired_event_hash"] == t1_start["event_hash"]
    assert t2_end["paired_event_hash"] == t2_start["event_hash"]
    # And tool_name on each end matches its start
    assert t1_end["payload_excerpt"]["tool_name"] == "t1"
    assert t2_end["payload_excerpt"]["tool_name"] == "t2"


# -- final_decision ----------------------------------------------------------

def test_decide_tool_emits_final_decision(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    A, T = uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_tool_start({"name": "decide"}, '{"scores":{}, "candidate_id":"res_001"}',
                    run_id=T, parent_run_id=A)
    h.on_tool_end(
        {"decision": "hire", "reasoning": "strong technical", "candidate_id": "res_001"},
        run_id=T, parent_run_id=A,
    )
    receipts = chain.get_receipts(h.last_run_id)
    types = [r["event_type"] for r in receipts]
    assert types == ["tool_start", "tool_end", "final_decision"]
    fd = receipts[2]["payload_excerpt"]
    assert fd["decision"] == "hire"
    assert fd["candidate_id"] == "res_001"
    assert fd["reasoning_hash"].startswith(HASH_PREFIX)


def test_non_decide_tool_does_not_emit_final_decision(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    A, T = uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_tool_start({"name": "resume_parse"}, "res_001", run_id=T, parent_run_id=A)
    h.on_tool_end({"id": "res_001"}, run_id=T, parent_run_id=A)
    receipts = chain.get_receipts(h.last_run_id)
    assert all(r["event_type"] != "final_decision" for r in receipts)


def test_decide_tool_with_string_json_output(tmp_path: Path):
    """Some LangChain flows pass tool output as a JSON string; final_decision
    should still extract correctly."""
    h, chain = _make_handler(tmp_path)
    A, T = uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_tool_start({"name": "decide"}, "{}", run_id=T, parent_run_id=A)
    payload = json.dumps({"decision": "reject", "reasoning": "too junior",
                          "candidate_id": "res_002"})
    h.on_tool_end(payload, run_id=T, parent_run_id=A)
    receipts = chain.get_receipts(h.last_run_id)
    fd = next(r for r in receipts if r["event_type"] == "final_decision")
    assert fd["payload_excerpt"]["decision"] == "reject"


# -- multiple runs -----------------------------------------------------------

def test_two_sequential_chain_invocations_are_independent(tmp_path: Path):
    h, chain = _make_handler(tmp_path)

    A1 = uuid4()
    h.on_chain_start({}, {}, run_id=A1)
    h.on_tool_start({"name": "t"}, "x", run_id=uuid4(), parent_run_id=A1)
    h.on_chain_end({}, run_id=A1)
    run1 = h.last_run_id

    A2 = uuid4()
    h.on_chain_start({}, {}, run_id=A2)
    h.on_tool_start({"name": "t"}, "y", run_id=uuid4(), parent_run_id=A2)
    h.on_chain_end({}, run_id=A2)
    run2 = h.last_run_id

    assert run1 != run2
    rows = list(chain._conn.execute("SELECT run_id FROM runs ORDER BY started_at"))
    assert {r["run_id"] for r in rows} == {run1, run2}


# -- errors ------------------------------------------------------------------

def test_llm_error_emits_error_event(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    A, L = uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_llm_start({"kwargs": {"model": "m", "temperature": 0.0}}, ["x"],
                   run_id=L, parent_run_id=A)
    h.on_llm_error(RuntimeError("rate limit"), run_id=L, parent_run_id=A)
    receipts = chain.get_receipts(h.last_run_id)
    assert receipts[-1]["event_type"] == "error"
    err_payload = receipts[-1]["payload_excerpt"]
    assert err_payload["stage"] == "llm"
    assert err_payload["error_type"] == "RuntimeError"


def test_tool_error_emits_error_event(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    A, T = uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_tool_start({"name": "resume_parse"}, "bogus", run_id=T, parent_run_id=A)
    h.on_tool_error(KeyError("no_such_id"), run_id=T, parent_run_id=A)
    receipts = chain.get_receipts(h.last_run_id)
    assert receipts[-1]["event_type"] == "error"
    assert receipts[-1]["payload_excerpt"]["stage"] == "tool"


# -- chain integrity end-to-end ---------------------------------------------

def test_full_run_chain_integrity_holds(tmp_path: Path):
    h, chain = _make_handler(tmp_path)
    A, L1, T1, T2 = uuid4(), uuid4(), uuid4(), uuid4()
    h.on_chain_start({}, {}, run_id=A)
    h.on_chat_model_start({"kwargs": {"model": "m", "temperature": 0.0}},
                          [[HumanMessage(content="plan")]], run_id=L1, parent_run_id=A)
    h.on_llm_end(LLMResult(generations=[[ChatGeneration(message=HumanMessage(content="ok"))]]),
                 run_id=L1, parent_run_id=A)
    h.on_tool_start({"name": "resume_parse"}, "res_001", run_id=T1, parent_run_id=A)
    h.on_tool_end({"id": "res_001"}, run_id=T1, parent_run_id=A)
    h.on_tool_start({"name": "decide"}, "{}", run_id=T2, parent_run_id=A)
    h.on_tool_end({"decision": "hire", "reasoning": "good", "candidate_id": "res_001"},
                  run_id=T2, parent_run_id=A)
    h.on_chain_end({"output": "hired"}, run_id=A)

    ok, err = chain.verify_chain(h.last_run_id)
    assert ok, err
