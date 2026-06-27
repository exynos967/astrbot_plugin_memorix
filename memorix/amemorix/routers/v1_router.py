"""Operation APIs (/v1/*) for the embedded WebUI."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..services import (
    DeleteService,
    MemoryService,
    PersonProfileApiService,
    QueryService,
)
from ...core.utils.runtime_self_check import ensure_runtime_self_check

router = APIRouter(prefix="/v1", tags=["v1"])

QUERY_EVENT_RETENTION_SECONDS = 2 * 60 * 60
QUERY_TREND_BUCKET_SECONDS = 5 * 60


class ImportTaskCreateRequest(BaseModel):
    mode: str = Field(default="text")
    payload: Any
    options: Dict[str, Any] = Field(default_factory=dict)


class SummaryTaskCreateRequest(BaseModel):
    session_id: Optional[str] = None
    source: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    context_length: int = 50


class QuerySearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None


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
    if latest_status in {"failed", "canceled"}:
        return latest_status
    return "ready"


def _status_from_episode_summary(summary: Dict[str, Any]) -> str:
    counts = (summary or {}).get("counts") or {}
    if int(counts.get("running", 0) or 0) > 0:
        return "running"
    if int(counts.get("pending", 0) or 0) > 0:
        return "waiting"
    if int(counts.get("failed", 0) or 0) > 0:
        return "failed"
    return "ready"


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

    queue_row = ctx.metadata_store.get_episode_source_rebuild(source)
    queue_status = str((queue_row or {}).get("status", "") or "").strip().lower()
    requested_at = (queue_row or {}).get("requested_at")
    managed_queue = False

    if queue_status == "running":
        raise HTTPException(status_code=409, detail="Episode source rebuild is already running")
    if queue_status in {"pending", "failed"}:
        managed_queue = ctx.metadata_store.mark_episode_source_running(
            source,
            requested_at=requested_at,
        )
        if not managed_queue:
            latest = ctx.metadata_store.get_episode_source_rebuild(source)
            latest_status = str((latest or {}).get("status", "") or "").strip().lower()
            if latest_status == "running":
                raise HTTPException(status_code=409, detail="Episode source rebuild is already running")
            requested_at = None

    try:
        result = await ctx.episode_service.rebuild_source(source)
        if managed_queue:
            ctx.metadata_store.mark_episode_source_done(source, requested_at=requested_at)
        return result
    except Exception as exc:
        if managed_queue:
            ctx.metadata_store.mark_episode_source_failed(
                source,
                str(exc),
                requested_at=requested_at,
            )
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
