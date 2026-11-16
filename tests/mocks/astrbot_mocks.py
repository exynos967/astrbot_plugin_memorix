"""AstrBot offline mock classes and test harnesses."""

from __future__ import annotations

import time
import types
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext


class MockPlain:
    """Mock for AstrBot Plain text message component."""

    def __init__(self, text: str):
        self.text = str(text)
        self.type = "plain"


class MockAt:
    """Mock for AstrBot At component."""

    def __init__(self, qq: str, name: str = ""):
        self.qq = str(qq)
        self.name = str(name)
        self.type = "at"


class MockReply:
    """Mock for AstrBot Reply component."""

    def __init__(self, sender_id: str, sender_nickname: str = ""):
        self.sender_id = str(sender_id)
        self.qq = str(sender_id)
        self.sender_nickname = str(sender_nickname)
        self.type = "reply"


class MockImage:
    """Mock for AstrBot Image component."""

    def __init__(self, url: str = "", path: str = ""):
        self.url = str(url)
        self.path = str(path)
        self.file = str(path or url)
        self.type = "image"


class MockTextPart(TextPart):
    """Offline test representation of TextPart with temporary context marker."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text
        self._no_save = False

    def mark_as_temp(self) -> MockTextPart:
        self._no_save = True
        return self

    def model_dump_for_context(self) -> dict[str, Any]:
        data = {"type": "text", "text": self.text}
        if self._no_save:
            data["_no_save"] = True
        return data


class MockProviderRequest:
    """Mock for AstrBot ProviderRequest in pipeline interception."""

    def __init__(
        self,
        prompt: Optional[str] = None,
        system_prompt: str = "You are a helpful assistant.",
        contexts: Optional[List[Dict[str, Any]]] = None,
        extra_user_content_parts: Optional[List[Any]] = None,
    ):
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.contexts = contexts or []
        self.extra_user_content_parts = (
            extra_user_content_parts if extra_user_content_parts is not None else []
        )
        self.func_tool = None


class MockLLMResponse:
    """Mock for AstrBot LLMResponse."""

    def __init__(
        self,
        role: str = "assistant",
        completion_text: str = "Mocked offline LLM response.",
        result_chain: Any = None,
        tools_call_name: Optional[str] = None,
        tools_call_args: Optional[Dict[str, Any]] = None,
    ):
        self.role = role
        self.completion_text = completion_text
        self.result_chain = result_chain
        self.tools_call_name = tools_call_name
        self.tools_call_args = tools_call_args or {}


class MockAstrMessageEvent(AstrMessageEvent):
    """Full-featured offline mock for AstrMessageEvent with full introspection."""

    def __init__(
        self,
        message_str: str = "Hello",
        sender_id: str = "u_1001",
        sender_name: str = "TestUser",
        group_id: str = "g_2001",
        platform_name: str = "aiocqhttp",
        platform_id: str = "qq_main",
        session_id: Optional[str] = None,
        is_private: bool = False,
        raw_message: Optional[Dict[str, Any]] = None,
        components: Optional[List[Any]] = None,
        self_id: str = "bot_999",
        is_admin: bool = False,
        role: str = "member",
    ):
        super().__init__()
        self._message_str = str(message_str)
        self._sender_id = str(sender_id)
        self._sender_name = str(sender_name)
        self._group_id = "" if is_private else str(group_id)
        self._platform_name = str(platform_name)
        self._platform_id = str(platform_id)
        self._is_private = bool(is_private)
        self._session_id = str(session_id or (self._sender_id if is_private else (self._group_id or self._sender_id)))
        self._self_id = str(self_id)
        self._is_admin = bool(is_admin or role == "admin")
        self.role = "admin" if self._is_admin else role

        # Format unified message origin (UMO)
        prefix = f"{self._platform_name}:"
        if self._is_private:
            self.unified_msg_origin = f"{prefix}FriendMessage:{self._sender_id}"
        else:
            self.unified_msg_origin = f"{prefix}GroupMessage:{self._group_id}"

        # Construct synthetic message_obj compatible with AstrBot
        if components is not None:
            resolved_components = list(components)
        else:
            resolved_components = [MockPlain(self._message_str)]

        self.message_obj = types.SimpleNamespace(
            message_id=str(uuid.uuid4()),
            session_id=self._session_id,
            group_id=self._group_id,
            self_id=self._self_id,
            sender=types.SimpleNamespace(user_id=self._sender_id, nickname=self._sender_name),
            message=resolved_components,
            message_str=self._message_str,
            raw_message=raw_message or {},
            timestamp=time.time(),
        )

        # Tracing flags
        self.sent_messages: List[Any] = []
        self._stopped = False
        self._should_call_llm = True

    @property
    def message_str(self) -> str:
        return self._message_str

    @message_str.setter
    def message_str(self, val: str) -> None:
        self._message_str = str(val)
        if hasattr(self, "message_obj"):
            self.message_obj.message_str = self._message_str
            self.message_obj.message = [MockPlain(self._message_str)]
            self.message_obj.message_id = str(uuid.uuid4())
            self.message_obj.timestamp = time.time()

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_platform_name(self) -> str:
        return self._platform_name

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def get_group_id(self) -> str:
        return self._group_id

    def get_self_id(self) -> str:
        return self._self_id

    def is_admin(self) -> bool:
        return self._is_admin

    def stop_event(self) -> None:
        self._stopped = True

    def continue_event(self) -> None:
        self._stopped = False

    def is_stopped(self) -> bool:
        return self._stopped

    def should_call_llm(self, status: Optional[bool] = None) -> bool:
        if status is not None:
            self._should_call_llm = bool(status)
        return self._should_call_llm

    async def send(self, message: Any) -> None:
        self.sent_messages.append(message)

    def plain_result(self, text: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(type="plain", text=str(text))


class MockFunctionToolManager:
    """Manages active LLM FunctionTools registered by plugins."""

    def __init__(self):
        self.func_list: List[FunctionTool] = []

    def get_func(self, name: str) -> Optional[FunctionTool]:
        return next((t for t in self.func_list if getattr(t, "name", "") == name), None)

    def remove_func(self, name: str) -> None:
        self.func_list = [t for t in self.func_list if getattr(t, "name", "") != name]


class MockChatProvider:
    """Deterministic offline chat provider for simulating LLM operations."""

    def __init__(
        self,
        provider_id: str = "mock_chat_provider",
        responses: Optional[Dict[str, str]] = None,
        default_response: str = "这是离线 Mock LLM 的确定性回复。",
        response_generator: Optional[Callable[[Optional[str], Optional[List[Dict[str, Any]]]], str]] = None,
    ):
        self.provider_id = provider_id
        self.responses = responses or {}
        self.default_response = default_response
        self.response_generator = response_generator
        self.tool_calls_queue: List[tuple[str, Dict[str, Any]]] = []
        self.call_history: List[Dict[str, Any]] = []

    async def text_chat(
        self,
        prompt: Optional[str] = None,
        contexts: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> MockLLMResponse:
        self.call_history.append(
            {
                "prompt": prompt,
                "contexts": contexts,
                "system_prompt": system_prompt,
                "kwargs": kwargs,
                "timestamp": time.time(),
            }
        )
        if self.tool_calls_queue:
            tool_name, tool_args = self.tool_calls_queue.pop(0)
            return MockLLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=tool_name,
                tools_call_args=tool_args,
            )

        if self.response_generator is not None:
            reply = self.response_generator(prompt, contexts)
            return MockLLMResponse(role="assistant", completion_text=reply)

        reply = self.default_response
        if prompt:
            for pattern, resp in self.responses.items():
                if pattern in prompt:
                    reply = resp
                    break
            else:
                if "严格输出 JSON 数组" in prompt:
                    reply = "[]"
        return MockLLMResponse(role="assistant", completion_text=reply)

    async def generate(self, *args, **kwargs) -> MockLLMResponse:
        return await self.text_chat(*args, **kwargs)


class MockAstrContext(Context):
    """Full-featured offline mock for AstrBot Context."""

    def __init__(
        self,
        default_config: Optional[Dict[str, Any]] = None,
        data_dir: Optional[Path] = None,
    ):
        super().__init__()
        self._config = AstrBotConfig(default_config or {})
        self.llm_tools = MockFunctionToolManager()
        self._default_provider = MockChatProvider()

        self.provider_manager = types.SimpleNamespace(
            llm_tools=self.llm_tools,
            inst_map={"default_chat": self._default_provider},
            provider_insts=[self._default_provider],
            embedding_provider_insts=[],
        )

        self.registered_web_apis: List[Dict[str, Any]] = []
        self.proactive_messages: List[tuple[str, Any]] = []
        self._data_dir = data_dir

    def add_llm_tools(self, *tools: FunctionTool) -> None:
        for tool in tools:
            # Overwrite if exists
            self.llm_tools.remove_func(getattr(tool, "name", ""))
            self.llm_tools.func_list.append(tool)

    def get_llm_tool_manager(self) -> MockFunctionToolManager:
        return self.llm_tools

    def register_web_api(
        self,
        route: str,
        view_handler: Callable,
        methods: List[str],
        desc: str = "",
    ) -> None:
        self.registered_web_apis.append(
            {
                "route": route,
                "view_handler": view_handler,
                "methods": methods,
                "desc": desc,
            }
        )

    async def send_message(self, session: Any, message_chain: Any) -> bool:
        self.proactive_messages.append((str(session), message_chain))
        return True

    def get_config(self, umo: Optional[str] = None) -> AstrBotConfig:
        return self._config

    async def get_using_provider_async(self, umo: Optional[str] = None) -> MockChatProvider:
        return self._default_provider

    def get_provider_by_id(self, provider_id: str) -> Optional[MockChatProvider]:
        return self.provider_manager.inst_map.get(provider_id)

    async def llm_generate(
        self,
        chat_provider_id: Optional[str] = None,
        prompt: Optional[str] = None,
        contexts: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> MockLLMResponse:
        provider = (
            self.get_provider_by_id(chat_provider_id)
            if chat_provider_id
            else self._default_provider
        )
        if not provider:
            provider = self._default_provider
        return await provider.text_chat(prompt=prompt, contexts=contexts, **kwargs)


def make_tool_call_context(
    event: AstrMessageEvent,
    context: Context,
) -> ContextWrapper[AstrAgentContext]:
    """Helper to wrap AstrMessageEvent and AstrContext into ContextWrapper for FunctionTool.call()."""
    agent_ctx = AstrAgentContext(context=context, event=event)
    return ContextWrapper(context=agent_ctx)


def _deep_merge_config(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def create_test_plugin_instance(
    tmp_path: Path,
    custom_config: Optional[Dict[str, Any]] = None,
):
    """Instantiate a fully isolated MemorixPlugin instance in tmp_path with offline mocks."""
    from astrbot_plugin_memorix.main import MemorixPlugin

    base_config: Dict[str, Any] = {
        "storage": {
            "data_dir": str(tmp_path / "memorix_data"),
        },
        "scope": {
            "mode": "group_global",
        },
        "embedding": {
            "provider": "builtin_hash",
        },
        "ingest": {
            "skip_command_messages": True,
            "command_prefixes": ["/", "!", "！"],
            "memory_write_mode": "auto",
        },
        "person_fact_writeback": {
            "enabled": True,
            "update_registry_memory_points": True,
        },
    }
    if custom_config:
        base_config = _deep_merge_config(base_config, custom_config)

    mock_context = MockAstrContext(default_config=base_config, data_dir=tmp_path)
    plugin = MemorixPlugin(mock_context, mock_context.get_config())
    return plugin, mock_context

