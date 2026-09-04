"""Provider-agnostic LLM access with failover across free tiers.

Groq, Cerebras and OpenRouter all speak the OpenAI chat-completions protocol, so one
client covers all three: only base_url, key and model change. They are tried in the
order listed in PROVIDER_SPECS and the first one that answers wins.

This exists because every free tier here has a hard daily cap (Groq 1k/day, Cerebras
14.4k/day, OpenRouter 50/day under $10 lifetime spend). With a single provider the app
simply stops working partway through a day.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 429 = rate limited, 5xx = upstream capacity. Both transient and worth retrying.
# 400/401/403/404 (bad key, bad model id) are not, and must surface immediately so a
# typo in a model name does not look like an outage.
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

ATTEMPTS_PER_PROVIDER = 2
BASE_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


# Ordered best-first. Groq is fastest and has a workable daily cap. OpenRouter is last
# because 50 requests a day is only a handful of builds.
PROVIDER_SPECS = (
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "GROQ_MODEL", "openai/gpt-oss-120b"),
    ("cerebras", "CEREBRAS_API_KEY", "https://api.cerebras.ai/v1",
     "CEREBRAS_MODEL", "qwen-3.8-27b"),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
)


def load_providers() -> List[Provider]:
    """Every provider whose API key is present, in preference order."""
    found = []
    for name, key_env, base_url, model_env, default_model in PROVIDER_SPECS:
        api_key = os.environ.get(key_env)
        if not api_key:
            continue
        found.append(Provider(
            name=name,
            base_url=base_url,
            api_key=api_key,
            model=os.environ.get(model_env, default_model),
        ))
    return found


def is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in RETRYABLE_STATUS:
        return True
    text = str(exc).upper()
    return any(m in text for m in (
        "RATE LIMIT", "RATE_LIMIT", "TIMEOUT", "TEMPORARILY", "OVERLOADED",
        "UNAVAILABLE", "CONNECTION", "TOO MANY REQUESTS",
    ))


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Gemini enforced a response schema for us. These models do not reliably honour
    response_format, and wrap JSON in prose or markdown fences often enough that
    parsing defensively is a requirement rather than a nicety.
    """
    if not text:
        raise ValueError("empty response")

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced object in the text.
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in response: " + text[:200])

    depth, in_string, escaped = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start:i + 1])

    raise ValueError("unbalanced JSON in response: " + text[:200])


class NoProvidersConfigured(RuntimeError):
    pass


class LLM:
    """Chat completions with per-provider retry and cross-provider failover."""

    def __init__(self, providers: Optional[List[Provider]] = None):
        self.providers = providers if providers is not None else load_providers()
        if not self.providers:
            raise NoProvidersConfigured(
                "Set at least one of GROQ_API_KEY, CEREBRAS_API_KEY, OPENROUTER_API_KEY"
            )
        logger.info("LLM providers: %s", ", ".join(
            p.name + "(" + p.model + ")" for p in self.providers))

    async def _run(self, label: str, call: Callable[[AsyncOpenAI, Provider], Awaitable]):
        last_exc = None
        for provider in self.providers:
            client = AsyncOpenAI(api_key=provider.api_key, base_url=provider.base_url)
            delay = BASE_BACKOFF_SECONDS
            for attempt in range(1, ATTEMPTS_PER_PROVIDER + 1):
                try:
                    return await call(client, provider)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "%s failed on %s/%s (attempt %d/%d): %s",
                        label, provider.name, provider.model,
                        attempt, ATTEMPTS_PER_PROVIDER, exc,
                    )
                    # A bad key or a bad model name will not fix itself on retry.
                    if not is_retryable(exc):
                        break
                    if attempt < ATTEMPTS_PER_PROVIDER:
                        await asyncio.sleep(delay)
                        delay *= 2
            logger.warning("%s: giving up on %s, trying next provider", label, provider.name)
        raise RuntimeError(label + ": all providers failed (" + str(last_exc) + ")")

    async def json(self, system: str, user: str, label: str,
                   max_tokens: int = 2048) -> dict:
        async def call(client: AsyncOpenAI, provider: Provider):
            resp = await client.chat.completions.create(
                model=provider.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=0.4,
            )
            return extract_json(resp.choices[0].message.content or "")

        return await self._run(label, call)

    async def stream(self, system: str, user: str, label: str,
                     on_delta: Callable[[str], Awaitable[None]],
                     max_tokens: int = 8192) -> str:
        """Stream a completion, calling on_delta with text as it arrives.

        The whole stream is retried as one unit. Resuming a half-finished stream would
        splice two different generations into one broken document.
        """
        async def call(client: AsyncOpenAI, provider: Provider):
            collected, batch, batch_len = [], [], 0
            stream = await client.chat.completions.create(
                model=provider.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.6,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                collected.append(delta)
                batch.append(delta)
                batch_len += len(delta)
                # Batch tokens so a fast model does not flood the websocket.
                if batch_len >= 40:
                    await on_delta("".join(batch))
                    batch, batch_len = [], 0
            if batch:
                await on_delta("".join(batch))
            return "".join(collected)

        return await self._run(label, call)
