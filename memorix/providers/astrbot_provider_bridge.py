"""Adapters that bridge Memorix with AstrBot native providers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple


def _extract_provider_id(provider: Any) -> str:
    """Read the ID from AstrBot's Provider.meta() contract."""
    if provider is None:
        return ""
    return str(provider.meta().id or "").strip()


class AstrBotProviderBridge:
    """AstrBot Context bridge for provider selection and invocation."""

    def __init__(
        self,
        *,
        astrbot_context: Any,
        chat_provider_id: str = "",
    ) -> None:
        self._context = astrbot_context
        self.chat_provider_id = str(chat_provider_id or "").strip()

    @property
    def enabled(self) -> bool:
        return self._context is not None

    async def resolve_chat_provider_id(self, unified_msg_origin: str = "") -> str:
        if self.chat_provider_id:
            return self.chat_provider_id
        provider = self._context.get_using_provider(unified_msg_origin or None)
        return _extract_provider_id(provider)

    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        unified_msg_origin: str = "",
    ) -> str:
        ctx = self._context
        if ctx is None:
            raise RuntimeError("AstrBot context is not available")

        provider_id = await self.resolve_chat_provider_id(unified_msg_origin)
        if not provider_id:
            raise RuntimeError("chat provider is not configured")

        resp = await ctx.llm_generate(
            chat_provider_id=provider_id,
            prompt=str(prompt or ""),
            temperature=float(temperature),
            max_tokens=int(max_tokens),
        )
        return str(resp.completion_text or "")


class AstrBotLLMClient:
    """Summary-compatible chat client backed by AstrBot llm_generate."""

    def __init__(self, *, provider_bridge: AstrBotProviderBridge, max_retries: int = 3):
        self.provider_bridge = provider_bridge
        self.max_retries = max(1, int(max_retries))

    async def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        unified_msg_origin: str = "",
    ) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self.provider_bridge.generate_text(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    unified_msg_origin=unified_msg_origin,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
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
        unified_msg_origin: str = "",
    ) -> Tuple[bool, Dict[str, Any], str]:
        text = await self.complete(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            unified_msg_origin=unified_msg_origin,
        )
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
