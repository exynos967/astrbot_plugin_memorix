"""
LLM Service Host Shim.
Bridges src.services.llm_service (LLMServiceClient & llm_api) to AstrBotProviderBridge / Context.llm_generate.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

_active_provider_bridge: Optional[Any] = None
_scoped_provider_bridge: ContextVar[Optional[Any]] = ContextVar("memorix_scoped_provider_bridge", default=None)


@contextmanager
def use_provider_bridge(bridge: Optional[Any]) -> Iterator[None]:
    """在当前异步上下文中绑定作用域级 ProviderBridge，避免并发实例互相覆盖。"""
    token = _scoped_provider_bridge.set(bridge)
    try:
        yield
    finally:
        _scoped_provider_bridge.reset(token)


def set_active_provider_bridge(bridge: Optional[Any]) -> None:
    """设置默认活跃的 AstrBotProviderBridge 实例。"""
    global _active_provider_bridge
    _active_provider_bridge = bridge


def get_scoped_provider_bridge() -> Optional[Any]:
    """仅读取当前异步上下文通过 use_provider_bridge 绑定的 ProviderBridge（不含进程全局回退）。"""
    return _scoped_provider_bridge.get()


def get_active_provider_bridge() -> Optional[Any]:
    """获取当前上下文或全局活跃的 AstrBotProviderBridge 实例。"""
    scoped = _scoped_provider_bridge.get()
    if scoped is not None:
        return scoped
    return _active_provider_bridge




@dataclass
class LLMGenerationOptions:
    temperature: float = 0.2
    max_tokens: int = 1200
    top_p: float = 0.95
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: Optional[list[str]] = None
    response_format: Optional[Dict[str, Any]] = None


@dataclass
class LLMResponseResult:
    """包装单次 LLM 响应结果，向上游消费方暴露 .response 等属性。"""

    response: str = ""
    reasoning: str = ""
    tool_calls: Optional[List[Any]] = None

    @classmethod
    def from_portable_output(cls, *, response: str = "", **kwargs: Any) -> "LLMResponseResult":
        return cls(response=response)


@dataclass
class LLMServiceRequest:
    """LLM 服务层统一请求对象，兼容任意上游关键字参数。"""

    prompt: Optional[str] = None
    task_name: str = "default"
    request_type: str = "text"
    session_id: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    options: Optional[LLMGenerationOptions] = None
    context_factory: Optional[Any] = None
    model_name: Optional[str] = None
    extra_kwargs: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        prompt: Optional[str] = None,
        task_name: str = "default",
        request_type: str = "text",
        session_id: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        options: Optional[LLMGenerationOptions] = None,
        context_factory: Optional[Any] = None,
        model_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.prompt = prompt
        self.task_name = task_name
        self.request_type = request_type
        self.session_id = session_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.options = options
        self.context_factory = context_factory
        self.model_name = model_name
        self.extra_kwargs = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class LLMServiceResult:
    """LLM 服务层统一响应对象。"""

    response: str = ""
    completion_text: str = ""
    success: bool = True
    completion: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.completion_text and self.response:
            self.completion_text = self.response
        elif not self.response and self.completion_text:
            self.response = self.completion_text

        if self.error and not self.response:
            self.success = False

        if self.completion is None:
            self.completion = LLMResponseResult(response=self.response)
        elif isinstance(self.completion, str):
            self.completion = LLMResponseResult(response=self.completion)

    @property
    def text(self) -> str:
        """兼容 LLMResult.text 访问。"""
        return self.response

    @classmethod
    def from_response_result(cls, completion: Any) -> "LLMServiceResult":
        resp_text = getattr(completion, "response", str(completion or ""))
        return cls(
            response=resp_text,
            completion_text=resp_text,
            success=True,
            completion=completion,
            error=None,
        )

    @classmethod
    def from_error(cls, error_message: str, error_detail: Optional[str] = None) -> "LLMServiceResult":
        err = error_detail or error_message
        return cls(
            response="",
            completion_text="",
            success=False,
            completion=LLMResponseResult(response=error_message),
            error=err,
        )


class LLMServiceClient:
    """兼容 MaiBot LLMServiceClient 协议的适配器。"""

    def __init__(self, task_name: str = "", request_type: str = "", session_id: str = "") -> None:
        self.task_name = task_name
        self.request_type = request_type
        self.session_id = session_id

    async def generate_response(self, prompt: str, options: Any = None, **kwargs: Any) -> LLMServiceResult:
        bridge = get_active_provider_bridge()
        if bridge is not None and hasattr(bridge, "generate_text"):
            try:
                temp = getattr(options, "temperature", kwargs.get("temperature", 0.2)) if options else kwargs.get("temperature", 0.2)
                max_t = getattr(options, "max_tokens", kwargs.get("max_tokens", 1200)) if options else kwargs.get("max_tokens", 1200)
                effective_task = str(kwargs.get("task_name") or self.task_name or "").strip()
                try:
                    text = await bridge.generate_text(
                        prompt,
                        temperature=temp,
                        max_tokens=max_t,
                        task_name=effective_task,
                    )
                except TypeError as type_err:
                    if "task_name" not in str(type_err):
                        raise
                    text = await bridge.generate_text(prompt, temperature=temp, max_tokens=max_t)
                return LLMServiceResult(
                    response=str(text or ""),
                    completion_text=str(text or ""),
                    success=True,
                    error=None,
                )
            except Exception as exc:
                return LLMServiceResult(
                    response="",
                    completion_text="",
                    success=False,
                    error=str(exc),
                )

        # 离线或未绑定 bridge 时的安全降级
        return LLMServiceResult(
            response="[]",
            completion_text="[]",
            success=False,
            error="No active provider bridge",
        )


def get_available_models() -> Dict[str, Any]:
    """返回宿主可用文本生成任务映射，桥接 A_Memorix model_routing 到 AstrBotProviderBridge。"""
    bridge = get_active_provider_bridge()
    if bridge is None or not bool(getattr(bridge, "enabled", False)):
        return {}
    from . import config_shim
    from .config_shim import TaskConfig

    model_id = str(getattr(bridge, "chat_provider_id", "") or "astrbot_default").strip() or "astrbot_default"
    fast_id = str(getattr(bridge, "fast_chat_provider_id", "") or "").strip()
    if not fast_id:
        cfg = config_shim.get_effective_config_dict()
        prov_cfg = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        fast_id = str(prov_cfg.get("fast_chat_provider_id") or cfg.get("fast_chat_provider_id") or "").strip()

    tasks: Dict[str, Any] = {
        "memory": TaskConfig(task_name="memory", model=model_id, model_list=[model_id]),
        "utils": TaskConfig(task_name="utils", model=model_id, model_list=[model_id]),
    }
    if fast_id:
        tasks["fast_model"] = TaskConfig(task_name="fast_model", model=fast_id, model_list=[fast_id])
    return tasks


async def generate(request: LLMServiceRequest) -> LLMServiceResult:
    """兼容 llm_api.generate(req) 契约。"""
    client = LLMServiceClient(
        task_name=request.task_name,
        request_type=request.request_type,
        session_id=request.session_id,
    )
    options = request.options
    if options is None and (request.temperature is not None or request.max_tokens is not None):
        options = LLMGenerationOptions(
            temperature=request.temperature if request.temperature is not None else 0.2,
            max_tokens=request.max_tokens if request.max_tokens is not None else 1200,
        )
    return await client.generate_response(request.prompt or "", options=options)

