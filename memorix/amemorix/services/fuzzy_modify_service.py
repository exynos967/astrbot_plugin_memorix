"""记忆模糊修正服务（跨作用域）。

用户以自然语言描述"记忆记错了/应改为…"时，本服务：
1. 检索可能受影响的候选记忆（段落/关系）；
2. 调用 LLM 生成结构化修改计划（mark_superseded / ingest_text / refresh_person_profile）；
3. 落表为 ``awaiting_confirmation`` 计划，等待人工确认；
4. 确认后执行：标记旧目标 superseded、级联处理受影响关系（标记 inactive 或
   段落-关系 stale 证据标记），并摄入新内容作为替代；
5. 支持回滚：恢复旧目标元数据、撤销级联标记、软删替代段落、停用替代关系。

架构镜像 ``FeedbackService``——跨作用域，依赖 ``ScopeRuntimeManager`` 遍历各
scope 的 ``AppContext``；每个 scope 独立 metadata_store，故修改计划表按 scope
隔离。LLM 调用统一经 ``generate_text(ctx, prompt, request_type=...)``。

安全默认（硬性）：``enabled=False``、``auto_execute_enabled=False``、
``confirm_threshold=0.85``、``allow_global_scope=False``。所有公共方法首先
判 enabled，未启用直接返回 ``fuzzy_modify_disabled``。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..common.logging import get_logger
from ...core.storage import detect_knowledge_type
from ...core.utils.hash import compute_hash, normalize_text
from ...core.utils.metadata import coerce_metadata_dict
from ...core.utils.model_routing import generate_text

logger = get_logger("A_Memorix.FuzzyModifyService")


# ---------------------------------------------------------------------- #
# 模块级纯函数 / 工具：normalize / cascade / scope
# ---------------------------------------------------------------------- #

def _trim_text(value: str, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _optional_float(value: Any) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except Exception:
        return None


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


def _argument_tokens(value: Any) -> List[str]:
    if isinstance(value, str):
        return _tokens([value])
    return _tokens(value)


def _merge_argument_tokens(*groups: Any) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for item in _argument_tokens(group):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


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


def _normalize_fuzzy_modify_scope(scope: str) -> str:
    """scope 归一化：profile/person/person_fact → person_profile；memory/general/chat → memory。"""
    token = str(scope or "").strip().lower()
    aliases = {
        "profile": "person_profile",
        "person": "person_profile",
        "person_fact": "person_profile",
        "memory": "memory",
        "general": "memory",
        "chat": "memory",
    }
    return aliases.get(token, token or "person_profile")


def _normalize_fuzzy_modify_relations(value: Any) -> List[Dict[str, Any]]:
    relations: List[Dict[str, Any]] = []
    for row in value or []:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject", "") or "").strip()
        predicate = str(row.get("predicate", "") or "").strip()
        obj = str(row.get("object", "") or "").strip()
        if not (subject and predicate and obj):
            continue
        relations.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "confidence": min(1.0, max(0.0, float(row.get("confidence", 1.0) or 1.0))),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            }
        )
    return relations


def _normalize_fuzzy_modify_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    evidence_type = str(item.get("evidence_type", "") or item.get("type", "") or "").strip()
    target_type = "relation" if evidence_type == "relation" else "paragraph"
    hash_value = str(item.get("hash", "") or "").strip()
    metadata = coerce_metadata_dict(item.get("metadata"))
    return {
        "candidate_id": f"{target_type}:{hash_value}",
        "target_type": target_type,
        "evidence_type": evidence_type,
        "hash": hash_value,
        "content": _trim_text(str(item.get("content", "") or item.get("title", "") or ""), 420),
        "source": str(item.get("source", "") or metadata.get("source", "") or "").strip(),
        "metadata": metadata,
        "score": item.get("score"),
    }


def _normalize_fuzzy_modify_plan(
    payload: Dict[str, Any],
    *,
    request_text: str,
    scope: str,
    person_id: str,
    chat_id: str,
    candidates: Sequence[Dict[str, Any]],
    max_targets: int,
) -> Dict[str, Any]:
    candidate_map = {
        str(item.get("candidate_id", "") or "").strip(): item
        for item in candidates
        if str(item.get("candidate_id", "") or "").strip()
    }
    hash_to_candidate = {
        str(item.get("hash", "") or "").strip(): item
        for item in candidates
        if str(item.get("hash", "") or "").strip()
    }
    confidence = min(1.0, max(0.0, float(payload.get("confidence", 0.0) or 0.0)))
    operations: List[Dict[str, Any]] = []
    for raw in payload.get("operations") or []:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action", "") or raw.get("op", "") or "").strip().lower()
        if action == "mark_superseded":
            candidate = candidate_map.get(str(raw.get("candidate_id", "") or "").strip())
            if candidate is None:
                candidate = hash_to_candidate.get(str(raw.get("hash", "") or "").strip())
            if candidate is None:
                logger.warning(
                    "记忆修正计划引用了候选集外的目标: action=%s candidate_id=%s hash=%s",
                    action,
                    str(raw.get("candidate_id", "") or ""),
                    str(raw.get("hash", "") or ""),
                )
                continue
            operations.append(
                {
                    "action": "mark_superseded",
                    "candidate_id": str(candidate.get("candidate_id", "") or ""),
                    "target_type": str(candidate.get("target_type", "") or ""),
                    "hash": str(candidate.get("hash", "") or ""),
                    "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                    "valid_to": _optional_float(raw.get("valid_to")),
                }
            )
            continue
        if action == "ingest_text":
            text = str(raw.get("text", "") or "").strip()
            if not text:
                continue
            operations.append(
                {
                    "action": "ingest_text",
                    "text": text,
                    "source_type": str(raw.get("source_type", "") or ("person_fact" if person_id else "memory")).strip(),
                    "chat_id": str(raw.get("chat_id", "") or chat_id).strip(),
                    "person_ids": _merge_argument_tokens(raw.get("person_ids"), [person_id]),
                    "participants": _argument_tokens(raw.get("participants")),
                    "tags": _merge_argument_tokens(raw.get("tags"), ["fuzzy_modify"]),
                    "relations": _normalize_fuzzy_modify_relations(raw.get("relations")),
                    "valid_from": _optional_float(raw.get("valid_from")),
                    "reason": str(raw.get("reason", "") or payload.get("reason", "") or request_text).strip(),
                }
            )
            continue
        if action == "refresh_person_profile":
            target_person_id = str(raw.get("person_id", "") or person_id).strip()
            if target_person_id:
                operations.append({"action": "refresh_person_profile", "person_id": target_person_id})
    operations = operations[: max(1, max_targets * 2)]
    target_count = sum(1 for item in operations if item.get("action") == "mark_superseded")
    if target_count > max_targets:
        kept = 0
        limited: List[Dict[str, Any]] = []
        for item in operations:
            if item.get("action") != "mark_superseded":
                limited.append(item)
                continue
            kept += 1
            if kept <= max_targets:
                limited.append(item)
        operations = limited
    if operations and not any(item.get("action") == "refresh_person_profile" for item in operations) and person_id:
        operations.append({"action": "refresh_person_profile", "person_id": person_id})
    return {
        "scope": scope,
        "request_text": request_text,
        "person_id": person_id,
        "chat_id": chat_id,
        "confidence": confidence,
        "risk_level": str(payload.get("risk_level", "medium") or "medium").strip(),
        "reason": str(payload.get("reason", "") or "").strip(),
        "operations": operations,
    }


def _fuzzy_modify_stale_source_operation_id(
    *,
    plan_id: str,
    paragraph_hash: str,
    relation_hash: str,
) -> str:
    return f"{str(plan_id or '').strip()}:{str(paragraph_hash or '').strip()}:{str(relation_hash or '').strip()}"


def _relation_has_remaining_paragraphs(
    metadata_store: Any,
    relation_hash: str,
    removing_hashes: Sequence[str],
) -> bool:
    """relation 是否仍被其它未删除、未过期的段落支撑（除 removing_hashes 外）。"""
    excluded = [str(item or "").strip() for item in removing_hashes if str(item or "").strip()]
    conn = metadata_store.get_connection()
    cursor = conn.cursor()
    if excluded:
        placeholders = ",".join(["?"] * len(excluded))
        cursor.execute(
            f"""
            SELECT p.metadata
            FROM paragraph_relations pr
            JOIN paragraphs p ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash = ?
              AND pr.paragraph_hash NOT IN ({placeholders})
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            """,
            tuple([relation_hash] + excluded),
        )
    else:
        cursor.execute(
            """
            SELECT p.metadata
            FROM paragraph_relations pr
            JOIN paragraphs p ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash = ?
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            """,
            (relation_hash,),
        )
    now = time.time()
    for row in cursor.fetchall():
        metadata = coerce_metadata_dict(row[0] if isinstance(row, tuple) and row else row)
        memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
        valid_to = _optional_float(memory_change.get("valid_to"))
        if valid_to is None or valid_to > now:
            return True
    return False


def _build_fuzzy_modify_paragraph_cascade(
    metadata_store: Any,
    *,
    paragraph_hash: str,
    reason: str,
    preview_only: bool,
    plan_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """计算段落被 superseded 时其关联关系的级联处置动作。"""
    paragraph_token = str(paragraph_hash or "").strip()
    if not paragraph_token:
        return {"relations": [], "entities": []}

    relations: List[Dict[str, Any]] = []
    raw_relations = metadata_store.get_paragraph_relations(paragraph_token)
    relation_hashes = [
        str(item.get("hash", "") or "").strip()
        for item in raw_relations
        if isinstance(item, dict) and str(item.get("hash", "") or "").strip()
    ]
    statuses = metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}
    now = time.time()
    for relation in raw_relations:
        if not isinstance(relation, dict):
            continue
        relation_hash = str(relation.get("hash", "") or "").strip()
        if not relation_hash:
            continue
        status = statuses.get(relation_hash, {})
        protected_until = _optional_float(status.get("protected_until")) or 0.0
        is_pinned = bool(status.get("is_pinned", False))
        protected = is_pinned or protected_until > now
        if protected:
            action = "skipped_protected"
            action_reason = "relation_is_pinned" if is_pinned else "relation_is_temporarily_protected"
        elif _relation_has_remaining_paragraphs(metadata_store, relation_hash, [paragraph_token]):
            action = "mark_stale_evidence"
            action_reason = "relation_has_other_active_paragraphs"
        else:
            action = "mark_inactive"
            action_reason = "only_supported_by_superseded_paragraph"
        relations.append(
            {
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_hash,
                "action": action,
                "reason": action_reason,
                "source_reason": reason,
                "subject": str(relation.get("subject", "") or ""),
                "predicate": str(relation.get("predicate", "") or ""),
                "object": str(relation.get("object", "") or ""),
                "is_pinned": is_pinned,
                "protected_until": protected_until or None,
                "is_inactive": bool(status.get("is_inactive", False)),
                "inactive_since": status.get("inactive_since"),
                "preview_only": preview_only,
                "source_operation_id": (
                    _fuzzy_modify_stale_source_operation_id(
                        plan_id=plan_id,
                        paragraph_hash=paragraph_token,
                        relation_hash=relation_hash,
                    )
                    if plan_id
                    else ""
                ),
            }
        )

    entities: List[Dict[str, Any]] = []
    for entity in metadata_store.get_paragraph_entities(paragraph_token):
        if not isinstance(entity, dict):
            continue
        entity_hash = str(entity.get("hash", "") or "").strip()
        if not entity_hash:
            continue
        entities.append(
            {
                "paragraph_hash": paragraph_token,
                "entity_hash": entity_hash,
                "action": "record_impact_only",
                "reason": "entity_state_has_no_superseded_semantics",
                "name": str(entity.get("name", "") or entity.get("entity", "") or ""),
                "type": str(entity.get("type", "") or entity.get("entity_type", "") or ""),
                "preview_only": preview_only,
            }
        )
    return {"relations": relations, "entities": entities}


def _build_fuzzy_modify_cascade_preview(
    metadata_store: Any,
    *,
    operations: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """预览阶段：汇总所有 mark_superseded 段落的级联影响。"""
    relations: List[Dict[str, Any]] = []
    entities: List[Dict[str, Any]] = []
    seen_relations: set[tuple[str, str]] = set()
    seen_entities: set[tuple[str, str]] = set()
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        if operation.get("action") != "mark_superseded":
            continue
        if str(operation.get("target_type", "") or "").strip() != "paragraph":
            continue
        paragraph_hash = str(operation.get("hash", "") or "").strip()
        if not paragraph_hash:
            continue
        cascade = _build_fuzzy_modify_paragraph_cascade(
            metadata_store,
            paragraph_hash=paragraph_hash,
            reason=str(operation.get("reason", "") or "").strip(),
            preview_only=True,
            plan_id="",
        )
        for item in cascade.get("relations", []):
            if not isinstance(item, dict):
                continue
            relation_hash = str(item.get("relation_hash", "") or "").strip()
            key = (paragraph_hash, relation_hash)
            if not relation_hash or key in seen_relations:
                continue
            seen_relations.add(key)
            relations.append(item)
        for item in cascade.get("entities", []):
            if not isinstance(item, dict):
                continue
            entity_hash = str(item.get("entity_hash", "") or "").strip()
            key = (paragraph_hash, entity_hash)
            if not entity_hash or key in seen_entities:
                continue
            seen_entities.add(key)
            entities.append(item)
    counts = {
        "relations": len(relations),
        "relations_mark_inactive": sum(1 for item in relations if item.get("action") == "mark_inactive"),
        "relations_mark_stale_evidence": sum(1 for item in relations if item.get("action") == "mark_stale_evidence"),
        "relations_skipped_protected": sum(1 for item in relations if item.get("action") == "skipped_protected"),
        "entities": len(entities),
    }
    return {"relations": relations, "entities": entities, "counts": counts}


def _execute_fuzzy_modify_paragraph_cascade(
    metadata_store: Any,
    *,
    paragraph_hash: str,
    plan_id: str,
    changed_at: float,
    reason: str,
) -> Dict[str, Any]:
    """执行阶段：按级联动作写入关系元数据 / inactive 标记 / 段落-关系 stale 证据标记。"""
    paragraph_token = str(paragraph_hash or "").strip()
    plan_token = str(plan_id or "").strip()
    cascade = _build_fuzzy_modify_paragraph_cascade(
        metadata_store,
        paragraph_hash=paragraph_token,
        reason=reason,
        preview_only=False,
        plan_id=plan_token,
    )
    result: Dict[str, Any] = {
        "relations_marked_inactive": [],
        "relations_marked_stale": [],
        "relations_skipped": [],
        "impacted_entities": cascade.get("entities", []),
        "stale_mark_snapshots": [],
    }

    for relation in cascade.get("relations", []):
        if not isinstance(relation, dict):
            continue
        relation_hash = str(relation.get("relation_hash", "") or "").strip()
        if not relation_hash:
            continue
        action = str(relation.get("action", "") or "").strip()
        if action == "skipped_protected":
            result["relations_skipped"].append(relation)
            continue
        if action == "mark_inactive":
            previous = metadata_store.get_relation(relation_hash)
            previous_metadata = coerce_metadata_dict((previous or {}).get("metadata"))
            patch = {
                "memory_change": {
                    "change_id": plan_token,
                    "change_type": "paragraph_cascade_inactive",
                    "changed_at": changed_at,
                    "changed_by": "memory_correction",
                    "reason": reason,
                    "source_paragraph_hash": paragraph_token,
                }
            }
            updated_metadata = metadata_store.update_relation_metadata(relation_hash, patch, merge=True)
            metadata_store.mark_relations_inactive([relation_hash], inactive_since=changed_at)
            result["relations_marked_inactive"].append(
                {
                    **relation,
                    "previous_metadata": previous_metadata,
                    "updated_metadata": updated_metadata if isinstance(updated_metadata, dict) else {},
                    "previous_is_inactive": bool((previous or {}).get("is_inactive", False)),
                    "previous_inactive_since": (previous or {}).get("inactive_since"),
                }
            )
            continue
        if action == "mark_stale_evidence":
            source_operation_id = _fuzzy_modify_stale_source_operation_id(
                plan_id=plan_token,
                paragraph_hash=paragraph_token,
                relation_hash=relation_hash,
            )
            previous_mark = metadata_store.get_paragraph_stale_relation_mark(
                paragraph_hash=paragraph_token,
                relation_hash=relation_hash,
            )
            written = metadata_store.upsert_paragraph_stale_relation_mark(
                paragraph_hash=paragraph_token,
                relation_hash=relation_hash,
                reason=reason or "memory_correction_paragraph_superseded",
                source_type="memory_correction",
                source_id=plan_token,
                source_operation_id=source_operation_id,
            )
            snapshot = {
                "paragraph_hash": paragraph_token,
                "relation_hash": relation_hash,
                "source_type": "memory_correction",
                "source_id": plan_token,
                "source_operation_id": source_operation_id,
                "previous_mark": previous_mark if isinstance(previous_mark, dict) else None,
                "written_mark": written if isinstance(written, dict) else {},
            }
            result["stale_mark_snapshots"].append(snapshot)
            result["relations_marked_stale"].append({**relation, "written_mark": written or {}})
    return result


# ---------------------------------------------------------------------- #
# 服务本体
# ---------------------------------------------------------------------- #

class FuzzyModifyService:
    """记忆模糊修正服务（跨作用域）。

    所有公共方法接收调用方已解析好的 ``AppContext``（admin/router/tools 负责
    按 scope_key 取 runtime），方法内部首先校验 ``enabled`` 配置。
    """

    def __init__(self, runtime_manager: Any, plugin_config: Dict[str, Any]) -> None:
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}

    # ------------------------------------------------------------------ #
    # 配置读取：integration.fuzzy_modify.<key>
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fuzzy_modify_cfg(ctx: Any, key: str, default: Any) -> Any:
        return ctx.get_config(f"integration.fuzzy_modify.{key}", default)

    @classmethod
    def _fuzzy_modify_cfg_enabled(cls, ctx: Any) -> bool:
        return bool(cls._fuzzy_modify_cfg(ctx, "enabled", False))

    @classmethod
    def _fuzzy_modify_cfg_auto_execute_enabled(cls, ctx: Any) -> bool:
        return bool(cls._fuzzy_modify_cfg(ctx, "auto_execute_enabled", False))

    @classmethod
    def _fuzzy_modify_cfg_confirm_threshold(cls, ctx: Any) -> float:
        return float(cls._fuzzy_modify_cfg(ctx, "confirm_threshold", 0.85) or 0.85)

    @classmethod
    def _fuzzy_modify_cfg_candidate_limit(cls, ctx: Any) -> int:
        return max(1, int(cls._fuzzy_modify_cfg(ctx, "candidate_limit", 20) or 20))

    @classmethod
    def _fuzzy_modify_cfg_max_targets(cls, ctx: Any) -> int:
        return max(1, int(cls._fuzzy_modify_cfg(ctx, "max_targets", 10) or 10))

    @classmethod
    def _fuzzy_modify_cfg_allow_global_scope(cls, ctx: Any) -> bool:
        return bool(cls._fuzzy_modify_cfg(ctx, "allow_global_scope", False))

    # ------------------------------------------------------------------ #
    # 落盘
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _save_all(ctx: Any) -> None:
        save_all = getattr(ctx, "save_all", None)
        if save_all is None:
            return
        try:
            await save_all()
        except Exception as exc:
            logger.warning(f"save_all 落盘失败: {exc}")

    # ------------------------------------------------------------------ #
    # preview
    # ------------------------------------------------------------------ #

    async def preview(
        self,
        ctx: Any,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}

        text = str(request_text or "").strip()
        if not text:
            return {"success": False, "error": "修改描述不能为空"}

        scope_token = _normalize_fuzzy_modify_scope(scope)
        pid = str(person_id or "").strip()
        keyword = str(person_keyword or "").strip()
        if scope_token == "person_profile":
            if not pid and keyword:
                person_profile_service = getattr(ctx, "person_profile_service", None)
                if person_profile_service is not None:
                    pid = person_profile_service.resolve_person_id(keyword)
            if not pid:
                return {"success": False, "error": "人物画像修改需要提供 person_id 或 person_keyword"}
        elif not chat_id and not self._fuzzy_modify_cfg_allow_global_scope(ctx):
            return {"success": False, "error": "非人物画像修正需要提供 chat_id，或开启全局记忆修正范围"}

        candidate_limit = min(max(1, int(limit or 20)), self._fuzzy_modify_cfg_candidate_limit(ctx))
        candidates = await self._collect_fuzzy_modify_candidates(
            ctx,
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            limit=candidate_limit,
        )
        if not candidates:
            return {"success": False, "error": "未找到可修改的候选记忆", "candidates": []}

        plan_payload = await self._build_fuzzy_modify_llm_plan(
            ctx,
            request_text=text,
            scope=scope_token,
            person_id=pid,
            person_keyword=keyword,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
        )
        plan = _normalize_fuzzy_modify_plan(
            plan_payload,
            request_text=text,
            scope=scope_token,
            person_id=pid,
            chat_id=str(chat_id or "").strip(),
            candidates=candidates,
            max_targets=self._fuzzy_modify_cfg_max_targets(ctx),
        )
        if not plan.get("operations"):
            return {
                "success": False,
                "error": str(plan.get("reason", "") or "LLM 未生成可执行修改计划"),
                "raw_plan": plan_payload,
                "candidates": candidates,
            }

        confidence = float(plan.get("confidence", 0.0) or 0.0)
        cascade_preview = _build_fuzzy_modify_cascade_preview(
            metadata_store,
            operations=plan.get("operations", []),
        )
        preview_payload = {
            "request_text": text,
            "scope": scope_token,
            "person_id": pid,
            "person_keyword": keyword,
            "chat_id": str(chat_id or "").strip(),
            "candidates": candidates,
            "operations": plan.get("operations", []),
            "cascade_preview": cascade_preview,
            "requires_confirmation": True,
            "confirm_threshold": self._fuzzy_modify_cfg_confirm_threshold(ctx),
            "reason": str(plan.get("reason", "") or ""),
        }
        record = metadata_store.create_fuzzy_modify_plan(
            request_text=text,
            scope=scope_token,
            target_person_id=pid,
            target_chat_id=str(chat_id or "").strip(),
            plan=plan,
            preview=preview_payload,
            status="awaiting_confirmation",
            confidence=confidence,
            requested_by=requested_by,
            reason=reason,
        )
        return {
            "success": True,
            "plan_id": str(record.get("plan_id", "") or ""),
            "plan": record,
            "preview": preview_payload,
            "requires_confirmation": True,
        }

    # ------------------------------------------------------------------ #
    # execute
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        ctx: Any,
        *,
        plan_id: str,
        confirmed: bool = False,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}

        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        status = str(plan_record.get("status", "") or "").strip()
        if status not in {"awaiting_confirmation", "failed", "executing"}:
            return {"success": False, "error": f"当前计划状态不可执行: {status}"}
        if not confirmed:
            confidence = _optional_float(plan_record.get("confidence")) or 0.0
            if (
                not self._fuzzy_modify_cfg_auto_execute_enabled(ctx)
                or confidence < self._fuzzy_modify_cfg_confirm_threshold(ctx)
            ):
                return {
                    "success": False,
                    "error": "需要用户确认后才能执行",
                    "requires_confirmation": True,
                    "plan_id": token,
                }

        previous_execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        attempt_started_at = time.time()
        executing_payload = {
            **previous_execution,
            "attempt": {
                "status": "executing",
                "started_at": attempt_started_at,
                "requested_by": requested_by,
                "reason": reason,
                "recovered_from_stale_executing": status == "executing",
            },
        }
        metadata_store.update_fuzzy_modify_plan(token, status="executing", execution=executing_payload)
        try:
            execution = await self._apply_fuzzy_modify_plan(
                ctx,
                plan_record=plan_record,
                requested_by=requested_by,
                reason=reason,
            )
            execution = {
                **execution,
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "finished",
                    "finished_at": time.time(),
                },
            }
            updated = metadata_store.update_fuzzy_modify_plan(
                token,
                status="executed" if bool(execution.get("success")) else "failed",
                execution=execution,
                executed_at=time.time() if bool(execution.get("success")) else None,
                reason=reason if reason else None,
            )
            return {"success": bool(execution.get("success")), "plan": updated, "execution": execution}
        except Exception as exc:
            logger.warning(f"记忆修正执行失败: {exc}", exc_info=True)
            execution = {
                **executing_payload,
                "success": False,
                "error": str(exc),
                "attempt": {
                    **executing_payload["attempt"],
                    "status": "failed",
                    "finished_at": time.time(),
                },
            }
            updated = metadata_store.update_fuzzy_modify_plan(
                token,
                status="failed",
                execution=execution,
                reason=reason if reason else None,
            )
            return {"success": False, "plan": updated, "execution": execution, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # get / list
    # ------------------------------------------------------------------ #

    async def get(self, ctx: Any, *, plan_id: str) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}
        record = metadata_store.get_fuzzy_modify_plan(str(plan_id or "").strip())
        if record is None:
            return {"success": False, "error": "修改计划不存在"}
        return {"success": True, "plan": record}

    async def list(self, ctx: Any, *, scope: str = "", status: str = "", limit: int = 50) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}
        statuses: Optional[List[str]] = None
        status_token = str(status or "").strip()
        if status_token:
            statuses = [status_token]
        items = metadata_store.list_fuzzy_modify_plans(
            limit=max(1, int(limit or 50)),
            statuses=statuses,
            scope=str(scope or "").strip(),
        )
        return {"success": True, "items": items, "count": len(items)}

    # ------------------------------------------------------------------ #
    # rollback
    # ------------------------------------------------------------------ #

    async def rollback(
        self,
        ctx: Any,
        *,
        plan_id: str,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}

        token = str(plan_id or "").strip()
        if not token:
            return {"success": False, "error": "plan_id 不能为空"}
        plan_record = metadata_store.get_fuzzy_modify_plan(token)
        if plan_record is None:
            return {"success": False, "error": "修改计划不存在"}
        if str(plan_record.get("status", "") or "") != "executed":
            return {"success": False, "error": "只有已执行的修改计划可以回滚"}

        execution = plan_record.get("execution") if isinstance(plan_record.get("execution"), dict) else {}
        stored_ids = _tokens(execution.get("stored_ids"))
        paragraph_hashes = [h for h in stored_ids if metadata_store.get_paragraph(h)]
        relation_hashes = [h for h in stored_ids if metadata_store.get_relation(h)]
        rollback_items: List[Dict[str, Any]] = []

        # 1. 软删替代段落
        if paragraph_hashes:
            delete_result = metadata_store.soft_delete_paragraphs(paragraph_hashes)
            rollback_items.append({"type": "delete_new_paragraphs", "result": delete_result})
            if not bool(delete_result.get("deleted_hashes")) and paragraph_hashes:
                # 软删未命中任何段落也视为成功（可能已被其它路径删除），仅当显式失败时回退
                pass

        # 2. 恢复被 superseded 的旧目标元数据 + 撤销级联标记
        restored_targets: List[Dict[str, Any]] = []
        restore_failures: List[Dict[str, str]] = []
        stale_marks_deleted: List[Dict[str, Any]] = []
        stale_marks_restored: List[Dict[str, Any]] = []
        stale_marks_skipped: List[Dict[str, Any]] = []
        for item in execution.get("superseded_targets") or []:
            if not isinstance(item, dict):
                continue
            target_type = str(item.get("target_type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            previous_metadata = item.get("previous_metadata") if isinstance(item.get("previous_metadata"), dict) else {}
            if target_type == "paragraph" and hash_value:
                cascade = item.get("cascade") if isinstance(item.get("cascade"), dict) else {}
                for relation_item in cascade.get("relations_marked_inactive") or []:
                    if not isinstance(relation_item, dict):
                        continue
                    relation_hash = str(relation_item.get("relation_hash", "") or "").strip()
                    if not relation_hash:
                        continue
                    previous_relation_metadata = (
                        relation_item.get("previous_metadata")
                        if isinstance(relation_item.get("previous_metadata"), dict)
                        else {}
                    )
                    updated_relation = metadata_store.update_relation_metadata(
                        relation_hash,
                        previous_relation_metadata,
                        merge=False,
                    )
                    if updated_relation is None:
                        restore_failures.append(
                            {"target_type": "relation", "hash": relation_hash, "error": "级联关系不存在"}
                        )
                        continue
                    if bool(relation_item.get("previous_is_inactive", False)):
                        metadata_store.mark_relations_inactive(
                            [relation_hash],
                            inactive_since=_optional_float(relation_item.get("previous_inactive_since")),
                        )
                    else:
                        metadata_store.mark_relations_active([relation_hash])
                    restored_targets.append(
                        {"target_type": "relation", "hash": relation_hash, "cascade_from": hash_value}
                    )

                for snapshot in cascade.get("stale_mark_snapshots") or []:
                    if not isinstance(snapshot, dict):
                        continue
                    paragraph_hash = str(snapshot.get("paragraph_hash", "") or hash_value).strip()
                    relation_hash = str(snapshot.get("relation_hash", "") or "").strip()
                    if not paragraph_hash or not relation_hash:
                        continue
                    rollback_mark = metadata_store.rollback_paragraph_stale_relation_mark(
                        paragraph_hash=paragraph_hash,
                        relation_hash=relation_hash,
                        expected_source_type=str(snapshot.get("source_type", "") or "memory_correction"),
                        expected_source_id=str(snapshot.get("source_id", "") or token),
                        expected_source_operation_id=str(snapshot.get("source_operation_id", "") or ""),
                        previous_mark=(
                            snapshot.get("previous_mark")
                            if isinstance(snapshot.get("previous_mark"), dict)
                            else None
                        ),
                    )
                    action = str(rollback_mark.get("action", "") or "").strip()
                    if action == "deleted":
                        stale_marks_deleted.append(rollback_mark)
                    elif action == "restored":
                        stale_marks_restored.append(rollback_mark)
                    elif action in {"skipped_due_to_source_mismatch", "restore_failed", "invalid_target"}:
                        stale_marks_skipped.append(rollback_mark)
                        if action in {"restore_failed", "invalid_target"}:
                            restore_failures.append(
                                {
                                    "target_type": "stale_mark",
                                    "hash": f"{paragraph_hash}:{relation_hash}",
                                    "error": action,
                                }
                            )
                    else:
                        stale_marks_skipped.append(rollback_mark)

                updated = metadata_store.update_paragraph_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append({"target_type": target_type, "hash": hash_value, "error": "目标段落不存在或已删除"})
                continue
            if target_type == "relation" and hash_value:
                updated = metadata_store.update_relation_metadata(hash_value, previous_metadata, merge=False)
                if updated is not None:
                    if bool(item.get("previous_is_inactive", False)):
                        metadata_store.mark_relations_inactive(
                            [hash_value],
                            inactive_since=_optional_float(item.get("previous_inactive_since")),
                        )
                    else:
                        metadata_store.mark_relations_active([hash_value])
                    restored_targets.append({"target_type": target_type, "hash": hash_value})
                else:
                    restore_failures.append({"target_type": target_type, "hash": hash_value, "error": "目标关系不存在"})

        # 3. 停用替代关系
        if relation_hashes:
            metadata_store.mark_relations_inactive(relation_hashes, inactive_since=time.time())

        if restored_targets or paragraph_hashes or relation_hashes:
            await self._save_all(ctx)

        rollback_success = not restore_failures
        rollback_result = {
            "success": rollback_success,
            "stored_ids_deleted": paragraph_hashes,
            "new_relations_deactivated": relation_hashes,
            "restored_targets": restored_targets,
            "restore_failures": restore_failures,
            "stale_marks_deleted": stale_marks_deleted,
            "stale_marks_restored": stale_marks_restored,
            "stale_marks_skipped": stale_marks_skipped,
            "items": rollback_items,
            "requested_by": requested_by,
            "reason": reason,
        }
        updated = metadata_store.update_fuzzy_modify_plan(
            token,
            status="rolled_back" if rollback_success else "rollback_failed",
            execution={**execution, "rollback": rollback_result},
            reason=reason if reason else None,
        )
        return {"success": rollback_success, "plan": updated, "rollback": rollback_result}

    # ------------------------------------------------------------------ #
    # reconcile：扫 awaiting_confirmation 过期计划
    # ------------------------------------------------------------------ #

    async def reconcile(self, ctx: Any) -> Dict[str, Any]:
        if not self._fuzzy_modify_cfg_enabled(ctx):
            return {"success": False, "error": "fuzzy_modify_disabled"}
        metadata_store = ctx.metadata_store
        if metadata_store is None:
            return {"success": False, "error": "metadata_store_unavailable"}
        # 仅标记 awaiting_confirmation 中明显异常的 executing 残留为 failed；
        # 不强制过期 awaiting_confirmation（人工确认无时效）。
        executing_plans = metadata_store.list_fuzzy_modify_plans(
            limit=50,
            statuses=["executing"],
        )
        marked: List[str] = []
        now = time.time()
        for record in executing_plans:
            execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
            attempt = execution.get("attempt") if isinstance(execution.get("attempt"), dict) else {}
            started_at = _optional_float(attempt.get("started_at")) or 0.0
            if started_at and (now - started_at) > 3600.0:
                metadata_store.update_fuzzy_modify_plan(
                    str(record.get("plan_id", "") or ""),
                    status="failed",
                    reason="reconcile_stale_executing",
                )
                marked.append(str(record.get("plan_id", "") or ""))
        return {
            "success": True,
            "stale_executing_marked": marked,
            "stale_executing_count": len(marked),
        }

    # ------------------------------------------------------------------ #
    # 候选收集 + LLM 计划
    # ------------------------------------------------------------------ #

    async def _collect_fuzzy_modify_candidates(
        self,
        ctx: Any,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        metadata_store = ctx.metadata_store
        candidates: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def append_candidate(item: Dict[str, Any]) -> None:
            candidate = _normalize_fuzzy_modify_candidate(item)
            candidate_type = str(candidate.get("target_type", "") or "").strip()
            hash_value = str(candidate.get("hash", "") or "").strip()
            key = (candidate_type, hash_value)
            if not candidate_type or not hash_value or key in seen:
                return
            if not self._is_fuzzy_modify_candidate_mutable(metadata_store, candidate, item):
                return
            seen.add(key)
            candidates.append(candidate)

        if scope == "person_profile":
            evidence_items = await self._collect_person_profile_evidence(
                ctx,
                person_id=person_id,
                person_keyword=person_keyword,
                limit=max(limit, 12),
            )
            for item in evidence_items:
                if isinstance(item, dict):
                    append_candidate(item)

        search_items = await self._collect_memory_search_hits(
            ctx,
            query=request_text,
            chat_id=chat_id,
            person_id=person_id,
            limit=limit,
        )
        for item in search_items:
            if isinstance(item, dict):
                append_candidate(item)
        return candidates[:limit]

    @staticmethod
    def _is_fuzzy_modify_candidate_mutable(
        metadata_store: Any,
        candidate: Dict[str, Any],
        raw_item: Dict[str, Any],
    ) -> bool:
        if raw_item.get("deletable") is False:
            return False
        target_type = str(candidate.get("target_type", "") or "").strip()
        hash_value = str(candidate.get("hash", "") or "").strip()
        if not target_type or not hash_value:
            return False
        if target_type == "paragraph":
            paragraph = metadata_store.get_paragraph(hash_value)
            return isinstance(paragraph, dict) and not bool(paragraph.get("is_deleted", 0))
        if target_type == "relation":
            relation = metadata_store.get_relation(hash_value, include_inactive=False)
            if relation is None:
                return False
            status = metadata_store.get_relation_status_batch([hash_value]).get(hash_value, {})
            if bool(status.get("is_inactive", False)) or bool(status.get("is_pinned", False)):
                return False
            protected_until = _optional_float(status.get("protected_until")) or 0.0
            return protected_until <= time.time()
        return False

    async def _collect_person_profile_evidence(
        self,
        ctx: Any,
        *,
        person_id: str,
        person_keyword: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        person_profile_service = getattr(ctx, "person_profile_service", None)
        if person_profile_service is None:
            return []
        try:
            profile = await person_profile_service.query_person_profile(
                person_id=str(person_id or "").strip(),
                person_keyword=str(person_keyword or "").strip(),
                top_k=max(4, int(limit)),
                ttl_seconds=60.0,
                force_refresh=False,
                source_note="fuzzy_modify.candidate_collect",
            )
        except Exception as exc:
            logger.warning(f"人物画像证据收集失败: {exc}")
            return []
        if not isinstance(profile, dict) or not bool(profile.get("success")):
            return []
        items: List[Dict[str, Any]] = []
        for key in ("relation_edges", "vector_evidence"):
            for item in profile.get(key) or []:
                if isinstance(item, dict):
                    items.append(item)
        return items

    async def _collect_memory_search_hits(
        self,
        ctx: Any,
        *,
        query: str,
        chat_id: str,
        person_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        # 延迟导入避免循环依赖。
        from .query_service import QueryService

        try:
            payload = await QueryService(ctx).aggregate(
                query=str(query or "").strip(),
                top_k=max(1, int(limit)),
                stream_id=str(chat_id or "").strip() or None,
                person=str(person_id or "").strip() or None,
                enforce_chat_filter=False,
            )
        except Exception as exc:
            logger.warning(f"记忆检索候选失败: {exc}")
            return []
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []
        hits: List[Dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            hash_value = str(item.get("hash", "") or "").strip()
            if not hash_value:
                continue
            hits.append(
                {
                    "hash": hash_value,
                    "type": str(item.get("type", "") or "").strip(),
                    "content": str(item.get("content", "") or "").strip(),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                    "score": item.get("score"),
                }
            )
        return hits

    async def _build_fuzzy_modify_llm_plan(
        self,
        ctx: Any,
        *,
        request_text: str,
        scope: str,
        person_id: str = "",
        person_keyword: str = "",
        chat_id: str = "",
        candidates: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {
            "request_text": request_text,
            "scope": scope,
            "person_id": person_id,
            "person_keyword": person_keyword,
            "chat_id": chat_id,
            "max_targets": self._fuzzy_modify_cfg_max_targets(ctx),
            "candidates": [
                {
                    "candidate_id": str(item.get("candidate_id", "") or ""),
                    "target_type": str(item.get("target_type", "") or ""),
                    "evidence_type": str(item.get("evidence_type", "") or ""),
                    "hash": str(item.get("hash", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "source": str(item.get("source", "") or ""),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
                for item in candidates
            ],
        }
        prompt = (
            "你是长期记忆模糊修正规划器。"
            "根据用户的修改意图与候选记忆列表，生成结构化修改计划。"
            "请严格输出 JSON 对象，不要输出解释文字。\n\n"
            f"request_payload: {json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "输出 JSON schema:\n"
            "{"
            "\"confidence\":0.0,"
            "\"risk_level\":\"low|medium|high\","
            "\"reason\":\"\","
            "\"operations\":["
            "{\"action\":\"mark_superseded\",\"candidate_id\":\"\",\"reason\":\"\",\"valid_to\":null}"
            "{\"action\":\"ingest_text\",\"text\":\"\",\"source_type\":\"\",\"chat_id\":\"\","
            "\"person_ids\":[],\"participants\":[],\"tags\":[],"
            "\"relations\":[{\"subject\":\"\",\"predicate\":\"\",\"object\":\"\",\"confidence\":1.0}],"
            "\"valid_from\":null,\"reason\":\"\"}"
            "{\"action\":\"refresh_person_profile\",\"person_id\":\"\"}"
            "]"
            "}\n"
            "约束:\n"
            "1. mark_superseded 的 candidate_id 必须来自候选列表。\n"
            "2. 仅当确有替代内容时才输出 ingest_text。\n"
            "3. 不确定时输出空 operations 并把 confidence 设为较低值。\n"
        )
        try:
            result = await generate_text(
                ctx,
                prompt,
                request_type="A_Memorix.FuzzyModify",
            )
            return _safe_json_loads(result.text) if result.success else {}
        except Exception as exc:
            logger.warning(f"记忆修正 LLM 规划失败: {exc}")
            return {}

    # ------------------------------------------------------------------ #
    # 应用计划
    # ------------------------------------------------------------------ #

    async def _apply_fuzzy_modify_plan(
        self,
        ctx: Any,
        *,
        plan_record: Dict[str, Any],
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        metadata_store = ctx.metadata_store
        plan = plan_record.get("plan") if isinstance(plan_record.get("plan"), dict) else {}
        operations = [dict(item) for item in plan.get("operations") or [] if isinstance(item, dict)]
        change_id = str(plan_record.get("plan_id", "") or f"fuzzy_{int(time.time())}")
        changed_at = time.time()
        stored_ids: List[str] = []
        ingest_results: List[Dict[str, Any]] = []
        superseded_targets: List[Dict[str, Any]] = []

        supersede_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in operations
            if item.get("action") == "mark_superseded" and str(item.get("hash", "") or "").strip()
        ]
        for index, operation in enumerate(
            [item for item in operations if item.get("action") == "ingest_text"],
            start=1,
        ):
            op_reason = str(
                operation.get("reason", "") or reason or plan.get("request_text", "") or ""
            ).strip()
            metadata = {
                "memory_change": {
                    "change_id": change_id,
                    "change_type": "ingest_text",
                    "changed_at": changed_at,
                    "changed_by": requested_by,
                    "reason": op_reason,
                    "supersedes_hashes": supersede_hashes,
                    "valid_from": operation.get("valid_from") or changed_at,
                },
                "source_request": str(
                    plan.get("request_text", "") or plan_record.get("request_text", "") or ""
                ),
            }
            result = await self._ingest_replacement(
                ctx,
                external_id=f"{change_id}:ingest:{index}",
                source_type=str(operation.get("source_type", "") or "memory"),
                text=str(operation.get("text", "") or ""),
                chat_id=str(operation.get("chat_id", "") or plan.get("chat_id", "") or ""),
                person_ids=_argument_tokens(operation.get("person_ids")),
                participants=_argument_tokens(operation.get("participants")),
                timestamp=_optional_float(operation.get("valid_from")) or changed_at,
                tags=_argument_tokens(operation.get("tags")),
                metadata=metadata,
                relations=operation.get("relations") if isinstance(operation.get("relations"), list) else [],
            )
            result_ids = _tokens(result.get("stored_ids"))
            stored_ids.extend(result_ids)
            ingest_results.append({"operation": operation, "result": result})

        replacement_hashes = list(stored_ids)
        for operation in [item for item in operations if item.get("action") == "mark_superseded"]:
            marked = self._mark_fuzzy_modify_target_superseded(
                metadata_store,
                operation=operation,
                change_id=change_id,
                changed_at=changed_at,
                changed_by=requested_by,
                replacement_hashes=replacement_hashes,
                plan_id=change_id,
                default_reason=reason or str(plan.get("request_text", "") or ""),
            )
            if marked:
                superseded_targets.append(marked)

        refreshed_profiles: List[Dict[str, Any]] = []
        for operation in [item for item in operations if item.get("action") == "refresh_person_profile"]:
            person_id = str(operation.get("person_id", "") or "").strip()
            if not person_id:
                continue
            refreshed_profiles.append(await self._refresh_person_profile(ctx, person_id))

        if superseded_targets:
            await self._save_all(ctx)

        return {
            "success": bool(stored_ids or superseded_targets or refreshed_profiles),
            "stored_ids": stored_ids,
            "ingest_results": ingest_results,
            "superseded_targets": superseded_targets,
            "refreshed_profiles": refreshed_profiles,
            "changed_at": changed_at,
            "changed_by": requested_by,
            "reason": reason,
        }

    @staticmethod
    def _mark_fuzzy_modify_target_superseded(
        metadata_store: Any,
        *,
        operation: Dict[str, Any],
        change_id: str,
        changed_at: float,
        changed_by: str,
        replacement_hashes: Sequence[str],
        plan_id: str,
        default_reason: str = "",
    ) -> Dict[str, Any]:
        target_type = str(operation.get("target_type", "") or "").strip()
        hash_value = str(operation.get("hash", "") or "").strip()
        if target_type not in {"paragraph", "relation"} or not hash_value:
            return {}
        valid_to = _optional_float(operation.get("valid_to")) or changed_at
        reason = str(operation.get("reason", "") or default_reason or "").strip()
        patch = {
            "memory_change": {
                "change_id": change_id,
                "change_type": "mark_superseded",
                "changed_at": changed_at,
                "changed_by": changed_by,
                "reason": reason,
                "valid_to": valid_to,
                "superseded_by_hashes": [
                    str(item or "").strip() for item in replacement_hashes if str(item or "").strip()
                ],
            }
        }
        if target_type == "paragraph":
            previous = metadata_store.get_paragraph(hash_value)
            if previous is None:
                return {}
            previous_metadata = coerce_metadata_dict(previous.get("metadata"))
            updated = metadata_store.update_paragraph_metadata(hash_value, patch, merge=True)
            if updated is None:
                return {}
            cascade = _execute_fuzzy_modify_paragraph_cascade(
                metadata_store,
                paragraph_hash=hash_value,
                plan_id=plan_id,
                changed_at=changed_at,
                reason=reason,
            )
            return {
                "target_type": target_type,
                "hash": hash_value,
                "previous_metadata": previous_metadata,
                "updated_metadata": updated,
                "cascade": cascade,
            }
        previous = metadata_store.get_relation(hash_value)
        if previous is None:
            return {}
        previous_metadata = coerce_metadata_dict(previous.get("metadata"))
        updated = metadata_store.update_relation_metadata(hash_value, patch, merge=True)
        if updated is None:
            return {}
        metadata_store.mark_relations_inactive([hash_value], inactive_since=valid_to)
        return {
            "target_type": target_type,
            "hash": hash_value,
            "previous_metadata": previous_metadata,
            "updated_metadata": updated,
            "previous_is_inactive": bool(previous.get("is_inactive", False)),
            "previous_inactive_since": previous.get("inactive_since"),
        }

    async def _ingest_replacement(
        self,
        ctx: Any,
        *,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str,
        person_ids: Sequence[str],
        participants: Sequence[str],
        timestamp: float,
        tags: Sequence[str],
        metadata: Dict[str, Any],
        relations: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """摄入替代内容到当前 scope 的 ctx（等价 IngestService.ingest_text 的核心路径）。"""
        metadata_store = ctx.metadata_store
        content = normalize_text(str(text or ""))
        source_kind = str(source_type or "memory").strip() or "memory"
        stream_id = str(chat_id or "").strip()
        external_token = str(external_id or "").strip() or compute_hash(f"{source_kind}:{stream_id}:{content}")
        if not content:
            return {"success": True, "stored_ids": [], "skipped_ids": [external_token], "reason": "empty_text"}

        person_tokens = _tokens(person_ids)
        participant_tokens = _tokens(participants)
        tag_tokens = _tokens(tags)
        entity_tokens = list(dict.fromkeys([*person_tokens, *participant_tokens]))
        relation_rows = [dict(item) for item in (relations or []) if isinstance(item, dict)]
        source = f"{source_kind}:{stream_id}" if stream_id else source_kind
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
        paragraph_meta = {k: v for k, v in paragraph_meta.items() if v not in (None, "", [])}
        time_meta = {"event_time": float(timestamp)} if timestamp else {}

        paragraph_hash = metadata_store.add_paragraph(
            content=content,
            source=source,
            metadata=paragraph_meta,
            knowledge_type=detect_knowledge_type(content).value,
            time_meta=time_meta or None,
        )

        warnings: List[str] = []
        # 段落向量：优先用 paragraph_vector_service，否则直接写 vector_store。
        paragraph_vector_service = getattr(ctx, "paragraph_vector_service", None)
        if paragraph_vector_service is not None and hasattr(paragraph_vector_service, "ensure_paragraph_vector"):
            try:
                vector_result = await paragraph_vector_service.ensure_paragraph_vector(paragraph_hash, content)
                vector_written = bool(vector_result.vector_written or vector_result.vector_already_exists)
                if not vector_written and vector_result.vector_state == "failed":
                    warnings.append(f"vector_write_failed: {vector_result.error}")
            except Exception as exc:
                vector_written = False
                warnings.append(f"vector_write_failed: {exc}")
        else:
            try:
                if paragraph_hash not in ctx.vector_store:
                    embedding = await ctx.embedding_manager.encode(content)
                    ctx.vector_store.add(vectors=embedding.reshape(1, -1), ids=[paragraph_hash])
                vector_written = True
            except Exception as exc:
                vector_written = False
                warnings.append(f"vector_write_failed: {exc}")

        for name in entity_tokens:
            try:
                metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)
            except Exception as exc:
                warnings.append(f"entity_write_failed: {exc}")

        relation_hashes: List[str] = []
        write_relation_vectors = bool(ctx.get_config("retrieval.relation_vectorization.enabled", True))
        relation_service = getattr(ctx, "relation_write_service", None)
        if relation_service is not None:
            for row in relation_rows:
                subject = str(row.get("subject", "") or "").strip()
                predicate = str(row.get("predicate", "") or "").strip()
                obj = str(row.get("object", "") or "").strip()
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
                    metadata_store.link_paragraph_relation(paragraph_hash, result.hash_value)
                    relation_hashes.append(result.hash_value)
                except Exception as exc:
                    warnings.append(f"relation_write_failed: {exc}")

        try:
            metadata_store.enqueue_episode_pending(paragraph_hash, source=source)
        except Exception as exc:
            warnings.append(f"episode_enqueue_failed: {exc}")

        return {
            "success": True,
            "stored_ids": [paragraph_hash, *relation_hashes],
            "skipped_ids": [],
            "source": source,
            "vector_written": vector_written,
            "warnings": warnings,
        }

    async def _refresh_person_profile(self, ctx: Any, person_id: str) -> Dict[str, Any]:
        person_profile_service = getattr(ctx, "person_profile_service", None)
        if person_profile_service is None:
            return {"success": False, "error": "person_profile_service_unavailable", "person_id": person_id}
        try:
            profile = await person_profile_service.query_person_profile(
                person_id=str(person_id or "").strip(),
                top_k=12,
                ttl_seconds=60.0,
                force_refresh=True,
                source_note="fuzzy_modify.refresh_person_profile",
            )
            if isinstance(profile, dict):
                profile.setdefault("person_id", person_id)
                return profile
        except Exception as exc:
            logger.warning(f"人物画像刷新失败: person_id={person_id} err={exc}")
        return {"success": False, "error": "person_profile_refresh_failed", "person_id": person_id}
