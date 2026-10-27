"""Build a chat-LLM client with priority: OpenRouter → OpenAI direct → Bifrost → Anthropic direct.

OpenRouter is an OpenAI-compatible gateway that serves the requested model
(Claude, GPT, Gemini) under its own slugs; it is the only path that both
honours the caller's `model` and works with one key for every provider.
Bifrost is an optional OpenAI-compatible LLM gateway (/v1, HTTP
Basic auth, model prefix "anthropic/<model>"). Direct OpenAI and direct
Anthropic are diagnostic/fallback paths.

Selection rule (first match wins):
0. If OPENROUTER_API_KEY is set → ChatOpenAI against openrouter.ai with the
   caller's `model` mapped to an OpenRouter slug (`openrouter_model`). The
   response metadata's `model_name` is the model OpenRouter actually served.
1. Else if OPENAI_API_KEY is set → ChatOpenAI against api.openai.com with the
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

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Model ids used in this repo → OpenRouter slugs (verified against
# GET https://openrouter.ai/api/v1/models on 2026-10-26). Ids that already
# contain a "/" are treated as OpenRouter slugs and passed through.
OPENROUTER_MODEL_SLUGS = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "gpt-4o-mini": "openai/gpt-4o-mini",
}


def openrouter_model(model: str) -> str:
    """Map a model id to its OpenRouter slug; unknown ids pass through unchanged."""
    if "/" in model:
        return model
    return OPENROUTER_MODEL_SLUGS.get(model, model)


def _bifrost_configured() -> bool:
    return bool(
        os.getenv("BIFROST_BASE_URL")
        and os.getenv("BIFROST_USERNAME")
        and os.getenv("BIFROST_PASSWORD")
    )


def make_chat_llm(*, model: str, temperature: float) -> BaseChatModel:
    """Return a chat-LLM client by env-var priority. See module docstring."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return ChatOpenAI(
            model=openrouter_model(model),
            temperature=temperature,
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=float(os.getenv("OPENROUTER_TIMEOUT", "60")),
        )

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
        "  - OPENROUTER_API_KEY (any provider, honours the requested model)\n"
        "  - OPENAI_API_KEY (diagnostic / fallback, uses gpt-4o-mini)\n"
        "  - BIFROST_BASE_URL + BIFROST_USERNAME + BIFROST_PASSWORD (preferred)\n"
        "  - ANTHROPIC_API_KEY (direct Anthropic API)\n"
        "in your .env or shell environment."
    )
