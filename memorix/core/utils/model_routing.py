"""A_Memorix 文本生成模型路由（插件本土化版）。

插件文本生成统一复用 AstrBot 已配置的 provider
（``ctx.provider_bridge`` → ``AstrBotLLMClient``）。

为兼容既有消费方（episode_segmentation / retrieval_tuning / web_import_manager），
保留 ``ResolvedLLMModel`` 占位与 ``generate_with_resolved_model`` 薄包装；新增
``generate_text(ctx, prompt)`` 作为本土化主入口，统一返回 ``LLMResult``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

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


# A_Memorix 文本生成任务优先级（与上游一致，供 pick_text_generation_task 默认偏好）。
A_MEMORIX_TEXT_TASK_PRIORITY = (
    "memory",
    "utils",
    "lpmm_entity_extract",
    "lpmm_rdf_build",
    "planner",
    "replyer",
    "learner",
    "emoji",
    "tool_use",
)


def task_has_model_list(task_config: Any) -> bool:
    """判断任务配置是否有可用模型候选。"""

    model_list = getattr(task_config, "model_list", [])
    return any(str(model_name).strip() for model_name in (model_list or []))


def get_text_generation_model_tasks(llm_api: Any, *, include_empty: bool = False) -> Dict[str, Any]:
    """从宿主 LLM API 中读取 A_Memorix 可用的文本生成任务配置。

    本土化兼容入口：插件单模型下 ``llm_api`` 为软导入桩（可能为 None 或 RecursiveStub），
    非真实宿主服务时返回空 dict，由调用方决定降级（如 web_import_manager._select_model
    抛 RuntimeError 或回退到 generate_text(ctx, prompt) 路径）。
    """

    if llm_api is None:
        return {}
    get_available_models = getattr(llm_api, "get_available_models", None)
    if not callable(get_available_models):
        return {}
    try:
        models = get_available_models() or {}
    except Exception:  # pragma: no cover - 桩对象调用兜底
        return {}
    if not isinstance(models, dict):
        return {}
    return {
        task_name: task_config
        for task_name, task_config in models.items()
        if is_text_generation_task_name(task_name)
        and (include_empty or task_has_model_list(task_config))
    }


def _iter_preferred_task_names(
    available_tasks: Dict[str, Any], preferred: Iterable[str]
) -> Iterable[str]:
    yielded: set[str] = set()
    for task_name in preferred:
        if task_name in available_tasks:
            yielded.add(task_name)
            yield task_name
    for task_name in available_tasks:
        if task_name not in yielded:
            yield task_name


def pick_text_generation_task(
    available_tasks: Dict[str, Any],
    preferred: Iterable[str] = A_MEMORIX_TEXT_TASK_PRIORITY,
) -> Tuple[Optional[str], Optional[Any]]:
    """按 A_Memorix 优先级选择文本生成任务。"""

    for task_name in _iter_preferred_task_names(available_tasks, preferred):
        task_config = available_tasks.get(task_name)
        if task_has_model_list(task_config):
            return task_name, task_config
    return None, None


def find_text_generation_task_for_model(
    available_tasks: Dict[str, Any], model_name: str
) -> Tuple[Optional[str], Optional[Any]]:
    """按模型名查找其所属的文本生成任务。"""

    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        return None, None
    for task_name, task_config in available_tasks.items():
        model_list = getattr(task_config, "model_list", []) or []
        task_models = [str(item).strip() for item in model_list if str(item).strip()]
        if normalized_model_name in task_models:
            return task_name, task_config
    return None, None


def build_single_model_task(model_name: str, template: Any) -> Any:
    """基于现有任务模板构造只包含单个文本生成模型的任务配置。"""

    return type(template)(
        model_list=[model_name],
        max_tokens=template.max_tokens,
        temperature=template.temperature,
        slow_threshold=template.slow_threshold,
        selection_strategy=template.selection_strategy,
        hard_timeout=template.hard_timeout,
    )


def resolve_text_generation_model_selector(
    available_tasks: Dict[str, Any], selector: str
) -> Tuple[Optional[str], Optional[Any], str]:
    """解析任务名或具体模型名选择器。"""

    normalized_selector = str(selector or "").strip()
    if not normalized_selector or normalized_selector.lower() == "auto":
        return None, None, ""

    task_config = available_tasks.get(normalized_selector)
    if task_has_model_list(task_config):
        return normalized_selector, task_config, ""

    task_name, task_config = find_text_generation_task_for_model(
        available_tasks, normalized_selector
    )
    if task_name and task_config:
        return task_name, build_single_model_task(normalized_selector, task_config), normalized_selector
    return None, None, ""


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
    temperature=..., max_tokens=...)`` 需 ctx 才能走 provider_bridge；调用方未传
    ctx 时返回失败结果。
    """

    tag = getattr(model, "task_name", "") or ""
    return await generate_text(
        ctx,
        prompt,
        temperature=0.2 if temperature is None else float(temperature),
        max_tokens=1200 if max_tokens is None else int(max_tokens),
        request_type=request_type or tag,
    )
