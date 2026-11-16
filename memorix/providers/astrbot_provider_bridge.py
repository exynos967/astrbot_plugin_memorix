"""Adapters that bridge Memorix with AstrBot native providers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple


class AstrBotProviderBridge:
    """AstrBot Context bridge for provider selection and invocation."""

    def __init__(
        self,
        *,
        astrbot_context: Any,
        chat_provider_id: str = "",
        fast_chat_provider_id: str = "",
    ) -> None:
        self._context = astrbot_context
        self.chat_provider_id = str(chat_provider_id or "").strip()
        self.fast_chat_provider_id = str(fast_chat_provider_id or "").strip()

    @property
    def enabled(self) -> bool:
        return self._context is not None

    async def resolve_provider_id_for_task(
        self,
        task_name: str = "",
        unified_msg_origin: str = "",
    ) -> str:
        clean_task = str(task_name or "").strip()
        if clean_task == "fast_model" and self.fast_chat_provider_id:
            return self.fast_chat_provider_id
        return await self.resolve_chat_provider_id(unified_msg_origin)

    async def resolve_chat_provider_id(self, unified_msg_origin: str = "") -> str:
        if self.chat_provider_id:
            return self.chat_provider_id
        ctx = self._context
        if ctx is None:
            return ""
        get_current = getattr(ctx, "get_current_chat_provider_id", None)
        if callable(get_current):
            provider_id = await get_current(unified_msg_origin)
            if provider_id:
                return str(provider_id)
        get_using_async = getattr(ctx, "get_using_provider_async", None)
        if callable(get_using_async):
            provider = await get_using_async(unified_msg_origin)
            if provider is not None:
                meta_fn = getattr(provider, "meta", None)
                if callable(meta_fn):
                    meta = meta_fn()
                    meta_id = getattr(meta, "id", "")
                    if meta_id:
                        return str(meta_id)
                prov_id = getattr(provider, "provider_id", "") or getattr(provider, "id", "")
                if prov_id:
                    return str(prov_id)
        get_using_sync = getattr(ctx, "get_using_provider", None)
        if callable(get_using_sync):
            provider = get_using_sync(unified_msg_origin)
            if provider is not None:
                prov_id = getattr(provider, "provider_id", "") or getattr(provider, "id", "")
                if prov_id:
                    return str(prov_id)
        return ""


    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        unified_msg_origin: str = "",
        task_name: str = "",
    ) -> str:
        ctx = self._context
        if ctx is None:
            raise RuntimeError("AstrBot context is not available")

        provider_id = await self.resolve_provider_id_for_task(task_name, unified_msg_origin)
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
        task_name: str = "",
    ) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                try:
                    return await self.provider_bridge.generate_text(
                        prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        unified_msg_origin=unified_msg_origin,
                        task_name=task_name,
                    )
                except TypeError as type_err:
                    if "task_name" not in str(type_err):
                        raise
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
        task_name: str = "",
    ) -> Tuple[bool, Dict[str, Any], str]:
        text = await self.complete(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            unified_msg_origin=unified_msg_origin,
            task_name=task_name,
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
