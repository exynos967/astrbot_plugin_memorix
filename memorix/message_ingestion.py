"""AstrBot 消息摄取编排。"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Protocol

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .adapters.astrbot_event_adapter import AstrbotEventAdapter
from .core.image.component_paths import build_chat_external_ref
from .core.utils.chat_migration_gate import get_chat_migration_gate
from .core.utils.hash import compute_hash
from .engine.host_shims.embedding_shim import (
    is_image_embedding_configured,
    is_image_memory_enabled,
)
from .services import PersonFactWritebackItem
from .utils.message_formatting import (
    AstrBotMessageFormatter,
    CapturedChatImage,
    _remove_quiet,
    capture_event_images_to_safe_dir,
    enrich_text_with_captions_detailed,
    format_astrbot_event_message,
    message_format_options_from_config,
)

if TYPE_CHECKING:
    from astrbot.api.provider import LLMResponse

_IMAGE_TOKEN_STRIP_RE = re.compile(r"\[图片(?::[^\]]*)?\]")
_EVENT_CAPTURE_ATTR = "_memorix_event_capture"
_EVENT_CAPTURE_TASK_ATTR = "_memorix_event_capture_task"
_EVENT_CAPTURE_FALLBACK_MAX = 128


@dataclass
class _EventCaptureState:
    captured_images: list[CapturedChatImage]
    text_with_placeholders: str
    plain_user_text: str
    self_id: str
    skip_placeholder_only: bool
    max_text_chars: int
    process_task: asyncio.Task[str] | None = None

    def cleanup_files(self) -> None:
        for item in self.captured_images:
            if item.safe_path:
                _remove_quiet(item.safe_path)


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
        self._fallback_captures: OrderedDict[int, tuple[Any, _EventCaptureState]] = OrderedDict()
        self._fallback_capture_tasks: OrderedDict[int, tuple[Any, asyncio.Task[_EventCaptureState]]] = OrderedDict()

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

    @staticmethod
    def _extract_self_id(event: AstrMessageEvent) -> str:
        return str(
            getattr(event, "get_self_id", lambda: "")()
            or getattr(getattr(event, "message_obj", None), "self_id", "")
            or ""
        )

    @classmethod
    def _extract_plain_user_statement(cls, text_with_placeholders: str, self_id: str) -> str:
        stripped = _IMAGE_TOKEN_STRIP_RE.sub(" ", str(text_with_placeholders or ""))
        stripped = re.sub(r"[ \t]+", " ", stripped).strip()
        stripped = cls._strip_leading_bot_mention(stripped, self_id)
        if AstrBotMessageFormatter._is_placeholder_only(stripped):
            return ""
        return stripped

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
        self_id = self._extract_self_id(event)
        return self._strip_leading_bot_mention(formatted.text, self_id)

    def _get_cached_capture(self, event: AstrMessageEvent) -> _EventCaptureState | None:
        cached = getattr(event, _EVENT_CAPTURE_ATTR, None)
        if isinstance(cached, _EventCaptureState):
            return cached
        entry = self._fallback_captures.get(id(event))
        if entry is not None and entry[0] is event:
            self._fallback_captures.move_to_end(id(event))
            return entry[1]
        return None

    def _store_cached_capture(self, event: AstrMessageEvent, state: _EventCaptureState) -> None:
        try:
            setattr(event, _EVENT_CAPTURE_ATTR, state)
            return
        except Exception:
            pass
        key = id(event)
        self._fallback_captures[key] = (event, state)
        self._fallback_captures.move_to_end(key)
        while len(self._fallback_captures) > _EVENT_CAPTURE_FALLBACK_MAX:
            self._fallback_captures.popitem(last=False)

    def _get_cached_capture_task(self, event: AstrMessageEvent) -> asyncio.Task[_EventCaptureState] | None:
        cached = getattr(event, _EVENT_CAPTURE_TASK_ATTR, None)
        if isinstance(cached, asyncio.Task):
            return cached
        entry = self._fallback_capture_tasks.get(id(event))
        if entry is not None and entry[0] is event:
            self._fallback_capture_tasks.move_to_end(id(event))
            return entry[1]
        return None

    def _store_cached_capture_task(self, event: AstrMessageEvent, task: asyncio.Task[_EventCaptureState]) -> None:
        try:
            setattr(event, _EVENT_CAPTURE_TASK_ATTR, task)
            return
        except Exception:
            pass
        key = id(event)
        self._fallback_capture_tasks[key] = (event, task)
        self._fallback_capture_tasks.move_to_end(key)
        while len(self._fallback_capture_tasks) > _EVENT_CAPTURE_FALLBACK_MAX:
            self._fallback_capture_tasks.popitem(last=False)

    def _max_image_bytes(self) -> int:
        img_cfg = self.plugin.config.get("image_memory", {}) if isinstance(self.plugin.config.get("image_memory"), dict) else {}
        return max(1024, int(img_cfg.get("max_bytes", 10 * 1024 * 1024) or (10 * 1024 * 1024)))

    async def _build_event_capture(
        self,
        event: AstrMessageEvent,
        *,
        need_safe_files: bool,
    ) -> _EventCaptureState:
        options = message_format_options_from_config(self.plugin.config)
        orig_skip_placeholder_only = bool(options.skip_placeholder_only)
        max_text_chars = max(200, int(options.max_text_chars or 2000))
        image_mem_enabled = is_image_memory_enabled(config=self.plugin.config)
        max_bytes = self._max_image_bytes()

        captured_images: list[CapturedChatImage] = []
        if need_safe_files:
            try:
                captured_images = await capture_event_images_to_safe_dir(
                    event,
                    copy_to_cache=True,
                    max_bytes=max_bytes,
                )
            except TypeError:
                captured_images = await capture_event_images_to_safe_dir(event)
        elif image_mem_enabled:
            try:
                captured_images = await capture_event_images_to_safe_dir(
                    event,
                    copy_to_cache=False,
                    max_bytes=max_bytes,
                )
            except TypeError:
                captured_images = await capture_event_images_to_safe_dir(event)

        try:
            options.include_image_caption = False
            options.skip_placeholder_only = False
            formatted = await format_astrbot_event_message(
                event,
                context=self.plugin.context,
                options=options,
            )
            self_id = self._extract_self_id(event)
            text_with_placeholders = self._strip_leading_bot_mention(formatted.text, self_id)
            plain_user_text = self._extract_plain_user_statement(text_with_placeholders, self_id)
            state = _EventCaptureState(
                captured_images=captured_images,
                text_with_placeholders=text_with_placeholders,
                plain_user_text=plain_user_text,
                self_id=self_id,
                skip_placeholder_only=orig_skip_placeholder_only,
                max_text_chars=max_text_chars,
            )
            self._store_cached_capture(event, state)
            return state
        except BaseException:
            for item in captured_images:
                if item.safe_path:
                    _remove_quiet(item.safe_path)
            raise

    async def capture_event_once(
        self,
        event: AstrMessageEvent,
        *,
        need_safe_files: bool | None = None,
    ) -> _EventCaptureState:
        cached = self._get_cached_capture(event)
        if cached is not None:
            return cached

        task = self._get_cached_capture_task(event)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            if need_safe_files is None:
                options = message_format_options_from_config(self.plugin.config)
                need_safe_files = bool(options.include_image_caption)
            task = asyncio.create_task(self._build_event_capture(event, need_safe_files=bool(need_safe_files)))
            self._store_cached_capture_task(event, task)
        return await asyncio.shield(task)

    async def _capture_event_once(self, event: AstrMessageEvent) -> _EventCaptureState:
        return await self.capture_event_once(event)

    def _finalize_prepared_text(self, raw_text: str, capture: _EventCaptureState) -> str:
        prepared = self._strip_leading_bot_mention(raw_text, capture.self_id)
        if capture.skip_placeholder_only and AstrBotMessageFormatter._is_placeholder_only(prepared):
            prepared = ""
        if len(prepared) > capture.max_text_chars:
            prepared = prepared[: max(0, capture.max_text_chars - 1)].rstrip() + "…"
        return prepared

    def _read_safe_image_bytes(self, safe_path: str) -> bytes | None:
        if not safe_path or not os.path.isfile(safe_path):
            return None
        max_bytes = self._max_image_bytes()
        try:
            file_size = os.path.getsize(safe_path)
            if file_size <= 0 or file_size > max_bytes:
                return None
            with open(safe_path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    async def _ingest_chat_images_if_enabled(
        self,
        *,
        event: AstrMessageEvent,
        capture: _EventCaptureState,
        raw_bytes_list: list[bytes | None],
        captions: list[str],
        prepared_text: str,
    ) -> None:
        if not capture.captured_images:
            return
        if not is_image_memory_enabled(config=self.plugin.config):
            return
        admin_service = getattr(self.plugin, "admin_service", None)
        if admin_service is None:
            return

        adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
        if adapted.sender_id and capture.self_id and adapted.sender_id == capture.self_id:
            return
        check_cmd_text = capture.plain_user_text or prepared_text
        if (
            self.plugin._bool_cfg(self.plugin.config, "ingest.skip_command_messages", True)
            and check_cmd_text
            and self._is_command_message(check_cmd_text)
        ):
            return
        if not await self.plugin._is_adapted_chat_enabled(adapted, adapted.sender_id):
            return

        chat_id = str(adapted.session_id or adapted.scope_key or "default").strip() or "default"
        occurred_at = float(adapted.timestamp) if adapted.timestamp else time.time()
        base_msg_id = str(adapted.message_id or "").strip()
        embedding_configured = is_image_embedding_configured(config=self.plugin.config)
        needs_job_drain = False

        async with get_chat_migration_gate().activity(chat_id):
            for idx, captured_img in enumerate(capture.captured_images):
                img_bytes = raw_bytes_list[idx] if idx < len(raw_bytes_list) else None
                if not img_bytes:
                    continue
                caption = str(captions[idx] if idx < len(captions) else "").strip()
                msg_id = base_msg_id or compute_hash(
                    f"{chat_id}:{adapted.sender_id}:{occurred_at}:{captured_img.component_path}"
                )
                external_ref = build_chat_external_ref(
                    chat_id=chat_id,
                    message_id=msg_id,
                    component_path=captured_img.component_path,
                )
                try:
                    ingest_res = await admin_service.image_admin(
                        scope_key=adapted.scope_key,
                        action="ingest",
                        image_bytes=img_bytes,
                        source_kind="chat",
                        external_ref=external_ref,
                        scope_type="chat",
                        chat_id=chat_id,
                        message_id=msg_id,
                        component_path=captured_img.component_path,
                        occurred_at=occurred_at,
                        user_statement=capture.plain_user_text,
                    )
                    if isinstance(ingest_res, dict) and ingest_res.get("success"):
                        emb_status = str(
                            ingest_res.get("embedding_status") or ingest_res.get("status") or ""
                        ).strip().lower()
                        if embedding_configured and emb_status in {"uninitialized", "pending"}:
                            needs_job_drain = True
                        if caption and ingest_res.get("content_hash"):
                            await admin_service.image_admin(
                                scope_key=adapted.scope_key,
                                action="describe",
                                content_hash=str(ingest_res["content_hash"]),
                                text=f"[图片：{caption}]",
                            )
                except Exception as exc:
                    logger.debug(
                        "[memorix] skip chat image memory ingest (%s): %s",
                        external_ref,
                        exc,
                        exc_info=True,
                    )

            if needs_job_drain and embedding_configured:
                try:
                    await admin_service.image_admin(scope_key=adapted.scope_key, action="process_jobs")
                except Exception as exc:
                    logger.debug("[memorix] background image embedding job drain skipped: %s", exc, exc_info=True)

    async def _process_captured_event_once(
        self,
        event: AstrMessageEvent,
        capture: _EventCaptureState,
    ) -> str:
        if not capture.captured_images:
            return self._finalize_prepared_text(capture.text_with_placeholders, capture)

        image_mem_enabled = is_image_memory_enabled(config=self.plugin.config)
        raw_bytes_list: list[bytes | None] = [
            (item.image_bytes or self._read_safe_image_bytes(item.safe_path)) if image_mem_enabled else None
            for item in capture.captured_images
        ]
        options = message_format_options_from_config(self.plugin.config)
        enriched = capture.text_with_placeholders
        captions: list[str] = ["" for _ in capture.captured_images]
        try:
            if options.include_image_caption:
                cache_dir = os.path.join(tempfile.gettempdir(), "astrbot_memorix_img_cache")
                safe_paths: list[str] = []
                for item in capture.captured_images:
                    if item.safe_path and os.path.isfile(item.safe_path):
                        safe_paths.append(item.safe_path)
                    elif item.image_bytes:
                        os.makedirs(cache_dir, exist_ok=True)
                        dst = os.path.join(cache_dir, f"{uuid.uuid4().hex}.jpg")
                        with open(dst, "wb") as fh:
                            fh.write(item.image_bytes)
                        item.safe_path = dst
                        safe_paths.append(dst)
                    else:
                        safe_paths.append("")
                if any(safe_paths):
                    enriched, captions = await enrich_text_with_captions_detailed(
                        capture.text_with_placeholders,
                        safe_paths,
                        self.plugin.context,
                        self.plugin.config,
                        event,
                    )
        finally:
            capture.cleanup_files()

        prepared_text = self._finalize_prepared_text(enriched, capture)
        await self._ingest_chat_images_if_enabled(
            event=event,
            capture=capture,
            raw_bytes_list=raw_bytes_list,
            captions=captions,
            prepared_text=prepared_text,
        )
        return prepared_text

    async def _ensure_event_processed(
        self,
        event: AstrMessageEvent,
        capture: _EventCaptureState,
    ) -> str:
        if capture.process_task is None:
            capture.process_task = asyncio.create_task(self._process_captured_event_once(event, capture))
        return await asyncio.shield(capture.process_task)

    async def _ingest_event_message(self, event: AstrMessageEvent, role: str, text: str) -> bool:
        adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
        normalized_role = str(role or "user").strip().lower() or "user"
        filter_user_id = adapted.sender_id
        self_id = self._extract_self_id(event)
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
        async with get_chat_migration_gate().activity(adapted.session_id):
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
            capture = await self._capture_event_once(event)
            self.plugin._spawn_background_task(self._record_message_background(event, capture))
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
        capture: _EventCaptureState,
    ) -> None:
        try:
            text = await self._ensure_event_processed(event, capture)
            adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
            if not await self.plugin._is_adapted_chat_enabled(adapted, adapted.sender_id):
                logger.debug(
                    "[memorix] skip chat-filtered message %s", self.plugin._event_ctx_text(event, adapted.scope_key)
                )
                return
            if not text and self.plugin._bool_cfg(self.plugin.config, "ingest.skip_empty_text", True):
                logger.debug("[memorix] skip empty/placeholder message %s", self.plugin._event_ctx_text(event))
                return
            check_cmd_text = capture.plain_user_text or text
            if self.plugin._bool_cfg(
                self.plugin.config, "ingest.skip_command_messages", True
            ) and self._is_command_message(check_cmd_text):
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
        except asyncio.CancelledError:
            if capture.process_task is None:
                capture.cleanup_files()
            raise
        except Exception as exc:
            logger.warning(
                "[memorix] ingest user message failed: %s (%s)", exc, self.plugin._event_ctx_text(event), exc_info=True
            )

    async def handle_llm_response(self, event: AstrMessageEvent, resp: "LLMResponse") -> None:
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text or self.plugin._is_cron_event(event):
            return
        try:
            capture = await self._capture_event_once(event)
            self.plugin._spawn_background_task(self._record_llm_response_background(event, text, capture))
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
        capture: _EventCaptureState,
    ) -> None:
        try:
            user_text = await self._ensure_event_processed(event, capture)
            adapted = AstrbotEventAdapter.from_event(event, self.plugin._resolve_scope(event))
            if not await self.plugin._is_adapted_chat_enabled(adapted, adapted.sender_id):
                logger.debug(
                    "[memorix] skip chat-filtered LLM response %s",
                    self.plugin._event_ctx_text(event, adapted.scope_key),
                )
                return
            check_cmd_text = capture.plain_user_text or user_text
            if self.plugin._bool_cfg(
                self.plugin.config, "ingest.skip_command_messages", True
            ) and self._is_command_message(check_cmd_text):
                logger.debug("[memorix] skip command LLM response %s", self.plugin._event_ctx_text(event))
                return
            ingested = await self._ingest_event_message(event, "assistant", text)
            if not ingested:
                return
            if user_text and not self._is_command_message(check_cmd_text):
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
        except asyncio.CancelledError:
            if capture.process_task is None:
                capture.cleanup_files()
            raise
        except Exception as exc:
            logger.warning(
                "[memorix] ingest llm response failed: %s (%s)", exc, self.plugin._event_ctx_text(event), exc_info=True
            )
