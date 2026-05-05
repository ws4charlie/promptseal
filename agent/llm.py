"""Build a chat-LLM client with priority: OpenAI direct → Bifrost → Anthropic direct.

Bifrost is the company's internal LLM gateway (OpenAI-compatible /v1, HTTP
Basic auth, model prefix "anthropic/<model>"). Direct OpenAI and direct
Anthropic are diagnostic/fallback paths.

Selection rule (first match wins):
1. If OPENAI_API_KEY is set → ChatOpenAI against api.openai.com with the
   hardcoded model "gpt-4o-mini" (the caller-supplied `model` is ignored
   because OpenAI doesn't recognize Claude model strings). Diagnostic path
   for verifying the callback handler against a real LLM without Bifrost.
2. Else if BIFROST_BASE_URL + BIFROST_USERNAME + BIFROST_PASSWORD are all
   set → ChatOpenAI through Bifrost. Model gets the "anthropic/" prefix.
3. Else if ANTHROPIC_API_KEY is set → ChatAnthropic direct.
4. Else raise with a clear message.
"""
from __future__ import annotations

import base64
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

OPENAI_DIAGNOSTIC_MODEL = "gpt-4o-mini"


def _bifrost_configured() -> bool:
    return bool(
        os.getenv("BIFROST_BASE_URL")
        and os.getenv("BIFROST_USERNAME")
        and os.getenv("BIFROST_PASSWORD")
    )


def make_chat_llm(*, model: str, temperature: float) -> BaseChatModel:
    """Return a chat-LLM client by env-var priority. See module docstring."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        # Diagnostic / fallback path. The caller-supplied `model` is ignored
        # on this branch — OpenAI's API rejects Claude model identifiers.
        return ChatOpenAI(
            model=OPENAI_DIAGNOSTIC_MODEL,
            temperature=temperature,
            api_key=openai_api_key,
        )

    if _bifrost_configured():
        base_url = os.environ["BIFROST_BASE_URL"]
        user = os.environ["BIFROST_USERNAME"]
        pwd = os.environ["BIFROST_PASSWORD"]
        timeout = float(os.getenv("BIFROST_TIMEOUT", "60"))
        creds = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
        return ChatOpenAI(
            model=f"anthropic/{model}",
            temperature=temperature,
            api_key="bifrost-placeholder",  # SDK requires a non-empty value; real auth is in headers
            base_url=base_url,
            default_headers={"Authorization": f"Basic {creds}"},
            timeout=timeout,
        )

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        return ChatAnthropic(model=model, temperature=temperature, api_key=anthropic_key)

    raise RuntimeError(
        "No LLM credentials configured. Set one of:\n"
        "  - OPENAI_API_KEY (diagnostic / fallback, uses gpt-4o-mini)\n"
        "  - BIFROST_BASE_URL + BIFROST_USERNAME + BIFROST_PASSWORD (preferred)\n"
        "  - ANTHROPIC_API_KEY (direct Anthropic API)\n"
        "in your .env or shell environment."
    )
