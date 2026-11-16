"""A_Memorix 内部模型任务选择工具。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from src.common.logger import get_logger
from src.common.data_models.llm_service_data_models import LLMServiceResult
from src.services import llm_service as llm_api

logger = get_logger("A_Memorix.ModelRouting")

NON_TEXT_GENERATION_TASK_NAMES = {"embedding", "voice", "vlm"}
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


@dataclass(frozen=True)
class LLMResult:
    """文本生成统一结果。"""

    success: bool
    text: str = ""
    error: str = ""


@dataclass(frozen=True)
class ResolvedLLMModel:
    """A_Memorix 内部使用的 LLM 选择结果。"""

    task_name: str
    task_config: Any
    selected_model_name: str = ""

    @property
    def is_single_model(self) -> bool:
        return bool(self.selected_model_name)


def task_has_model_list(task_config: Any) -> bool:
    """判断任务配置是否有可用模型候选。"""

    model_list = getattr(task_config, "model_list", [])
    return any(str(model_name).strip() for model_name in (model_list or []))


def is_text_generation_task_name(task_name: str) -> bool:
    """判断任务名是否适合 A_Memorix 的普通文本生成调用。"""

    return str(task_name or "").strip().lower() not in NON_TEXT_GENERATION_TASK_NAMES


def get_text_generation_model_tasks(llm_api: Any, *, include_empty: bool = False) -> Dict[str, Any]:
    """从宿主 LLM API 中读取 A_Memorix 可用的文本生成任务配置。"""

    models = getattr(llm_api, "get_available_models", lambda: {})() or {}
    return {
        task_name: task_config
        for task_name, task_config in models.items()
        if is_text_generation_task_name(task_name) and (include_empty or task_has_model_list(task_config))
    }


def _iter_preferred_task_names(available_tasks: Dict[str, Any], preferred: Iterable[str]) -> Iterable[str]:
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
    available_tasks: Dict[str, Any],
    model_name: str,
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
        max_tokens=getattr(template, "max_tokens", None),
        temperature=getattr(template, "temperature", None),
        selection_strategy=getattr(template, "selection_strategy", None),
        hard_timeout=getattr(template, "hard_timeout", None),
    )


def resolve_text_generation_model_selector(
    available_tasks: Dict[str, Any],
    selector: str,
) -> Tuple[Optional[str], Optional[Any], str]:
    """解析任务名或具体模型名选择器。"""

    normalized_selector = str(selector or "").strip()
    if not normalized_selector or normalized_selector.lower() == "auto":
        return None, None, ""

    task_config = available_tasks.get(normalized_selector)
    if task_has_model_list(task_config):
        return normalized_selector, task_config, ""

    task_name, task_config = find_text_generation_task_for_model(available_tasks, normalized_selector)
    if task_name and task_config:
        return task_name, build_single_model_task(normalized_selector, task_config), normalized_selector
    return None, None, ""


def resolve_llm_client(ctx: Any) -> Any:
    """本土化核心：只走 AstrBot provider bridge。"""
    bridge = getattr(ctx, "provider_bridge", None) if ctx is not None else None
    if bridge is not None and bool(getattr(bridge, "enabled", False)):
        from ...providers.astrbot_provider_bridge import AstrBotLLMClient

        try:
            return AstrBotLLMClient(provider_bridge=bridge)
        except Exception as exc:
            raise RuntimeError(f"启用 AstrBotLLMClient 失败: {exc}") from exc

    raise RuntimeError("AstrBot provider bridge is not available")


async def generate_text(
    ctx: Any,
    prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1200,
    request_type: str = "",
    unified_msg_origin: str = "",
) -> LLMResult:
    try:
        client = resolve_llm_client(ctx)
        text = await client.complete(
            str(prompt or ""),
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            unified_msg_origin=str(unified_msg_origin or ""),
        )
        text = str(text or "")
        if not text:
            return LLMResult(success=False, text="", error="empty_llm_response")
        return LLMResult(success=True, text=text)
    except Exception as exc:
        tag = f"[{request_type}] " if request_type else ""
        logger.error(f"{tag}文本生成失败: {exc}")
        return LLMResult(success=False, text="", error=str(exc))


async def generate_text_with_model(
    model: ResolvedLLMModel,
    prompt: str,
    *,
    request_type: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMServiceResult:
    """按 A_Memorix 解析出的模型执行文本生成。"""

    if not model.is_single_model:
        return await llm_api.generate(
            llm_api.LLMServiceRequest(
                task_name=model.task_name,
                request_type=request_type,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

    client = llm_api.LLMServiceClient(task_name=model.task_name, request_type=request_type)
    if hasattr(client, "_orchestrator"):
        client._orchestrator.model_for_task = model.task_config
        client._orchestrator.model_usage = {model.selected_model_name: (0, 0, 0)}

        def _refresh_single_model_task() -> Any:
            client._orchestrator.model_for_task = model.task_config
            client._orchestrator.model_usage = {
                model.selected_model_name: client._orchestrator.model_usage.get(
                    model.selected_model_name,
                    (0, 0, 0),
                )
            }
            return model.task_config

        client._orchestrator._refresh_task_config = _refresh_single_model_task

    try:
        completion = await client.generate_response(
            prompt=prompt,
            options=llm_api.LLMGenerationOptions(
                temperature=temperature or 0.2,
                max_tokens=max_tokens or 1200,
            ),
        )
        if hasattr(llm_api.LLMServiceResult, "from_response_result"):
            return llm_api.LLMServiceResult.from_response_result(completion)
        return completion
    except Exception as exc:
        error_message = f"生成内容时出错: {exc}"
        logger.error(f"[A_Memorix.ModelRouting] {error_message}")
        if hasattr(llm_api.LLMServiceResult, "from_error"):
            return llm_api.LLMServiceResult.from_error(error_message, str(exc))
        return LLMServiceResult(response="", error=str(exc))


async def generate_with_resolved_model(
    model: ResolvedLLMModel,
    *args: Any,
    request_type: str = "",
    prompt: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> LLMServiceResult:
    """兼容旧版/上游参数签名的文本生成接口。"""

    if len(args) == 1:
        if "prompt" in kwargs:
            request_type = str(args[0] or "")
        else:
            prompt = str(args[0] or "")
    elif len(args) >= 2:
        request_type = str(args[0] or "")
        prompt = str(args[1] or "")

    if "request_type" in kwargs:
        request_type = str(kwargs["request_type"] or "")
    if "prompt" in kwargs:
        prompt = str(kwargs["prompt"] or "")
    if "temperature" in kwargs:
        temperature = kwargs["temperature"]
    if "max_tokens" in kwargs:
        max_tokens = kwargs["max_tokens"]

    return await generate_text_with_model(
        model,
        prompt=prompt,
        request_type=request_type,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def resolve_fast_text_generation_model(llm_api: Any) -> ResolvedLLMModel:
    """轻量任务只选择快速模型或 utils，避免落到记忆总结、规划等任务。"""
    available_tasks = get_text_generation_model_tasks(llm_api)
    for task_name in ("fast_model", "utils"):
        task_config = available_tasks.get(task_name)
        if task_config is not None and task_has_model_list(task_config):
            return ResolvedLLMModel(task_name=task_name, task_config=task_config)
    raise RuntimeError("快速记忆任务需要配置 fast_model 或 utils 模型")


def resolve_default_text_generation_task(llm_api: Any) -> str:
    """解析默认文本生成任务，避免宿主默认值落到 embedding。"""

    available_tasks = get_text_generation_model_tasks(llm_api)
    task_name, _ = pick_text_generation_task(available_tasks)
    if not task_name:
        raise RuntimeError("没有可用的文本生成模型配置")
    return task_name


def resolve_text_generation_task_name_from_model_config(
    llm_api: Any,
    model_config: Any,
    *,
    preferred_task_name: str = "",
) -> str:
    """根据旧版 TaskConfig 对象解析文本生成任务名。"""

    available_tasks = get_text_generation_model_tasks(llm_api)
    if not available_tasks:
        raise RuntimeError("没有可用的文本生成模型配置")

    normalized_preferred = str(preferred_task_name or "").strip()
    if normalized_preferred and normalized_preferred in available_tasks:
        return normalized_preferred

    for task_name, task_config in available_tasks.items():
        if task_config is model_config:
            return task_name

    requested_model_list = [
        str(item).strip() for item in (getattr(model_config, "model_list", []) or []) if str(item).strip()
    ]
    if requested_model_list:
        for task_name, task_config in available_tasks.items():
            candidate_model_list = [
                str(item).strip() for item in (getattr(task_config, "model_list", []) or []) if str(item).strip()
            ]
            if requested_model_list == candidate_model_list:
                return task_name

        for requested_model in requested_model_list:
            task_name, _ = find_text_generation_task_for_model(available_tasks, requested_model)
            if task_name:
                logger.info(f"旧版文本生成 model_config 按模型 `{requested_model}` 近似映射到任务 `{task_name}`")
                return task_name

    fallback_task_name, _ = pick_text_generation_task(available_tasks)
    if fallback_task_name:
        if normalized_preferred:
            logger.warning(f"无法映射文本生成 model_config，回退默认任务: preferred={normalized_preferred}")
        return fallback_task_name
    raise RuntimeError("没有可用的文本生成模型配置")
