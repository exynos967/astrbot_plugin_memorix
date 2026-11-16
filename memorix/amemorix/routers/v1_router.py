"""Operation APIs (/v1/*) for the embedded WebUI."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...core.utils.runtime_self_check import ensure_runtime_self_check
from ..services import (
    DeleteService,
    MemoryService,
    PersonProfileApiService,
    QueryService,
)

router = APIRouter(prefix="/v1", tags=["v1"])


class StructuredAdminRequest(BaseModel):
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.post("/memory/delete-admin")
async def delete_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in body.payload.items() if key not in {"scope_key", "action"}}
    try:
        return await _admin_service(request).delete_admin(scope_key=_scope_key(request), action=body.action, **payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/facts")
async def fact_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in body.payload.items() if key not in {"scope_key", "action"}}
    return await _admin_service(request).fact_admin(scope_key=_scope_key(request), action=body.action, **payload)


@router.post("/person/aliases")
async def person_alias_admin(request: Request, body: StructuredAdminRequest):
    if body.action not in {"get_aliases", "set_aliases", "delete_aliases"}:
        raise HTTPException(status_code=400, detail="不支持的人物别名操作")
    payload = {key: value for key, value in body.payload.items() if key not in {"scope_key", "action"}}
    return await _admin_service(request).profile_admin(scope_key=_scope_key(request), action=body.action, **payload)

QUERY_EVENT_RETENTION_SECONDS = 2 * 60 * 60
QUERY_TREND_BUCKET_SECONDS = 5 * 60


class ImportTaskCreateRequest(BaseModel):
    mode: str = Field(default="text")
    payload: Any
    options: Dict[str, Any] = Field(default_factory=dict)


class UploadTicketCreateRequest(BaseModel):
    kind: str
    options: Dict[str, Any] = Field(default_factory=dict)


class SummaryTaskCreateRequest(BaseModel):
    session_id: Optional[str] = None
    source: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    context_length: int = 50


class QuerySearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None


class QueryAutoRequest(BaseModel):
    query: str
    top_k: int = 5
    stream_id: Optional[str] = None
    group_id: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = None
    strict_source: bool = False


class QueryTimeRequest(BaseModel):
    query: str = ""
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    person: Optional[str] = None
    source: Optional[str] = None
    top_k: Optional[int] = None


class QueryEntityRequest(BaseModel):
    entity_name: str


class QueryRelationRequest(BaseModel):
    subject: str = ""
    predicate: str = ""
    object: str = ""


class QueryEpisodeRequest(BaseModel):
    query: str = ""
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    person: Optional[str] = None
    source: Optional[str] = None
    top_k: Optional[int] = None
    include_paragraphs: bool = False


class QueryAggregateRequest(BaseModel):
    query: str = ""
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    person: Optional[str] = None
    source: Optional[str] = None
    top_k: Optional[int] = None
    mix: bool = True
    mix_top_k: Optional[int] = None


class EpisodeRebuildRequest(BaseModel):
    source: str


class DeleteParagraphRequest(BaseModel):
    paragraph_hash: str


class DeleteEntityRequest(BaseModel):
    entity_name: str


class DeleteRelationRequest(BaseModel):
    relation: str


class MemoryStatusRequest(BaseModel):
    pass


class MemoryProtectRequest(BaseModel):
    id: str
    hours: float = 24.0


class MemoryReinforceRequest(BaseModel):
    id: str


class MemoryFreezeRequest(BaseModel):
    id: str


class MemoryRestoreRequest(BaseModel):
    hash: str
    type: str = "relation"


class PersonQueryRequest(BaseModel):
    person_id: str = ""
    person_keyword: str = ""
    top_k: int = 12
    force_refresh: bool = False


class PersonOverrideRequest(BaseModel):
    person_id: str
    override_text: str
    updated_by: str = "v1"


class PersonOverrideDeleteRequest(BaseModel):
    person_id: str


class PersonRegistryUpsertRequest(BaseModel):
    person_id: str
    person_name: str = ""
    nickname: str = ""
    user_id: str = ""
    platform: str = ""
    group_nick_name: Any = None
    memory_points: Any = None
    last_know: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TuningAdminRequest(BaseModel):
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class FeedbackAdminRequest(BaseModel):
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class FuzzyModifyAdminRequest(BaseModel):
    # 镜像 FeedbackAdminRequest：action 决定调度分支，可变参数走 payload 透传
    # payload 支持 request_text / plan_id / person_id / person_keyword / chat_id /
    # limit / confirmed / requested_by / reason / status 等字段
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class ChatMigrationPreviewRequest(BaseModel):
    source_chat_id: str
    target_chat_id: str
    migrate_transcript: bool = True
    note: str = ""
    requested_by: str = "webui"


class ChatMigrationExecuteRequest(BaseModel):
    confirmed: bool = False
    confirm_overwrite: bool = False
    refresh_fingerprint: bool = False


def _ctx(request: Request):
    return request.app.state.context


def _task_manager(request: Request):
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Task manager not initialized")
    return manager


def _task_or_404(task):
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _admin_service(request: Request):
    service = getattr(request.app.state, "admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="admin service not initialized")
    return service


def _scope_key(request: Request) -> str:
    return str(getattr(request.app.state, "scope_key", "") or "")


def _record_query_event(request: Request, query_type: str) -> None:
    now = time.time()
    events = list(getattr(request.app.state, "query_events", []) or [])
    cutoff = now - QUERY_EVENT_RETENTION_SECONDS
    events = [item for item in events if float(item.get("ts", 0.0) or 0.0) >= cutoff]
    events.append({"ts": now, "type": str(query_type or "query")})
    request.app.state.query_events = events


def _query_events(request: Request, *, now: Optional[float] = None) -> list[dict[str, Any]]:
    current = time.time() if now is None else float(now)
    cutoff = current - QUERY_EVENT_RETENTION_SECONDS
    events = [
        item
        for item in list(getattr(request.app.state, "query_events", []) or [])
        if float(item.get("ts", 0.0) or 0.0) >= cutoff
    ]
    request.app.state.query_events = events
    return events


def _recent_query_counts(request: Request, *, seconds: float = 60.0) -> Dict[str, int]:
    now = time.time()
    cutoff = now - max(1.0, float(seconds))
    counts: Dict[str, int] = {"total": 0}
    events = _query_events(request, now=now)
    for item in events:
        ts = float(item.get("ts", 0.0) or 0.0)
        if ts < cutoff:
            continue
        kind = str(item.get("type") or "query")
        counts[kind] = counts.get(kind, 0) + 1
        counts["total"] += 1
    return counts


def _query_event_histogram(
    request: Request,
    *,
    seconds: float = QUERY_EVENT_RETENTION_SECONDS,
    bucket_seconds: float = QUERY_TREND_BUCKET_SECONDS,
) -> Dict[str, Any]:
    now = time.time()
    span = max(float(bucket_seconds), float(seconds))
    bucket = max(60.0, float(bucket_seconds))
    bucket_count = max(1, int((span + bucket - 1) // bucket))
    start = now - bucket_count * bucket
    buckets = [
        {
            "start": start + index * bucket,
            "end": start + (index + 1) * bucket,
            "total": 0,
            "types": {},
        }
        for index in range(bucket_count)
    ]
    for item in _query_events(request, now=now):
        ts = float(item.get("ts", 0.0) or 0.0)
        if ts < start:
            continue
        index = min(bucket_count - 1, max(0, int((ts - start) // bucket)))
        kind = str(item.get("type") or "query")
        types = buckets[index]["types"]
        types[kind] = int(types.get(kind, 0) or 0) + 1
        buckets[index]["total"] += 1
    return {
        "seconds": int(bucket_count * bucket),
        "bucket_seconds": int(bucket),
        "total": sum(int(item["total"] or 0) for item in buckets),
        "buckets": buckets,
    }


def _status_from_async_summary(summary: Dict[str, Any]) -> str:
    counts = summary.get("counts") or {}
    latest = summary.get("latest") or {}
    if int(counts.get("running", 0) or 0) > 0:
        return "running"
    if int(counts.get("queued", 0) or 0) > 0:
        return "waiting"
    latest_status = str(latest.get("status") or "").strip().lower()
    if latest_status == "cancelled":
        latest_status = "canceled"
    if latest_status in {"failed", "canceled"}:
        return latest_status
    return "ready"


def _merge_native_import_summary(base_summary: Dict[str, Any], import_task_manager: Any) -> Dict[str, Any]:
    if import_task_manager is None:
        return base_summary
    native_tasks = getattr(import_task_manager, "_tasks", None)
    if not isinstance(native_tasks, dict) or not native_tasks:
        return base_summary

    raw_counts = dict(base_summary.get("counts") or {})
    counts: Dict[str, int] = {
        key: int(raw_counts.get(key, 0) or 0)
        for key in ("queued", "running", "succeeded", "failed", "canceled", "total", *raw_counts.keys())
    }
    latest = base_summary.get("latest") if isinstance(base_summary.get("latest"), dict) else None
    latest_ts = float((latest or {}).get("updated_at") or (latest or {}).get("created_at") or 0.0)

    status_map = {
        "queued": "queued",
        "preparing": "queued",
        "running": "running",
        "cancel_requested": "running",
        "completed": "succeeded",
        "completed_with_errors": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "canceled",
        "canceled": "canceled",
    }

    for record in list(native_tasks.values()):
        to_summary = getattr(record, "to_summary", None)
        item = to_summary() if callable(to_summary) else (dict(record) if isinstance(record, dict) else None)
        if not isinstance(item, dict):
            continue
        raw_status = str(item.get("status") or "").strip().lower()
        bucket = status_map.get(raw_status, "queued")
        counts[bucket] = int(counts.get(bucket, 0) or 0) + 1
        counts["total"] = int(counts.get("total", 0) or 0) + 1
        item_ts = float(item.get("updated_at") or item.get("created_at") or 0.0)
        if latest is None or item_ts >= latest_ts:
            latest = item
            latest_ts = item_ts

    return {**base_summary, "counts": counts, "latest": latest}


def _status_from_episode_summary(summary: Dict[str, Any]) -> str:
    counts = (summary or {}).get("counts") or {}
    if int(counts.get("running", 0) or 0) > 0:
        return "running"
    if int(counts.get("pending", 0) or 0) > 0:
        return "waiting"
    if int(counts.get("failed", 0) or 0) > 0:
        return "failed"
    return "ready"


@router.post("/upload-tickets")
async def create_upload_ticket(request: Request, body: UploadTicketCreateRequest):
    bridge = getattr(request.app.state, "webui_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="WebUI bridge not initialized")
    try:
        return bridge.create_upload_ticket(scope_key=_scope_key(request), body=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/import/limits")
async def import_limits(request: Request):
    return request.app.state.import_task_manager.get_import_limits()


@router.post("/import/tasks")
async def create_import_task(request: Request, body: ImportTaskCreateRequest):
    manager = _task_manager(request)
    task = await manager.enqueue_import_task(body.model_dump())
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
    }


@router.get("/import/tasks/{task_id}")
async def get_import_task(request: Request, task_id: str):
    manager = _task_manager(request)
    return _task_or_404(manager.get_task(task_id))


@router.get("/dashboard/status")
async def dashboard_status(request: Request):
    ctx = _ctx(request)
    stats = await QueryService(ctx).stats()
    metadata_stats = stats.get("metadata_store") or {}
    query_counts = _recent_query_counts(request)
    query_trend = _query_event_histogram(request)
    import_summary = ctx.metadata_store.get_async_task_summary(task_type="import")
    task_mgr = getattr(request.app.state, "task_manager", None)
    native_import_mgr = getattr(task_mgr, "import_task_manager", None) or getattr(
        request.app.state, "import_task_manager", None
    )
    import_summary = _merge_native_import_summary(import_summary, native_import_mgr)
    episode_summary = ctx.metadata_store.get_episode_source_rebuild_summary(failed_limit=5)
    runtime_report = getattr(ctx, "_runtime_self_check_report", None)
    runtime_status = "unknown"
    if isinstance(runtime_report, dict):
        runtime_status = "ready" if runtime_report.get("ok") else "failed"

    return {
        "updated_at": time.time(),
        "stats": stats,
        "services": {
            "graph": {
                "status": "ready",
                "nodes": stats.get("graph_store", {}).get("num_nodes", 0),
                "relations": stats.get("graph_store", {}).get("num_edges", 0),
                "vectors": stats.get("vector_store", {}).get("num_vectors", 0),
            },
            "query": {
                "status": "ready",
                "recent_seconds": 60,
                "recent_count": query_counts.get("aggregate", 0),
                "recent_total_count": query_counts.get("total", 0),
                "trend_seconds": query_trend["seconds"],
                "trend_bucket_seconds": query_trend["bucket_seconds"],
                "trend_total_count": query_trend["total"],
                "trend_buckets": query_trend["buckets"],
            },
            "episode": {
                "status": _status_from_episode_summary(episode_summary),
                "count": int(metadata_stats.get("episode_count", 0) or 0),
                "queue": episode_summary,
            },
            "import": {
                "status": _status_from_async_summary(import_summary),
                "latest_task": import_summary.get("latest"),
                "counts": import_summary.get("counts"),
            },
            "person": {
                "status": "ready",
                "profile_count": int(metadata_stats.get("person_profile_count", 0) or 0),
            },
            "runtime": {
                "status": runtime_status,
                "report": runtime_report,
            },
        },
    }


@router.post("/query/search")
async def query_search(request: Request, body: QuerySearchRequest):
    _record_query_event(request, "search")
    service = QueryService(_ctx(request))
    try:
        return await service.search(query=body.query, top_k=body.top_k)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/auto")
async def query_auto(request: Request, body: QueryAutoRequest):
    _record_query_event(request, "auto")
    service = QueryService(_ctx(request))
    try:
        return await service.auto_search(
            query=body.query,
            top_k=body.top_k,
            stream_id=body.stream_id,
            group_id=body.group_id,
            user_id=body.user_id,
            source=body.source,
            strict_source=body.strict_source,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/time")
async def query_time(request: Request, body: QueryTimeRequest):
    _record_query_event(request, "time")
    service = QueryService(_ctx(request))
    try:
        return await service.time_search(
            query=body.query,
            time_from=body.time_from,
            time_to=body.time_to,
            person=body.person,
            source=body.source,
            top_k=body.top_k,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/entity")
async def query_entity(request: Request, body: QueryEntityRequest):
    _record_query_event(request, "entity")
    service = QueryService(_ctx(request))
    try:
        return await service.entity(entity_name=body.entity_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/relation")
async def query_relation(request: Request, body: QueryRelationRequest):
    _record_query_event(request, "relation")
    service = QueryService(_ctx(request))
    try:
        return await service.relation(subject=body.subject, predicate=body.predicate, obj=body.object)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/episode")
async def query_episode(request: Request, body: QueryEpisodeRequest):
    _record_query_event(request, "episode")
    service = QueryService(_ctx(request))
    try:
        return await service.episode(
            query=body.query,
            time_from=body.time_from,
            time_to=body.time_to,
            person=body.person,
            source=body.source,
            top_k=body.top_k,
            include_paragraphs=body.include_paragraphs,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query/aggregate")
async def query_aggregate(request: Request, body: QueryAggregateRequest):
    _record_query_event(request, "aggregate")
    service = QueryService(_ctx(request))
    try:
        return await service.aggregate(
            query=body.query,
            time_from=body.time_from,
            time_to=body.time_to,
            person=body.person,
            source=body.source,
            top_k=body.top_k,
            mix=body.mix,
            mix_top_k=body.mix_top_k,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/query/stats")
async def query_stats(request: Request):
    service = QueryService(_ctx(request))
    return await service.stats()


@router.post("/episode/rebuild")
async def episode_rebuild(request: Request, body: EpisodeRebuildRequest):
    ctx = _ctx(request)
    source = str(body.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")

    try:
        return await ctx.episode_service.rebuild_source(source)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/episode/{episode_id}")
async def episode_get(request: Request, episode_id: str, include_paragraphs: bool = Query(False)):
    ctx = _ctx(request)
    episode = ctx.metadata_store.get_episode_by_id(episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    if include_paragraphs:
        episode["paragraphs"] = ctx.metadata_store.get_episode_paragraphs(episode_id)
    return episode


@router.post("/runtime/self_check")
async def runtime_self_check(request: Request, force: bool = Query(False)):
    return await ensure_runtime_self_check(_ctx(request), force=force)


@router.post("/delete/paragraph")
async def delete_paragraph(request: Request, body: DeleteParagraphRequest):
    service = DeleteService(_ctx(request))
    try:
        return await service.paragraph(body.paragraph_hash)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/delete/entity")
async def delete_entity(request: Request, body: DeleteEntityRequest):
    service = DeleteService(_ctx(request))
    try:
        return await service.entity(body.entity_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/delete/relation")
async def delete_relation(request: Request, body: DeleteRelationRequest):
    service = DeleteService(_ctx(request))
    try:
        return await service.relation(body.relation)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/delete/clear")
async def delete_clear(request: Request):
    service = DeleteService(_ctx(request))
    try:
        return await service.clear()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/memory/status")
async def memory_status(request: Request, body: MemoryStatusRequest):
    del body
    service = MemoryService(_ctx(request))
    return await service.status()


@router.post("/memory/protect")
async def memory_protect(request: Request, body: MemoryProtectRequest):
    service = MemoryService(_ctx(request))
    return await service.protect(query_or_hash=body.id, hours=body.hours)


@router.post("/memory/reinforce")
async def memory_reinforce(request: Request, body: MemoryReinforceRequest):
    service = MemoryService(_ctx(request))
    return await service.reinforce(query_or_hash=body.id)


@router.post("/memory/freeze")
async def memory_freeze(request: Request, body: MemoryFreezeRequest):
    service = MemoryService(_ctx(request))
    return await service.freeze(query_or_hash=body.id)


@router.post("/memory/restore")
async def memory_restore(request: Request, body: MemoryRestoreRequest):
    service = MemoryService(_ctx(request))
    return await service.restore(hash_value=body.hash, restore_type=body.type)


@router.post("/person/query")
async def person_query(request: Request, body: PersonQueryRequest):
    service = PersonProfileApiService(_ctx(request))
    try:
        return await service.query(
            person_id=body.person_id,
            person_keyword=body.person_keyword,
            top_k=body.top_k,
            force_refresh=body.force_refresh,
            source_note="v1:person_query",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/person/override")
async def person_override(request: Request, body: PersonOverrideRequest):
    service = PersonProfileApiService(_ctx(request))
    try:
        return await service.set_override(
            person_id=body.person_id,
            override_text=body.override_text,
            updated_by=body.updated_by,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/person/override")
async def person_override_delete(request: Request, body: PersonOverrideDeleteRequest):
    service = PersonProfileApiService(_ctx(request))
    try:
        return await service.delete_override(person_id=body.person_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/person/registry/upsert")
async def person_registry_upsert(request: Request, body: PersonRegistryUpsertRequest):
    service = PersonProfileApiService(_ctx(request))
    try:
        data = await service.upsert_registry(body.model_dump())
        return {"success": True, "item": data}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/person/registry/list")
async def person_registry_list(
    request: Request,
    keyword: str = Query("", description="keyword"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=200),
):
    service = PersonProfileApiService(_ctx(request))
    return await service.list_registry(keyword=keyword, page=page, page_size=page_size)


@router.post("/summary/tasks")
async def create_summary_task(request: Request, body: SummaryTaskCreateRequest):
    manager = _task_manager(request)
    task = await manager.enqueue_summary_task(body.model_dump())
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
    }


@router.get("/summary/tasks/{task_id}")
async def get_summary_task(request: Request, task_id: str):
    manager = _task_manager(request)
    return _task_or_404(manager.get_task(task_id))


@router.post("/tuning")
async def tuning_admin(request: Request, body: TuningAdminRequest):
    service = _admin_service(request)
    scope = _scope_key(request)
    try:
        return await service.tuning_admin(scope_key=scope, action=body.action, **(body.payload or {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/feedback")
async def feedback_admin(request: Request, body: FeedbackAdminRequest):
    service = _admin_service(request)
    scope = _scope_key(request)
    try:
        return await service.feedback_admin(scope_key=scope, action=body.action, **(body.payload or {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fuzzy_modify")
async def fuzzy_modify_admin(request: Request, body: FuzzyModifyAdminRequest):
    service = _admin_service(request)
    scope = _scope_key(request)
    try:
        return await service.fuzzy_modify_admin(scope_key=scope, action=body.action, **(body.payload or {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/correction")
async def correction_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in (body.payload or {}).items() if key not in {"scope_key", "action"}}
    try:
        return await _admin_service(request).correction_admin(scope_key=_scope_key(request), action=body.action, **payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bundles")
async def bundle_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in (body.payload or {}).items() if key not in {"scope_key", "action"}}
    try:
        return await _admin_service(request).bundle_admin(scope_key=_scope_key(request), action=body.action, **payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/images")
async def image_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in (body.payload or {}).items() if key not in {"scope_key", "action"}}
    try:
        return await _admin_service(request).image_admin(scope_key=_scope_key(request), action=body.action, **payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/chat-migrations")
async def chat_migration_admin(request: Request, body: StructuredAdminRequest):
    payload = {key: value for key, value in (body.payload or {}).items() if key not in {"scope_key", "action"}}
    try:
        return await _admin_service(request).chat_migration_admin(
            scope_key=_scope_key(request),
            action=body.action,
            **payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chat-migrations/chats")
async def list_chat_migration_chats(
    request: Request,
    query: str = Query("", description="过滤关键词"),
    limit: int = Query(100, ge=1, le=500),
):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    return await AstrBotChatMigrationService(_ctx(request)).list_chats(query=query, limit=limit)


@router.get("/chat-migrations")
async def list_chat_migration_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    status: str = Query("", description="状态过滤"),
):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    return await AstrBotChatMigrationService(_ctx(request)).list_jobs(limit=limit, status=status)


@router.post("/chat-migrations/preview")
async def preview_chat_migration(request: Request, body: ChatMigrationPreviewRequest):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    try:
        return await AstrBotChatMigrationService(_ctx(request)).preview(
            source_chat_id=body.source_chat_id,
            target_chat_id=body.target_chat_id,
            migrate_transcript=body.migrate_transcript,
            requested_by=body.requested_by,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chat-migrations/{job_id}")
async def get_chat_migration_job(request: Request, job_id: str):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    result = await AstrBotChatMigrationService(_ctx(request)).get_job(job_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error") or "迁移任务不存在"))
    return result


@router.post("/chat-migrations/{job_id}/execute")
async def execute_chat_migration_job(request: Request, job_id: str, body: ChatMigrationExecuteRequest):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    try:
        return await AstrBotChatMigrationService(_ctx(request)).execute(
            job_id=job_id,
            confirmed=bool(body.confirmed or body.confirm_overwrite),
            refresh_fingerprint=body.refresh_fingerprint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/chat-migrations/{job_id}/recover")
async def recover_chat_migration_job(request: Request, job_id: str):
    from ...services.chat_migration_service import AstrBotChatMigrationService

    try:
        return await AstrBotChatMigrationService(_ctx(request)).recover(job_id=job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/{component_name}")
async def unified_contract_admin(component_name: str, request: Request, body: StructuredAdminRequest):
    try:
        return await _admin_service(request).dispatch_contract(
            scope_key=_scope_key(request),
            component_name=component_name,
            action=body.action,
            payload=body.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
