"""Ingest orchestration service."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..app_context import ScopeRuntimeManager
from ..core.storage import detect_knowledge_type
from ..core.utils.hash import compute_hash, normalize_text
from .content_router import MemoryContentRouter


class IngestService:
    def __init__(self, runtime_manager: ScopeRuntimeManager, plugin_config: Dict[str, Any]):
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self.content_router = MemoryContentRouter(self.plugin_config)

    @staticmethod
    def _nested(config: Dict[str, Any], key: str, default: Any = None) -> Any:
        current: Any = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _direct_enabled(self, role: str) -> bool:
        mode = (
            str(self._nested(self.plugin_config, "ingest.memory_write_mode", "transcript_only") or "transcript_only")
            .strip()
            .lower()
        )
        if mode not in {"direct", "transcript_only", "both", "auto"}:
            mode = "transcript_only"
        if mode in {"transcript_only", "auto"}:
            return False
        if str(role or "").strip().lower() == "assistant":
            return bool(self._nested(self.plugin_config, "ingest.direct_write_assistant", True))
        return True

    def _build_source(self, source_type: str, session_id: str, person_ids: list[str]) -> str:
        kind = str(source_type or "chat").strip() or "chat"
        session = str(session_id or "").strip()
        if person_ids:
            return f"{kind}:{session}:{','.join(person_ids)}"
        if session:
            return f"{kind}:{session}"
        return kind

    async def _ensure_paragraph_vector(
        self,
        *,
        ctx: Any,
        paragraph_hash: str,
        content: str,
        warnings: list[str],
    ) -> bool:
        service = getattr(ctx, "paragraph_vector_service", None)
        if service is not None and hasattr(service, "ensure_paragraph_vector"):
            result = await service.ensure_paragraph_vector(paragraph_hash, content)
            if getattr(result, "vector_state", "") == "failed":
                warnings.append(f"vector_write_failed: {getattr(result, 'error', '')}")
            return bool(getattr(result, "vector_written", False) or getattr(result, "vector_already_exists", False))

        if paragraph_hash in ctx.vector_store:
            updater = getattr(ctx.metadata_store, "update_vector_index", None)
            if callable(updater):
                updater("paragraph", paragraph_hash, 1)
            return True
        try:
            embedding = await ctx.embedding_manager.encode(content)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            ctx.vector_store.add(vectors=embedding, ids=[paragraph_hash])
            updater = getattr(ctx.metadata_store, "update_vector_index", None)
            if callable(updater):
                updater("paragraph", paragraph_hash, 1)
            return True
        except Exception as exc:
            updater = getattr(ctx.metadata_store, "update_vector_index", None)
            if callable(updater):
                updater("paragraph", paragraph_hash, -1)
            warnings.append(f"vector_write_failed: {exc}")
            return False

    async def _write_direct_memory(
        self,
        *,
        ctx: Any,
        session_id: str,
        role: str,
        text: str,
        source: str,
        user_id: str,
        group_id: str,
        group_name: str,
        platform: str,
        unified_msg_origin: str,
        sender_name: str,
        message_id: str,
        role_origin: str,
        timestamp: float,
        time_meta: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        content = normalize_text(text)
        if not content:
            return {"success": True, "stored_ids": [], "skipped_ids": [], "reason": "empty_text"}

        normalized_role = str(role or "user").strip().lower() or "user"
        person_ids = []
        if normalized_role == "user" and user_id:
            person_ids.append(f"{platform}:{user_id}" if platform else user_id)
        participants = [sender_name] if normalized_role == "user" and sender_name else []
        source_type = "chat_message"
        external_id = str(message_id or "").strip()
        if not external_id:
            external_id = compute_hash(f"{source_type}:{session_id}:{role}:{timestamp}:{content}")

        memory_source = str(source or "").strip() or self._build_source(source_type, session_id, person_ids)
        metadata = {
            "external_id": external_id,
            "source_type": source_type,
            "chat_id": str(session_id or "").strip(),
            "person_ids": person_ids,
            "participants": participants,
            "role": normalized_role,
            "role_origin": str(role_origin or role or "").strip(),
            "sender_id": str(user_id or "").strip(),
            "sender_name": str(sender_name or "").strip(),
            "group_id": str(group_id or "").strip(),
            "group_name": str(group_name or "").strip(),
            "platform": str(platform or "").strip(),
            "unified_msg_origin": str(unified_msg_origin or "").strip(),
            "message_id": str(message_id or "").strip(),
            "transcript_source": source,
        }
        metadata = {
            key: value
            for key, value in metadata.items()
            if value is not None and value != "" and value != []
        }

        paragraph_hash = ctx.metadata_store.add_paragraph(
            content=content,
            source=memory_source,
            metadata=metadata,
            knowledge_type=detect_knowledge_type(content).value,
            time_meta=time_meta or ({"event_time": timestamp} if timestamp else None),
        )

        warnings: list[str] = []
        vector_written = await self._ensure_paragraph_vector(
            ctx=ctx,
            paragraph_hash=paragraph_hash,
            content=content,
            warnings=warnings,
        )

        entities = [item for item in [*person_ids, *participants] if str(item or "").strip()]
        for name in dict.fromkeys(entities):
            ctx.metadata_store.add_entity(
                name=str(name),
                source_paragraph=paragraph_hash,
                metadata={"source": "astrbot_event", "chat_id": session_id},
            )

        ctx.metadata_store.enqueue_episode_pending(paragraph_hash, source=memory_source)
        try:
            ctx.vector_store.save()
            ctx.graph_store.save()
            sparse_index = getattr(ctx, "sparse_index", None)
            if sparse_index is not None and getattr(sparse_index.config, "enabled", False):
                sparse_index.ensure_loaded()
        except Exception as exc:
            warnings.append(f"persist_failed: {exc}")

        return {
            "success": True,
            "stored_ids": [paragraph_hash],
            "skipped_ids": [],
            "source": memory_source,
            "vector_written": vector_written,
            "warnings": warnings,
        }

    async def ingest_message(
        self,
        *,
        scope_key: str,
        session_id: str,
        role: str,
        content: str,
        source: str,
        user_id: str = "",
        group_id: str = "",
        group_name: str = "",
        platform: str = "",
        unified_msg_origin: str = "",
        sender_name: str = "",
        message_id: str = "",
        role_origin: str = "",
        timestamp: float = 0,
        time_meta: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        filter_user_id: str = "",
    ) -> Dict[str, Any]:
        text = str(content or "").strip()
        if not text and bool(self.plugin_config.get("ingest", {}).get("skip_empty_text", True)):
            return {"success": True, "skipped": True, "reason": "empty"}

        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        session = str(session_id or "").strip() or f"scope:{scope_key}"
        if respect_filter and hasattr(ctx, "is_chat_enabled"):
            check_user_id = str(filter_user_id or user_id or "").strip()
            if not ctx.is_chat_enabled(stream_id=session, group_id=group_id, user_id=check_user_id):
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "chat_filtered",
                    "result": {
                        "mode": "filtered",
                        "transcript": {"session_id": session, "stored": False},
                    },
                }
        route = self.content_router.route_message(
            role=role,
            text=text,
            metadata={
                "scope_key": scope_key,
                "session_id": session,
                "user_id": str(user_id or "").strip(),
                "group_id": str(group_id or "").strip(),
                "group_name": str(group_name or "").strip(),
                "platform": str(platform or "").strip(),
            },
        )
        if not route.store_transcript and not route.write_direct:
            return {
                "success": True,
                "skipped": True,
                "reason": route.reason,
                "result": {
                    "mode": route.route,
                    "route": {
                        "store_transcript": route.store_transcript,
                        "write_direct": route.write_direct,
                        "fact_candidate": route.fact_candidate,
                        "reason": route.reason,
                    },
                },
            }

        ctx.metadata_store.upsert_transcript_session(
            session_id=session,
            source=source,
            metadata={
                "scope_key": scope_key,
                "user_id": str(user_id or "").strip(),
                "group_id": str(group_id or "").strip(),
                "group_name": str(group_name or "").strip(),
                "platform": str(platform or "").strip(),
                "unified_msg_origin": str(unified_msg_origin or "").strip(),
            },
        )
        msg_record = {"role": str(role or "user"), "content": text}
        ts_val = float(timestamp) if timestamp else None
        if ts_val:
            msg_record["timestamp"] = ts_val
        msg_meta = {
            "sender_name": str(sender_name or "").strip(),
            "sender_id": str(user_id or "").strip(),
            "group_id": str(group_id or "").strip(),
            "group_name": str(group_name or "").strip(),
            "platform": str(platform or "").strip(),
            "session_id": session,
            "unified_msg_origin": str(unified_msg_origin or "").strip(),
            "message_id": str(message_id or "").strip(),
            "role_origin": str(role_origin or role or "").strip(),
        }
        filtered_meta = {key: value for key, value in msg_meta.items() if value}
        if filtered_meta:
            msg_record.update(filtered_meta)
        transcript_stored = False
        if route.store_transcript:
            ctx.metadata_store.append_transcript_messages(
                session_id=session,
                messages=[msg_record],
            )
            transcript_stored = True

        direct_result: Optional[Dict[str, Any]] = None
        if route.write_direct:
            direct_result = await self._write_direct_memory(
                ctx=ctx,
                session_id=session,
                role=role,
                text=text,
                source=source,
                user_id=user_id,
                group_id=group_id,
                group_name=group_name,
                platform=platform,
                unified_msg_origin=unified_msg_origin,
                sender_name=sender_name,
                message_id=message_id,
                role_origin=role_origin,
                timestamp=float(timestamp) if timestamp else time.time(),
                time_meta=time_meta,
            )

        return {
            "success": True,
            "skipped": False,
            "result": {
                "mode": "direct" if direct_result is not None else "transcript_only",
                "route": {
                    "store_transcript": route.store_transcript,
                    "write_direct": route.write_direct,
                    "fact_candidate": route.fact_candidate,
                    "reason": route.reason,
                },
                "transcript": {"session_id": session, "stored": transcript_stored},
                "direct": direct_result,
            },
        }

    @staticmethod
    def _as_list(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = []
        return [str(item).strip() for item in values if str(item or "").strip()]

    async def ingest_text(
        self,
        *,
        scope_key: str,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[list[str]] = None,
        participants: Optional[list[str]] = None,
        timestamp: Optional[float] = None,
        time_start: Any = None,
        time_end: Any = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entities: Optional[list[str]] = None,
        relations: Optional[list[Dict[str, Any]]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        content = normalize_text(text)
        source_kind = str(source_type or "tool_text").strip() or "tool_text"
        stream_id = str(chat_id or "").strip()
        external_token = str(external_id or "").strip() or compute_hash(f"{source_kind}:{stream_id}:{content}")
        if not content:
            return {"success": True, "stored_ids": [], "skipped_ids": [external_token], "reason": "empty_text"}

        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        if respect_filter and hasattr(ctx, "is_chat_enabled") and not ctx.is_chat_enabled(stream_id, group_id, user_id):
            return {"success": True, "stored_ids": [], "skipped_ids": [external_token], "detail": "chat_filtered"}

        person_tokens = self._as_list(person_ids)
        participant_tokens = self._as_list(participants)
        tag_tokens = self._as_list(tags)
        entity_tokens = list(dict.fromkeys([*self._as_list(entities), *person_tokens, *participant_tokens]))
        relation_rows = [dict(item) for item in (relations or []) if isinstance(item, dict)]
        source = self._build_source(source_kind, stream_id, person_tokens)
        paragraph_meta = dict(metadata or {})
        paragraph_meta.update(
            {
                "external_id": external_token,
                "source_type": source_kind,
                "chat_id": stream_id,
                "person_ids": person_tokens,
                "participants": participant_tokens,
                "tags": tag_tokens,
            }
        )
        paragraph_meta = {key: value for key, value in paragraph_meta.items() if value not in (None, "", [])}
        time_meta: Dict[str, Any] = {}
        if timestamp is not None:
            time_meta["event_time"] = timestamp
        if time_start is not None:
            time_meta["event_time_start"] = time_start
        if time_end is not None:
            time_meta["event_time_end"] = time_end

        paragraph_hash = ctx.metadata_store.add_paragraph(
            content=content,
            source=source,
            metadata=paragraph_meta,
            knowledge_type=detect_knowledge_type(content).value,
            time_meta=time_meta or None,
        )

        warnings: list[str] = []
        vector_written = await self._ensure_paragraph_vector(
            ctx=ctx,
            paragraph_hash=paragraph_hash,
            content=content,
            warnings=warnings,
        )

        for name in entity_tokens:
            ctx.metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)

        relation_hashes: list[str] = []
        write_relation_vectors = bool(ctx.get_config("retrieval.relation_vectorization.enabled", False))
        relation_service = getattr(ctx, "relation_write_service", None)
        if relation_service is not None:
            for row in relation_rows:
                subject = str(row.get("subject", "") or "").strip()
                predicate = str(row.get("predicate", "") or "").strip()
                obj = str(row.get("object", "") or row.get("obj", "") or "").strip()
                if not (subject and predicate and obj):
                    continue
                try:
                    result = await relation_service.upsert_relation_with_vector(
                        subject=subject,
                        predicate=predicate,
                        obj=obj,
                        confidence=float(row.get("confidence", 1.0) or 1.0),
                        source_paragraph=paragraph_hash,
                        metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else paragraph_meta,
                        write_vector=write_relation_vectors,
                    )
                    ctx.metadata_store.link_paragraph_relation(paragraph_hash, result.hash_value)
                    relation_hashes.append(result.hash_value)
                except Exception as exc:
                    warnings.append(f"relation_write_failed: {exc}")

        ctx.metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
        try:
            ctx.vector_store.save()
            ctx.graph_store.save()
            sparse_index = getattr(ctx, "sparse_index", None)
            if sparse_index is not None and getattr(sparse_index.config, "enabled", False):
                sparse_index.ensure_loaded()
        except Exception as exc:
            warnings.append(f"persist_failed: {exc}")

        return {
            "success": True,
            "stored_ids": [paragraph_hash, *relation_hashes],
            "skipped_ids": [],
            "source": source,
            "vector_written": vector_written,
            "warnings": warnings,
        }

    async def ingest_summary(
        self,
        *,
        scope_key: str,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[list[str]] = None,
        time_start: Any = None,
        time_end: Any = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        summary_meta = dict(metadata or {})
        summary_meta.setdefault("kind", "chat_summary")
        return await self.ingest_text(
            scope_key=scope_key,
            external_id=external_id,
            source_type="chat_summary",
            text=text,
            chat_id=chat_id,
            participants=participants,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=summary_meta,
            respect_filter=respect_filter,
            user_id=user_id,
            group_id=group_id,
        )
