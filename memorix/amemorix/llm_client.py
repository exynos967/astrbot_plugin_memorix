"""OpenAI-compatible chat completion client."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

from openai import AsyncOpenAI

from .common.logging import get_logger

logger = get_logger("A_Memorix.LLMClient")


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
    ):
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout_seconds = float(timeout_seconds or 60.0)
        self.max_retries = max(1, int(max_retries))
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if not self.base_url:
            raise RuntimeError("OpenAI-compatible chat base_url is not configured")
        if not self.model:
            raise RuntimeError("OpenAI-compatible chat model is not configured")
        if self._client is None:
            kwargs = {
                "api_key": self.api_key or "EMPTY",
                "timeout": self.timeout_seconds,
                "max_retries": 0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_client()
                resp = await client.chat.completions.create(
                    model=self.model,
                    temperature=float(temperature),
                    max_tokens=int(max_tokens),
                    messages=[{"role": "user", "content": prompt}],
                )
                choice = resp.choices[0] if resp.choices else None
                if not choice:
                    return ""
                return str(choice.message.content or "")
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(min(6.0, 2 ** (attempt - 1)))
        if last_exc is not None:
            raise last_exc
        return ""

    async def complete_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> Tuple[bool, Dict[str, Any], str]:
        text = await self.complete(prompt, temperature=temperature, max_tokens=max_tokens)
        if not text:
            return False, {}, ""
        raw = text.strip()
        try:
            return True, json.loads(raw), raw
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    return True, json.loads(raw[start : end + 1]), raw
                except json.JSONDecodeError:
                    pass
        return False, {}, raw
