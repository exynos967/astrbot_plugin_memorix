"""MaiBot-style person fact writeback for AstrBot events."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..app_context import ScopeRuntimeManager
from ..core.utils.hash import compute_hash, normalize_text
from ..providers.astrbot_provider_bridge import AstrBotProviderBridge
from .content_router import MemoryContentRouter


@dataclass(slots=True)
class PersonFactWritebackItem:
    scope_key: str
    session_id: str
    user_text: str
    assistant_text: str
    user_id: str = ""
    group_id: str = ""
    group_name: str = ""
    platform: str = ""
    sender_name: str = ""
    message_id: str = ""
    timestamp: float = 0.0
    unified_msg_origin: str = ""


class PersonFactWritebackService:
    """Extract stable user-supported facts after a bot reply.

    The service intentionally writes only facts that are grounded by the user's
    latest message. The bot reply is provided as context, not as evidence.
    """

    def __init__(self, runtime_manager: ScopeRuntimeManager, plugin_config: Dict[str, Any] | None = None) -> None:
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self._queue: asyncio.Queue[PersonFactWritebackItem] = asyncio.Queue(maxsize=self._queue_maxsize())
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False

    @staticmethod
    def _nested(config: Dict[str, Any], key: str, default: Any = None) -> Any:
        current: Any = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self._nested(self.plugin_config, key, default)

    def _enabled(self) -> bool:
        return bool(self._cfg("person_fact_writeback.enabled", False))

    def _queue_maxsize(self) -> int:
        try:
            return max(1, int(self._cfg("person_fact_writeback.queue_maxsize", 256) or 256))
        except (TypeError, ValueError):
            return 256

    def _max_facts_per_turn(self) -> int:
        try:
            return max(1, min(10, int(self._cfg("person_fact_writeback.max_facts_per_turn", 5) or 5)))
        except (TypeError, ValueError):
            return 5

    def _min_user_text_chars(self) -> int:
        try:
            return max(1, int(self._cfg("person_fact_writeback.min_user_text_chars", 4) or 4))
        except (TypeError, ValueError):
            return 4

    def _max_registry_facts(self) -> int:
        try:
            return max(1, int(self._cfg("person_fact_writeback.max_registry_facts", 30) or 30))
        except (TypeError, ValueError):
            return 30

    def _max_evidence_chars(self) -> int:
        try:
            return max(80, int(self._cfg("person_fact_writeback.max_evidence_chars", 800) or 800))
        except (TypeError, ValueError):
            return 800

    async def start(self) -> None:
        if not self._enabled():
            return
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(self._worker_loop(), name="memorix_person_fact_writeback")

    async def close(self) -> None:
        self._stopping = True
        worker = self._worker_task
        self._worker_task = None
        if worker is None:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[memorix] close person fact writeback worker failed: %s", exc)

    async def enqueue(self, item: PersonFactWritebackItem) -> None:
        if not self._enabled() or self._stopping:
            return
        if not self._is_candidate(item):
            return
        await self.start()
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning("[memorix] person fact writeback queue full, skip message_id=%s", item.message_id)

    def _is_candidate(self, item: PersonFactWritebackItem) -> bool:
        user_text = str(item.user_text or "").strip()
        assistant_text = str(item.assistant_text or "").strip()
        if not user_text or len(user_text) < self._min_user_text_chars():
            return False
        if not assistant_text:
            return False
        if MemoryContentRouter._looks_ephemeral(assistant_text) or MemoryContentRouter._looks_placeholder_only(assistant_text):
            return False
        if not str(item.user_id or "").strip():
            return False
        return True

    async def _worker_loop(self) -> None:
        try:
            while not self._stopping:
                item = await self._queue.get()
                try:
                    await self._handle_item(item)
                except Exception as exc:
                    logger.warning("[memorix] person fact writeback failed: %s", exc, exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _handle_item(self, item: PersonFactWritebackItem) -> None:
        runtime = await self.runtime_manager.get_runtime(item.scope_key)
        ctx = runtime.context
        checker = getattr(ctx, "is_chat_enabled", None)
        if callable(checker) and not checker(
            stream_id=item.session_id,
            group_id=item.group_id,
            user_id=item.user_id,
        ):
            logger.debug(
                "[memorix] skip person fact writeback by chat filter: session=%s user=%s group=%s",
                item.session_id,
                item.user_id,
                item.group_id,
            )
            return
        person_id = self._person_id(item)
        if not person_id:
            return

        record = ctx.metadata_store.get_person_registry(person_id)
        if record is None:
            record = ctx.metadata_store.upsert_person_registry(
                person_id=person_id,
                person_name=item.sender_name or item.user_id,
                nickname=item.sender_name or item.user_id,
                user_id=item.user_id,
                platform=item.platform,
                group_nick_name=[],
                memory_points=[],
                last_know=float(item.timestamp) if item.timestamp else time.time(),
                metadata={"source": "person_fact_writeback"},
            )
        display_name = str(record.get("display_name") or record.get("person_name") or item.sender_name or item.user_id)

        facts = await self._extract_facts(
            ctx=ctx,
            person_name=display_name,
            user_text=item.user_text,
            assistant_text=item.assistant_text,
            item=item,
        )
        if not facts:
            return

        stored = []
        for fact in facts[: self._max_facts_per_turn()]:
            stored_hash = await self._store_fact(ctx=ctx, item=item, person_id=person_id, person_name=display_name, fact=fact)
            if stored_hash:
                stored.append(stored_hash)
        if stored:
            self._merge_registry_facts(ctx.metadata_store, person_id=person_id, facts=facts)
            logger.debug("[memorix] person facts stored count=%s person=%s", len(stored), person_id)

    def _person_id(self, item: PersonFactWritebackItem) -> str:
        uid = str(item.user_id or "").strip()
        if not uid:
            return ""
        platform = str(item.platform or "").strip()
        return f"{platform}:{uid}" if platform else uid

    async def _extract_facts(self, *, ctx: Any, person_name: str, user_text: str, assistant_text: str, item: PersonFactWritebackItem) -> List[str]:
        prompt = self._build_prompt(
            person_name=person_name,
            user_text=self._truncate(user_text, self._max_evidence_chars()),
            assistant_text=self._truncate(assistant_text, self._max_evidence_chars()),
        )
        try:
            raw = await self._complete(ctx, prompt, item)
        except Exception as exc:
            logger.debug("[memorix] person fact extractor failed: %s", exc, exc_info=True)
            return []
        return self._parse_fact_list(raw)[: self._max_facts_per_turn()]

    def _build_prompt(self, *, person_name: str, user_text: str, assistant_text: str) -> str:
        return f"""你要从用户原始发言中提取“关于{person_name}的稳定事实”。

