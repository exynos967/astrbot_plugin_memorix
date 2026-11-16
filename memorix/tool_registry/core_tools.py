"""Core user-facing Memorix FunctionTools (search, ingest, profile, maintain, stats)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from astrbot.api import logger
from pydantic import Field
from pydantic.dataclasses import dataclass

from .base import MemorixToolBase
from .formatting import (
    _format_search_result_for_llm,
    _search_hit_items,
    _to_float_or_none,
    _to_int,
    _tool_result,
)

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class MemorixSearchTool(MemorixToolBase):
    name: str = "search_memory"
    description: str = (
        "搜索 Memorix 长期记忆。需要回忆用户偏好、历史对话、人物关系、时间线事件或已存事实时调用。"
        "默认使用 mode=search。只有用户明确询问某个时间范围（如昨天、上周、某日期）时才使用 time/hybrid，"
        "且 time/hybrid 必须至少提供 time_start 或 time_end，否则会按 MaiBot 行为返回参数错误，不会自动降级。"
        "episode/aggregate 用于事件片段或聚合检索；工具结果会返回证据列表。"
        "命中数大于 0 时应基于证据回答；证据只说明对象被提到时，要说明只查到提及线索，不要编造。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询文本，尽量简短具体。"},
                "limit": {"type": "integer", "description": "返回条数，默认 5，最大 50。"},
                "mode": {
                    "type": "string",
                    "description": (
                        "检索模式。默认 search；没有时间条件时不要用 hybrid/time。"
                        "time/hybrid 必须提供 time_start 或 time_end。"
                    ),
                    "enum": ["search", "time", "hybrid", "episode", "aggregate"],
                },
                "chat_id": {"type": "string", "description": "聊天流/session_id；留空使用当前会话。"},
                "person_id": {"type": "string", "description": "人物 ID 或关键词，可选。"},
                "time_start": {
                    "type": "string",
                    "description": "起始时间，支持 YYYY-MM-DD/昨天/上周等；使用 mode=time/hybrid 时至少填它或 time_end。",
                },
                "time_end": {
                    "type": "string",
                    "description": "结束时间，支持 YYYY-MM-DD/今天等；使用 mode=time/hybrid 时至少填它或 time_start。",
                },
            },
            "required": ["query"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = self._event(context)
        scope_key = self._scope_key(event)
        adapted = self._adapted(event, scope_key)
        mode = str(kwargs.get("mode", "search") or "search").strip().lower()
        if mode not in {"search", "time", "hybrid", "episode", "aggregate"}:
            mode = "search"
        query = str(kwargs.get("query", "") or "").strip()
        if not query:
            return _tool_result({"success": False, "error": "query is required", "scope": scope_key})
        limit = _to_int(kwargs.get("limit", 5), 5, min_value=1, max_value=50)
        chat_id = str(kwargs.get("chat_id", "") or "").strip() or adapted.session_id
        person_id = str(kwargs.get("person_id", "") or "").strip()
        time_start = str(kwargs.get("time_start", "") or "").strip() or None
        time_end = str(kwargs.get("time_end", "") or "").strip() or None
        respect_filter = True
        source = self._source_for_event(event, scope_key) if respect_filter else None
        strict_source = bool(source)
        group_id = adapted.group_id
        user_id = adapted.sender_id

        try:
            if mode == "episode":
                data = await self.plugin.query_service.episode(
                    scope_key=scope_key,
                    query=query,
                    time_from=time_start,
                    time_to=time_end,
                    person=person_id or None,
                    source=source,
                    top_k=limit,
                    stream_id=chat_id,
                    group_id=group_id,
                    user_id=user_id,
                    enforce_chat_filter=respect_filter,
                )
            elif mode == "aggregate":
                data = await self.plugin.query_service.aggregate(
                    scope_key=scope_key,
                    query=query,
                    time_from=time_start,
                    time_to=time_end,
                    person=person_id or None,
                    source=source,
                    top_k=limit,
                    stream_id=chat_id,
                    group_id=group_id,
                    user_id=user_id,
                    enforce_chat_filter=respect_filter,
                )
            elif mode in {"time", "hybrid"} or time_start or time_end:
                data = await self.plugin.query_service.time_search(
                    scope_key=scope_key,
                    query=query,
                    time_from=time_start,
                    time_to=time_end,
                    person=person_id or None,
                    source=source,
                    top_k=limit,
                    stream_id=chat_id,
                    group_id=group_id,
                    user_id=user_id,
                    enforce_chat_filter=respect_filter,
                )
            else:
                data = await self.plugin.query_service.search(
                    scope_key=scope_key,
                    query=query,
                    top_k=limit,
                    stream_id=chat_id,
                    group_id=group_id,
                    user_id=user_id,
                    source=source,
                    strict_source=strict_source,
                    enforce_chat_filter=respect_filter,
                )
            data["scope"] = scope_key
            data["chat_id"] = chat_id
            await self._maybe_enqueue_feedback(
                data,
                query=query,
                scope_key=scope_key,
                chat_id=chat_id,
                group_id=group_id,
                user_id=user_id,
            )
            return _format_search_result_for_llm(data, limit=limit)
        except Exception as exc:
            logger.warning("[memorix] search_memory tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})

    async def _maybe_enqueue_feedback(
        self,
        data: dict,
        *,
        query: str,
        scope_key: str,
        chat_id: str,
        group_id: str,
        user_id: str,
    ) -> None:
        """检索命中后入队反馈纠错任务；失败静默，绝不影响检索结果。"""
        try:
            plugin = self.plugin
            enqueue = getattr(plugin, "enqueue_feedback", None)
            if not callable(enqueue):
                return
            if not await plugin.feedback_service_enabled(scope_key):
                return
            hit_hashes = [
                str(item.get("hash") or item.get("hash_value") or "").strip()
                for item in _search_hit_items(data)
            ]
            hit_hashes = [h for h in hit_hashes if h]
            if not hit_hashes:
                return
            await enqueue(
                scope_key=scope_key,
                query=query,
                chat_id=chat_id,
                group_id=group_id,
                user_id=user_id,
                hit_hashes=hit_hashes,
            )
        except Exception:
            logger.debug("[memorix] feedback enqueue skipped", exc_info=True)


@dataclass
class MemorixIngestTextTool(MemorixToolBase):
    name: str = "ingest_text"
    description: str = (
        "把明确值得长期保存的普通文本写入 Memorix。"
        "仅在用户明确要求记住、或信息对后续长期对话有稳定价值时调用；不要记录一次性闲聊或敏感无关内容。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "external_id": {"type": "string", "description": "外部幂等 ID；留空自动生成。"},
                "source_type": {"type": "string", "description": "来源类型，默认 tool_text。"},
                "text": {"type": "string", "description": "要写入长期记忆的文本。"},
                "chat_id": {"type": "string", "description": "聊天流/session_id；留空使用当前会话。"},
                "timestamp": {"type": "number", "description": "事件时间戳；留空使用当前事件时间。"},
                "time_start": {"type": "string", "description": "可选事件开始时间。"},
                "time_end": {"type": "string", "description": "可选事件结束时间。"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签，可选。"},
                "metadata": {"type": "object", "description": "附加元数据，可选。"},
                "person_ids": {"type": "array", "items": {"type": "string"}, "description": "相关人物 ID，可选。"},
                "participants": {"type": "array", "items": {"type": "string"}, "description": "参与者名称，可选。"},
                "entities": {"type": "array", "items": {"type": "string"}, "description": "相关实体，可选。"},
                "relations": {
                    "type": "array",
                    "description": "结构化关系，可选。每项包含 subject/predicate/object/confidence。",
                    "items": {"type": "object"},
                },
            },
            "required": ["text"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = self._event(context)
        scope_key = self._scope_key(event)
        adapted = self._adapted(event, scope_key)
        text = str(kwargs.get("text", "") or "").strip()
        if not text:
            return _tool_result({"success": False, "error": "text is empty", "scope": scope_key})
        respect_filter = True
        await self._upsert_current_sender(event, scope_key, respect_filter=respect_filter)

        timestamp = _to_float_or_none(kwargs.get("timestamp")) or float(adapted.timestamp or time.time())
        chat_id = str(kwargs.get("chat_id", "") or "").strip() or adapted.session_id
        source_type = str(kwargs.get("source_type", "tool_text") or "tool_text").strip() or "tool_text"
        external_id = str(kwargs.get("external_id", "") or "").strip() or str(getattr(adapted, "message_id", "") or "")

        try:
            data = await self.plugin.ingest_service.ingest_text(
                scope_key=scope_key,
                external_id=external_id,
                source_type=source_type,
                text=text,
                chat_id=chat_id,
                person_ids=kwargs.get("person_ids") if isinstance(kwargs.get("person_ids"), list) else [],
                participants=kwargs.get("participants") if isinstance(kwargs.get("participants"), list) else [],
                timestamp=timestamp,
                time_start=kwargs.get("time_start"),
                time_end=kwargs.get("time_end"),
                tags=kwargs.get("tags") if isinstance(kwargs.get("tags"), list) else [],
                metadata=kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {},
                entities=kwargs.get("entities") if isinstance(kwargs.get("entities"), list) else [],
                relations=kwargs.get("relations") if isinstance(kwargs.get("relations"), list) else [],
                respect_filter=respect_filter,
                user_id=adapted.sender_id,
                group_id=adapted.group_id,
            )
            data["scope"] = scope_key
            data["chat_id"] = chat_id
            data["source_type"] = source_type
            data["tags"] = kwargs.get("tags") if isinstance(kwargs.get("tags"), list) else []
            return _tool_result(data)
        except Exception as exc:
            logger.warning("[memorix] ingest_text tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})


@dataclass
class MemorixIngestSummaryTool(MemorixToolBase):
    name: str = "ingest_summary"
    description: str = "把一段聊天摘要/阶段总结写入 Memorix 长期记忆。适合在对话阶段结束或用户要求总结记住时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "external_id": {"type": "string", "description": "外部幂等 ID；留空自动生成。"},
                "chat_id": {"type": "string", "description": "聊天流/session_id；留空使用当前会话。"},
                "text": {"type": "string", "description": "摘要文本。"},
                "time_start": {"type": "string", "description": "摘要覆盖的开始时间。"},
                "time_end": {"type": "string", "description": "摘要覆盖的结束时间。"},
                "participants": {"type": "array", "items": {"type": "string"}, "description": "参与者，可选。"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签，可选。"},
                "metadata": {"type": "object", "description": "附加元数据，可选。"},
            },
            "required": ["text"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = self._event(context)
        scope_key = self._scope_key(event)
        adapted = self._adapted(event, scope_key)
        text = str(kwargs.get("text") or kwargs.get("summary") or "").strip()
        if not text:
            return _tool_result({"success": False, "error": "text is empty", "scope": scope_key})
        respect_filter = True
        await self._upsert_current_sender(event, scope_key, respect_filter=respect_filter)

        chat_id = str(kwargs.get("chat_id", "") or "").strip() or adapted.session_id
        external_id = str(kwargs.get("external_id", "") or "").strip() or f"summary:{chat_id}:{int(time.time())}"

        try:
            data = await self.plugin.ingest_service.ingest_summary(
                scope_key=scope_key,
                external_id=external_id,
                chat_id=chat_id,
                text=text,
                participants=kwargs.get("participants") if isinstance(kwargs.get("participants"), list) else [],
                time_start=kwargs.get("time_start"),
                time_end=kwargs.get("time_end"),
                tags=kwargs.get("tags") if isinstance(kwargs.get("tags"), list) else [],
                metadata=kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {},
                respect_filter=respect_filter,
                user_id=adapted.sender_id,
                group_id=adapted.group_id,
            )
            data["scope"] = scope_key
            data["chat_id"] = chat_id
            data["summary_external_id"] = external_id
            data["participants"] = kwargs.get("participants") if isinstance(kwargs.get("participants"), list) else []
            data["tags"] = kwargs.get("tags") if isinstance(kwargs.get("tags"), list) else []
            return _tool_result(data)
        except Exception as exc:
            logger.warning("[memorix] ingest_summary tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})


@dataclass
class MemorixPersonProfileTool(MemorixToolBase):
    name: str = "get_person_profile"
    description: str = "获取指定人物的长期画像、偏好和相关证据。需要了解用户或相关人物背景时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "人物 ID；留空时使用当前发送者。"},
                "person_keyword": {"type": "string", "description": "人物关键词/昵称；person_id 不确定时使用。"},
                "person_name": {"type": "string", "description": "人物名称/昵称（person_keyword 的别名）。"},
                "chat_id": {"type": "string", "description": "聊天流/session_id；可选。"},
                "limit": {"type": "integer", "description": "证据条数，默认 10。"},
            },
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = self._event(context)
        scope_key = self._scope_key(event)
        adapted = self._adapted(event, scope_key)
        person_id = str(kwargs.get("person_id", "") or "").strip()
        keyword = str(kwargs.get("person_keyword") or kwargs.get("person_name") or "").strip()
        if not person_id and not keyword and adapted.sender_id and not self._is_cron_event(event):
            person_id = f"{adapted.platform}:{adapted.sender_id}"
            keyword = adapted.sender_name or adapted.sender_id
        limit = _to_int(kwargs.get("limit", 10), 10, min_value=1, max_value=50)
        chat_id = str(kwargs.get("chat_id", "") or "").strip() or adapted.session_id
        if not await self._is_chat_enabled(
            event,
            scope_key,
            chat_id=chat_id,
            user_id=adapted.sender_id,
        ):
            return _tool_result(
                {
                    "success": True,
                    "filtered": True,
                    "scope": scope_key,
                    "chat_id": chat_id,
                    "detail": "chat_filtered",
                }
            )
        try:
            data = await self.plugin.profile_service.query(
                scope_key=scope_key,
                person_id=person_id,
                person_keyword=keyword,
                top_k=limit,
                force_refresh=False,
            )
            data["scope"] = scope_key
            data["chat_id"] = chat_id
            return _tool_result(data)
        except Exception as exc:
            logger.warning("[memorix] get_person_profile tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})


@dataclass
class MemorixMaintainTool(MemorixToolBase):
    name: str = "maintain_memory"
    description: str = "维护长期记忆关系状态。支持 reinforce/protect/restore/freeze/status；仅在用户明确要求管理记忆时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "动作：reinforce/protect/restore/freeze/status。回收站请使用 memory_v5_admin。",
                    "enum": ["reinforce", "protect", "restore", "freeze", "status"],
                },
                "target": {"type": "string", "description": "目标 hash 或查询文本。"},
                "hours": {"type": "number", "description": "protect 的保护时长；<=0 表示永久 pin。"},
                "restore_type": {"type": "string", "description": "restore 类型：relation/entity。"},
            },
            "required": ["action"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        event = self._event(context)
        scope_key = self._scope_key(event)
        action = str(kwargs.get("action", "") or "").strip().lower()
        target = str(kwargs.get("target", "") or "").strip()
        try:
            if action == "status":
                data = await self.plugin.memory_service.status(scope_key=scope_key)
            elif action == "protect":
                data = await self.plugin.memory_service.protect(
                    scope_key=scope_key,
                    query_or_hash=target,
                    hours=float(kwargs.get("hours", 24.0) or 24.0),
                )
            elif action == "reinforce":
                data = await self.plugin.memory_service.reinforce(scope_key=scope_key, query_or_hash=target)
            elif action == "restore":
                data = await self.plugin.memory_service.restore(
                    scope_key=scope_key,
                    hash_value=target,
                    restore_type=str(kwargs.get("restore_type", "relation") or "relation"),
                )
            elif action == "freeze":
                data = await self.plugin.memory_service.freeze(scope_key=scope_key, query_or_hash=target)
            else:
                return _tool_result({"success": False, "error": f"unsupported action: {action}", "scope": scope_key})
            data["scope"] = scope_key
            return _tool_result(data)
        except Exception as exc:
            logger.warning("[memorix] maintain_memory tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})


@dataclass
class MemorixStatsTool(MemorixToolBase):
    name: str = "memory_stats"
    description: str = "获取当前 Memorix 长期记忆统计信息。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        del kwargs
        event = self._event(context)
        scope_key = self._scope_key(event)
        try:
            data = await self.plugin.query_service.stats(scope_key=scope_key)
            data["scope"] = scope_key
            return _tool_result(data)
        except Exception as exc:
            logger.warning("[memorix] memory_stats tool failed: %s", exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key})


__all__ = [
    "MemorixIngestSummaryTool",
    "MemorixIngestTextTool",
    "MemorixMaintainTool",
    "MemorixPersonProfileTool",
    "MemorixSearchTool",
    "MemorixStatsTool",
]
