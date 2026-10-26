"""The scoring prompt and output parsing used by the `score_candidate` tool.

Kept free of LangChain imports so the evaluation harness (`eval/`) can run the
exact same prompt and the exact same parsing rule as the agent, without
installing the agent's dependencies. `agent/tools.py` imports from here, so
there is a single source of truth.
"""
from __future__ import annotations

import json
from typing import Any

SCORING_SYSTEM_PROMPT = (
    "You are a senior technical interviewer. Score the candidate on three axes "
    "(integers 1-10): technical_score, culture_score, ambiguity_score. "
    "Return ONLY valid JSON with those three keys, no other text."
)

# Fields the agent passes to the scorer (see score_candidate's signature).
SCORED_FIELDS = ("name", "yoe_react", "yoe_python", "education", "highlights")


def build_user_message(parsed: dict[str, Any]) -> str:
    """The human-turn text sent to the scoring model."""
    return "Candidate resume:\n" + json.dumps(parsed, indent=2, ensure_ascii=False)


def parse_scores(raw: str) -> dict[str, Any]:
    """Strict parse, exactly as the agent does it.

    Anything that is not a bare JSON object (for example JSON wrapped in a
    markdown code fence) is treated as a parse error and scored as
    technical 0 / culture 0 / ambiguity 10, which `decide` turns into a reject.
    """
    try:
        scores = json.loads(raw)
    except (ValueError, TypeError):
        return {
            "technical_score": 0,
            "culture_score": 0,
            "ambiguity_score": 10,
            "_parse_error": True,
            "raw": raw,
        }
    return scores


def decide_rule(technical_score: int, culture_score: int, ambiguity_score: int) -> str:
    """The deterministic hire/reject rule used by the `decide` tool."""
    tech = int(technical_score or 0)
    culture = int(culture_score or 0)
    ambiguity = int(ambiguity_score or 10)
    if tech >= 7 and culture >= 6 and ambiguity <= 7:
        return "hire"
    return "reject"
