"""Read-only summary of a public GitHub profile.

Calls the public REST API (`/users/{u}` and `/users/{u}/repos`), with an
optional `GITHUB_TOKEN`, a 10 s timeout and an on-disk cache under
`~/.cache/promptseal/github/`. When `PROMPTSEAL_GITHUB_FIXTURES=<dir>` is set,
reads `<dir>/<username>.json` (`{"user": {...}, "repos": [...]}`, raw API
shapes) instead of the network. Errors are returned as `{"error": ...}`.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.github.com"
TIMEOUT_S = 10.0
CACHE_DIR = Path.home() / ".cache" / "promptseal" / "github"
CACHE_TTL_S = 24 * 3600
FIXTURES_ENV = "PROMPTSEAL_GITHUB_FIXTURES"

USERNAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


def _fetch(username: str) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(base_url=API_BASE, headers=headers, timeout=TIMEOUT_S) as client:
        user = client.get(f"/users/{username}")
        if user.status_code == 404:
            raise LookupError(f"github user not found: {username}")
        user.raise_for_status()
        repos = client.get(f"/users/{username}/repos", params={"sort": "updated", "per_page": 10})
        repos.raise_for_status()
    return {"user": user.json(), "repos": repos.json()}


def _load_raw(username: str) -> dict[str, Any]:
    fixtures = os.getenv(FIXTURES_ENV)
    if fixtures:
        path = Path(fixtures) / f"{username.lower()}.json"
        if not path.is_file():
            raise LookupError(f"github user not found: {username}")
        return json.loads(path.read_text(encoding="utf-8"))

    cache_path = CACHE_DIR / f"{username.lower()}.json"
    if cache_path.is_file() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_S:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    raw = _fetch(username)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw), encoding="utf-8")
    except OSError:
        pass  # the cache is an optimisation only
    return raw


def summarize(raw: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    user = raw.get("user") or {}
    repos = [r for r in raw.get("repos") or [] if isinstance(r, dict)]
    now = now or datetime.now(timezone.utc)
    created = user.get("created_at")
    age_years = None
    if created:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_years = round((now - created_dt).days / 365.25, 1)
    languages = Counter(r["language"] for r in repos if r.get("language"))
    return {
        "username": user.get("login"),
        "public_repos": user.get("public_repos"),
        "followers": user.get("followers"),
        "account_created": created,
        "account_age_years": age_years,
        "top_languages": [lang for lang, _ in languages.most_common(5)],
        "recent_repos": [
            {
                "name": r.get("name"),
                "description": r.get("description"),
                "language": r.get("language"),
                "stars": r.get("stargazers_count"),
                "fork": r.get("fork"),
                "updated_at": r.get("updated_at"),
            }
            for r in repos[:10]
        ],
    }


def profile(username: str) -> dict[str, Any]:
    """Compact public-profile summary, or {"error": ...}. Never raises."""
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        return {"error": f"invalid github username: {username!r}"}
    try:
        return summarize(_load_raw(username))
    except LookupError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # network, HTTP status, bad JSON, bad fixture
        return {"error": f"github lookup failed: {type(exc).__name__}: {exc}"}
