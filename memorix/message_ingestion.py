"""AstrBot 消息摄取编排。"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Coroutine, Protocol

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .adapters.astrbot_event_adapter import AstrbotEventAdapter
from .services import PersonFactWritebackItem
from .utils.message_formatting import (
    copy_images_to_safe_dir,
    enrich_text_with_captions,
    format_astrbot_event_message,
    message_format_options_from_config,
)

if TYPE_CHECKING:
    from astrbot.api.provider import LLMResponse


class MessageIngestionHost(Protocol):
    config: dict
    context: Any
    ingest_service: Any
    profile_service: Any
    summary_service: Any
    person_fact_writeback_service: Any

    def _resolve_scope(self, event: AstrMessageEvent) -> str: ...

    def _is_cron_event(self, event: AstrMessageEvent) -> bool: ...

    def _spawn_background_task(self, coro: Coroutine[Any, Any, Any]) -> Any: ...

    async def _is_adapted_chat_enabled(self, adapted: Any, user_id: str = "") -> bool: ...

    def _event_ctx_text(self, event: AstrMessageEvent, scope_key: str = "") -> str: ...

    @classmethod
    def _bool_cfg(cls, config: dict, key: str, default: bool) -> bool: ...


class MessageIngestionController:
    """Normalize AstrBot events and dispatch them to Memorix services."""

    def __init__(self, plugin: MessageIngestionHost) -> None:
        self.plugin = plugin

    @staticmethod
    def _normalize_command_prefixes(raw) -> list[str]:
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(item or "") for item in raw]
        else:
            values = []
        prefixes: list[str] = []
        seen = set()
        for item in values:
            prefix = str(item or "").strip()
            if not prefix or prefix in seen:
                continue
            seen.add(prefix)
            prefixes.append(prefix)
        return prefixes or ["/"]

    @staticmethod
    def _strip_leading_mentions(text: str) -> str:
        current = str(text or "").lstrip()
        while True:
            changed = False
            if current.startswith("@"):
                parts = current.split(maxsplit=1)
                if len(parts) == 2:
                    current = parts[1].lstrip()
                    changed = True
            elif current.startswith("[CQ:at,"):
                idx = current.find("]")
                if idx > 0:
                    current = current[idx + 1 :].lstrip()
                    changed = True
            if not changed:
                return current

    @staticmethod
    def _strip_leading_bot_mention(text: str, self_id: str) -> str:
        bot_id = str(self_id or "").strip()
        current = str(text or "").lstrip()
        if not bot_id:
            return current
        patterns = (
            rf"^@\S+\({re.escape(bot_id)}\)(?:\s+|$)",
            rf"^@{re.escape(bot_id)}(?:\s+|$)",
            rf"^\[CQ:at,qq={re.escape(bot_id)}\]\s*",
        )
        while True:
            updated = current
            for pattern in patterns:
                updated = re.sub(pattern, "", updated, count=1).lstrip()
            if updated == current:
                return current
            current = updated

    def _command_prefixes(self) -> list[str]:
        ingest_cfg = self.plugin.config.get("ingest", {}) if isinstance(self.plugin.config.get("ingest"), dict) else {}
        raw_prefixes = ingest_cfg.get("command_prefixes", ingest_cfg.get("command_prefix", ["/"]))
        fingerprint = repr(raw_prefixes)
        if getattr(self, "_command_prefixes_fingerprint", None) != fingerprint:
            self._cached_command_prefixes = self._normalize_command_prefixes(raw_prefixes)
            self._command_prefixes_fingerprint = fingerprint
        return self._cached_command_prefixes

    def _is_command_message(self, text: str) -> bool:
        prefixes = self._command_prefixes()
        content = str(text or "").lstrip()
        if not content:
            return False
        candidates = [content]
        mention_stripped = self._strip_leading_mentions(content)
        if mention_stripped and mention_stripped != content:
            candidates.append(mention_stripped)
        for candidate in candidates:
            for prefix in prefixes:
                if not candidate.startswith(prefix):
                    continue
                if len(candidate) == len(prefix):
                    return True
                if prefix[-1].isalnum():
                    next_char = candidate[len(prefix) : len(prefix) + 1]
                    if next_char and (next_char.isalnum() or next_char == "_"):
                        continue
                return True
        return False

    async def _format_event_text_for_memory(
        self,
        event: AstrMessageEvent,
        *,
        skip_image_caption: bool = False,
    ) -> str:
        options = message_format_options_from_config(self.plugin.config)
        if skip_image_caption:
            options.include_image_caption = False
        formatted = await format_astrbot_event_message(
            event,
            context=self.plugin.context,
            options=options,
        )
        self_id = str(
            getattr(event, "get_self_id", lambda: "")()
            or getattr(getattr(event, "message_obj", None), "self_id", "")
            or ""
        )
        return self._strip_leading_bot_mention(formatted.text, self_id)

    async def _ingest_event_message(self, event: AstrMessageEvent, role: str, text: str) -> bool:
        adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
        normalized_role = str(role or "user").strip().lower() or "user"
        filter_user_id = adapted.sender_id
        self_id = str(
            getattr(event, "get_self_id", lambda: "")()
            or getattr(getattr(event, "message_obj", None), "self_id", "")
            or ""
        )
        sender_id = adapted.sender_id
        sender_name = adapted.sender_name
        event_timestamp = adapted.timestamp
        if normalized_role == "assistant":
            sender_id = self_id or "assistant"
            sender_name = "assistant"
            event_timestamp = time.time()
        elif normalized_role == "user" and adapted.sender_id and self_id and adapted.sender_id == self_id:
            return False

        content = str(text or "").strip()
        if not content and self.plugin._bool_cfg(self.plugin.config, "ingest.skip_empty_text", True):
            return False

        if not await self.plugin._is_adapted_chat_enabled(adapted, filter_user_id):
            logger.debug(
                "[memorix] skip chat-filtered message role=%s %s",
                normalized_role,
                self.plugin._event_ctx_text(event, adapted.scope_key),
            )
            return False

        source = f"chat:{adapted.platform}:{adapted.session_id}"
        result = await self.plugin.ingest_service.ingest_message(
            scope_key=adapted.scope_key,
            session_id=adapted.session_id,
            role=normalized_role,
            content=content,
            source=source,
            user_id=sender_id,
            group_id=adapted.group_id,
            group_name=adapted.group_name,
            platform=adapted.platform,
            unified_msg_origin=adapted.unified_msg_origin,
            sender_name=sender_name,
            message_id=adapted.message_id,
            role_origin=normalized_role,
            timestamp=event_timestamp,
            time_meta={"event_time": event_timestamp} if event_timestamp else None,
            filter_user_id=filter_user_id,
        )
        if bool(result.get("skipped", False)):
            return False

        if normalized_role == "user":
            await self.plugin.profile_service.upsert_registry_from_event(
                scope_key=adapted.scope_key,
                platform=adapted.platform,
                sender_id=sender_id,
                sender_name=sender_name or sender_id,
                group_id=adapted.group_id,
                group_name=adapted.group_name,
                session_id=adapted.session_id,
                unified_msg_origin=adapted.unified_msg_origin,
                timestamp=float(event_timestamp) if event_timestamp else None,
            )
        logger.debug(
            "[memorix] ingested role=%s chars=%s %s",
            normalized_role,
            len(content),
            self.plugin._event_ctx_text(event, adapted.scope_key),
        )
        return True

    async def handle_message(self, event: AstrMessageEvent) -> None:
        if not self.plugin._bool_cfg(self.plugin.config, "ingest.record_all_events", True):
            return
        if self.plugin._is_cron_event(event):
            return
        try:
            safe_paths = await copy_images_to_safe_dir(event)
            text = await self._format_event_text_for_memory(event, skip_image_caption=True)
            self.plugin._spawn_background_task(self._record_message_background(event, text, safe_paths))
        except Exception as exc:
            logger.warning(
                "[memorix] prepare user message failed: %s (%s)",
                exc,
                self.plugin._event_ctx_text(event),
                exc_info=True,
            )

    async def _record_message_background(
        self,
        event: AstrMessageEvent,
        text: str,
        safe_paths: list[str] | None = None,
    ) -> None:
        try:
            if safe_paths:
                text = await enrich_text_with_captions(
                    text,
                    safe_paths,
                    self.plugin.context,
                    self.plugin.config,
                    event,
                )
            adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
            if not await self.plugin._is_adapted_chat_enabled(adapted, adapted.sender_id):
                logger.debug(
                    "[memorix] skip chat-filtered message %s", self.plugin._event_ctx_text(event, adapted.scope_key)
                )
                return
            if not text and self.plugin._bool_cfg(self.plugin.config, "ingest.skip_empty_text", True):
                logger.debug("[memorix] skip empty/placeholder message %s", self.plugin._event_ctx_text(event))
                return
            if self.plugin._bool_cfg(
                self.plugin.config, "ingest.skip_command_messages", True
            ) and self._is_command_message(text):
                logger.debug("[memorix] skip command message %s", self.plugin._event_ctx_text(event))
                return
            ingested = await self._ingest_event_message(event, "user", text)
            if not ingested:
                return
            if not self.plugin._bool_cfg(self.plugin.config, "summarization.auto_import.after_reply_only", True):
                await self.plugin.summary_service.maybe_enqueue_auto_summary(
                    scope_key=adapted.scope_key,
                    session_id=adapted.session_id,
                )
        except Exception as exc:
            logger.warning(
                "[memorix] ingest user message failed: %s (%s)", exc, self.plugin._event_ctx_text(event), exc_info=True
            )

    async def handle_llm_response(self, event: AstrMessageEvent, resp: "LLMResponse") -> None:
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text or self.plugin._is_cron_event(event):
            return
        try:
            safe_paths = await copy_images_to_safe_dir(event)
            user_text = await self._format_event_text_for_memory(event, skip_image_caption=True)
            self.plugin._spawn_background_task(self._record_llm_response_background(event, text, user_text, safe_paths))
        except Exception as exc:
            logger.warning(
                "[memorix] prepare LLM response failed: %s (%s)",
                exc,
                self.plugin._event_ctx_text(event),
                exc_info=True,
            )

    async def _record_llm_response_background(
        self,
        event: AstrMessageEvent,
        text: str,
        user_text: str,
        safe_paths: list[str] | None = None,
    ) -> None:
        try:
            if safe_paths:
                user_text = await enrich_text_with_captions(
                    user_text,
                    safe_paths,
                    self.plugin.context,
                    self.plugin.config,
                    event,
                )
            adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
            if not await self.plugin._is_adapted_chat_enabled(adapted, adapted.sender_id):
                logger.debug(
                    "[memorix] skip chat-filtered LLM response %s",
                    self.plugin._event_ctx_text(event, adapted.scope_key),
                )
                return
            if self.plugin._bool_cfg(
                self.plugin.config, "ingest.skip_command_messages", True
            ) and self._is_command_message(user_text):
                logger.debug("[memorix] skip command LLM response %s", self.plugin._event_ctx_text(event))
                return
            ingested = await self._ingest_event_message(event, "assistant", text)
            if not ingested:
                return
            if user_text and not self._is_command_message(user_text):
                await self.plugin.person_fact_writeback_service.enqueue(
                    PersonFactWritebackItem(
                        scope_key=adapted.scope_key,
                        session_id=adapted.session_id,
                        user_text=user_text,
                        assistant_text=text,
                        user_id=adapted.sender_id,
                        group_id=adapted.group_id,
                        group_name=adapted.group_name,
                        platform=adapted.platform,
                        sender_name=adapted.sender_name,
                        message_id=adapted.message_id,
                        timestamp=float(adapted.timestamp) if adapted.timestamp else time.time(),
                        unified_msg_origin=adapted.unified_msg_origin,
                    )
                )
            result = await self.plugin.summary_service.maybe_enqueue_auto_summary(
                scope_key=adapted.scope_key,
                session_id=adapted.session_id,
            )
            if result.get("queued"):
                logger.debug(
                    "[memorix] auto summary queued task=%s %s",
                    str(result.get("task_id", "") or ""),
                    self.plugin._event_ctx_text(event, adapted.scope_key),
                )
        except Exception as exc:
            logger.warning(
                "[memorix] ingest llm response failed: %s (%s)", exc, self.plugin._event_ctx_text(event), exc_info=True
            )
