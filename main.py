from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .memorix.engine import MemoryEngineFacade, ScopeEngineManager
from .memorix.memory_injection import MEMORY_INJECTION_MARKER, MemoryInjectionController
from .memorix.message_ingestion import MessageIngestionController
from .memorix.providers import AstrBotProviderBridge
from .memorix.scope_router import ScopeRouter
from .memorix.services.container import DomainServiceContainer
from .memorix.tools import build_memorix_tools
from .memorix.webui.plugin_page_bridge import PluginPageWebUIBridge

if TYPE_CHECKING:
    from astrbot.api.provider import LLMResponse, ProviderRequest

PLUGIN_VERSION = "2.0.0"


@register(
    "astrbot_plugin_memorix",
    "薄暝",
    "让 AstrBot 拥有可检索、可维护、可追溯的长期记忆",
    PLUGIN_VERSION,
)
class MemorixPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = dict(config or {})

        # 1. 作用域路由 (Feature #15: 严格以 event.unified_msg_origin 及其映射作为真理之源)
        self.scope_router = ScopeRouter(mode=str(self.config.get("scope", {}).get("mode", "group_global")))

        # 2. 宿主提供商桥接 (Feature #2, #10)
        provider_cfg = self.config.get("provider", {})
        chat_provider_id = (
            str(provider_cfg.get("chat_provider_id", "") or "")
            if isinstance(provider_cfg, dict)
            else ""
        )
        fast_chat_provider_id = (
            str(provider_cfg.get("fast_chat_provider_id", "") or "")
            if isinstance(provider_cfg, dict)
            else ""
        )
        self.provider_bridge = AstrBotProviderBridge(
            astrbot_context=context,
            chat_provider_id=chat_provider_id,
            fast_chat_provider_id=fast_chat_provider_id,
        )
        from .memorix.engine.host_shims import install_host_shims

        install_host_shims(config=self.config, provider_bridge=self.provider_bridge)

        # 3. 领域服务容器与统一作用域引擎管理器 (共享唯一 ScopeRuntimeManager，彻底消除双实例脑裂与 .npz 覆盖竞争)
        self._explicit_base_data_dir: Path | None = None
        self._services = DomainServiceContainer(
            config=self.config,
            context=context,
            base_data_dir=None,
            provider_bridge=self.provider_bridge,
        )
        self.scope_manager = ScopeEngineManager(
            base_data_dir=None,
            config=self.config,
            provider_bridge=self.provider_bridge,
            runtime_manager=self._services.runtime_manager,
            ingest_service=self._services.ingest_service,
            query_service=self._services.query_service,
            admin_service=self._services.admin_service,
        )
        self._services.engine_manager = self.scope_manager
        self.engine_manager = self.scope_manager  # 别名兼容

        # 挂载服务引用至实例属性，保持完全可测试性与向后兼容 (支持 monkeypatch)
        self.runtime_manager = self._services.runtime_manager
        self.ingest_service = self._services.ingest_service
        self.query_service = self._services.query_service
        self.memory_service = self._services.memory_service
        self.profile_service = self._services.profile_service
        self.summary_service = self._services.summary_service
        self.person_fact_writeback_service = self._services.person_fact_writeback_service
        self.feedback_service = self._services.feedback_service
        self.fuzzy_modify_service = self._services.fuzzy_modify_service
        self.admin_service = self._services.admin_service

        # 4. 宿主适配协同器 (Feature #10, #13)
        self.webui_page_bridge = PluginPageWebUIBridge(
            runtime_manager=self.runtime_manager,
            scope_resolver=self._resolve_dashboard_webui_scope,
            admin_service=self.admin_service,
        )
        self.memory_injection = MemoryInjectionController(self)
        self.message_ingestion = MessageIngestionController(self)

        self._llm_tools: list[Any] = []
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def base_data_dir(self) -> Path:
        return self._resolve_base_data_dir()

    @base_data_dir.setter
    def base_data_dir(self, value: Path | str | None) -> None:
        resolved = Path(value).resolve() if value is not None and str(value).strip() else None
        self._explicit_base_data_dir = resolved
        if hasattr(self, "runtime_manager") and self.runtime_manager is not None:
            self.runtime_manager.base_data_dir = resolved

    def _resolve_base_data_dir(self) -> Path:
        """安全解析插件持久化存储根目录，与 ScopeRuntimeManager 严格同源且绝不依赖私有 astrbot.core.* 模块。"""
        if hasattr(self, "runtime_manager") and self.runtime_manager is not None:
            resolver = getattr(self.runtime_manager, "_resolve_plugin_data_dir", None)
            if callable(resolver):
                return Path(resolver("astrbot_plugin_memorix")).resolve()
        from .memorix.app_context import resolve_plugin_data_dir

        return resolve_plugin_data_dir(
            plugin_config=self.config,
            astrbot_context=self.context,
            plugin_name="astrbot_plugin_memorix",
            base_data_dir=self._explicit_base_data_dir,
        )

    async def get_scope_engine(self, scope_or_event: str | AstrMessageEvent) -> MemoryEngineFacade:
        """根据 scope_key 或 AstrMessageEvent 动态检索当前会话作用域的 MemoryEngineFacade。"""
        if isinstance(scope_or_event, str):
            scope_key = scope_or_event
        else:
            scope_key = self._resolve_scope(scope_or_event)
        return await self.scope_manager.get_engine(scope_key)

    async def initialize(self) -> None:
        logger.info("[memorix] initialize start")
        try:
            # 1. 注册 WebUI Bridge
            self.webui_page_bridge.register(self.context, plugin_name="astrbot_plugin_memorix")

            # 2. 挂载 FunctionTools
            self._llm_tools = build_memorix_tools(self)
            self.context.add_llm_tools(*self._llm_tools)

            # 3. 启动领域服务后台循环 (写回与反馈审计)
            p_start = getattr(self.person_fact_writeback_service, "start", None)
            if callable(p_start):
                await p_start()
            f_start = getattr(self.feedback_service, "start_background_loops", None)
            if callable(f_start):
                await f_start()
        except BaseException:
            await self.terminate()
            raise
        logger.info("[memorix] initialize done")

    async def terminate(self) -> None:
        logger.info("[memorix] terminate start")
        # 1. 移除已注册的 LLM 工具
        self._remove_llm_tools()

        # 2. 优雅取消并等待活跃宿主后台协程 (有界超时保障)
        pending = [task for task in self._background_tasks if not task.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                from .memorix.core.utils.async_work import cancel_and_wait_for_tasks

                try:
                    await cancel_and_wait_for_tasks(pending, operation="MemorixPlugin.terminate")
                except Exception:
                    logger.warning("[memorix] background tasks did not finish within stop timeout", exc_info=True)

        # 3. 按序优雅关闭组件 (兼容存量测试的 monkeypatch: person -> webui -> feedback -> admin -> runtime)
        p_close = getattr(self.person_fact_writeback_service, "close", None)
        if callable(p_close):
            await self._close_component("person fact writeback", p_close)

        w_close = getattr(self.webui_page_bridge, "close", None)
        if callable(w_close):
            await self._close_component("webui", w_close)

        f_stop = getattr(self.feedback_service, "stop_background_loops", None)
        if callable(f_stop):
            await self._close_component("feedback service", f_stop)

        a_close = getattr(self.admin_service, "close", None)
        if callable(a_close):
            await self._close_component("admin service", a_close)

        r_close = getattr(self.runtime_manager, "close_all", None)
        if callable(r_close):
            await self._close_component("runtime manager", r_close)

        # 4. 彻底平稳关闭所有活跃作用域的 MemoryEngineFacade (WAL 刷盘与写者锁释放)
        await self._close_component("scope engine manager", self.scope_manager.shutdown_all)

        # 5. 释放宿主桥接全局引用与 src.A_memorix 别名缓存，避免热重载残留旧模块引用
        try:
            import sys

            from .memorix.engine.host_shims import llm_shim

            if llm_shim.get_active_provider_bridge() is self.provider_bridge:
                llm_shim.set_active_provider_bridge(None)
            for mod_key in [k for k in list(sys.modules.keys()) if k == "src.A_memorix" or k.startswith("src.A_memorix.")]:
                sys.modules.pop(mod_key, None)
        except Exception:
            pass

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

        for tool in tools:
            try:
                tool_manager.remove_func(tool.name)
            except Exception:
                logger.warning("[memorix] remove LLM tool failed: %s", tool.name, exc_info=True)

    def _resolve_scope(self, event: AstrMessageEvent) -> str:
        """严格基于事件统一消息源 (unified_msg_origin) 及平台路由隔离作用域，防御混淆代理。"""
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
        active_scopes = self.scope_manager.list_active_scopes()
        if active_scopes:
            return str(active_scopes[-1])
        known_scopes = self.runtime_manager.list_scope_keys()
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
        except Exception:
            logger.error("[memorix] runtime initialization failed during chat filter check: scope=%s", adapted.scope_key, exc_info=True)
            return False

        try:
            checker = getattr(runtime.context, "is_chat_enabled", None)
            if not callable(checker):
                return False
            return bool(
                checker(
                    stream_id=adapted.session_id,
                    group_id=adapted.group_id,
                    user_id=str(user_id or adapted.sender_id or "").strip(),
                )
            )
        except Exception:
            logger.warning("[memorix] chat filter check failed: scope=%s", adapted.scope_key, exc_info=True)
            return False


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
        """KV-Cache 友好记忆注入：仅向 extra_user_content_parts 追加带 mark_as_temp 的临时块，系统提示词完全静态。"""
        await self.memory_injection.inject(event, request)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_messages(self, event: AstrMessageEvent) -> None:
        await self.message_ingestion.handle_message(event)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: "LLMResponse") -> None:
        await self.message_ingestion.handle_llm_response(event, response)


__all__ = ["MEMORY_INJECTION_MARKER", "MemorixPlugin"]
