"""LangChain tools for the hiring agent.

Called in order by the agent (see `hiring_agent.SYSTEM_PROMPT`):
- resume_parse: look up a resume by id (fields plus a plain-text rendering)
- redact_pii: regex-based PII removal (emails, phones, URLs, addresses)
- get_job_description: read-only job description by id
- github_profile: read-only public GitHub summary (only if a handle was found)
- score_candidate: LLM sub-call to score (this nests an llm_start/end inside
  the surrounding tool_start/end — important for the demo's richness). Its
  output carries the prompt version/sha256, requested and served model, PII
  redaction counts and the injection flag, so they land in the audit log.
- decide: rule-based hire/reject (deterministic so the demo result is stable)

All tools are least privilege: no writes, no side effects beyond the GitHub cache.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from . import github, pii, prompt_registry
from .llm import make_chat_llm

RESUMES_PATH = Path(__file__).parent / "data" / "resumes.json"
JOBS_DIR = Path(__file__).parent / "data" / "jobs"
DEFAULT_JOB_ID = "senior_fullstack"
_JOB_ID_RE = re.compile(r"[a-z0-9_]+")

_SCORING_PROMPT_NAME = "scoring"
_SCORING_MODEL = "claude-haiku-4-5-20251001"
_SCORING_TEMPERATURE = 0.0


def _load_resumes() -> dict[str, dict[str, Any]]:
    data = json.loads(RESUMES_PATH.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data}


def render_resume_text(fields: dict[str, Any]) -> str:
    """Plain-text rendering of the demo resume fields (the name is kept separate)."""
    return (
        f"React experience: {fields.get('yoe_react')} years\n"
        f"Python experience: {fields.get('yoe_python')} years\n"
        f"Education: {fields.get('education')}\n"
        f"Highlights: {fields.get('highlights')}"
    )


def _read_job_description(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"invalid job_id: {job_id!r} (allowed: [a-z0-9_]+)")
    path = JOBS_DIR / f"{job_id}.md"
    if not path.is_file():
        raise LookupError(f"unknown job_id: {job_id}")
    return path.read_text(encoding="utf-8")


def _served_model(response: Any) -> str | None:
    meta = getattr(response, "response_metadata", None) or {}
    return meta.get("model_name") or meta.get("model")


@tool
def resume_parse(resume_id: str) -> dict:
    """Look up a candidate resume by id.

    Returns the resume fields (name, yoe_react, yoe_python, education,
    highlights) plus resume_text, a plain-text rendering without the name.
    Returns an error dict if the id is unknown.
    """
    resumes = _load_resumes()
    if resume_id not in resumes:
        return {"error": f"unknown resume_id: {resume_id}"}
    r = dict(resumes[resume_id])
    r.pop("expected_decision", None)  # never leak ground-truth to the LLM
    r["resume_text"] = render_resume_text(r)
    return r


@tool
def redact_pii(text: str) -> dict:
    """Remove PII (emails, phone numbers, URLs, street addresses) from resume text.

    Regex-based, no LLM. Returns {text, redactions: {kind: count},
    github_username: str | None}; a github.com/<user> handle is extracted
    before URLs are removed.
    """
    return pii.redact(text)


@tool
def get_job_description(job_id: str) -> str:
    """Return the job description (markdown) for a job id such as "senior_fullstack".

    Read-only. Returns a string starting with "error:" for an invalid or unknown id.
    """
    try:
        return _read_job_description(job_id)
    except (ValueError, LookupError) as exc:
        return f"error: {exc}"


@tool
def github_profile(username: str) -> dict:
    """Read-only summary of a public GitHub profile.

    Returns public repo count, top languages, recent repos and account age,
    or {error: ...}. Call only with a username found by redact_pii.
    """
    return github.profile(username)


@tool
def score_candidate(
    name: str,
    yoe_react: int | None = None,
    yoe_python: int | None = None,
    education: str | None = None,
    highlights: str | None = None,
    resume_text: str | None = None,
    job_id: str | None = None,
    prompt_version: str | None = None,
) -> dict:
    """Score a candidate on technical, culture, and ambiguity axes (1-10 each).

    Preferred: pass name, resume_text (the redacted text) and job_id; the
    registry's active prompt scores it against the job description. Legacy v1:
    pass name, yoe_react, yoe_python, education, highlights. Flat parameters so
    tool calling reliably populates each field. Internally invokes the LLM —
    this nesting produces an extra llm_start/end pair inside the surrounding
    tool_start/end events. Returns the scores plus audit fields.
    """
    if prompt_version is None and resume_text is None:
        prompt_version = "v1"
    prompt = prompt_registry.load_prompt(_SCORING_PROMPT_NAME, prompt_version, model=_SCORING_MODEL)

    fields = {
        "name": name,
        "yoe_react": yoe_react,
        "yoe_python": yoe_python,
        "education": education,
        "highlights": highlights,
    }
    pii_redactions = None
    if prompt.input_format == prompt_registry.INPUT_FORMAT_RESUME_TEXT_V2:
        job_id = job_id or DEFAULT_JOB_ID
        try:
            job_description = _read_job_description(job_id)
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}
        redacted = pii.redact(resume_text if resume_text is not None else render_resume_text(fields))
        pii_redactions = redacted["redactions"]
        user_message = prompt_registry.build_user_message(
            prompt, name=name, job_description=job_description, resume_text=redacted["text"]
        )
    else:
        job_id = None
        user_message = prompt_registry.build_user_message(
            prompt, name=name, job_description=None, fields=fields
        )

    llm = make_chat_llm(
        model=_SCORING_MODEL,
        temperature=_SCORING_TEMPERATURE,
    )
    messages = [
        SystemMessage(content=prompt.text),
        HumanMessage(content=user_message),
    ]
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    scores = prompt_registry.parse_scores(prompt, raw)
    if not isinstance(scores, dict):
        scores = {"raw": scores}

    injection = None
    if prompt.input_format == prompt_registry.INPUT_FORMAT_RESUME_TEXT_V2:
        injection = scores.get("injection_suspected")
    return {
        **scores,
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "job_id": job_id,
        "model_requested": _SCORING_MODEL,
        "model_served": _served_model(response),
        "pii_redactions": pii_redactions,
        "injection_suspected": injection,
    }


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


TOOLS = [resume_parse, redact_pii, get_job_description, github_profile, score_candidate, decide]
