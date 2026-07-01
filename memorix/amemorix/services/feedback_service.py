"""反馈纠错服务（跨作用域）。

当检索命中可能存在偏差、用户随后在窗口期内发出"纠错"信号时，后台分类器判定
confirm/reject/correct，自动遗忘错误关系、补写纠正关系，并触发受影响段落标记、
episode 重建与人物画像刷新入队。整个过程可回滚：保留 rollback_plan，必要时撤销
遗忘、软删纠正段落、清除 stale 标记并重新入队重建/刷新。

架构上与 ``IngestService`` 一致——跨作用域，依赖 ``ScopeRuntimeManager`` 遍历各
scope 的 ``AppContext``；每个 scope 独立 metadata_store，故 feedback 任务表、
reconcile 队列均按 scope 隔离。LLM 调用统一经 ``generate_text(ctx, prompt)``，
复用 AstrBot provider bridge。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..common.logging import get_logger
from ..common.message_api import MessageAPI
from ...core.utils.hash import compute_hash
from ...core.utils.model_routing import generate_text
from .person_profile_service import PersonProfileApiService

logger = get_logger("A_Memorix.FeedbackService")


class FeedbackService:
    """反馈纠错后台服务（跨作用域）。"""

    def __init__(
        self,
        runtime_manager: Any,
        plugin_config: Dict[str, Any],
        *,
        ingest_service: Any,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self.ingest_service = ingest_service

        self._loops: List[asyncio.Task] = []
        self._stopping = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 静态工具（无 self 依赖，纯函数）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _tokens(values: Optional[Iterable[Any]]) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in values or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    @classmethod
    def _merge_tokens(cls, *groups: Optional[Iterable[Any]]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for group in groups:
            for item in cls._tokens(group):
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except Exception:
                return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    @staticmethod
    def _safe_json_loads(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            from json_repair import repair_json

            repaired = repair_json(text)
            payload = json.loads(repaired) if isinstance(repaired, str) else repaired
        except Exception:
            payload = None
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _format_relation_text(subject: Any, predicate: Any, obj: Any) -> str:
        return " ".join(
            [
                str(subject or "").strip(),
                str(predicate or "").strip(),
                str(obj or "").strip(),
            ]
        ).strip()

    @staticmethod
    def _chat_source(session_id: str) -> Optional[str]:
        """纠错写入段落的 source 标签，须与 ``IngestService._build_source`` 对齐。

        ``_ingest_feedback_relations`` 用 ``source_type="chat_summary"``，故段落
        source 形如 ``chat_summary:<session_id>``；此处保持一致，便于 episode
        重建入队时按相同 source 命中段落。
        """
        clean = str(session_id or "").strip()
        return f"chat_summary:{clean}" if clean else None

    @staticmethod
    def _feedback_signal_tokens() -> tuple[str, ...]:
        return (
            "不对",
            "错了",
            "你记错",
            "记错了",
            "不是",
            "并不是",
            "纠正",
            "更正",
            "改成",
            "应该是",
            "实际是",
            "说反了",
        )

    @classmethod
    def _feedback_contains_signal(cls, content: str) -> bool:
        lowered = str(content or "").lower()
        return any(token in lowered for token in cls._feedback_signal_tokens())

    @classmethod
    def _feedback_noise(cls, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        if cls._feedback_contains_signal(content):
            return False
        if len(content) <= 2:
            return True
        markers = (
            "哈哈",
            "好的",
            "收到",
            "谢谢",
            "嗯嗯",
            "晚安",
            "早安",
            "拜拜",
            "在吗",
        )
        return len(content) <= 8 and any(marker in content for marker in markers)

    @staticmethod
    def _should_invoke_feedback_classifier(feedback_messages: List[str]) -> bool:
        if not feedback_messages:
            return False
        lowered = "\n".join(feedback_messages).lower()
        return any(token in lowered for token in FeedbackService._feedback_signal_tokens())

    @staticmethod
    def _feedback_apply_result_status(apply_result: Dict[str, Any]) -> str:
        if bool(apply_result.get("applied")):
            return "applied"
        reason = str(apply_result.get("reason", "") or "").strip().lower()
        if reason in {"low_confidence", "no_relation_targets"} or reason.startswith("decision_"):
            return "skipped"
        return "error"

    @staticmethod
    def _normalize_feedback_decision(
        payload: Dict[str, Any],
        *,
        hit_hashes: Sequence[str],
    ) -> Dict[str, Any]:
        allowed = {"confirm", "reject", "correct", "supplement", "none"}
        decision = str(payload.get("decision", "") or "").strip().lower()
        if decision not in allowed:
            decision = "none"
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        valid_hashes = {str(item or "").strip() for item in hit_hashes if str(item or "").strip()}
        target_hashes_raw = payload.get("target_hashes")
        if isinstance(target_hashes_raw, str):
            target_hashes_candidates = [target_hashes_raw]
        elif isinstance(target_hashes_raw, list):
            target_hashes_candidates = target_hashes_raw
        else:
            target_hashes_candidates = []
        target_hashes = [
            str(item or "").strip()
            for item in target_hashes_candidates
            if str(item or "").strip() in valid_hashes
        ]

        corrected_relations: List[Dict[str, Any]] = []
        raw_relations = payload.get("corrected_relations")
        if isinstance(raw_relations, list):
            for item in raw_relations:
                if not isinstance(item, dict):
                    continue
                subject = str(item.get("subject", "") or "").strip()
                predicate = str(item.get("predicate", "") or "").strip()
                obj = str(item.get("object", "") or "").strip()
                if not (subject and predicate and obj):
                    continue
                try:
                    rel_conf = float(item.get("confidence", 1.0) or 1.0)
                except (TypeError, ValueError):
                    rel_conf = 1.0
                corrected_relations.append(
                    {
                        "subject": subject,
                        "predicate": predicate,
                        "object": obj,
                        "confidence": min(1.0, max(0.0, rel_conf)),
                    }
                )
        corrected_relations = corrected_relations[:6]

        return {
            "decision": decision,
            "confidence": confidence,
            "target_hashes": target_hashes,
            "corrected_relations": corrected_relations,
            "reason": str(payload.get("reason", "") or "").strip(),
            "raw": payload,
        }

    @staticmethod
    def _build_feedback_rollback_plan_summary(rollback_plan: Dict[str, Any]) -> Dict[str, Any]:
        corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
        return {
            "forgotten_relations": list(rollback_plan.get("forgotten_relations") or []),
            "corrected_write": corrected_write,
            "stale_marks": list(rollback_plan.get("stale_marks") or []),
            "episode_sources": FeedbackService._tokens(rollback_plan.get("episode_sources")),
            "profile_person_ids": FeedbackService._tokens(rollback_plan.get("profile_person_ids")),
            "affected_counts": {
                "forgotten_relations": len(list(rollback_plan.get("forgotten_relations") or [])),
                "corrected_relations": len(list(corrected_write.get("corrected_relations") or [])),
                "correction_paragraphs": len(list(corrected_write.get("paragraph_hashes") or [])),
                "stale_marks": len(list(rollback_plan.get("stale_marks") or [])),
                "episode_sources": len(FeedbackService._tokens(rollback_plan.get("episode_sources"))),
                "profile_person_ids": len(FeedbackService._tokens(rollback_plan.get("profile_person_ids"))),
            },
        }

    # ------------------------------------------------------------------ #
    # 配置访问（per-scope ctx，统一 integration.feedback_correction_* 前缀）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fb_cfg(ctx: Any, key: str, default: Any) -> Any:
        return ctx.get_config(f"integration.feedback_correction_{key}", default)

    @staticmethod
    def _fb_cfg_enabled(ctx: Any) -> bool:
        return bool(FeedbackService._fb_cfg(ctx, "enabled", False))

    @staticmethod
    def _fb_cfg_window_hours(ctx: Any) -> float:
        return max(0.1, float(FeedbackService._fb_cfg(ctx, "window_hours", 12.0) or 12.0))

    @staticmethod
    def _fb_cfg_check_interval_seconds(ctx: Any) -> float:
        minutes = max(1, int(FeedbackService._fb_cfg(ctx, "check_interval_minutes", 30) or 30))
        return float(minutes) * 60.0

    @staticmethod
    def _fb_cfg_batch_size(ctx: Any) -> int:
        return max(1, int(FeedbackService._fb_cfg(ctx, "batch_size", 20) or 20))

    @staticmethod
    def _fb_cfg_auto_apply_threshold(ctx: Any) -> float:
        value = float(FeedbackService._fb_cfg(ctx, "auto_apply_threshold", 0.85) or 0.85)
        return min(1.0, max(0.0, value))

    @staticmethod
    def _fb_cfg_max_messages(ctx: Any) -> int:
        return max(1, int(FeedbackService._fb_cfg(ctx, "max_feedback_messages", 30) or 30))

    @staticmethod
    def _fb_cfg_prefilter_enabled(ctx: Any) -> bool:
        return bool(FeedbackService._fb_cfg(ctx, "prefilter_enabled", True))

    @staticmethod
    def _fb_cfg_paragraph_mark_enabled(ctx: Any) -> bool:
        return bool(FeedbackService._fb_cfg(ctx, "paragraph_mark_enabled", True))

    @staticmethod
    def _fb_cfg_profile_refresh_enabled(ctx: Any) -> bool:
        return bool(FeedbackService._fb_cfg(ctx, "profile_refresh_enabled", True))

    @staticmethod
    def _fb_cfg_episode_rebuild_enabled(ctx: Any) -> bool:
        return bool(FeedbackService._fb_cfg(ctx, "episode_rebuild_enabled", True))

    @staticmethod
    def _fb_cfg_reconcile_interval_seconds(ctx: Any) -> float:
        minutes = max(1, int(FeedbackService._fb_cfg(ctx, "reconcile_interval_minutes", 5) or 5))
        return float(minutes) * 60.0

    @staticmethod
    def _fb_cfg_reconcile_batch_size(ctx: Any) -> int:
        return max(1, int(FeedbackService._fb_cfg(ctx, "reconcile_batch_size", 20) or 20))

    @staticmethod
    def _fb_cfg_window_label(ctx: Any) -> str:
        hours = FeedbackService._fb_cfg_window_hours(ctx)
        if abs(hours - round(hours)) < 1e-9:
            return f"{int(round(hours))}h"
        return f"{hours:.2f}h"

    # ------------------------------------------------------------------ #
    # 入队与提取
    # ------------------------------------------------------------------ #

    async def enqueue_feedback(
        self,
        scope_key: str,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: Any = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        if not self._fb_cfg_enabled(ctx):
            return {"success": False, "queued": False, "reason": "feedback_correction_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "queued": False, "reason": "metadata_store_unavailable"}

        clean_tool_id = str(query_tool_id or "").strip()
        clean_session_id = str(session_id or "").strip()
        if not clean_tool_id or not clean_session_id:
            return {"success": False, "queued": False, "reason": "missing_required_fields"}

        content = structured_content if isinstance(structured_content, dict) else {}
        hits = content.get("hits")
        if not isinstance(hits, list) or not hits:
            return {"success": False, "queued": False, "reason": "no_hits"}

        query_time = self._coerce_datetime(query_timestamp) or datetime.now()
        due_at = query_time + timedelta(hours=self._fb_cfg_window_hours(ctx))
        saved = metadata_store.enqueue_feedback_task(
            query_tool_id=clean_tool_id,
            session_id=clean_session_id,
            query_timestamp=query_time.timestamp(),
            due_at=due_at.timestamp(),
            query_snapshot=content,
        )
        if not isinstance(saved, dict):
            return {"success": False, "queued": False, "reason": "db_save_failed"}

        logger.debug(
            f"反馈纠错任务入队: query_tool_id={clean_tool_id} due_at={due_at.isoformat()}",
        )
        return {
            "success": True,
            "queued": True,
            "query_tool_id": clean_tool_id,
            "due_at": due_at.isoformat(),
            "task": saved,
        }

    @classmethod
    def _extract_feedback_messages(
        cls,
        ctx: Any,
        *,
        session_id: str,
        query_time: datetime,
        due_time: datetime,
        max_messages: int,
    ) -> List[str]:
        message_api = MessageAPI(ctx.metadata_store)
        raw_messages = message_api.get_messages_by_time_in_chat(
            chat_id=session_id,
            start_time=query_time.timestamp(),
            end_time=due_time.timestamp(),
            limit=max(1, int(max_messages) * 4),
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
        )
        collected: List[str] = []
        seen = set()
        for item in raw_messages:
            text = str(getattr(item, "processed_plain_text", "") or "").strip()
            if cls._feedback_noise(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            collected.append(text)
        if len(collected) > max_messages:
            collected = collected[-max_messages:]
        return collected

    def _build_feedback_hit_briefs(self, ctx: Any, hits: List[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
        metadata_store = ctx.metadata_store
        briefs: List[Dict[str, Any]] = []
        for raw in hits[: max(1, int(limit))]:
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            subject = str(metadata.get("subject", "") or "").strip()
            predicate = str(metadata.get("predicate", "") or "").strip()
            obj = str(metadata.get("object", "") or "").strip()
            linked_relation_hashes: List[str] = []
            linked_relation_texts: List[str] = []

            item_type = str(raw.get("type", "") or "").strip()
            item_hash = str(raw.get("hash", "") or "").strip()
            if item_type == "paragraph" and item_hash and metadata_store is not None:
                linked_relations = metadata_store.get_paragraph_relations(item_hash)
                for relation in linked_relations:
                    relation_hash = str(relation.get("hash", "") or "").strip()
                    if not relation_hash or relation_hash in linked_relation_hashes:
                        continue
                    linked_relation_hashes.append(relation_hash)
                    rel_subject = str(relation.get("subject", "") or "").strip()
                    rel_predicate = str(relation.get("predicate", "") or "").strip()
                    rel_object = str(relation.get("object", "") or "").strip()
                    relation_text = self._format_relation_text(rel_subject, rel_predicate, rel_object)
                    if relation_text:
                        linked_relation_texts.append(relation_text)
                    if not (subject and predicate and obj):
                        subject = rel_subject
                        predicate = rel_predicate
                        obj = rel_object
            briefs.append(
                {
                    "hash": item_hash,
                    "type": item_type,
                    "content": str(raw.get("content", "") or "").strip(),
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "linked_relation_hashes": linked_relation_hashes[:6],
                    "linked_relation_texts": linked_relation_texts[:3],
                }
            )
        return briefs

    # ------------------------------------------------------------------ #
    # 分类与应用
    # ------------------------------------------------------------------ #

    async def _classify_feedback(
        self,
        ctx: Any,
        *,
        query_tool_id: str,
        query_text: str,
        hit_briefs: List[Dict[str, Any]],
        feedback_messages: List[str],
    ) -> Dict[str, Any]:
        prompt = (
            "你是长期记忆纠错分类器。"
            "你会根据“记忆检索命中列表”和“用户后续反馈”判断是否需要修正记忆。"
            "请严格输出 JSON 对象，不要输出解释文字。\n\n"
            f"query_tool_id: {query_tool_id}\n"
            f"原查询: {query_text}\n"
            f"候选命中: {json.dumps(hit_briefs, ensure_ascii=False)}\n"
            f"反馈消息: {json.dumps(feedback_messages, ensure_ascii=False)}\n\n"
            "输出 JSON schema:\n"
            "{"
            "\"decision\":\"confirm|reject|correct|supplement|none\","
            "\"confidence\":0.0,"
            "\"target_hashes\":[\"命中列表中的 hash\"],"
            "\"corrected_relations\":[{\"subject\":\"\",\"predicate\":\"\",\"object\":\"\",\"confidence\":1.0}],"
            "\"reason\":\"\""
            "}\n"
            "约束:\n"
            "1. 只有当反馈明确指向错误时才输出 reject/correct。\n"
            "2. target_hashes 必须来自候选命中 hash。\n"
            "3. corrected_relations 仅在 decision=correct 时填写，且必须是明确三元组。\n"
            "4. 不确定时输出 decision=none, confidence<=0.5。"
        )
        try:
            result = await generate_text(
                ctx,
                prompt,
                request_type="A_Memorix.FeedbackClassify",
            )
            payload = self._safe_json_loads(result.text) if result.success else {}
        except Exception as exc:
            logger.warning(f"反馈分类器调用失败: {exc}")
            payload = {}
        return payload

    def _resolve_feedback_relation_hashes(
        self,
        ctx: Any,
        *,
        target_hashes: Sequence[str],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        metadata_store = ctx.metadata_store
        resolved: List[str] = []
        seen: set[str] = set()
        for target_hash in target_hashes:
            token = str(target_hash or "").strip()
            if not token:
                continue
            hit = hit_map.get(token) if isinstance(hit_map, dict) else None
            item_type = str((hit or {}).get("type", "") or "").strip()
            if item_type == "relation":
                if token not in seen:
                    seen.add(token)
                    resolved.append(token)
                continue
            if item_type != "paragraph":
                continue

            linked_candidates = self._tokens((hit or {}).get("linked_relation_hashes"))
            if not linked_candidates and metadata_store is not None:
                for relation in metadata_store.get_paragraph_relations(token):
                    linked_hash = str(relation.get("hash", "") or "").strip()
                    if linked_hash:
                        linked_candidates.append(linked_hash)

            for linked_hash in linked_candidates:
                if linked_hash in seen:
                    continue
                seen.add(linked_hash)
                resolved.append(linked_hash)
        return resolved

    async def _ingest_feedback_relations(
        self,
        ctx: Any,
        scope_key: str,
        *,
        query_tool_id: str,
        session_id: str,
        relation_hashes: List[str],
        corrected_relations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        supersedes_hash = relation_hashes[0] if relation_hashes else ""
        relation_rows: List[Dict[str, Any]] = []
        for row in corrected_relations:
            relation_rows.append(
                {
                    "subject": str(row.get("subject", "") or "").strip(),
                    "predicate": str(row.get("predicate", "") or "").strip(),
                    "object": str(row.get("object", "") or "").strip(),
                    "confidence": float(row.get("confidence", 1.0) or 1.0),
                    "metadata": {
                        "supersedes_hash": supersedes_hash,
                        "supersedes_hashes": relation_hashes,
                        "from_query_tool_id": query_tool_id,
                        "feedback_window": self._fb_cfg_window_label(ctx),
                    },
                }
            )
        plain_text = "；".join(
            f"{item['subject']} {item['predicate']} {item['object']}"
            for item in relation_rows
            if item.get("subject") and item.get("predicate") and item.get("object")
        )
        external_id = compute_hash(
            "feedback_correction:"
            + query_tool_id
            + ":"
            + json.dumps(relation_rows, ensure_ascii=False, sort_keys=True)
        )
        payload = await self.ingest_service.ingest_text(
            scope_key=scope_key,
            external_id=external_id,
            source_type="chat_summary",
            text=plain_text,
            chat_id=session_id,
            relations=relation_rows,
            metadata={
                "from_query_tool_id": query_tool_id,
                "feedback_window": self._fb_cfg_window_label(ctx),
                "supersedes_hashes": relation_hashes,
                "feedback_correction_source": True,
            },
            respect_filter=False,
        )
        if isinstance(payload, dict):
            stored_ids = self._tokens(payload.get("stored_ids"))
            corrected_relation_hashes = stored_ids[1:]
            payload["external_id"] = external_id
            payload["source"] = self._chat_source(session_id)
            payload["paragraph_hashes"] = stored_ids[:1]
            payload["corrected_relation_hashes"] = corrected_relation_hashes
            base_success = bool(payload.get("success")) if "success" in payload else True
            payload["success"] = base_success and bool(corrected_relation_hashes)
            if not payload["success"] and not str(payload.get("error", "") or "").strip():
                payload["error"] = "missing_corrected_relations"
            return payload
        return {"success": False, "error": "invalid_ingest_payload"}

    def _restore_feedback_relations_from_snapshots(
        self,
        ctx: Any,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        snapshots: Dict[str, Dict[str, Any]],
        current_statuses: Optional[Dict[str, Dict[str, Any]]] = None,
        reason: str,
    ) -> Dict[str, List[str]]:
        metadata_store = ctx.metadata_store
        restored_hashes: List[str] = []
        failed_hashes: List[str] = []
        status_map = current_statuses if isinstance(current_statuses, dict) else {}

        for relation_hash in self._tokens(relation_hashes):
            snapshot = snapshots.get(relation_hash) if isinstance(snapshots, dict) else None
            if not isinstance(snapshot, dict) or not snapshot:
                failed_hashes.append(relation_hash)
                continue

            after_status = metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
            if after_status is None:
                failed_hashes.append(relation_hash)
                continue

            restored_hashes.append(relation_hash)
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="compensate_restore_relation",
                target_hash=relation_hash,
                before_payload=status_map.get(relation_hash, {}),
                after_payload=after_status,
                reason=reason,
            )

        return {
            "restored_hashes": restored_hashes,
            "failed_hashes": failed_hashes,
        }

    def _mark_feedback_stale_paragraphs(
        self,
        ctx: Any,
        *,
        task_id: int,
        query_tool_id: str,
        relation_hashes: Sequence[str],
        reason: str,
    ) -> Dict[str, List[str]]:
        metadata_store = ctx.metadata_store
        if metadata_store is None or not self._fb_cfg_paragraph_mark_enabled(ctx):
            return {}

        relation_tokens = self._tokens(relation_hashes)
        paragraph_map = metadata_store.get_paragraph_hashes_by_relation_hashes(relation_tokens)
        for relation_hash, paragraph_hashes in paragraph_map.items():
            for paragraph_hash in paragraph_hashes:
                metadata_store.upsert_paragraph_stale_relation_mark(
                    paragraph_hash=paragraph_hash,
                    relation_hash=relation_hash,
                    query_tool_id=query_tool_id,
                    task_id=task_id,
                    reason=reason,
                )
        return paragraph_map

    def _load_paragraph_rows(self, ctx: Any, paragraph_hashes: Sequence[str]) -> List[Dict[str, Any]]:
        metadata_store = ctx.metadata_store
        hashes = [str(item or "").strip() for item in paragraph_hashes if str(item or "").strip()]
        if not hashes:
            return []
        rows: List[Dict[str, Any]] = []
        for hash_value in hashes:
            row = metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        return rows

    def _enqueue_feedback_episode_rebuilds(
        self,
        ctx: Any,
        *,
        paragraph_hashes: Sequence[str],
        session_id: str,
        include_correction_source: bool,
    ) -> List[str]:
        metadata_store = ctx.metadata_store
        if metadata_store is None or not self._fb_cfg_episode_rebuild_enabled(ctx):
            return []

        sources = self._tokens(
            row.get("source", "")
            for row in self._load_paragraph_rows(ctx, paragraph_hashes)
            if isinstance(row, dict)
        )
        correction_source = self._chat_source(session_id)
        if include_correction_source and correction_source:
            sources = self._merge_tokens(sources, [correction_source])

        queued: List[str] = []
        for source in sources:
            if metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction"):
                queued.append(source)
        return queued

    def _enqueue_feedback_profile_refreshes(
        self,
        ctx: Any,
        *,
        person_ids: Sequence[str],
        query_tool_id: str,
    ) -> List[str]:
        metadata_store = ctx.metadata_store
        if metadata_store is None or not self._fb_cfg_profile_refresh_enabled(ctx):
            return []

        queued: List[str] = []
        for person_id in self._tokens(person_ids):
            payload = metadata_store.enqueue_person_profile_refresh(
                person_id=person_id,
                reason="feedback_correction",
                source_query_tool_id=query_tool_id,
            )
            if isinstance(payload, dict):
                queued.append(person_id)
        return queued

    def _resolve_feedback_related_person_ids(
        self,
        ctx: Any,
        *,
        old_relation_rows: Sequence[Dict[str, Any]],
        corrected_relations: Sequence[Dict[str, Any]],
    ) -> List[str]:
        person_profile_service = getattr(ctx, "person_profile_service", None)
        if person_profile_service is None:
            return []
        candidates = self._tokens(
            value
            for row in list(old_relation_rows) + list(corrected_relations)
            if isinstance(row, dict)
            for value in (row.get("subject"), row.get("object"))
        )
        resolved: List[str] = []
        seen = set()
        for candidate in candidates:
            person_id = person_profile_service.resolve_person_id(candidate)
            if not person_id or person_id in seen:
                continue
            seen.add(person_id)
            resolved.append(person_id)
        return resolved

    async def _apply_feedback_decision(
        self,
        ctx: Any,
        scope_key: str,
        *,
        task_id: int,
        query_tool_id: str,
        session_id: str,
        decision: Dict[str, Any],
        hit_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"applied": False, "reason": "metadata_store_unavailable"}
        threshold = self._fb_cfg_auto_apply_threshold(ctx)
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        if confidence < threshold:
            return {
                "applied": False,
                "reason": "low_confidence",
                "threshold": threshold,
                "confidence": confidence,
            }

        decision_type = str(decision.get("decision", "none") or "none").strip().lower()
        if decision_type not in {"reject", "correct"}:
            return {
                "applied": False,
                "reason": f"decision_{decision_type}_no_auto_apply",
            }

        target_hashes = [
            str(item or "").strip()
            for item in (decision.get("target_hashes") or [])
            if str(item or "").strip()
        ]
        relation_hashes = self._resolve_feedback_relation_hashes(
            ctx,
            target_hashes=target_hashes,
            hit_map=hit_map,
        )
        if not relation_hashes:
            return {"applied": False, "reason": "no_relation_targets"}

        corrected_relations = [
            dict(item)
            for item in (decision.get("corrected_relations") or [])
            if isinstance(item, dict)
        ]
        if decision_type == "correct" and not corrected_relations:
            return {
                "applied": False,
                "reason": "missing_corrected_relations",
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        old_relation_rows = list(metadata_store.get_relations_by_hashes(relation_hashes, include_inactive=True).values())
        before_status = metadata_store.get_relation_status_batch(relation_hashes)
        # forget：解除保护并置 inactive，对应"遗忘错误关系"。
        now = time.time()
        metadata_store.update_relations_protection(relation_hashes, protected_until=0.0, is_pinned=False)
        metadata_store.mark_relations_inactive(relation_hashes, inactive_since=now)
        forget_result = {"success": True, "detail": f"forget {len(relation_hashes)} 条关系", "hashes": relation_hashes}
        after_status = metadata_store.get_relation_status_batch(relation_hashes)
        for hash_value in relation_hashes:
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="forget_relation",
                target_hash=hash_value,
                before_payload=before_status.get(hash_value) if isinstance(before_status, dict) else {},
                after_payload=after_status.get(hash_value) if isinstance(after_status, dict) else {},
                reason=str(decision.get("reason", "") or ""),
            )

        ingest_result = None
        corrected_relation_hash_candidates: List[str] = []
        corrected_relation_specs_by_hash: Dict[str, Dict[str, Any]] = {}
        if decision_type == "correct" and corrected_relations:
            for item in corrected_relations:
                try:
                    relation_hash = metadata_store.compute_relation_hash(
                        str(item.get("subject", "") or "").strip(),
                        str(item.get("predicate", "") or "").strip(),
                        str(item.get("object", "") or "").strip(),
                    )
                except Exception:
                    continue
                if not relation_hash:
                    continue
                corrected_relation_hash_candidates.append(relation_hash)
                corrected_relation_specs_by_hash[relation_hash] = {
                    "subject": str(item.get("subject", "") or "").strip(),
                    "predicate": str(item.get("predicate", "") or "").strip(),
                    "object": str(item.get("object", "") or "").strip(),
                }
        corrected_relation_before_status = (
            metadata_store.get_relation_status_batch(corrected_relation_hash_candidates)
            if corrected_relation_hash_candidates
            else {}
        )
        forget_success = True
        if not forget_success:
            return {
                "applied": False,
                "reason": "forget_failed",
                "error": "forget_failed",
                "forget": forget_result,
                "ingest": ingest_result,
                "relation_hashes": relation_hashes,
                "stale_paragraph_hashes": [],
                "episode_rebuild_sources": [],
                "profile_refresh_person_ids": [],
                "rollback_plan_summary": {},
            }

        stale_paragraph_map: Dict[str, List[str]] = {}
        stale_paragraph_hashes: List[str] = []
        episode_rebuild_sources: List[str] = []
        profile_refresh_person_ids: List[str] = []
        rollback_plan: Dict[str, Any] = {}
        if decision_type == "correct" and corrected_relations:
            ingest_result = await self._ingest_feedback_relations(
                ctx,
                scope_key,
                query_tool_id=query_tool_id,
                session_id=session_id,
                relation_hashes=relation_hashes,
                corrected_relations=corrected_relations,
            )
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="ingest_correction",
                target_hash=relation_hashes[0] if relation_hashes else "",
                before_payload={"target_hashes": relation_hashes},
                after_payload=ingest_result,
                reason=str(decision.get("reason", "") or ""),
            )

            ingest_success = bool((ingest_result or {}).get("success")) if isinstance(ingest_result, dict) else False
            if not ingest_success:
                compensation_result = self._restore_feedback_relations_from_snapshots(
                    ctx,
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    relation_hashes=relation_hashes,
                    snapshots=before_status if isinstance(before_status, dict) else {},
                    current_statuses=after_status if isinstance(after_status, dict) else {},
                    reason=str(decision.get("reason", "") or "") or "feedback_correction_ingest_failed",
                )
                restore_failed_hashes = compensation_result.get("failed_hashes", [])
                return {
                    "applied": False,
                    "reason": "correction_restore_failed" if restore_failed_hashes else "correction_ingest_failed",
                    "error": str((ingest_result or {}).get("error", "") or "correction_ingest_failed"),
                    "forget": forget_result,
                    "ingest": ingest_result,
                    "relation_hashes": relation_hashes,
                    "stale_paragraph_hashes": [],
                    "episode_rebuild_sources": [],
                    "profile_refresh_person_ids": [],
                    "restored_relation_hashes": compensation_result.get("restored_hashes", []),
                    "restore_failed_hashes": restore_failed_hashes,
                    "rollback_plan_summary": {},
                }
        else:
            ingest_success = False

        applied = forget_success if decision_type == "reject" else (forget_success and ingest_success)
        if applied:
            stale_paragraph_map = self._mark_feedback_stale_paragraphs(
                ctx,
                task_id=task_id,
                query_tool_id=query_tool_id,
                relation_hashes=relation_hashes,
                reason=str(decision.get("reason", "") or "") or "feedback_correction",
            )
            stale_paragraph_hashes = self._merge_tokens(
                *[
                    paragraph_hashes
                    for paragraph_hashes in stale_paragraph_map.values()
                    if isinstance(paragraph_hashes, list)
                ]
            )
            episode_rebuild_sources = self._enqueue_feedback_episode_rebuilds(
                ctx,
                paragraph_hashes=stale_paragraph_hashes,
                session_id=session_id,
                include_correction_source=bool(ingest_success),
            )
            profile_refresh_person_ids = self._enqueue_feedback_profile_refreshes(
                ctx,
                person_ids=self._resolve_feedback_related_person_ids(
                    ctx,
                    old_relation_rows=old_relation_rows,
                    corrected_relations=corrected_relations,
                ),
                query_tool_id=query_tool_id,
            )
            for relation_hash, paragraph_hashes in stale_paragraph_map.items():
                for paragraph_hash in paragraph_hashes:
                    metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="mark_stale_paragraph",
                        target_hash=paragraph_hash,
                        after_payload={"relation_hash": relation_hash},
                        reason=str(decision.get("reason", "") or ""),
                    )
            for source in episode_rebuild_sources:
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_episode_rebuild",
                    target_hash=source,
                    reason=str(decision.get("reason", "") or ""),
                )
            for person_id in profile_refresh_person_ids:
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=str(decision.get("reason", "") or ""),
                )
            forgotten_relations = []
            for row in old_relation_rows:
                relation_hash = str(row.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                forgotten_relations.append(
                    {
                        "hash": relation_hash,
                        "subject": str(row.get("subject", "") or "").strip(),
                        "predicate": str(row.get("predicate", "") or "").strip(),
                        "object": str(row.get("object", "") or "").strip(),
                        "before_status": before_status.get(relation_hash) if isinstance(before_status, dict) else {},
                    }
                )

            corrected_write: Dict[str, Any] = {}
            if isinstance(ingest_result, dict):
                stored_relation_hashes = self._tokens(ingest_result.get("corrected_relation_hashes"))
                corrected_write = {
                    "external_id": str(ingest_result.get("external_id", "") or "").strip(),
                    "source": str(ingest_result.get("source", "") or "").strip(),
                    "paragraph_hashes": self._tokens(ingest_result.get("paragraph_hashes")),
                    "corrected_relation_hashes": stored_relation_hashes,
                    "corrected_relations": [
                        {
                            "hash": relation_hash,
                            **corrected_relation_specs_by_hash.get(relation_hash, {}),
                            "existed_before": relation_hash in corrected_relation_before_status,
                            "before_status": corrected_relation_before_status.get(relation_hash, {}),
                        }
                        for relation_hash in stored_relation_hashes
                    ],
                }

            rollback_plan = {
                "task_id": task_id,
                "query_tool_id": query_tool_id,
                "session_id": session_id,
                "decision_type": decision_type,
                "forgotten_relations": forgotten_relations,
                "corrected_write": corrected_write,
                "stale_marks": [
                    {"paragraph_hash": paragraph_hash, "relation_hash": relation_hash}
                    for relation_hash, paragraph_hashes in stale_paragraph_map.items()
                    for paragraph_hash in (paragraph_hashes or [])
                    if str(paragraph_hash or "").strip()
                ],
                "episode_sources": episode_rebuild_sources,
                "profile_person_ids": profile_refresh_person_ids,
                "created_at": time.time(),
            }
            metadata_store.update_feedback_task_rollback_plan(
                task_id=task_id,
                rollback_plan=rollback_plan,
            )
            # 应用成功后统一落盘，确保遗忘/纠错的关系与向量索引与 metadata_store 一致。
            await self._save_all(ctx)
        return {
            "applied": applied,
            "forget": forget_result,
            "ingest": ingest_result,
            "relation_hashes": relation_hashes,
            "stale_paragraph_hashes": stale_paragraph_hashes,
            "episode_rebuild_sources": episode_rebuild_sources,
            "profile_refresh_person_ids": profile_refresh_person_ids,
            "rollback_plan_summary": self._build_feedback_rollback_plan_summary(rollback_plan) if rollback_plan else {},
        }

    async def _process_feedback_task(self, ctx: Any, scope_key: str, task: Dict[str, Any]) -> None:
        metadata_store = ctx.metadata_store
        task_id = int(task.get("id") or 0)
        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        if task_id <= 0 or not query_tool_id:
            return

        metadata_store.mark_feedback_task_running(task_id)

        decision_payload: Dict[str, Any] = {}
        session_id = str(task.get("session_id", "") or "").strip()
        try:
            structured = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
            if not session_id:
                session_id = str(structured.get("chat_id", "") or "").strip()
            if not session_id:
                raise RuntimeError("反馈任务缺少 session_id")
            hits_raw = structured.get("hits")
            if not isinstance(hits_raw, list) or not hits_raw:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_hits"}
                metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            query_timestamp = self._coerce_datetime(task.get("query_timestamp")) or datetime.now()
            due_at = self._coerce_datetime(task.get("due_at")) or (
                query_timestamp + timedelta(hours=self._fb_cfg_window_hours(ctx))
            )
            if due_at <= query_timestamp:
                due_at = query_timestamp + timedelta(hours=self._fb_cfg_window_hours(ctx))

            feedback_messages = self._extract_feedback_messages(
                ctx,
                session_id=session_id,
                query_time=query_timestamp,
                due_time=due_at,
                max_messages=self._fb_cfg_max_messages(ctx),
            )
            if not feedback_messages:
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "no_feedback_messages"}
                metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            if self._fb_cfg_prefilter_enabled(ctx) and not self._should_invoke_feedback_classifier(feedback_messages):
                decision_payload = {"decision": "none", "confidence": 1.0, "reason": "prefilter_skipped"}
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="skip",
                    reason="prefilter_skipped",
                    after_payload={"feedback_messages": feedback_messages},
                )
                metadata_store.finalize_feedback_task(
                    task_id=task_id,
                    status="skipped",
                    decision_payload=decision_payload,
                )
                return

            hit_briefs = self._build_feedback_hit_briefs(ctx, hits_raw)
            hit_map = {str(item.get("hash", "") or "").strip(): item for item in hit_briefs if str(item.get("hash", "") or "").strip()}
            raw_decision = await self._classify_feedback(
                ctx,
                query_tool_id=query_tool_id,
                query_text=str(structured.get("query", "") or ""),
                hit_briefs=hit_briefs,
                feedback_messages=feedback_messages,
            )
            decision_payload = self._normalize_feedback_decision(raw_decision, hit_hashes=list(hit_map.keys()))
            decision_payload["feedback_message_count"] = len(feedback_messages)
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="classification",
                after_payload=decision_payload,
                reason=str(decision_payload.get("reason", "") or ""),
            )

            apply_result = await self._apply_feedback_decision(
                ctx,
                scope_key,
                task_id=task_id,
                query_tool_id=query_tool_id,
                session_id=session_id,
                decision=decision_payload,
                hit_map=hit_map,
            )
            decision_payload["apply_result"] = apply_result
            final_status = self._feedback_apply_result_status(apply_result)
            metadata_store.finalize_feedback_task(
                task_id=task_id,
                status=final_status,
                decision_payload=decision_payload,
                last_error=str(apply_result.get("error", "") or "") if final_status == "error" else "",
            )
        except Exception as exc:
            logger.warning(f"反馈纠错任务处理失败: task_id={task_id} err={exc}", exc_info=True)
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="error",
                reason=str(exc),
                after_payload=decision_payload if decision_payload else None,
            )
            metadata_store.finalize_feedback_task(
                task_id=task_id,
                status="error",
                decision_payload=decision_payload if decision_payload else None,
                last_error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # reconcile：人物画像刷新 / episode 重建批次
    # ------------------------------------------------------------------ #

    async def _process_feedback_profile_refresh_batch(self, ctx: Any, *, limit: int) -> Dict[str, Any]:
        metadata_store = ctx.metadata_store
        person_profile_service = getattr(ctx, "person_profile_service", None)
        if metadata_store is None or person_profile_service is None:
            return {"processed": 0, "refreshed": 0, "failed": 0, "items": [], "failures": []}

        rows = metadata_store.fetch_person_profile_refresh_batch(
            limit=max(1, int(limit or 1)),
            max_retry=max(1, int(ctx.get_config("person_profile.max_retry", 3) or 3)),
        )
        api_service = PersonProfileApiService(ctx)
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            person_id = str(row.get("person_id", "") or "").strip()
            requested_at = row.get("requested_at")
            if not person_id:
                continue
            if not metadata_store.mark_person_profile_refresh_running(person_id, requested_at=requested_at):
                continue
            try:
                profile = await api_service.query(
                    person_id=person_id,
                    top_k=max(4, int(ctx.get_config("person_profile.top_k_evidence", 12) or 12)),
                    force_refresh=True,
                    source_note="feedback_service.profile_refresh",
                )
                if isinstance(profile, dict) and bool(profile.get("success")):
                    metadata_store.mark_person_profile_refresh_done(person_id, requested_at=requested_at)
                    items.append(
                        {
                            "person_id": person_id,
                            "profile_version": int(profile.get("profile_version", 0) or 0),
                            "profile_source": str(profile.get("profile_source", "") or ""),
                        }
                    )
                else:
                    error = str((profile or {}).get("error", "") or "person profile refresh failed")
                    metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                    failures.append({"person_id": person_id, "error": error})
            except Exception as exc:
                error = str(exc)[:500]
                metadata_store.mark_person_profile_refresh_failed(person_id, error, requested_at=requested_at)
                failures.append({"person_id": person_id, "error": error})
        return {
            "processed": len(items) + len(failures),
            "refreshed": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    async def _process_feedback_episode_rebuild_batch(self, ctx: Any, *, limit: int) -> Dict[str, Any]:
        metadata_store = ctx.metadata_store
        episode_service = getattr(ctx, "episode_service", None)
        if metadata_store is None or episode_service is None:
            return {"processed": 0, "rebuilt": 0, "failed": 0, "items": [], "failures": []}

        rows = metadata_store.fetch_episode_source_rebuild_batch(
            limit=max(1, int(limit or 1)),
            max_retry=max(1, int(ctx.get_config("episode.pending_max_retry", 3) or 3)),
        )
        items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            source = str(row.get("source", "") or "").strip()
            requested_at = row.get("requested_at")
            if not source:
                continue
            if not metadata_store.mark_episode_source_running(source, requested_at=requested_at):
                continue
            try:
                result = await episode_service.rebuild_source(source)
                metadata_store.mark_episode_source_done(source, requested_at=requested_at)
                items.append(result if isinstance(result, dict) else {"source": source})
            except Exception as exc:
                error = str(exc)[:500]
                metadata_store.mark_episode_source_failed(source, error, requested_at=requested_at)
                failures.append({"source": source, "error": error})
        return {
            "processed": len(items) + len(failures),
            "rebuilt": len(items),
            "failed": len(failures),
            "items": items,
            "failures": failures,
        }

    # ------------------------------------------------------------------ #
    # 回滚
    # ------------------------------------------------------------------ #

    async def rollback_feedback_task(
        self,
        ctx: Any,
        *,
        task_id: int,
        requested_by: str,
        reason: str,
    ) -> Dict[str, Any]:
        metadata_store = ctx.metadata_store
        task = metadata_store.get_feedback_task_by_id(task_id)
        if task is None:
            return {"success": False, "error": "反馈纠错任务不存在"}
        if str(task.get("status", "") or "").strip().lower() != "applied":
            return {"success": False, "error": "仅 applied 的反馈纠错任务允许回退"}
        rollback_status = str(task.get("rollback_status", "") or "none").strip().lower()
        if rollback_status == "rolled_back":
            return {
                "success": True,
                "already_rolled_back": True,
                "task": self._build_feedback_task_detail(ctx, task),
                "result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
            }
        if rollback_status == "running":
            return {"success": False, "error": "该反馈纠错任务正在回退中", "task": self._build_feedback_task_detail(ctx, task)}

        query_tool_id = str(task.get("query_tool_id", "") or "").strip()
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        if not rollback_plan:
            running_task = metadata_store.mark_feedback_task_rollback_running(
                task_id=task_id,
                requested_by=requested_by,
                reason=reason,
            )
            if running_task is None:
                latest_task = metadata_store.get_feedback_task_by_id(task_id)
                latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
                if latest_status == "running":
                    return {
                        "success": False,
                        "error": "该反馈纠错任务正在回退中",
                        "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
                    }
                if latest_status == "rolled_back":
                    return {
                        "success": True,
                        "already_rolled_back": True,
                        "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
                        "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                    }
                return {
                    "success": False,
                    "error": "无法进入回退状态",
                    "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
                }
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason="rollback_plan_missing",
            )
            failed = metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_error="rollback_plan_missing",
            )
            return {"success": False, "error": "缺少 rollback_plan，无法回退", "task": failed}

        running_task = metadata_store.mark_feedback_task_rollback_running(
            task_id=task_id,
            requested_by=requested_by,
            reason=reason,
        )
        if running_task is None:
            latest_task = metadata_store.get_feedback_task_by_id(task_id)
            latest_status = str((latest_task or {}).get("rollback_status", "") or "none").strip().lower()
            if latest_status == "running":
                return {
                    "success": False,
                    "error": "该反馈纠错任务正在回退中",
                    "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
                }
            if latest_status == "rolled_back":
                return {
                    "success": True,
                    "already_rolled_back": True,
                    "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
                    "result": (latest_task or {}).get("rollback_result") if isinstance((latest_task or {}).get("rollback_result"), dict) else {},
                }
            return {
                "success": False,
                "error": "无法进入回退状态",
                "task": self._build_feedback_task_detail(ctx, latest_task) if isinstance(latest_task, dict) else None,
            }

        result: Dict[str, Any] = {
            "task_id": task_id,
            "query_tool_id": query_tool_id,
            "restored_relation_hashes": [],
            "reverted_corrected_relation_hashes": [],
            "deleted_correction_paragraph_hashes": [],
            "cleared_stale_mark_count": 0,
            "episode_sources_queued": [],
            "profile_person_ids_queued": [],
            "warnings": [],
        }
        try:
            forgotten_relations = rollback_plan.get("forgotten_relations") if isinstance(rollback_plan.get("forgotten_relations"), list) else []
            for item in forgotten_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                if not relation_hash or not snapshot:
                    continue
                before_status = metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                after_status = metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                if after_status is None:
                    result["warnings"].append(f"restore_old_relation_failed:{relation_hash}")
                    continue
                result["restored_relation_hashes"].append(relation_hash)
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_restore_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            corrected_write = rollback_plan.get("corrected_write") if isinstance(rollback_plan.get("corrected_write"), dict) else {}
            correction_paragraph_hashes = self._tokens(corrected_write.get("paragraph_hashes"))
            deleted_paragraphs = metadata_store.soft_delete_paragraphs(correction_paragraph_hashes)
            result["deleted_correction_paragraph_hashes"] = deleted_paragraphs.get("deleted_hashes", [])
            paragraph_rows = deleted_paragraphs.get("paragraph_rows") if isinstance(deleted_paragraphs.get("paragraph_rows"), dict) else {}
            deleted_external_refs = deleted_paragraphs.get("deleted_external_refs") if isinstance(deleted_paragraphs.get("deleted_external_refs"), list) else []
            deleted_ref_map: Dict[str, List[Dict[str, Any]]] = {}
            for ref in deleted_external_refs:
                if not isinstance(ref, dict):
                    continue
                paragraph_hash = str(ref.get("paragraph_hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                deleted_ref_map.setdefault(paragraph_hash, []).append(ref)
            for paragraph_hash in result["deleted_correction_paragraph_hashes"]:
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_delete_correction_paragraph",
                    target_hash=paragraph_hash,
                    before_payload={
                        "paragraph": paragraph_rows.get(paragraph_hash) if isinstance(paragraph_rows.get(paragraph_hash), dict) else {},
                        "external_refs": deleted_ref_map.get(paragraph_hash, []),
                    },
                    reason=reason,
                )

            corrected_relations = corrected_write.get("corrected_relations") if isinstance(corrected_write.get("corrected_relations"), list) else []
            for item in corrected_relations:
                if not isinstance(item, dict):
                    continue
                relation_hash = str(item.get("hash", "") or "").strip()
                if not relation_hash:
                    continue
                before_status = metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash, {})
                if bool(item.get("existed_before")):
                    snapshot = item.get("before_status") if isinstance(item.get("before_status"), dict) else {}
                    after_status = metadata_store.restore_relation_status_from_snapshot(relation_hash, snapshot)
                else:
                    metadata_store.update_relations_protection([relation_hash], protected_until=0.0, is_pinned=False)
                    metadata_store.mark_relations_inactive([relation_hash], inactive_since=time.time())
                    after_status = metadata_store.get_relation_status_batch([relation_hash]).get(relation_hash)
                if after_status is None:
                    result["warnings"].append(f"revert_corrected_relation_failed:{relation_hash}")
                    continue
                result["reverted_corrected_relation_hashes"].append(relation_hash)
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_revert_corrected_relation",
                    target_hash=relation_hash,
                    before_payload=before_status,
                    after_payload=after_status,
                    reason=reason,
                )

            stale_marks_raw = rollback_plan.get("stale_marks") if isinstance(rollback_plan.get("stale_marks"), list) else []
            stale_marks: List[tuple[str, str]] = []
            for item in stale_marks_raw:
                if not isinstance(item, dict):
                    continue
                paragraph_hash = str(item.get("paragraph_hash", "") or "").strip()
                relation_hash = str(item.get("relation_hash", "") or "").strip()
                if not paragraph_hash or not relation_hash:
                    continue
                stale_marks.append((paragraph_hash, relation_hash))
            result["cleared_stale_mark_count"] = metadata_store.delete_paragraph_stale_relation_marks(stale_marks)
            for paragraph_hash, relation_hash in stale_marks:
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_clear_stale_mark",
                    target_hash=paragraph_hash,
                    after_payload={"relation_hash": relation_hash},
                    reason=reason,
                )

            for source in self._tokens(rollback_plan.get("episode_sources")):
                if metadata_store.enqueue_episode_source_rebuild(source, reason="feedback_correction_rollback"):
                    result["episode_sources_queued"].append(source)
                    metadata_store.append_feedback_action_log(
                        task_id=task_id,
                        query_tool_id=query_tool_id,
                        action_type="rollback_enqueue_episode_rebuild",
                        target_hash=source,
                        reason=reason,
                    )

            for person_id in self._tokens(rollback_plan.get("profile_person_ids")):
                payload = metadata_store.enqueue_person_profile_refresh(
                    person_id=person_id,
                    reason="feedback_correction_rollback",
                    source_query_tool_id=query_tool_id,
                )
                if not isinstance(payload, dict):
                    continue
                result["profile_person_ids_queued"].append(person_id)
                metadata_store.append_feedback_action_log(
                    task_id=task_id,
                    query_tool_id=query_tool_id,
                    action_type="rollback_enqueue_profile_refresh",
                    target_hash=person_id,
                    reason=reason,
                )

            await self._save_all(ctx)
            final_task = metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="rolled_back",
                rollback_result=result,
            )
            return {"success": True, "result": result, "task": self._build_feedback_task_detail(ctx, final_task or running_task)}
        except Exception as exc:
            logger.warning(f"反馈纠错回退失败: task_id={task_id} err={exc}", exc_info=True)
            metadata_store.append_feedback_action_log(
                task_id=task_id,
                query_tool_id=query_tool_id,
                action_type="rollback_error",
                reason=str(exc),
                after_payload=result if result else None,
            )
            final_task = metadata_store.finalize_feedback_task_rollback(
                task_id=task_id,
                rollback_status="error",
                rollback_result=result if result else None,
                rollback_error=str(exc),
            )
            return {
                "success": False,
                "error": str(exc),
                "result": result,
                "task": self._build_feedback_task_detail(ctx, final_task or running_task),
            }

    # ------------------------------------------------------------------ #
    # admin 视图
    # ------------------------------------------------------------------ #

    def _build_feedback_task_summary(self, ctx: Any, task: Dict[str, Any]) -> Dict[str, Any]:
        query_snapshot = task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {}
        decision_payload = task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {}
        rollback_plan = task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
        return {
            "task_id": int(task.get("id", 0) or 0),
            "query_tool_id": str(task.get("query_tool_id", "") or "").strip(),
            "session_id": str(task.get("session_id", "") or "").strip(),
            "query_text": str(query_snapshot.get("query", "") or "").strip(),
            "query_timestamp": task.get("query_timestamp"),
            "task_status": str(task.get("status", "") or "").strip().lower(),
            "decision": str(decision_payload.get("decision", "") or "").strip().lower(),
            "decision_confidence": float(decision_payload.get("confidence", 0.0) or 0.0),
            "feedback_message_count": int(decision_payload.get("feedback_message_count", 0) or 0),
            "rollback_status": str(task.get("rollback_status", "") or "none").strip().lower() or "none",
            "affected_counts": self._build_feedback_rollback_plan_summary(rollback_plan).get("affected_counts", {}),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }

    def _build_feedback_task_detail(self, ctx: Any, task: Dict[str, Any]) -> Dict[str, Any]:
        detail = self._build_feedback_task_summary(ctx, task)
        detail.update(
            {
                "query_snapshot": task.get("query_snapshot") if isinstance(task.get("query_snapshot"), dict) else {},
                "decision_payload": task.get("decision_payload") if isinstance(task.get("decision_payload"), dict) else {},
                "rollback_plan_summary": self._build_feedback_rollback_plan_summary(
                    task.get("rollback_plan") if isinstance(task.get("rollback_plan"), dict) else {}
                ),
                "rollback_result": task.get("rollback_result") if isinstance(task.get("rollback_result"), dict) else {},
                "rollback_error": str(task.get("rollback_error", "") or "").strip(),
                "rollback_requested_by": str(task.get("rollback_requested_by", "") or "").strip(),
                "rollback_reason": str(task.get("rollback_reason", "") or "").strip(),
                "rollback_requested_at": task.get("rollback_requested_at"),
                "rolled_back_at": task.get("rolled_back_at"),
                "action_logs": ctx.metadata_store.list_feedback_action_logs(int(task.get("id", 0) or 0))
                if ctx.metadata_store is not None
                else [],
            }
        )
        return detail

    async def list_feedback_tasks(self, ctx: Any, *, limit: int = 50, statuses: Optional[List[str]] = None, rollback_statuses: Optional[List[str]] = None, query: str = "") -> Dict[str, Any]:
        items = ctx.metadata_store.list_feedback_tasks(
            limit=max(1, int(limit or 50)),
            statuses=statuses,
            rollback_statuses=rollback_statuses,
            query=str(query or "").strip(),
        )
        return {
            "success": True,
            "items": [self._build_feedback_task_summary(ctx, task) for task in items],
            "count": len(items),
        }

    async def get_feedback_task(self, ctx: Any, task_id: int) -> Dict[str, Any]:
        task = ctx.metadata_store.get_feedback_task_by_id(int(task_id or 0))
        if task is None:
            return {"success": False, "error": "反馈纠错任务不存在"}
        return {"success": True, "task": self._build_feedback_task_detail(ctx, task)}

    # ------------------------------------------------------------------ #
    # 落盘 + 后台 loop
    # ------------------------------------------------------------------ #

    async def _save_all(self, ctx: Any) -> None:
        """统一落盘入口：复用 AppContext.save_all 持久化 vector/graph。"""
        save_all = getattr(ctx, "save_all", None)
        if save_all is None:
            return
        try:
            await save_all()
        except Exception as exc:
            logger.warning(f"save_all 落盘失败: {exc}")

    async def _feedback_correction_loop(self) -> None:
        try:
            while not self._stopping:
                sleep_seconds = float(self._min_check_interval_seconds())
                processed_any = False
                for scope_key in self.runtime_manager.get_known_scopes():
                    if self._stopping:
                        break
                    try:
                        runtime = await self.runtime_manager.get_runtime(scope_key)
                    except Exception as exc:
                        logger.warning(f"feedback loop 取 runtime 失败 scope={scope_key}: {exc}")
                        continue
                    ctx = runtime.context
                    if not self._fb_cfg_enabled(ctx) or ctx.metadata_store is None:
                        continue
                    tasks = ctx.metadata_store.fetch_due_feedback_tasks(
                        limit=self._fb_cfg_batch_size(ctx),
                        now=time.time(),
                    )
                    if not tasks:
                        continue
                    processed_any = True
                    for task in tasks:
                        if self._stopping:
                            break
                        if not isinstance(task, dict):
                            continue
                        await self._process_feedback_task(ctx, scope_key, task)
                await asyncio.sleep(2.0 if processed_any else sleep_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction loop 异常: {exc}")

    async def _feedback_correction_reconcile_loop(self) -> None:
        try:
            while not self._stopping:
                await asyncio.sleep(self._min_reconcile_interval_seconds())
                if self._stopping:
                    break
                for scope_key in self.runtime_manager.get_known_scopes():
                    if self._stopping:
                        break
                    try:
                        runtime = await self.runtime_manager.get_runtime(scope_key)
                    except Exception as exc:
                        logger.warning(f"reconcile loop 取 runtime 失败 scope={scope_key}: {exc}")
                        continue
                    ctx = runtime.context
                    if not self._fb_cfg_enabled(ctx) or ctx.metadata_store is None:
                        continue
                    batch_size = self._fb_cfg_reconcile_batch_size(ctx)
                    if self._fb_cfg_profile_refresh_enabled(ctx):
                        await self._process_feedback_profile_refresh_batch(ctx, limit=batch_size)
                    if self._fb_cfg_episode_rebuild_enabled(ctx):
                        await self._process_feedback_episode_rebuild_batch(ctx, limit=batch_size)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"feedback_correction_reconcile loop 异常: {exc}")

    @staticmethod
    def _min_check_interval_seconds() -> float:
        # loop 遍历多 scope，取一个保守的全局下限避免空转；各 scope 的实际间隔由 enabled 判断自然延后。
        return 60.0

    @staticmethod
    def _min_reconcile_interval_seconds() -> float:
        return 60.0

    async def start_background_loops(self) -> None:
        async with self._lock:
            self._stopping = False
            self._loops = [
                asyncio.create_task(self._feedback_correction_loop(), name="A_Memorix.feedback_correction"),
                asyncio.create_task(self._feedback_correction_reconcile_loop(), name="A_Memorix.feedback_correction_reconcile"),
            ]

    async def stop_background_loops(self) -> None:
        async with self._lock:
            self._stopping = True
            loops = list(self._loops)
            self._loops = []
        for task in loops:
            task.cancel()
        if loops:
            await asyncio.gather(*loops, return_exceptions=True)

    async def trigger_reconcile(self, scope_key: Optional[str] = None) -> Dict[str, Any]:
        """手动触发一次 reconcile（admin 调用），不依赖后台 loop 周期。"""
        results: Dict[str, Any] = {}
        scopes = [scope_key] if scope_key else list(self.runtime_manager.get_known_scopes())
        for key in scopes:
            try:
                runtime = await self.runtime_manager.get_runtime(key)
            except Exception as exc:
                results[key] = {"error": str(exc)}
                continue
            ctx = runtime.context
            if not self._fb_cfg_enabled(ctx) or ctx.metadata_store is None:
                results[key] = {"skipped": "disabled"}
                continue
            batch_size = self._fb_cfg_reconcile_batch_size(ctx)
            profile = await self._process_feedback_profile_refresh_batch(ctx, limit=batch_size) if self._fb_cfg_profile_refresh_enabled(ctx) else {"processed": 0}
            episode = await self._process_feedback_episode_rebuild_batch(ctx, limit=batch_size) if self._fb_cfg_episode_rebuild_enabled(ctx) else {"processed": 0}
            results[key] = {"profile_refresh": profile, "episode_rebuild": episode}
        return results
