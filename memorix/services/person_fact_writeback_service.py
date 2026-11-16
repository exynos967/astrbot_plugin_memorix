"""MaiBot-style person fact writeback for AstrBot events."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger("astrbot_plugin_memorix")

from ..amemorix.context import activate_context_shims
from ..app_context import ScopeRuntimeManager
from ..core.utils.async_work import cancel_and_wait_for_tasks, cancellation_safe_to_thread
from ..core.utils.chat_migration_gate import get_chat_migration_gate
from ..core.utils.fact_deduplication import (
    FactDeduplicator,
    fact_scope_lock,
    normalized_fact,
    observation_id,
)
from ..core.utils.hash import compute_hash, normalize_text
from ..core.utils.summary_identity import observe_identity
from ..engine.host_shims import config_shim, llm_shim
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

    _EXTRACTION_CACHE_MAX = 256

    def __init__(self, runtime_manager: ScopeRuntimeManager, plugin_config: Dict[str, Any] | None = None) -> None:
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self._queue: asyncio.Queue[PersonFactWritebackItem] = asyncio.Queue(maxsize=self._queue_maxsize())
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._extraction_cache: OrderedDict[tuple[str, str, str], List[str]] = OrderedDict()

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
        if worker is None:
            return
        try:
            await cancel_and_wait_for_tasks([worker], operation="PersonFactWritebackService.close")
        except Exception as exc:
            logger.warning("[memorix] close person fact writeback worker failed: %s", exc)
            return
        if self._worker_task is worker:
            self._worker_task = None

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
        if MemoryContentRouter._looks_ephemeral(assistant_text) or MemoryContentRouter._looks_placeholder_only(
            assistant_text
        ):
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
        async with get_chat_migration_gate().activity(item.session_id):
            await self._handle_unpaused_item(item)

    async def _handle_unpaused_item(self, item: PersonFactWritebackItem) -> None:
        runtime = await self.runtime_manager.get_runtime(item.scope_key)
        ctx = runtime.context
        with activate_context_shims(ctx):
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
            if (
                item.platform
                and item.user_id
                and item.sender_name
                and hasattr(ctx.metadata_store, "set_person_identity_aliases")
            ):
                try:
                    observe_identity(
                        ctx.metadata_store,
                        platform=item.platform,
                        user_id=item.user_id,
                        aliases=[item.sender_name],
                        group_id=item.group_id,
                    )
                except Exception:
                    logger.debug("[memorix] person fact observe_identity skipped", exc_info=True)
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

            stored: List[str] = []
            if hasattr(ctx.metadata_store, "get_db_path"):
                async with fact_scope_lock(ctx.metadata_store, "person", person_id):
                    for fact in facts[: self._max_facts_per_turn()]:
                        stored_hash = await self._store_fact(
                            ctx=ctx, item=item, person_id=person_id, person_name=display_name, fact=fact
                        )
                        if stored_hash:
                            stored.append(stored_hash)
            else:
                for fact in facts[: self._max_facts_per_turn()]:
                    stored_hash = await self._store_fact(
                        ctx=ctx, item=item, person_id=person_id, person_name=display_name, fact=fact
                    )
                    if stored_hash:
                        stored.append(stored_hash)
            if stored:
                # 新事实以可撤回账本为准，不再追加到无法追溯证据的 registry 字符串。
                logger.debug("[memorix] person facts stored count=%s person=%s", len(stored), person_id)

    def _person_id(self, item: PersonFactWritebackItem) -> str:
        uid = str(item.user_id or "").strip()
        if not uid:
            return ""
        platform = str(item.platform or "").strip()
        return f"{platform}:{uid}" if platform else uid

    async def _extract_facts(
        self, *, ctx: Any, person_name: str, user_text: str, assistant_text: str, item: PersonFactWritebackItem
    ) -> List[str]:
        truncated_user = self._truncate(user_text, self._max_evidence_chars())
        truncated_assistant = self._truncate(assistant_text, self._max_evidence_chars())
        person_id = self._person_id(item)
        cache_key = (
            str(item.session_id or item.scope_key or "").strip(),
            person_id,
            truncated_user,
        )
        cached = self._extraction_cache.get(cache_key)
        if cached is not None:
            self._extraction_cache.move_to_end(cache_key)
            return list(cached[: self._max_facts_per_turn()])

        prompt = self._build_prompt(
            person_name=person_name,
            user_text=truncated_user,
            assistant_text=truncated_assistant,
        )
        try:
            raw = await self._complete(ctx, prompt, item)
        except Exception as exc:
            logger.debug("[memorix] person fact extractor failed: %s", exc, exc_info=True)
            return []
        parsed_ok, facts = self._parse_fact_list_result(raw)
        if not parsed_ok:
            return []
        trimmed = facts[: self._max_facts_per_turn()]
        self._extraction_cache[cache_key] = list(trimmed)
        self._extraction_cache.move_to_end(cache_key)
        while len(self._extraction_cache) > self._EXTRACTION_CACHE_MAX:
            self._extraction_cache.popitem(last=False)
        return trimmed

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
        if bridge is None or not getattr(bridge, "enabled", False):
            raise RuntimeError("AstrBot provider bridge is unavailable")
        selected_bridge = bridge
        if provider_id:
            selected_bridge = AstrBotProviderBridge(
                astrbot_context=getattr(bridge, "_context", None),
                chat_provider_id=provider_id,
            )
        return await selected_bridge.generate_text(
            prompt,
            temperature=float(self._cfg("person_fact_writeback.temperature", 0.1) or 0.1),
            max_tokens=int(self._cfg("person_fact_writeback.max_tokens", 800) or 800),
            unified_msg_origin=item.unified_msg_origin,
        )

    @staticmethod
    def _parse_fact_list_result(raw: str) -> tuple[bool, List[str]]:
        text = str(raw or "").strip()
        if not text:
            return False, []
        payload: Any = None
        parsed = False
        for candidate in (text, PersonFactWritebackService._extract_json_array_text(text)):
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
                parsed = True
                break
            except json.JSONDecodeError:
                continue
        if not parsed:
            return False, []
        if isinstance(payload, dict):
            for key in ("facts", "items", "result"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            return False, []

        facts: List[str] = []
        seen = set()
        for item in payload:
            fact = normalize_text(str(item or "").strip().strip("- "))
            if len(fact) < 4 or fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return True, facts

    @classmethod
    def _parse_fact_list(cls, raw: str) -> List[str]:
        _, facts = cls._parse_fact_list_result(raw)
        return facts

    @staticmethod
    def _extract_json_array_text(text: str) -> str:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return text[start : end + 1]
        return ""

    @staticmethod
    def _active_paragraph_vector_store(ctx: Any) -> Any:
        dual_checker = getattr(ctx, "_dual_vector_pools_enabled", None)
        dual_enabled = bool(dual_checker()) if callable(dual_checker) else bool(getattr(ctx, "_dual_vector_pools_ready", False))
        if dual_enabled:
            p_store = getattr(ctx, "paragraph_vector_store", None)
            if p_store is not None:
                return p_store
        return getattr(ctx, "vector_store", None)

    @staticmethod
    async def _persist_ctx(ctx: Any) -> None:
        save_all = getattr(ctx, "save_all", None)
        if callable(save_all):
            await save_all()
            return
        v_store = getattr(ctx, "vector_store", None)
        if v_store is not None and hasattr(v_store, "save"):
            v_store.save()
        g_store = getattr(ctx, "graph_store", None)
        if g_store is not None and hasattr(g_store, "save"):
            g_store.save()

    @staticmethod
    def _is_temporal_fact(text: str) -> bool:
        return bool(
            re.search(
                r"今天|昨天|明天|刚刚|再次|每天|参加|跑了|买了|完成|出差|去了|\d{1,4}[-/年月日:]|today|yesterday|again|attended|ran|bought|今日|昨日|明日|参加|購入",
                str(text or ""),
                re.I,
            )
        )

    def _find_uncertain_claim_match(
        self,
        *,
        ctx: Any,
        person_id: str,
        content: str,
        obs_id: str,
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        """只读探查既有 uncertain 声明，避免在去重规划提交前提前污染 SQLite 状态。"""
        now = time.time()
        rows = ctx.metadata_store.query(
            "SELECT * FROM fact_claims WHERE scope_type='person' AND scope_id=? "
            "AND status='active' AND stability='uncertain' "
            "AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?) "
            "AND value_normalized=? ORDER BY updated_at DESC LIMIT 4",
            (person_id, now, now, normalized_fact(content)),
        )
        for row in rows:
            evidence = ctx.metadata_store.get_fact_evidence(row["claim_id"])
            para_hash = next(
                (str(ev["evidence_id"]) for ev in evidence if ev.get("evidence_type") == "paragraph" and ev.get("evidence_id")),
                "",
            )
            if not para_hash:
                continue
            paragraph = ctx.metadata_store.get_paragraph(para_hash)
            if not paragraph or paragraph.get("is_deleted"):
                continue
            source_obs_ids = {
                str(ev["evidence_id"])
                for ev in evidence
                if ev.get("evidence_type") == "source" and ev.get("evidence_id")
            }
            if obs_id in source_obs_ids:
                return "replay", {"paragraph_hash": para_hash, "row": row}
            if self._is_temporal_fact(content):
                return "none", None
            return "promote", {"paragraph_hash": para_hash, "row": row}
        return "none", None

    async def _plan_fact_dedup(
        self,
        *,
        dedup: FactDeduplicator,
        ctx: Any,
        person_id: str,
        content: str,
        source_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        fact_entry = {"fact_id": "f1", "text": content, "person_ids": [person_id]}
        ctx_cfg = getattr(ctx, "config", None) or self.plugin_config
        bridge = getattr(ctx, "provider_bridge", None)
        try:
            with config_shim.use_scope_config(ctx_cfg), llm_shim.use_provider_bridge(bridge):
                return await dedup.plan(
                    scope_type="person",
                    scope_id=person_id,
                    facts=[fact_entry],
                    source=source_info,
                )
        except Exception as exc:
            logger.debug("[memorix] FactDeduplicator LLM plan fallback to deterministic check: %s", exc)
            paragraph_ids = await asyncio.to_thread(dedup._scope_paragraphs, "person", person_id)
            found = await asyncio.to_thread(dedup._candidates, "person", person_id, content, paragraph_ids)
            exact = (
                None
                if self._is_temporal_fact(content)
                else next(
                    (row for row in found if normalized_fact(str(row.get("text") or "")) == normalized_fact(content)),
                    None,
                )
            )
            decision: Dict[str, Any] = (
                {"fact_id": "f1", "action": "reuse", "target": exact}
                if exact is not None
                else {"fact_id": "f1", "action": "keep"}
            )
            return {
                "scope_type": "person",
                "scope_id": person_id,
                "facts": [fact_entry],
                "source": source_info,
                "decisions": [decision],
                "relation_support": {},
            }

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
        chat_id = str(item.session_id or item.scope_key or "default").strip() or "default"
        msg_id = str(item.message_id or "").strip()
        obs_id = observation_id(
            platform=str(item.platform or ""),
            chat_id=chat_id,
            message_ids=[msg_id] if msg_id else [],
            window_id="" if msg_id else compute_hash(f"{chat_id}:{person_id}:{timestamp}:{content}"),
        )
        source_info: Dict[str, Any] = {
            "platform": str(item.platform or ""),
            "chat_id": chat_id,
            "message_ids": [msg_id] if msg_id else [],
            "observed_at": timestamp,
            "observation_id": obs_id,
        }
        base_external_id = compute_hash(f"person_fact:{chat_id}:{person_id}:{fact_text}")
        receipt_external_id = f"{base_external_id}:{obs_id}"
        receipt = ctx.metadata_store.get_external_memory_ref(receipt_external_id)
        if receipt:
            saved_meta = receipt.get("metadata") or {}
            if saved_meta.get("dedup_phase") == "complete":
                existing_para = ctx.metadata_store.get_paragraph(receipt["paragraph_hash"])
                if existing_para is not None and not existing_para.get("is_deleted"):
                    return str(receipt["paragraph_hash"])

        uncertain_action, uncertain_match = self._find_uncertain_claim_match(
            ctx=ctx,
            person_id=person_id,
            content=content,
            obs_id=obs_id,
        )
        if uncertain_action == "replay" and uncertain_match is not None:
            return str(uncertain_match["paragraph_hash"])

        dedup = FactDeduplicator(
            ctx.metadata_store,
            getattr(ctx, "embedding_manager", None),
            self._active_paragraph_vector_store(ctx),
        )
        promote_row: Optional[Dict[str, Any]] = None
        if uncertain_action == "promote" and uncertain_match is not None:
            promote_row = uncertain_match["row"]
            target_hash = str(uncertain_match["paragraph_hash"])
            fact_entry = {"fact_id": "f1", "text": content, "person_ids": [person_id]}
            plan = {
                "scope_type": "person",
                "scope_id": person_id,
                "facts": [fact_entry],
                "source": source_info,
                "decisions": [
                    {
                        "fact_id": "f1",
                        "action": "reuse",
                        "target": {
                            "claim_id": promote_row["claim_id"],
                            "fact_key": str(promote_row["fact_key"]),
                            "paragraph_hash": target_hash,
                            "text": str(promote_row.get("value_text") or content),
                        },
                    }
                ],
                "relation_support": {},
            }
        else:
            plan = await self._plan_fact_dedup(
                dedup=dedup,
                ctx=ctx,
                person_id=person_id,
                content=content,
                source_info=source_info,
            )

        decision = plan["decisions"][0]
        reused = decision.get("action") == "reuse"
        source = f"person_fact:{item.session_id}:{person_id}" if item.session_id else f"person_fact:{person_id}"

        if reused:
            paragraph_hash = str(decision["target"]["paragraph_hash"])
            classification = {
                "authority": "summary_derived",
                "stability": "stable",
                "profile_section": "stable_facts",
            }
        else:
            metadata = {
                "external_id": receipt_external_id,
                "source_type": "person_fact",
                "chat_id": item.session_id or chat_id,
                "person_ids": [person_id],
                "person_id": person_id,
                "participants": [person_name],
                "sender_id": item.user_id,
                "sender_name": item.sender_name,
                "group_id": item.group_id,
                "group_name": item.group_name,
                "platform": item.platform,
                "message_id": item.message_id,
                "evidence_text": self._truncate(item.user_text, self._max_evidence_chars()),
                "writeback_source": "person_fact_writeback",
                "dedup_managed": True,
                "dedup_phase": "pending",
                "dedup_fact_texts": [content],
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
                else:
                    target_store = self._active_paragraph_vector_store(ctx)
                    if target_store is not None and paragraph_hash not in target_store:
                        embedding = await ctx.embedding_manager.encode(content)
                        if getattr(embedding, "ndim", 1) == 1:
                            embedding = embedding.reshape(1, -1)
                        target_store.add(vectors=embedding, ids=[paragraph_hash])
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

            ctx.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
            classification = {
                "authority": "summary_derived",
                "stability": "uncertain",
                "profile_section": "uncertain_notes",
            }

        def _commit_claim_atomically() -> List[str]:
            if reused and promote_row is not None and hasattr(ctx.metadata_store, "transaction"):
                with ctx.metadata_store.transaction(immediate=True):
                    ctx.metadata_store.upsert_fact_claim(
                        scope_type="person",
                        scope_id=person_id,
                        fact_key=str(promote_row["fact_key"]),
                        value_text=str(promote_row["value_text"]),
                        stability="stable",
                        profile_section="stable_facts",
                        authority=str(promote_row.get("authority") or "summary_derived"),
                        confidence=max(0.75, float(promote_row.get("confidence") or 0.5)),
                        reason="astrbot_person_fact_repeated_confirmation",
                        observed_at=timestamp,
                    )
                    return dedup.commit(plan, paragraph_hash, classification)
            if reused and promote_row is not None:
                ctx.metadata_store.upsert_fact_claim(
                    scope_type="person",
                    scope_id=person_id,
                    fact_key=str(promote_row["fact_key"]),
                    value_text=str(promote_row["value_text"]),
                    stability="stable",
                    profile_section="stable_facts",
                    authority=str(promote_row.get("authority") or "summary_derived"),
                    confidence=max(0.75, float(promote_row.get("confidence") or 0.5)),
                    reason="astrbot_person_fact_repeated_confirmation",
                    observed_at=timestamp,
                )
            return dedup.commit(plan, paragraph_hash, classification)

        claim_ids = await cancellation_safe_to_thread(_commit_claim_atomically)
        if not reused:
            ctx.metadata_store.update_paragraph_metadata(
                paragraph_hash,
                {"fact_claim_ids": claim_ids, "dedup_phase": "complete"},
                merge=True,
            )
            enqueue_source_rebuild = getattr(ctx.metadata_store, "enqueue_episode_source_rebuild", None)
            if callable(enqueue_source_rebuild):
                enqueue_source_rebuild(source, reason="dedup_person_fact_complete")
        ctx.metadata_store.upsert_external_memory_ref(
            external_id=receipt_external_id,
            paragraph_hash=paragraph_hash,
            source_type="person_fact",
            metadata={
                "person_id": person_id,
                "chat_id": chat_id,
                "input_text": content,
                "dedup_phase": "complete",
                "claim_ids": claim_ids,
            },
        )
        ctx.metadata_store.enqueue_person_profile_refresh(
            person_id=person_id,
            reason="person_fact_deduplicated" if reused else "fact_evidence_written",
        )
        try:
            await self._persist_ctx(ctx)
        except Exception:
            logger.debug("[memorix] person fact persist failed", exc_info=True)
        return paragraph_hash

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        content = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(content) <= max_chars:
            return content
        return f"{content[: max(0, max_chars - 1)]}…"

