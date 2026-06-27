"""A_Memorix 文本生成模型路由（插件本土化版）。

插件只有单模型 LLM，统一走 ``LLMClient``。本土化策略——优先复用 AstrBot 已配置的
provider（``ctx.provider_bridge`` → ``AstrBotLLMClient``），降级到环境变量驱动的
``LLMClient``。

为兼容既有消费方（episode_segmentation / retrieval_tuning / web_import_manager），
保留 ``ResolvedLLMModel`` 占位与 ``generate_with_resolved_model`` 薄包装；新增
``generate_text(ctx, prompt)`` 作为本土化主入口，统一返回 ``LLMResult``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ...amemorix.common.logging import get_logger

logger = get_logger("A_Memorix.ModelRouting")

# 非文本生成任务名集合，仅保留供 is_text_generation_task_name 复用。
NON_TEXT_GENERATION_TASK_NAMES = {"embedding", "voice", "vlm"}


@dataclass(frozen=True)
class LLMResult:
    """文本生成统一结果。"""

    success: bool
    text: str = ""
    error: str = ""


@dataclass(frozen=True)
class ResolvedLLMModel:
    """兼容占位：插件单模型下无多任务编排，仅保留 task_name 供日志。

    保留该类是为了让 episode_segmentation / retrieval_tuning / web_import_manager
    既有调用形态（``generate_with_resolved_model(resolved_model, ...)``）不必大改；
    其 ``task_config`` 在插件中恒为 None，不再承载编排逻辑。
    """

    task_name: str = "memory"
    task_config: Any = None
    selected_model_name: str = ""

    @property
    def is_single_model(self) -> bool:
        return bool(self.selected_model_name)


def is_text_generation_task_name(task_name: str) -> bool:
    """判断任务名是否适合 A_Memorix 的普通文本生成调用。"""
    return str(task_name or "").strip().lower() not in NON_TEXT_GENERATION_TASK_NAMES


def resolve_llm_client(ctx: Any) -> Any:
    """本土化核心：优先 AstrBot provider bridge，降级到 env LLMClient。

    返回的对象需提供 ``async complete(prompt, *, temperature, max_tokens) -> str``，
    ``LLMClient`` 与 ``AstrBotLLMClient`` 均满足该接口，下游零分支。
    """

    bridge = getattr(ctx, "provider_bridge", None) if ctx is not None else None
    if bridge is not None and bool(getattr(bridge, "enabled", False)):
        # 延迟导入避免与 providers 包形成环依赖。
        from ...providers.astrbot_provider_bridge import AstrBotLLMClient

        try:
            return AstrBotLLMClient(provider_bridge=bridge)
        except Exception as exc:  # pragma: no cover - 降级保护
            logger.warning(f"启用 AstrBotLLMClient 失败，降级 env LLMClient: {exc}")
    client = getattr(ctx, "llm_client", None)
    if client is None:
        from ...amemorix.llm_client import LLMClient

        client = LLMClient()
    return client


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


async def generate_with_resolved_model(
    model: Optional[ResolvedLLMModel],
    request_type: str,
    prompt: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    ctx: Any = None,
) -> LLMResult:
    """兼容旧消费方的薄包装：忽略多模型编排，转调 generate_text。

    旧调用形态 ``generate_with_resolved_model(resolved_model, request_type, prompt,
    temperature=..., max_tokens=...)`` 需 ctx 才能走 provider_bridge；若调用方未传
    ctx，则降级用 env LLMClient。
    """

    tag = getattr(model, "task_name", "") or ""
    return await generate_text(
        ctx,
        prompt,
        temperature=0.2 if temperature is None else float(temperature),
        max_tokens=1200 if max_tokens is None else int(max_tokens),
        request_type=request_type or tag,
    )
