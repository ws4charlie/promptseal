# Compliance notes: PromptSeal hiring-agent demo

> **Engineering notes, not legal advice.** This maps the demo's technical controls to the concerns a
> regulator, auditor or candidate would raise. It does not establish that any deployment complies with
> any law. Anyone deploying an automated screening tool needs their own legal review.

Scope: the LangChain hiring agent in `agent/` (`resume_parse → redact_pii → get_job_description →
[github_profile] → score_candidate → decide`), audited by the PromptSeal callback handler
(`promptseal/handler.py`), and evaluated by `eval/` (v2: `eval/v2/README.md`).

## Summary

| Concern | Control in the demo | Evidence | Gap / what production would need |
|---|---|---|---|
| PII minimisation | `redact_pii` removes emails, phones, URLs, street addresses before scoring | `agent/pii.py`; `pii_redactions` counts in `score_candidate` output | Regex only (no names, no free-text addresses); `resume_parse` output is still unredacted and is captured in the audit log |
| Automated decision-making | `decide` is a fixed, published rule over three scores | `agent/tools.py`; `final_decision` receipt | The AI decides PASS/REJECT with no human review (backlog) |
| Bias | Name-swap audit, impact ratio, four-fifths flag; v2 prompt forbids using name/demographics | `eval/analyze.py` bias audit; `eval/v2/README.md` | Small n; proxy groups; "culture" axis is a bias risk |
| Audit trail | Signed, hash-chained receipts for every LLM and tool call; prompt version/sha and served model per scoring call | `promptseal/`; `score_candidate` audit fields; eval `calls.jsonl` | Receipts retain personal data; retention policy not implemented |
| Security / injection | Spotlighting delimiters, injection flag, least-privilege tools | `agent/prompt_registry.py`, `agent/prompts/scoring/v2.md`; eval injection report | Flag is advisory (logged, not blocking) |
| Prompt change control | Immutable, sha-pinned prompt files; regression gate before switching versions | `agent/prompts/registry.json`; `analyze.py compare` | Gate is point-estimate based, small n |

## 1. PII minimisation

- `redact_pii` (`agent/pii.py`) is deterministic regex redaction of emails, phone numbers, URLs and
  street-address-like lines. A `github.com/<user>` handle is extracted separately so the scorer never
  sees it. `score_candidate` (v2 path) redacts the resume text itself before building the message and
  returns the redaction counts, so the counts land in the audit log.
- The candidate **name is sent to the scoring model** (as data inside `<resume>`); the v2 prompt tells
  the model not to use it. Removing it entirely would be stronger minimisation; we keep it so the
  name-swap audit measures what the model actually does with names.
- Gaps: regex misses names inside the text, addresses without a street keyword, and other identifiers
  (dates of birth, photos are not applicable here). `resume_parse` returns the unredacted record, and
  the PromptSeal handler captures tool outputs (`tool_end` payload: an excerpt plus a hash of the full
  output), so the audit log holds personal data. A production deployment should redact before any
  receipt is written, or store only hashes of personal data in receipts.

## 2. Automated decision-making

- Today the AI's scores and a fixed rule (`decide`: PASS iff technical >= 7, culture >= 6,
  ambiguity <= 7) decide. There is no human acknowledgement step; that is a deliberate product decision
  for the demo and is on the backlog.
- A production deployment would need at least: a human reviewer who sees the scores, the rationale and
  the evidence quotes and can override before any adverse outcome; notice to candidates that an
  automated tool is used and how to request an alternative process or human review; a way for a
  candidate to obtain the main reasons for the outcome (the v2 rationale and the receipt chain support
  this); and monitoring of outcomes by group over time, not just a one-off audit.

## 3. Bias

