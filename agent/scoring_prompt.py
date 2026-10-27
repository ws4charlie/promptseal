"""The v1 scoring prompt and output parsing (`scoring/v1` in the prompt registry).

Kept free of LangChain imports so the v1 evaluation harness (`eval/`) can run the
exact same prompt and the exact same parsing rule as the agent. The prompt text
is loaded from `agent/prompts/scoring/v1.md` via `agent/prompt_registry.py`, the
single source of truth; newer versions are used through the registry directly.
"""
from __future__ import annotations

from typing import Any

from . import prompt_registry

SCORING_PROMPT_V1 = prompt_registry.load_prompt("scoring", "v1")
SCORING_SYSTEM_PROMPT = SCORING_PROMPT_V1.text

# Fields the agent passes to the scorer (see score_candidate's signature).
SCORED_FIELDS = ("name", "yoe_react", "yoe_python", "education", "highlights")


def build_user_message(parsed: dict[str, Any]) -> str:
    """The human-turn text sent to the scoring model."""
    return prompt_registry.build_user_message(
        SCORING_PROMPT_V1, name=parsed.get("name", ""), job_description=None, fields=parsed
    )


def parse_scores(raw: str) -> dict[str, Any]:
    """Strict parse, exactly as the agent does it.

    Anything that is not a bare JSON object (for example JSON wrapped in a
    markdown code fence) is treated as a parse error and scored as
    technical 0 / culture 0 / ambiguity 10, which `decide` turns into a reject.
    """
    return prompt_registry.parse_scores(SCORING_PROMPT_V1, raw)


def decide_rule(technical_score: int, culture_score: int, ambiguity_score: int) -> str:
    """The deterministic hire/reject rule used by the `decide` tool."""
    tech = int(technical_score or 0)
    culture = int(culture_score or 0)
    ambiguity = int(ambiguity_score or 10)
    if tech >= 7 and culture >= 6 and ambiguity <= 7:
        return "hire"
    return "reject"
