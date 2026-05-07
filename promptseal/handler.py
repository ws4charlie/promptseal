"""LangChain BaseCallbackHandler that emits PromptSeal receipts.

Every LLM/tool start+end is captured as a paired pair of signed receipts.
Pairing uses LangChain's per-call run_id (UUID) — each on_*_start stores its
event_hash keyed by run_id, and the matching on_*_end pops it and embeds it
as paired_event_hash on the end receipt.

PromptSeal "run_id" (string in DB) groups all events from one outermost agent
chain invocation. It's generated when the outermost on_chain_start fires
(parent_run_id is None). Nested calls (e.g. score_candidate tool invoking an
LLM internally) inherit the same PromptSeal run_id.

Only sync hooks are implemented. BRIEF §13 pitfall: async hooks silently
swallow exceptions in some LangChain versions.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage

from .canonical import canonical_sha256
from .chain import ReceiptChain
from .receipt import HASH_PREFIX, build_signed_receipt

logger = logging.getLogger(__name__)


def _hash_str(text: str) -> str:
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


_PAYLOAD_TEXT_LIMIT = 32 * 1024  # 32 KB safety cap per field


def _capture_text(text: Any, limit: int = _PAYLOAD_TEXT_LIMIT) -> str | None:
    """Capture text for payload_excerpt with safety cap.

    Hash field captures full content cryptographically; text field is for
    human-readable evidence display.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    truncated_count = len(text) - limit
    return text[:limit] + f"... [TRUNCATED {truncated_count} chars; full hash recorded separately]"


