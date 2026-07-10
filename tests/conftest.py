import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    core_mod = types.ModuleType("astrbot.core")
    agent_mod = types.ModuleType("astrbot.core.agent")
    message_mod = types.ModuleType("astrbot.core.agent.message")
    run_context_mod = types.ModuleType("astrbot.core.agent.run_context")
    tool_mod = types.ModuleType("astrbot.core.agent.tool")
    astr_agent_context_mod = types.ModuleType("astrbot.core.astr_agent_context")
    platform_mod = types.ModuleType("astrbot.core.platform")
    astr_message_event_mod = types.ModuleType("astrbot.core.platform.astr_message_event")
    utils_mod = types.ModuleType("astrbot.core.utils")
    path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class FunctionTool:
        name = ""
        description = ""
        parameters = {}
        active = True
        handler_module_path = None

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)

        def __class_getitem__(cls, _item):
            return cls

    class ContextWrapper:
        def __init__(self, context=None, messages=None, tool_call_timeout=120):
            self.context = context
            self.messages = messages or []
            self.tool_call_timeout = tool_call_timeout

        def __class_getitem__(cls, _item):
            return cls

    class TextPart:
        def __init__(self, text: str):
            self.text = text
            self._no_save = False

        def mark_as_temp(self):
            self._no_save = True
            return self

        def model_dump_for_context(self):
            data = {"type": "text", "text": self.text}
            if self._no_save:
                data["_no_save"] = True
            return data

    class AstrAgentContext:
        def __init__(self, context=None, event=None, extra=None):
            self.context = context
            self.event = event
            self.extra = extra or {}

    class AstrMessageEvent:
        pass

    class AstrBotConfig(dict):
        pass

    class Context:
        def __init__(self):
            self._tool_manager = types.SimpleNamespace(func_list=[])

        def add_llm_tools(self, *tools):
            self._tool_manager.func_list.extend(tools)

        def get_llm_tool_manager(self):
            return self._tool_manager

    class Star:
        def __init__(self, context):
            self.context = context

    def register(*args, **kwargs):
        del args, kwargs

        def deco(cls):
            return cls

        return deco

    class _Filter:
        class EventMessageType:
            ALL = "ALL"

        class PermissionType:
            ADMIN = "ADMIN"

        def command(self, *args, **kwargs):
            del args, kwargs
            return lambda func: func

        def command_group(self, *args, **kwargs):
            del args, kwargs

            def deco(func):
                def command_decorator(*c_args, **c_kwargs):
                    del c_args, c_kwargs
                    return lambda sub_func: sub_func

                func.command = command_decorator
                return func

            return deco

        def permission_type(self, *args, **kwargs):
            del args, kwargs
            return lambda func: func

        def event_message_type(self, *args, **kwargs):
            del args, kwargs
            return lambda func: func

        def on_llm_request(self, *args, **kwargs):
            del args, kwargs
            return lambda func: func

        def on_llm_response(self, *args, **kwargs):
            del args, kwargs
            return lambda func: func

    def get_astrbot_data_path(*args, **kwargs):
        del args, kwargs
        return str(ROOT / ".test-astrbot-data")

    api_mod.logger = _Logger()
    api_mod.AstrBotConfig = AstrBotConfig
    api_mod.FunctionTool = FunctionTool
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = _Filter()
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register
    run_context_mod.ContextWrapper = ContextWrapper
    tool_mod.FunctionTool = FunctionTool
    tool_mod.ToolExecResult = str
    astr_agent_context_mod.AstrAgentContext = AstrAgentContext
    astr_message_event_mod.AstrMessageEvent = AstrMessageEvent

    astrbot_mod.api = api_mod
    astrbot_mod.core = core_mod
    core_mod.agent = agent_mod
    core_mod.astr_agent_context = astr_agent_context_mod
    core_mod.platform = platform_mod
    core_mod.utils = utils_mod
    agent_mod.run_context = run_context_mod
    agent_mod.message = message_mod
    agent_mod.tool = tool_mod
    message_mod.TextPart = TextPart
    platform_mod.astr_message_event = astr_message_event_mod
    utils_mod.astrbot_path = path_mod
    path_mod.get_astrbot_data_path = get_astrbot_data_path

    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.agent"] = agent_mod
    sys.modules["astrbot.core.agent.run_context"] = run_context_mod
    sys.modules["astrbot.core.agent.message"] = message_mod
    sys.modules["astrbot.core.agent.tool"] = tool_mod
    sys.modules["astrbot.core.astr_agent_context"] = astr_agent_context_mod
    sys.modules["astrbot.core.platform"] = platform_mod
    sys.modules["astrbot.core.platform.astr_message_event"] = astr_message_event_mod
    sys.modules["astrbot.core.utils"] = utils_mod
    sys.modules["astrbot.core.utils.astrbot_path"] = path_mod


def _install_openai_stub() -> None:
    if "openai" in sys.modules:
        return

    openai_mod = types.ModuleType("openai")

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            del args, kwargs

    openai_mod.AsyncOpenAI = AsyncOpenAI
    sys.modules["openai"] = openai_mod


_install_astrbot_stub()
_install_openai_stub()
