"""LLM-generated natural-language summary of a run's audit trail.

Per D2 (PLAN §3): summaries are NOT first-class evidence. They're convenience
text describing what the agent did — the receipts themselves remain the
law-grade record. Tier 3 customers can opt to include the summary's sha256
as an extra Merkle leaf via `update_summary_merkle_flag(..., True)`; default
is `included_in_merkle=False`.

PII protection (PLAN §8 R6): receipt payload_excerpt is already a hash-of-
content abstraction (system_prompt_hash, messages_hash, args_hash, etc.) —
no raw resume / messages flow into the prompt. The one exception is
`final_decision`, which carries `candidate_id: "res_NNN"` directly. We
include it in the prompt (the LLM needs to know the agent reached a
decision), then post-filter the LLM's output for `res_\\d{3}` patterns and
refuse to store any summary that leaked one.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.llm import make_chat_llm
from .chain import ReceiptChain
from .run_summary import insert_run_summary

# Patterns the summary MUST NOT contain. The candidate-id format is the
# load-bearing one — if the LLM echoes `res_002` from the final_decision
# payload into prose, we want to catch it loudly rather than silently store.
_PII_PATTERNS = (
    re.compile(r"\bres_\d{3}\b"),
)

DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

_PROMPT_TEMPLATE = """\
You are summarizing an audit trail for an AI agent's run. Below are signed
receipts representing each step the agent took. Produce a 3-5 sentence
natural-language summary of what the agent did, what tools it called, and
what decision it reached.

Strict rules:
- Use ONLY information that appears in the receipts.
- Do NOT speculate beyond the data shown.
- Do NOT include any candidate identifier (e.g. res_001, res_002) verbatim.
  Refer to candidates as "the candidate" or "the applicant".
- Output plain prose only — no headings, no bullets, no JSON.

Receipts ({n_receipts} events for run {run_id}):
{receipts_block}
"""


class PromptSealPiiError(Exception):
    """Raised when an LLM-generated summary contains text that matches a PII
    pattern (e.g. a candidate id). The summary is NOT stored."""


def _format_receipts_for_prompt(receipts: list[dict[str, Any]]) -> str:
    """Render receipts as a compact bullet list the LLM can scan."""
    lines: list[str] = []
    for i, r in enumerate(receipts):
        payload = r.get("payload_excerpt", {})
        # Compact one-line JSON keeps the prompt token-cheap and the order
        # deterministic (sort_keys via canonicalize would be overkill here).
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        lines.append(
            f"{i + 1:2d}. [{r['event_type']}] {r['timestamp']} {payload_str}"
        )
    return "\n".join(lines)


def _build_prompt(run_id: str, receipts: list[dict[str, Any]]) -> str:
    return _PROMPT_TEMPLATE.format(
        run_id=run_id,
        n_receipts=len(receipts),
        receipts_block=_format_receipts_for_prompt(receipts),
    )


def _check_no_pii(text: str) -> None:
    """Raise PromptSealPiiError on any pattern hit. No-op otherwise."""
    for pat in _PII_PATTERNS:
        match = pat.search(text)
        if match:
            raise PromptSealPiiError(
                f"summary contains PII match for pattern {pat.pattern!r}: "
                f"{match.group(0)!r}. Refusing to store. Re-prompt the model "
                "or strip the offending span before retrying."
            )


def summarize_run(
    run_id: str,
    *,
    db_path: str | Path | None = None,
    llm_provider: str = DEFAULT_LLM_PROVIDER,
    llm_model: str = DEFAULT_LLM_MODEL,
) -> dict[str, Any]:
    """Generate + store a summary for `run_id`. Returns the stored dict.

    The summary is stored with `included_in_merkle=False` (D2 default). To
    opt the summary into the run's Merkle tree, call
    `run_summary.update_summary_merkle_flag(run_id, True)` afterwards.

    Raises PromptSealPiiError if the LLM output contains a candidate id.
    Raises whatever ReceiptChain raises if the run doesn't exist.
    """
    db = Path(db_path) if db_path is not None else None
    chain = ReceiptChain(db if db else "promptseal.sqlite")
    try:
        receipts = chain.get_receipts(run_id)
    finally:
        chain.close()

    if not receipts:
        raise ValueError(f"run {run_id!r} has no receipts in DB")

    prompt = _build_prompt(run_id, receipts)

    llm = make_chat_llm(model=llm_model, temperature=0.0)
    response = llm.invoke([{"role": "user", "content": prompt}])
    summary_text = _coerce_to_text(response).strip()

    if not summary_text:
        raise ValueError("LLM returned empty summary")

    _check_no_pii(summary_text)

    # The provider name we record is what the caller asked for, not what
    # make_chat_llm picked at runtime (Bifrost vs OpenAI vs Anthropic). The
    # caller is the source of truth for "what tier did we want?"; the
    # provider auto-selection is a deployment detail.
    return insert_run_summary(
        run_id=run_id,
        summary_text=summary_text,
        llm_provider=llm_provider,
        llm_model=llm_model,
        included_in_merkle=False,
        db_path=db,
    )


def _coerce_to_text(response: Any) -> str:
    """LangChain BaseChatModel.invoke can return AIMessage / dict / str
    depending on version. Normalize to plain text."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Anthropic structured-content lists: [{type:"text", text:"..."}, ...]
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(str(chunk.get("text", "")))
            elif isinstance(chunk, str):
                parts.append(chunk)
        return "".join(parts)
    return str(content)


# Tiny helper for the test harness: builds the same prompt the production
# path sends to the LLM. Lets tests inspect prompt structure without any
# LLM call. Not part of the public API.
def _prompt_for_run(run_id: str, db_path: str | Path | None = None) -> str:
    db = Path(db_path) if db_path is not None else None
    chain = ReceiptChain(db if db else "promptseal.sqlite")
    try:
        receipts = chain.get_receipts(run_id)
    finally:
        chain.close()
    return _build_prompt(run_id, receipts)
