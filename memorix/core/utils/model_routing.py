"""A_Memorix 文本生成模型路由（插件本土化版）。

插件文本生成统一复用 AstrBot 已配置的 provider
（``ctx.provider_bridge`` → ``AstrBotLLMClient``），并通过
``generate_text(ctx, prompt)`` 统一返回 ``LLMResult``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...amemorix.common.logging import get_logger

logger = get_logger("A_Memorix.ModelRouting")

@dataclass(frozen=True)
class LLMResult:
    """文本生成统一结果。"""

    success: bool
    text: str = ""
    error: str = ""


def resolve_llm_client(ctx: Any) -> Any:
    """本土化核心：只走 AstrBot provider bridge。

    返回的对象需提供 ``async complete(prompt, *, temperature, max_tokens) -> str``，
    ``AstrBotLLMClient`` 满足该接口，下游零分支。
    """

    bridge = getattr(ctx, "provider_bridge", None) if ctx is not None else None
    if bridge is not None and bool(getattr(bridge, "enabled", False)):
        # 延迟导入避免与 providers 包形成环依赖。
        from ...providers.astrbot_provider_bridge import AstrBotLLMClient

        try:
            return AstrBotLLMClient(provider_bridge=bridge)
        except Exception as exc:  # pragma: no cover - 构造保护
            raise RuntimeError(f"启用 AstrBotLLMClient 失败: {exc}") from exc

    raise RuntimeError("AstrBot provider bridge is not available")


async def generate_text(
    ctx: Any,
    prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1200,
    request_type: str = "",
) -> LLMResult:
    """本土化主入口：解析客户端并执行一次文本生成，统一返回 LLMResult。"""

    try:
        client = resolve_llm_client(ctx)
        text = await client.complete(
            str(prompt or ""),
            temperature=float(temperature),
            max_tokens=int(max_tokens),
        )
        text = str(text or "")
        if not text:
            return LLMResult(success=False, text="", error="empty_llm_response")
        return LLMResult(success=True, text=text)
    except Exception as exc:
        tag = f"[{request_type}] " if request_type else ""
        logger.error(f"{tag}文本生成失败: {exc}")
        return LLMResult(success=False, text="", error=str(exc))