- **Name-swap audit.** `eval/v2/samples.json` has 12 base resumes, each presented under four name groups
  (white/black × female/male, names from Bertrand & Mullainathan 2004) with identical text.
  `eval/analyze.py` reports the selection rate per group and the **impact ratio** (lowest group rate /
  highest), flagged below **0.8**. The 0.8 threshold is the US federal "four-fifths rule": a selection
  rate for a group below four-fifths of the highest group's rate is generally regarded as evidence of
  adverse impact (29 CFR 1607.4(D), https://www.law.cornell.edu/cfr/text/29/1607.4).
- n is small (6 sets per split, fewer in the pilot), so the audit can raise a flag but cannot clear a
  model. Name group is a proxy for perceived race and sex; it says nothing about other protected
  characteristics.
- **"Culture" axis risk.** Inferring "culture fit" from a resume is a known channel for bias (it can
  reward background, writing style or affinity rather than job-relevant behaviour). v2 narrows it to
  stated working behaviours from the job description and forbids using background, but the axis is
  kept for compatibility with `decide` and the dashboard. Dropping or redefining it is on the backlog.
- **NYC Local Law 144** (automated employment decision tools): an employer or employment agency may not
  use such a tool unless it has had a bias audit within one year before use, a summary of the results
  is publicly available, and candidates receive notice
  (https://www.nyc.gov/site/dca/about/automated-employment-decision-tools.page). The name-swap impact
  ratio here follows the same idea (selection rates by group), but it is an internal engineering check
  on synthetic name assignments, not an independent bias audit of real applicant outcomes.
- **EU AI Act**: Annex III lists AI systems intended for the recruitment or selection of natural
  persons, including analysing and filtering job applications, as high-risk
  (https://artificialintelligenceact.eu/annex/3/). A deployment in the EU would carry the high-risk
  obligations (risk management, data governance, logging, transparency, human oversight, accuracy and
  robustness). The audit trail and evaluation here are inputs to that, not a substitute.

## 4. Audit trail

- PromptSeal records every LLM call and tool call of an agent run as a signed receipt, hash-chained per
  run and Merkle-anchored (see the root `README.md`). Tampering with any receipt breaks the chain.
- `score_candidate` returns, and therefore logs: `prompt_name`, `prompt_version`, `prompt_sha256`,
  `job_id`, `model_requested`, `model_served`, `pii_redactions`, `injection_suspected`, and the v2
  rationale with evidence quotes. An auditor can tie a decision to the exact prompt text (sha-pinned in
  `agent/prompts/registry.json`; registered files are immutable) and to the model that actually served.
- The eval harness records the same `prompt_version` / `prompt_sha256` and the served model per call in
  `calls.jsonl` and `run_meta.json`; labels were committed before any model under test ran
  (commit `1c5d791`).
- Known quirk: `agent/llm.py:make_chat_llm` uses gpt-4o-mini whenever `OPENAI_API_KEY` is set,
  regardless of the requested model. The `model_requested` / `model_served` pair in the receipt makes
  that visible; a production deployment should fail closed instead.

## 5. Security

- **Spotlighting.** v2 wraps the job description and the resume in `<job_description>` / `<resume>`
  delimiters, neutralises delimiter-like strings inside the data, and the system prompt states that the
  content is data, not instructions.
- **Injection flag.** v2 asks the model to set `injection_suspected` and to score only the rest of the
  resume. The flag is logged in the receipt; it does not block the decision. The eval measures the flag
  rate on injection variants vs clean items, and the regression gate fails any prompt under which an
  injection variant flips from reject to PASS.
- **Least-privilege tools:**

| Tool | Access | Side effects |
|---|---|---|
| `resume_parse` | Read a resume record by id from `agent/data/resumes.json` | None |
| `redact_pii` | Pure function over the given text | None |
| `get_job_description` | Read a job file by id (`[a-z0-9_]+`) from `agent/data/jobs/` | None |
| `github_profile` | Read-only public GitHub REST API (`api.github.com`), optional token, 10 s timeout | Writes a response cache under `~/.cache/promptseal/github/` |
| `score_candidate` | Reads the prompt registry and job file; one LLM call | Network call to the model provider (sends the redacted resume and the name) |
| `decide` | Pure function over three scores | None |

No tool writes to an ATS, sends email or schedules anything.

## 6. Data retention

- The demo has no retention policy: receipts (SQLite, `promptseal/chain.py`), eval raw outputs
  (`eval/results/*/calls.jsonl`, gitignored) and the GitHub cache persist until deleted by hand.
- The resume data is a public, anonymised dataset (Djinni) plus generated names; no real applicant
  data is processed.
- A production deployment needs a defined retention period for candidate data and receipts, deletion on
  request where the law gives that right, and a design where deleting personal data does not break the
  receipt chain (for example receipts that hold only hashes of personal data, with the data itself in a
  separate deletable store).
