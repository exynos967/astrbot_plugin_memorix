"""
聊天总结与知识导入工具（独立版）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ...amemorix.common.logging import get_logger
from ...amemorix.llm_client import LLMClient

from ..embedding.api_adapter import EmbeddingAPIAdapter
from ..storage import (
    GraphStore,
    KnowledgeType,
    MetadataStore,
    VectorStore,
    get_knowledge_type_from_string,
)
from .paragraph_vector_service import ParagraphVectorWriteService

logger = get_logger("A_Memorix.SummaryImporter")

SUMMARY_PROMPT_TEMPLATE = """
你是 {bot_name}。{personality_context}
现在你需要对以下一段聊天记录进行总结，并提取其中的重要知识。

聊天记录内容：
{chat_history}

请完成以下任务：
1. **生成总结**：以第三人称或机器人的视角，简洁明了地总结这段对话的主要内容、发生的事件或讨论的主题。
2. **提取实体与关系**：识别并提取对话中提到的重要实体以及它们之间的关系。

请严格以 JSON 格式输出，格式如下：
{{
  "summary": "总结文本内容",
  "entities": ["张三", "李四"],
  "relations": [
    {{"subject": "张三", "predicate": "认识", "object": "李四"}}
  ]
}}

注意：
1. 总结应具有叙事性，能够作为长程记忆的一部分。
2. 直接使用实体的实际名称，不要使用 e1/e2 等代号。
3. 实体与关系尽量使用原文措辞。
4. 如果没有关系，relations 返回空数组。
"""


def _message_metadata(message: Dict[str, Any]) -> Dict[str, Any]:
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        merged = dict(metadata)
        merged.update(nested)
        return merged
    return metadata


def _message_speaker_identity(message: Dict[str, Any]) -> str:
    metadata = _message_metadata(message)
    for key in ("sender_name", "person_name", "nickname", "group_nick_name"):
        candidate = str(message.get(key) or metadata.get(key) or "").strip()
        if candidate:
            return candidate

    platform = str(message.get("platform") or metadata.get("platform") or "").strip()
    sender_id = str(
        message.get("sender_id")
        or message.get("user_id")
        or metadata.get("sender_id")
        or metadata.get("user_id")
        or ""
    ).strip()
    if sender_id:
        return f"{platform}:{sender_id}" if platform else sender_id
    return ""


def _coerce_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _normalize_entity_items(raw_entities: Any) -> List[str]:
    """归一化实体列表，兼容字符串与 dict 形态，去重并保留显示名。"""
    if not isinstance(raw_entities, list):
        return []
    entities: List[str] = []
    seen = set()
    for item in raw_entities:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or item.get("entity") or "").strip()
        else:
            name = ""
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(name)
    return entities


class SummaryImporter:
    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        plugin_config: dict,
        llm_client: Optional[LLMClient] = None,
    ):
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.plugin_config = plugin_config or {}
        self.llm_client = llm_client
        self.relation_write_service = (
            self.plugin_config.get("relation_write_service")
            if isinstance(self.plugin_config, dict)
            else None
        )
        configured_paragraph_vector_service = (
            self.plugin_config.get("paragraph_vector_service")
            if isinstance(self.plugin_config, dict)
            else None
        )
        self.paragraph_vector_service = configured_paragraph_vector_service or ParagraphVectorWriteService(
            metadata_store=metadata_store,
            vector_store=vector_store,
            embedding_manager=embedding_manager,
        )

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.plugin_config if isinstance(self.plugin_config, dict) else {}
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _plugin_instance(self) -> Any:
        """获取 plugin_instance（上游通过 plugin_config 注入的 ctx 引用）。"""
        return self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None

    def _dual_vector_pools_enabled(self) -> bool:
        """是否启用双池（段落池 + 图池）向量写入。

        优先调用 plugin_instance 上的同名检查器；否则回退到配置字典判定。
        """
        plugin_instance = self._plugin_instance()
        checker = getattr(plugin_instance, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        if isinstance(self.plugin_config, dict):
            retrieval_cfg = self.plugin_config.get("retrieval", {}) or {}
            vector_pools_cfg = retrieval_cfg.get("vector_pools", {}) if isinstance(retrieval_cfg, dict) else {}
            configured_mode = (
                str(vector_pools_cfg.get("mode", "dual") or "dual").strip().lower()
                if isinstance(vector_pools_cfg, dict)
                else "dual"
            )
            runtime_cfg = self.plugin_config.get("runtime", {}) or {}
            if isinstance(runtime_cfg, dict) and "vector_pools_ready" in runtime_cfg:
                return configured_mode == "dual" and bool(runtime_cfg.get("vector_pools_ready"))
            return configured_mode == "dual"
        return False

    def _graph_vector_store(self) -> VectorStore:
        """获取图池向量存储；双池未启用或未注入时安全回退到主向量存储。"""
        if self._dual_vector_pools_enabled() and isinstance(self.plugin_config, dict):
            store = self.plugin_config.get("graph_vector_store")
            if store is not None:
                return store
        return self.vector_store

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    async def _ensure_entity_vector(self, *, entity_hash: str, name: str) -> None:
        """写入实体向量到图池；graph 池不可用时安全降级跳过。"""
        token = str(entity_hash or "").strip()
        text = str(name or "").strip()
        if not token or not text or not self._dual_vector_pools_enabled():
            return

        target_store = self._graph_vector_store()
        vector_id = self._graph_vector_id("entity", token)
        if target_store is None or vector_id in target_store:
            return
        try:
            embedding = await self.embedding_manager.encode(text)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            target_store.add(vectors=embedding, ids=[vector_id])
        except Exception as exc:
            # graph 池不可用时安全降级：仅记录日志，不抛错，metadata/graph 仍保留
            logger.warning("总结导入实体向量写入失败，跳过 entity 向量: entity=%s error=%s", text, exc)

    def _build_chat_text(self, messages: List[Dict[str, Any]], *, bot_name: str = "") -> str:
        lines: List[str] = []
        for item in messages:
            role = str(item.get("role", "user") or "user")
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            speaker = ""
            if role.strip().lower() == "user":
                speaker = _message_speaker_identity(item)
            elif role.strip().lower() == "assistant":
                speaker = str(bot_name or "").strip()
            label = speaker or role
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    def _derive_transcript_time_meta(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        event_times: List[float] = []
        fallback_times: List[float] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            metadata = _message_metadata(item)
            explicit_ts = None
            for key in ("timestamp", "time", "ts"):
                explicit_ts = _coerce_timestamp(item.get(key))
                if explicit_ts is None:
                    explicit_ts = _coerce_timestamp(metadata.get(key))
                if explicit_ts is not None:
                    break
            if explicit_ts is not None:
                event_times.append(explicit_ts)
                continue
            created_at = _coerce_timestamp(item.get("created_at"))
            if created_at is not None:
                fallback_times.append(created_at)

        times = event_times or fallback_times
        if not times:
            return {}

        start = min(times)
        end = max(times)
        confidence = 0.95 if event_times else 0.6
        time_meta: Dict[str, Any] = {
            "event_time_start": start,
            "event_time_end": end,
            "time_granularity": "minute",
            "time_confidence": confidence,
        }
        if abs(end - start) <= 1.0:
            time_meta["event_time"] = end
        return time_meta

    def _transcript_session_metadata(self, session_id: str) -> Dict[str, Any]:
        getter = getattr(self.metadata_store, "get_transcript_session", None)
        if not callable(getter):
            return {}
        session = getter(session_id)
        if not isinstance(session, dict):
            return {}
        metadata = session.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}

    def _fallback_summary(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = " ".join(str(m.get("content", "") or "").strip() for m in messages if str(m.get("content", "")).strip())
        merged = merged[:500]
        return {"summary": merged or "暂无可总结内容", "entities": [], "relations": []}

    async def _generate_summary_payload(
        self,
        messages: List[Dict[str, Any]],
        *,
        bot_name: str = "",
        personality_context: str = "",
    ) -> Dict[str, Any]:
        if not messages:
            return self._fallback_summary(messages)

        history = self._build_chat_text(messages, bot_name=bot_name)
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            bot_name=bot_name or "助手",
            personality_context=personality_context,
            chat_history=history,
        )

        if self.llm_client is None:
            return self._fallback_summary(messages)

        try:
            ok, payload, raw = await self.llm_client.complete_json(prompt, temperature=0.2, max_tokens=1200)
            if ok and isinstance(payload, dict):
                return payload
            logger.warning("Summary LLM returned non-JSON, fallback parser used.")
            if raw:
                start = raw.find("{")
                end = raw.rfind("}")
                if start >= 0 and end > start:
                    try:
                        parsed = json.loads(raw[start : end + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
        except Exception as exc:
            logger.warning("Summary LLM call failed: %s", exc)

        return self._fallback_summary(messages)

    async def import_from_transcript(
        self,
        *,
        session_id: str,
        messages: List[Dict[str, Any]],
        source: str = "",
        context_length: Optional[int] = None,
        bot_name: str = "",
        personality_context: str = "",
    ) -> Tuple[bool, str]:
        try:
            session_metadata = self._transcript_session_metadata(session_id)
            session_metadata["imported_at"] = time.time()
            session = self.metadata_store.upsert_transcript_session(
                session_id=session_id,
                source=source or f"transcript:{session_id}",
                metadata=session_metadata,
            )
            self.metadata_store.append_transcript_messages(session_id=session["session_id"], messages=messages)

            limit = int(context_length) if context_length is not None else int(self._cfg("summarization.context_length", 50))
            transcript_messages = self.metadata_store.get_transcript_messages(session["session_id"], limit=max(1, limit))
            payload = await self._generate_summary_payload(
                transcript_messages,
                bot_name=bot_name,
                personality_context=personality_context,
            )

            summary = str(payload.get("summary", "") or "").strip()
            entities = payload.get("entities", [])
            relations = payload.get("relations", [])
            time_meta = self._derive_transcript_time_meta(transcript_messages)
            if not summary:
                return False, "总结为空"

            await self._execute_import(
                summary=summary,
                entities=entities if isinstance(entities, list) else [],
                relations=relations if isinstance(relations, list) else [],
                stream_id=session["session_id"],
                time_meta=time_meta or None,
            )

            self.vector_store.save()
            self.graph_store.save()
            return True, f"总结导入成功: session={session['session_id']}"
        except Exception as exc:
            logger.error("Summary transcript import failed: %s", exc, exc_info=True)
            return False, str(exc)

    async def import_from_stream(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        bot_name: str = "",
        personality_context: str = "",
    ) -> Tuple[bool, str]:
        if include_personality is False:
            personality_context = ""
        limit = int(context_length) if context_length is not None else int(self._cfg("summarization.context_length", 50))
        messages = self.metadata_store.get_transcript_messages(stream_id, limit=max(1, limit))
        if not messages:
            return False, "未找到可总结的聊天记录（请先写入 transcript）"
        return await self.import_from_transcript(
            session_id=stream_id,
            messages=messages,
            source=f"chat_summary:{stream_id}",
            context_length=limit,
            bot_name=bot_name,
            personality_context=personality_context,
        )

    async def _execute_import(
        self,
        summary: str,
        entities: List[str],
        relations: List[Dict[str, str]],
        stream_id: str,
        time_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        type_str = self._cfg("summarization.default_knowledge_type", "narrative")
        knowledge_type = get_knowledge_type_from_string(type_str) or KnowledgeType.NARRATIVE
        metadata = self._transcript_session_metadata(stream_id)
        metadata.update({"source_type": "chat_summary", "chat_id": stream_id})
        metadata = {key: value for key, value in metadata.items() if value not in (None, "", [])}

        hash_value = self.metadata_store.add_paragraph(
            content=summary,
            source=f"chat_summary:{stream_id}",
            metadata=metadata,
            knowledge_type=knowledge_type.value,
            time_meta=time_meta,
        )

        await self.paragraph_vector_service.ensure_paragraph_vector(hash_value, summary)

        # 导入实体：归一化 + 图节点 + 实体元数据 + 图池向量（双池可用时）
        normalized_entities = _normalize_entity_items(entities)
        if normalized_entities:
            self.graph_store.add_nodes(normalized_entities)
            for name in normalized_entities:
                entity_hash = self.metadata_store.add_entity(name=name, source_paragraph=hash_value)
                await self._ensure_entity_vector(entity_hash=entity_hash, name=name)

        for rel in relations:
            s = str(rel.get("subject", "")).strip()
            p = str(rel.get("predicate", "")).strip()
            o = str(rel.get("object", "")).strip()
            if not (s and p and o):
                continue
            if self.relation_write_service is not None:
                await self.relation_write_service.upsert_relation_with_vector(
                    subject=s,
                    predicate=p,
                    obj=o,
                    confidence=1.0,
                    source_paragraph=hash_value,
                    write_vector=bool(self._cfg("retrieval.relation_vectorization.enabled", True)),
                )
            else:
                rel_hash = self.metadata_store.add_relation(
                    subject=s,
                    predicate=p,
                    obj=o,
                    confidence=1.0,
                    source_paragraph=hash_value,
                )
                self.graph_store.add_edges([(s, o)], relation_hashes=[rel_hash])

        logger.info("Summary imported: %s", hash_value[:8])