def _json_safe(obj: Any) -> Any:
    """Best-effort coercion to JSON-hashable form for hashing only."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    return str(obj)


def _hash_obj(obj: Any) -> str:
    return HASH_PREFIX + canonical_sha256(_json_safe(obj))


def _extract_model_kwargs(serialized: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(serialized, dict):
        return {}
    kwargs = serialized.get("kwargs") or {}
    return {
        "model": kwargs.get("model") or kwargs.get("model_name"),
        "temperature": kwargs.get("temperature"),
    }


def _split_system(messages: list[list[BaseMessage]]) -> tuple[str, list[dict[str, str]]]:
    """Pull system content out of the first batch of chat messages."""
    flat = messages[0] if messages else []
    system_text = ""
    non_system: list[dict[str, str]] = []
    for m in flat:
        if isinstance(m, BaseMessage) and m.type == "system":
            system_text += str(m.content)
        elif isinstance(m, BaseMessage):
            non_system.append({"type": m.type, "content": str(m.content)})
        else:
            non_system.append({"type": "unknown", "content": str(m)})
    return system_text, non_system


def _llm_output_text(response: Any) -> str:
    pieces: list[str] = []
    for gen_list in (getattr(response, "generations", None) or []):
        for gen in gen_list:
            text = getattr(gen, "text", None)
            if text:
                pieces.append(text)
                continue
            msg = getattr(gen, "message", None)
            if msg is not None:
                pieces.append(str(getattr(msg, "content", "")))
    return "\n".join(pieces)


def _extract_tool_calls(response: Any) -> list[dict[str, Any]] | None:
    """Pull AIMessage.tool_calls from the first generation, if present.

    Returns a normalized list of {name, arguments, id} dicts so the receipt
    captures *what tool the model decided to invoke and with which args* —
    the input-side counterpart to on_tool_start (which fires after the agent
    runtime has already routed the call). Catches both modern (TypedDict)
    and legacy (object) tool_call shapes; tolerates `args` / `arguments`
    naming variations across LangChain / provider SDK versions.

    Returns None if the response had no tool_calls (text-only LLM output)
    or if the structure couldn't be parsed (best-effort, never raises).
    """
    try:
        gens = getattr(response, "generations", None) or []
        if not gens or not gens[0]:
            return None
        first_gen = gens[0][0]
        message = getattr(first_gen, "message", None)
        if message is None:
            return None
        tool_calls_raw = getattr(message, "tool_calls", None)
        if not tool_calls_raw:
            return None
        out: list[dict[str, Any]] = []
        for tc in tool_calls_raw:
            if isinstance(tc, dict):
                args = tc.get("args")
                if args is None:
                    args = tc.get("arguments")
                out.append({
                    "name": tc.get("name"),
                    "arguments": _json_safe(args),
                    "id": tc.get("id"),
                })
            else:
                args = getattr(tc, "args", None)
                if args is None:
                    args = getattr(tc, "arguments", None)
                out.append({
                    "name": getattr(tc, "name", None),
                    "arguments": _json_safe(args),
                    "id": getattr(tc, "id", None),
                })
        return out
    except Exception:
        return None


class PromptSealCallbackHandler(BaseCallbackHandler):
    """LangChain callback that mints a signed receipt per LLM/tool start/end."""

    def __init__(
        self,
        *,
        sk: Ed25519PrivateKey,
        chain: ReceiptChain,
        agent_id: str,
        agent_erc8004_token_id: int | None,
    ) -> None:
        super().__init__()
        self._sk = sk
        self._chain = chain
        self._agent_id = agent_id
        self._token_id = agent_erc8004_token_id
        # LangChain run_id (UUID) → PromptSeal run_id (str, the DB key)
        self._lc_to_ps: dict[UUID, str] = {}
        # LangChain run_id (UUID) → event_hash of the matching *_start
        self._pending_starts: dict[UUID, str] = {}
        # LangChain run_id → tool name (so tool_end knows what fired)
        self._tool_names: dict[UUID, str] = {}
        # PromptSeal run_id of the most-recently-opened outer run.
        self.last_run_id: str | None = None

    # -- internal helpers --------------------------------------------------

    def _resolve_ps_run(self, run_id: UUID, parent_run_id: UUID | None) -> str:
        if run_id in self._lc_to_ps:
            return self._lc_to_ps[run_id]
        if parent_run_id is not None and parent_run_id in self._lc_to_ps:
            ps = self._lc_to_ps[parent_run_id]
        else:
            ps = f"run-{uuid4().hex[:12]}"
            self._chain.open_run(ps, self._agent_id)
            self.last_run_id = ps
        self._lc_to_ps[run_id] = ps
        return ps

    def _emit(
        self,
        ps_run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        paired_event_hash: str | None = None,
    ) -> str:
        receipt = build_signed_receipt(
            sk=self._sk,
            agent_id=self._agent_id,
            agent_erc8004_token_id=self._token_id,
            event_type=event_type,
            payload_excerpt=payload,
            parent_hash=self._chain.latest_event_hash(ps_run_id),
            paired_event_hash=paired_event_hash,
        )
        self._chain.append(ps_run_id, receipt)
        return receipt["event_hash"]

    # -- chain (run boundaries) --------------------------------------------

    def on_chain_start(
        self,
        serialized,
        inputs,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        # Only the outermost chain demarcates a PromptSeal run.
        # No receipt is emitted; chain_start/end are run-boundary markers.
        self._resolve_ps_run(run_id, parent_run_id)

    def on_chain_end(
        self,
        outputs,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        if parent_run_id is None:
            ps = self._lc_to_ps.get(run_id)
            if ps is not None:
                self._chain.close_run(ps)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        self._emit(ps, "error", {
            "stage": "chain",
            "error_type": type(error).__name__,
            "message_hash": _hash_str(str(error)),
        })

    # -- LLM ----------------------------------------------------------------

    def on_llm_start(
        self,
        serialized,
        prompts,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        mk = _extract_model_kwargs(serialized)
        payload = {
            "model": mk.get("model"),
            "temperature": mk.get("temperature"),
            "system_prompt_hash": None,
            "prompts": [_capture_text(p) for p in prompts],
            "messages_hash": _hash_obj(prompts),
        }
        eh = self._emit(ps, "llm_start", payload)
        self._pending_starts[run_id] = eh

    def on_chat_model_start(
        self,
        serialized,
        messages,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        mk = _extract_model_kwargs(serialized)
        system_text, non_system = _split_system(messages)
        payload = {
            "model": mk.get("model"),
            "temperature": mk.get("temperature"),
            "system_prompt": _capture_text(system_text) if system_text else None,
            "system_prompt_hash": _hash_str(system_text) if system_text else None,
            "messages": [
                {"type": m["type"], "content": _capture_text(m["content"])}
                for m in non_system
            ],
            "messages_hash": _hash_obj(non_system),
        }
        eh = self._emit(ps, "llm_start", payload)
        self._pending_starts[run_id] = eh

    def on_llm_end(
        self,
        response,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._lc_to_ps.get(run_id) or self._resolve_ps_run(run_id, parent_run_id)
        paired = self._pending_starts.pop(run_id, None)
        try:
            output_text = _llm_output_text(response)
        except Exception:
            output_text = str(response)
        llm_output = getattr(response, "llm_output", None) or {}
        payload = {
            "output_text": _capture_text(output_text),
            "output_hash": _hash_str(output_text),
            "tool_calls": _extract_tool_calls(response),
            "token_usage": _json_safe(
                llm_output.get("usage") or llm_output.get("token_usage")
            ),
            "finish_reason": llm_output.get("stop_reason")
            or llm_output.get("finish_reason"),
        }
        self._emit(ps, "llm_end", payload, paired_event_hash=paired)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        paired = self._pending_starts.pop(run_id, None)
        self._emit(ps, "error", {
            "stage": "llm",
            "error_type": type(error).__name__,
            "message_hash": _hash_str(str(error)),
        }, paired_event_hash=paired)

    # -- Tools --------------------------------------------------------------

    def on_tool_start(
        self,
        serialized,
        input_str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        inputs=None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        tool_name = (serialized or {}).get("name") or "unknown"
        self._tool_names[run_id] = tool_name
        args_repr = inputs if inputs is not None else input_str
        payload = {
            "tool_name": tool_name,
            "args": (
                _json_safe(args_repr)
                if isinstance(args_repr, dict)
                else _capture_text(args_repr)
            ),
            "args_hash": _hash_obj(args_repr),
        }
        eh = self._emit(ps, "tool_start", payload)
        self._pending_starts[run_id] = eh

    def on_tool_end(
        self,
        output,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._lc_to_ps.get(run_id) or self._resolve_ps_run(run_id, parent_run_id)
        paired = self._pending_starts.pop(run_id, None)
        tool_name = self._tool_names.pop(run_id, "unknown")
        output_str = output if isinstance(output, str) else str(output)
        payload = {
            "tool_name": tool_name,
            "output": _capture_text(output_str),
            "output_hash": _hash_str(output_str),
        }
        self._emit(ps, "tool_end", payload, paired_event_hash=paired)
        if tool_name == "decide":
            decision = self._extract_decision(output)
            if decision is not None:
                self._emit(ps, "final_decision", decision)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs,
    ) -> None:
        ps = self._resolve_ps_run(run_id, parent_run_id)
        paired = self._pending_starts.pop(run_id, None)
        self._tool_names.pop(run_id, None)
        self._emit(ps, "error", {
            "stage": "tool",
            "error_type": type(error).__name__,
            "message_hash": _hash_str(str(error)),
        }, paired_event_hash=paired)

    # -- final_decision extraction ----------------------------------------

    @staticmethod
    def _extract_decision(output: Any) -> dict[str, Any] | None:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (ValueError, TypeError):
                return None
        if not isinstance(output, dict):
            return None
        decision = output.get("decision")
        if decision not in ("hire", "reject"):
            return None
        reasoning_text = str(output.get("reasoning", ""))
        return {
            "candidate_id": output.get("candidate_id"),
            "decision": decision,
            "reasoning": _capture_text(reasoning_text),
            "reasoning_hash": _hash_str(reasoning_text),
        }
