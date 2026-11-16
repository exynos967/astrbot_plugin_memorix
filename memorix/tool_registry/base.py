"""Base FunctionTool abstraction using strictly public astrbot.api interfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent
from pydantic import Field
from pydantic.dataclasses import dataclass

from ..adapters.astrbot_event_adapter import AstrbotEventAdapter

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class MemorixToolBase(FunctionTool[Any]):
    """Base class for all Memorix FunctionTools enforcing scope isolation."""

    plugin: Any = Field(default=None, repr=False, exclude=True)

    def _event(self, context: ContextWrapper[AstrAgentContext]) -> AstrMessageEvent:
        event = context.context.event
        if event is None:
            raise ValueError("Memorix tools require an AstrBot message event.")
        return event

    def _scope_key(self, event: AstrMessageEvent, scope_key: str = "") -> str:
        # LLM 传入的 scope_key 一律丢弃：成员工具不能跨会话读写，管理员工具也不当 confused deputy。
        del scope_key
        return self.plugin._resolve_scope(event)

    def _adapted(self, event: AstrMessageEvent, scope_key: str):
        return AstrbotEventAdapter.from_event(event, scope_key)

    @staticmethod
    def _is_cron_event(event: AstrMessageEvent) -> bool:
        return str(getattr(event, "get_platform_name", lambda: "")() or "").strip() == "cron"

    def _source_for_event(self, event: AstrMessageEvent, scope_key: str) -> str:
        adapted = self._adapted(event, scope_key)
        return f"chat:{adapted.platform}:{adapted.session_id}"

    async def _is_chat_enabled(
        self,
        event: AstrMessageEvent,
        scope_key: str,
        *,
        chat_id: str = "",
        user_id: str = "",
    ) -> bool:
        runtime_manager = getattr(self.plugin, "runtime_manager", None)
        get_runtime = getattr(runtime_manager, "get_runtime", None)
        if not callable(get_runtime):
            return False

        adapted = self._adapted(event, scope_key)
        try:
            runtime = await get_runtime(scope_key)
            checker = getattr(runtime.context, "is_chat_enabled", None)
            if not callable(checker):
                return False
            return bool(
                checker(
                    stream_id=str(chat_id or adapted.session_id or "").strip(),
                    group_id=adapted.group_id,
                    user_id=str(user_id or adapted.sender_id or "").strip(),
                )
            )
        except Exception as exc:
            logger.warning("[memorix] tool chat filter check failed: %s", exc, exc_info=True)
            return False

    async def _upsert_current_sender(
        self,
        event: AstrMessageEvent,
        scope_key: str,
        *,
        respect_filter: bool = True,
    ) -> None:
        if self._is_cron_event(event):
            return
        adapted = self._adapted(event, scope_key)
        if not adapted.sender_id:
            return
        if respect_filter and not await self._is_chat_enabled(event, scope_key):
            return
        await self.plugin.profile_service.upsert_registry_from_event(
            scope_key=adapted.scope_key,
            platform=adapted.platform,
            sender_id=adapted.sender_id,
            sender_name=adapted.sender_name or adapted.sender_id,
            group_id=adapted.group_id,
            session_id=adapted.session_id,
            unified_msg_origin=adapted.unified_msg_origin,
            timestamp=float(adapted.timestamp) if adapted.timestamp else None,
        )


__all__ = ["MemorixToolBase"]
