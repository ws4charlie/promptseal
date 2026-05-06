"""LangChain tools for the hiring agent.

Three tools, called in order by the agent:
- resume_parse: lookup a resume by id
- score_candidate: LLM sub-call to score (this nests an llm_start/end inside
  the surrounding tool_start/end — important for the demo's richness)
- decide: rule-based hire/reject (deterministic so the demo result is stable)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from .llm import make_chat_llm

RESUMES_PATH = Path(__file__).parent / "data" / "resumes.json"

_SCORING_MODEL = "claude-haiku-4-5-20251001"
_SCORING_TEMPERATURE = 0.0


def _load_resumes() -> dict[str, dict[str, Any]]:
    data = json.loads(RESUMES_PATH.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data}


@tool
def resume_parse(resume_id: str) -> dict:
    """Look up a candidate resume by id.

    Returns the resume fields (name, yoe_react, yoe_python, education,
    highlights). Returns an error dict if the id is unknown.
    """
    resumes = _load_resumes()
    if resume_id not in resumes:
        return {"error": f"unknown resume_id: {resume_id}"}
    r = dict(resumes[resume_id])
    r.pop("expected_decision", None)  # never leak ground-truth to the LLM
    return r


@tool
def score_candidate(
    name: str,
    yoe_react: int,
    yoe_python: int,
    education: str,
    highlights: str,
) -> dict:
    """Score a candidate on technical, culture, and ambiguity axes (1-10 each).

    Flat-parameter signature so OpenAI tool calling reliably populates each
    field. Internally invokes the LLM — this nesting produces an extra
    llm_start/end pair inside the surrounding tool_start/end events.
    """
    parsed = {
        "name": name,
        "yoe_react": yoe_react,
        "yoe_python": yoe_python,
        "education": education,
        "highlights": highlights,
    }
    llm = make_chat_llm(
        model=_SCORING_MODEL,
        temperature=_SCORING_TEMPERATURE,
    )
    messages = [
        SystemMessage(content=(
            "You are a senior technical interviewer. Score the candidate on three axes "
            "(integers 1-10): technical_score, culture_score, ambiguity_score. "
            "Return ONLY valid JSON with those three keys, no other text."
        )),
        HumanMessage(content=(
            "Candidate resume:\n"
            f"{json.dumps(parsed, indent=2, ensure_ascii=False)}"
        )),
    ]
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    try:
        scores = json.loads(raw)
    except (ValueError, TypeError):
        scores = {
            "technical_score": 0,
            "culture_score": 0,
            "ambiguity_score": 10,
            "_parse_error": True,
            "raw": raw,
        }
    return scores


@tool
def decide(
    technical_score: int,
    culture_score: int,
    ambiguity_score: int,
    candidate_id: str,
) -> dict:
    """Make a hire/reject decision based on candidate scores.

    Returns {decision: "hire" | "reject", reasoning: str, candidate_id: str}.
    """
    tech = int(technical_score or 0)
    culture = int(culture_score or 0)
    ambiguity = int(ambiguity_score or 10)

    if tech >= 7 and culture >= 6 and ambiguity <= 7:
        decision = "hire"
        reasoning = (
            f"Strong technical ({tech}/10) and culture ({culture}/10), "
            f"low ambiguity ({ambiguity}/10)."
        )
    elif tech < 4 or culture < 4:
        decision = "reject"
        reasoning = f"Weak fundamentals: technical {tech}/10, culture {culture}/10."
    else:
        decision = "reject"
        reasoning = (
            f"Ambiguous case (ambiguity {ambiguity}/10) with mid-tier "
            f"technical {tech}/10; defaulting to reject."
        )
    return {"decision": decision, "reasoning": reasoning, "candidate_id": candidate_id}


TOOLS = [resume_parse, score_candidate, decide]
