"""Regex-based PII redaction for resume text (no LLM, deterministic).

Removes emails, phone numbers, URLs and street-address-like lines before a
resume reaches a scoring model. A `github.com/<user>` handle is kept separately
so the agent can look up the public profile without the scorer seeing it.
"""
from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

_TLDS = r"(?:com|org|net|io|dev|me|co|ai|app|info|tech|xyz|so|page|gg|ly|ua|uk|de|eu|us|ca|in)"
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')\]]+"
    r"|\b(?:[a-z0-9-]+\.)+" + _TLDS + r"/[^\s<>\"')\]]*"
)
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?![A-Za-z0-9-])", re.IGNORECASE)
_GITHUB_RESERVED = {"orgs", "features", "about", "settings", "marketplace", "topics", "sponsors", "login"}

# Candidate phone numbers; filtered by digit count and a year-range check below.
PHONE_RE = re.compile(
    r"(?<![\w/.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]\d{2,4}){1,4}(?![\w/])"
    r"|(?<![\w/.])\+\d{9,15}(?!\w)"
)
_YEAR_RE = re.compile(r"(?:19|20)\d\d")

_STREET_WORDS = (
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|"
    r"place|pl|square|sq|highway|hwy|terrace|parkway|pkwy)"
)
ADDRESS_LINE_RE = re.compile(
    r"^[ \t]*(?:(?:home\s+)?address\s*:.*"
    r"|\d{1,5}[a-z]?\s+(?:[A-Za-z0-9.'-]+\s+){0,4}" + _STREET_WORDS + r"\b\.?.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _is_phone(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    if not 9 <= len(digits) <= 15:
        return False
    groups = re.findall(r"\d+", match)
    # "2015-2019 2020-2023" and similar year ranges are not phone numbers.
    return not all(len(g) == 4 and _YEAR_RE.fullmatch(g) for g in groups)


def redact(text: str) -> dict[str, Any]:
    """Return {"text", "redactions": {kind: count}, "github_username"}."""
    counts = {"email": 0, "phone": 0, "url": 0, "address": 0}

    github_username = None
    for m in GITHUB_RE.finditer(text):
        if m.group(1).lower() not in _GITHUB_RESERVED:
            github_username = m.group(1)
            break

    def sub(kind: str, placeholder: str):
        def _repl(m: re.Match) -> str:
            counts[kind] += 1
            return placeholder
        return _repl

    def phone_repl(m: re.Match) -> str:
        if not _is_phone(m.group(0)):
            return m.group(0)
        counts["phone"] += 1
        return "[PHONE]"

    out = ADDRESS_LINE_RE.sub(sub("address", "[ADDRESS]"), text)
    out = EMAIL_RE.sub(sub("email", "[EMAIL]"), out)
    out = URL_RE.sub(sub("url", "[URL]"), out)
    out = PHONE_RE.sub(phone_repl, out)
    return {"text": out, "redactions": counts, "github_username": github_username}