目标人物：{person_name}
用户原始发言证据：
{user_text}

机器人回复仅作为理解上下文，不能作为事实来源：
{assistant_text}

请只提取满足以下条件的事实：
1. 必须能被“用户原始发言证据”直接支持。
2. 明确是关于目标人物本人的信息。
3. 具有相对稳定性，可以作为长期记忆保存。
4. 用简洁中文陈述句表达。
5. 如果用户原始发言中出现“我/我的/自己”，默认指目标人物，请改写成关于目标人物的第三人称事实。

不要提取临时情绪、客套话、当前动作、机器人建议、猜测、玩笑、承诺或无关信息。
严格输出 JSON 数组，例如：["{person_name}喜欢深夜打游戏"]。
如果没有可写入的事实，输出 []。"""

    async def _complete(self, ctx: Any, prompt: str, item: PersonFactWritebackItem) -> str:
        bridge = getattr(ctx, "provider_bridge", None)
        provider_id = str(self._cfg("person_fact_writeback.chat_provider_id", "") or "").strip()
        if bridge is not None and getattr(bridge, "enabled", False):
            selected_bridge = bridge
            if provider_id:
                selected_bridge = AstrBotProviderBridge(
                    astrbot_context=getattr(bridge, "_context", None),
                    chat_provider_id=provider_id,
                    embedding_provider_id=str(getattr(bridge, "embedding_provider_id", "") or ""),
                )
            return await selected_bridge.generate_text(
                prompt,
                temperature=float(self._cfg("person_fact_writeback.temperature", 0.1) or 0.1),
                max_tokens=int(self._cfg("person_fact_writeback.max_tokens", 800) or 800),
                unified_msg_origin=item.unified_msg_origin,
            )

        llm_client = getattr(ctx, "llm_client", None)
        if llm_client is None or not callable(getattr(llm_client, "complete", None)):
            return ""
        return await llm_client.complete(
            prompt,
            temperature=float(self._cfg("person_fact_writeback.temperature", 0.1) or 0.1),
            max_tokens=int(self._cfg("person_fact_writeback.max_tokens", 800) or 800),
        )

    @staticmethod
    def _parse_fact_list(raw: str) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        payload: Any = None
        for candidate in (text, PersonFactWritebackService._extract_json_array_text(text)):
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            for key in ("facts", "items", "result"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            return []

        facts: List[str] = []
        seen = set()
        for item in payload:
            fact = normalize_text(str(item or "").strip().strip("- "))
            if len(fact) < 4 or fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return facts

    @staticmethod
    def _extract_json_array_text(text: str) -> str:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return text[start : end + 1]
        return ""

    async def _store_fact(
        self,
        *,
        ctx: Any,
        item: PersonFactWritebackItem,
        person_id: str,
        person_name: str,
        fact: str,
    ) -> str:
        fact_text = normalize_text(fact)
        if not fact_text:
            return ""
        content = fact_text if person_name in fact_text else f"{person_name}：{fact_text}"
        timestamp = float(item.timestamp) if item.timestamp else time.time()
        source = f"person_fact:{item.session_id}:{person_id}"
        metadata = {
            "external_id": compute_hash(f"person_fact:{item.session_id}:{person_id}:{item.message_id}:{fact_text}"),
            "source_type": "person_fact",
            "chat_id": item.session_id,
            "person_ids": [person_id],
            "participants": [person_name],
            "sender_id": item.user_id,
            "sender_name": item.sender_name,
            "group_id": item.group_id,
            "group_name": item.group_name,
            "platform": item.platform,
            "message_id": item.message_id,
            "evidence_text": self._truncate(item.user_text, self._max_evidence_chars()),
            "writeback_source": "person_fact_writeback",
        }
        paragraph_hash = ctx.metadata_store.add_paragraph(
            content=content,
            source=source,
            metadata={key: value for key, value in metadata.items() if value not in ("", [], None)},
            knowledge_type="factual",
            time_meta={"event_time": timestamp},
        )
        try:
            paragraph_vector_service = getattr(ctx, "paragraph_vector_service", None)
            if paragraph_vector_service is not None and hasattr(paragraph_vector_service, "ensure_paragraph_vector"):
                await paragraph_vector_service.ensure_paragraph_vector(paragraph_hash, content)
            elif paragraph_hash not in ctx.vector_store:
                embedding = await ctx.embedding_manager.encode(content)
                if getattr(embedding, "ndim", 1) == 1:
                    embedding = embedding.reshape(1, -1)
                ctx.vector_store.add(vectors=embedding, ids=[paragraph_hash])
                updater = getattr(ctx.metadata_store, "update_vector_index", None)
                if callable(updater):
                    updater("paragraph", paragraph_hash, 1)
        except Exception as exc:
            updater = getattr(ctx.metadata_store, "update_vector_index", None)
            if callable(updater):
                updater("paragraph", paragraph_hash, -1)
            logger.debug("[memorix] person fact vector write failed: %s", exc, exc_info=True)

        for entity in dict.fromkeys([person_id, person_name]):
            if str(entity or "").strip():
                try:
                    ctx.metadata_store.add_entity(
                        name=str(entity),
                        source_paragraph=paragraph_hash,
                        metadata={"source": "person_fact_writeback", "chat_id": item.session_id},
                    )
                except Exception:
                    logger.debug("[memorix] person fact add entity failed", exc_info=True)
        try:
            ctx.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
            ctx.vector_store.save()
            ctx.graph_store.save()
        except Exception:
            logger.debug("[memorix] person fact persist failed", exc_info=True)
        return paragraph_hash

    def _merge_registry_facts(self, metadata_store: Any, *, person_id: str, facts: List[str]) -> None:
        if not bool(self._cfg("person_fact_writeback.update_registry_memory_points", True)):
            return
        record = metadata_store.get_person_registry(person_id)
        if not record:
            return
        existing = record.get("memory_points") if isinstance(record.get("memory_points"), list) else []
        merged: List[str] = []
        seen = set()
        for item in [*existing, *facts]:
            text = normalize_text(str(item or ""))
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        merged = merged[-self._max_registry_facts() :]
        metadata_store.upsert_person_registry(
            person_id=person_id,
            person_name=str(record.get("person_name", "") or ""),
            nickname=str(record.get("nickname", "") or ""),
            user_id=str(record.get("user_id", "") or ""),
            platform=str(record.get("platform", "") or ""),
            group_nick_name=record.get("group_nick_name") or [],
            memory_points=merged,
            last_know=time.time(),
            metadata=record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        content = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(content) <= max_chars:
            return content
        return f"{content[: max(0, max_chars - 1)]}…"
