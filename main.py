from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .memorix.amemorix.services.feedback_service import FeedbackService
from .memorix.amemorix.services.fuzzy_modify_service import FuzzyModifyService
from .memorix.app_context import ScopeRuntimeManager
from .memorix.memory_injection import MEMORY_INJECTION_MARKER, MemoryInjectionController
from .memorix.message_ingestion import MessageIngestionController
from .memorix.scope_router import ScopeRouter
from .memorix.services import (
    AdminService,
    IngestService,
    MemoryService,
    PersonFactWritebackService,
    ProfileService,
    QueryService,
    SummaryService,
)
from .memorix.tools import build_memorix_tools
from .memorix.webui.plugin_page_bridge import PluginPageWebUIBridge

if TYPE_CHECKING:
    from astrbot.api.provider import LLMResponse, ProviderRequest

PLUGIN_VERSION = "0.9.5"


@register(
    "astrbot_plugin_memorix",
    "薄暝",
    "A_Memorix memory plugin with embedded WebUI",
    PLUGIN_VERSION,
)
class MemorixPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = dict(config or {})
        self.scope_router = ScopeRouter(mode=str(self.config.get("scope", {}).get("mode", "group_global")))
        self.runtime_manager = ScopeRuntimeManager(
            plugin_name="astrbot_plugin_memorix",
            plugin_config=self.config,
            astrbot_context=context,
        )

        self.ingest_service = IngestService(self.runtime_manager, self.config)
        self.query_service = QueryService(self.runtime_manager)
        self.memory_service = MemoryService(self.runtime_manager)
        self.profile_service = ProfileService(self.runtime_manager)
        self.summary_service = SummaryService(self.runtime_manager)
        self.person_fact_writeback_service = PersonFactWritebackService(self.runtime_manager, self.config)
        self.feedback_service = FeedbackService(
            self.runtime_manager,
            self.config,
            ingest_service=self.ingest_service,
        )
        self.fuzzy_modify_service = FuzzyModifyService(self.runtime_manager, self.config)
        self.admin_service = AdminService(
            self.runtime_manager,
            feedback_service=self.feedback_service,
            fuzzy_modify_service=self.fuzzy_modify_service,
        )
        self.webui_page_bridge = PluginPageWebUIBridge(
            runtime_manager=self.runtime_manager,
            scope_resolver=self._resolve_dashboard_webui_scope,
            admin_service=self.admin_service,
        )
        self.memory_injection = MemoryInjectionController(self)
        self.message_ingestion = MessageIngestionController(self)
        self._llm_tools = []
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def initialize(self) -> None:
        logger.info("[memorix] initialize start")
        try:
            self.webui_page_bridge.register(self.context, plugin_name="astrbot_plugin_memorix")
            self._llm_tools = build_memorix_tools(self)
            self.context.add_llm_tools(*self._llm_tools)
            await self.person_fact_writeback_service.start()
            await self.feedback_service.start_background_loops()
        except Exception:
            self._remove_llm_tools()
            await self._close_component("feedback service", self.feedback_service.stop_background_loops)
            await self._close_component("person fact writeback", self.person_fact_writeback_service.close)
            await self._close_component("webui", self.webui_page_bridge.close)
            raise
        logger.info("[memorix] initialize done")

    async def terminate(self) -> None:
        logger.info("[memorix] terminate start")
        self._remove_llm_tools()
        pending = [task for task in self._background_tasks if not task.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                for task in pending:
                    task.cancel()
        await self._close_component("person fact writeback", self.person_fact_writeback_service.close)
        await self._close_component("webui", self.webui_page_bridge.close)
        await self._close_component("feedback service", self.feedback_service.stop_background_loops)
        await self._close_component("admin service", self.admin_service.close)
        await self._close_component("runtime manager", self.runtime_manager.close_all)
        logger.info("[memorix] terminate done")

    @staticmethod
    async def _close_component(name: str, close: Callable[[], Awaitable[None]]) -> None:
        try:
            await close()
        except Exception:
            logger.warning("[memorix] close %s failed", name, exc_info=True)

    def _remove_llm_tools(self) -> None:
        tools, self._llm_tools = self._llm_tools, []
        if not tools:
            return

        get_tool_manager = getattr(self.context, "get_llm_tool_manager", None)
        if not callable(get_tool_manager):
            logger.warning("[memorix] LLM tool manager is unavailable during cleanup")
            return
        try:
            tool_manager = get_tool_manager()
        except Exception:
            logger.warning("[memorix] get LLM tool manager failed during cleanup", exc_info=True)
            return

        remove_func = getattr(tool_manager, "remove_func", None)
        if not callable(remove_func):
            logger.warning("[memorix] LLM tool manager does not provide remove_func")
            return
        for tool in tools:
            try:
                remove_func(tool.name)
            except Exception:
                logger.warning("[memorix] remove LLM tool failed: %s", tool.name, exc_info=True)

    def _resolve_scope(self, event: AstrMessageEvent) -> str:
        return self.scope_router.resolve(event)

    @staticmethod
    def _is_cron_event(event: AstrMessageEvent) -> bool:
        return str(event.get_platform_name() or "").strip() == "cron"

    def _spawn_background_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _resolve_dashboard_webui_scope(self) -> str:
        known_scopes = self.runtime_manager.get_known_scopes()
        return str(known_scopes[-1]) if known_scopes else "default"

    @staticmethod
    def _cfg_value(config: dict, key: str, default: Any = None) -> Any:
        current: Any = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @classmethod
    def _bool_cfg(cls, config: dict, key: str, default: bool) -> bool:
        return bool(cls._cfg_value(config, key, default))

    @classmethod
    def _int_cfg(
        cls,
        config: dict,
        key: str,
        default: int,
        *,
        min_value: int = 1,
        max_value: int | None = None,
    ) -> int:
        try:
            value = int(cls._cfg_value(config, key, default) or default)
        except (TypeError, ValueError):
            value = int(default)
        value = max(min_value, value)
        return min(max_value, value) if max_value is not None else value

    @staticmethod
    def _event_ctx_text(event: AstrMessageEvent, scope_key: str = "") -> str:
        platform = str(getattr(event, "get_platform_name", lambda: "unknown")() or "unknown")
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group = str(getattr(event, "get_group_id", lambda: "")() or "")
        session = str(
            getattr(getattr(event, "message_obj", None), "session_id", "") or getattr(event, "unified_msg_origin", "")
        )
        return (
            f"scope={scope_key or 'unknown'} platform={platform} session={session or '-'} "
            f"sender={sender or '-'} group={group or '-'}"
        )

    async def _is_adapted_chat_enabled(self, adapted, user_id: str = "") -> bool:
        try:
            runtime = await self.runtime_manager.get_runtime(adapted.scope_key)
            checker = getattr(runtime.context, "is_chat_enabled", None)
            if not callable(checker):
                return True
            return bool(
                checker(
                    stream_id=adapted.session_id,
                    group_id=adapted.group_id,
                    user_id=str(user_id or adapted.sender_id or "").strip(),
                )
            )
        except Exception:
            logger.warning("[memorix] chat filter check failed: scope=%s", adapted.scope_key, exc_info=True)
            return True

    async def feedback_service_enabled(self, scope_key: str) -> bool:
        try:
            runtime = await self.runtime_manager.get_runtime(scope_key)
            return bool(runtime.context.get_config("integration.feedback_correction_enabled", False))
        except Exception:
            return False

    async def enqueue_feedback(
        self,
        *,
        scope_key: str,
        query: str,
        chat_id: str,
        group_id: str = "",
        user_id: str = "",
        hit_hashes: list | None = None,
    ) -> dict:
        query_tool_id = f"search:{time.time_ns()}"
        structured_content = {
            "query": str(query or ""),
            "chat_id": str(chat_id or ""),
            "group_id": str(group_id or ""),
            "user_id": str(user_id or ""),
            "hits": [{"hash": item, "type": "paragraph"} for item in (hit_hashes or []) if item],
        }
        return await self.feedback_service.enqueue_feedback(
            scope_key=scope_key,
            query_tool_id=query_tool_id,
            session_id=str(chat_id or ""),
            query_timestamp=time.time(),
            structured_content=structured_content,
        )

    def _is_command_message(self, text: str) -> bool:
        return self.message_ingestion._is_command_message(text)

    def _command_prefixes(self) -> list[str]:
        return self.message_ingestion._command_prefixes()

    async def _ingest_event_message(self, event: AstrMessageEvent, role: str, text: str) -> bool:
        return await self.message_ingestion._ingest_event_message(event, role, text)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request: "ProviderRequest") -> None:
        await self.memory_injection.inject(event, request)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_messages(self, event: AstrMessageEvent) -> None:
        await self.message_ingestion.handle_message(event)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: "LLMResponse") -> None:
        await self.message_ingestion.handle_llm_response(event, response)


__all__ = ["MEMORY_INJECTION_MARKER", "MemorixPlugin"]
