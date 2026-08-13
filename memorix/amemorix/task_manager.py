"""Background task manager for import/summary and maintenance loops."""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .common.logging import get_logger
from .context import AppContext
from .services import (
    ImportService,
    MemoryService,
    PersonProfileApiService,
    SummaryService,
)

logger = get_logger("A_Memorix.TaskManager")

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"


class TaskManager:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.import_service = ImportService(ctx)
        self.summary_service = SummaryService(ctx)
        self.memory_service = MemoryService(ctx)
        self.person_service = PersonProfileApiService(ctx)

        queue_maxsize = int(self.ctx.get_config("tasks.queue_maxsize", 1024))
        self.import_queue: asyncio.Queue[Tuple[str, Dict[str, Any]]] = asyncio.Queue(maxsize=max(1, queue_maxsize))
        self.summary_queue: asyncio.Queue[Tuple[str, Dict[str, Any]]] = asyncio.Queue(maxsize=max(1, queue_maxsize))
        self._workers: List[asyncio.Task] = []
        self._stopping = False
        self._bulk_summary_lock = asyncio.Lock()
        self._pending_auto_summary_sessions: set[str] = set()

    async def start(self) -> None:
        self._stopping = False
        import_workers = max(1, int(self.ctx.get_config("tasks.import_workers", 1)))
        summary_workers = max(1, int(self.ctx.get_config("tasks.summary_workers", 1)))

        for idx in range(import_workers):
            self._workers.append(asyncio.create_task(self._import_worker(idx), name=f"import-worker-{idx}"))
        for idx in range(summary_workers):
            self._workers.append(asyncio.create_task(self._summary_worker(idx), name=f"summary-worker-{idx}"))

        self._workers.append(asyncio.create_task(self._auto_save_loop(), name="auto-save-loop"))
        self._workers.append(asyncio.create_task(self._memory_maintenance_loop(), name="memory-maint-loop"))
        self._workers.append(asyncio.create_task(self._paragraph_vector_backfill_loop(), name="paragraph-vector-loop"))
        self._workers.append(asyncio.create_task(self._person_profile_refresh_loop(), name="person-profile-loop"))
        self._workers.append(asyncio.create_task(self._episode_generation_loop(), name="episode-generation-loop"))
        logger.info("TaskManager started with %s workers", len(self._workers))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("TaskManager stopped")

    async def enqueue_import_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        task = self.ctx.metadata_store.create_async_task(task_id=task_id, task_type="import", payload=payload)
        await self.import_queue.put((task_id, payload))
        return task

    async def enqueue_summary_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        task = self.ctx.metadata_store.create_async_task(task_id=task_id, task_type="summary", payload=payload)
        await self.summary_queue.put((task_id, payload))
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.ctx.metadata_store.get_async_task(task_id)

    def _auto_summary_enabled(self) -> bool:
        return bool(self.ctx.get_config("summarization.enabled", True)) and bool(
            self.ctx.get_config("summarization.auto_import.enabled", True)
        )

    def _auto_summary_context_length(self) -> int:
        return max(1, int(self.ctx.get_config("summarization.context_length", 50) or 50))

    def _count_new_transcript_messages(
        self,
        *,
        session_id: str,
        last_message_created_at: Optional[float],
    ) -> Tuple[int, Optional[float]]:
        conn = self.ctx.metadata_store._conn
        if conn is None or not session_id:
            return 0, None

        cursor = conn.cursor()
        if last_message_created_at is None:
            cursor.execute(
                """
                SELECT COUNT(*), MAX(created_at)
                FROM transcript_messages
                WHERE session_id = ?
                """,
                (str(session_id),),
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*), MAX(created_at)
                FROM transcript_messages
                WHERE session_id = ? AND created_at > ?
                """,
                (str(session_id), float(last_message_created_at)),
            )
        row = cursor.fetchone() or (0, None)
        return int(row[0] or 0), (float(row[1]) if row[1] is not None else None)

    async def maybe_enqueue_auto_summary(self, *, session_id: str) -> Dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            return {"queued": False, "reason": "empty_session"}
        if not self._auto_summary_enabled():
            return {"queued": False, "reason": "disabled"}
        if sid in self._pending_auto_summary_sessions:
            return {"queued": False, "reason": "already_pending"}

        transcript_session = self.ctx.metadata_store.get_transcript_session(sid)
        if not transcript_session:
            return {"queued": False, "reason": "session_not_found"}

        session_meta = transcript_session.get("metadata") if isinstance(transcript_session, dict) else {}
        if not isinstance(session_meta, dict):
            session_meta = {}

        group_id = str(session_meta.get("group_id", "") or "").strip() or None
        user_id = str(session_meta.get("user_id", "") or "").strip() or None
        if not self.ctx.is_chat_enabled(stream_id=sid, group_id=group_id, user_id=user_id):
            return {"queued": False, "reason": "chat_filtered"}

        summary_state = self.ctx.metadata_store.get_transcript_summary_state(sid) or {}
        cooldown_minutes = float(self.ctx.get_config("summarization.auto_import.cooldown_minutes", 30) or 30)
        cooldown_seconds = max(0.0, cooldown_minutes * 60.0)
        last_summary_at = summary_state.get("last_summary_at")
        now_ts = datetime.datetime.now().timestamp()
        if last_summary_at is not None:
            try:
                if (now_ts - float(last_summary_at)) < cooldown_seconds:
                    return {"queued": False, "reason": "cooldown"}
            except (TypeError, ValueError):
                pass

        new_message_count, last_message_created_at = self._count_new_transcript_messages(
            session_id=sid,
            last_message_created_at=summary_state.get("last_message_created_at"),
        )
        min_new_messages = max(1, int(self.ctx.get_config("summarization.auto_import.min_new_messages", 12) or 12))
        if new_message_count < min_new_messages:
            return {"queued": False, "reason": "insufficient_new_messages", "new_message_count": new_message_count}

        payload = {
            "session_id": sid,
            "messages": [],
            "source": f"chat_summary:{sid}",
            "context_length": self._auto_summary_context_length(),
            "persist_messages": False,
            "auto_import": True,
            "last_message_created_at": last_message_created_at,
        }
        task = await self.enqueue_summary_task(payload)
        self._pending_auto_summary_sessions.add(sid)
        return {
            "queued": True,
            "task_id": str(task.get("task_id", "") or ""),
            "new_message_count": new_message_count,
        }

    async def run_bulk_summary_import(
        self,
        *,
        limit: Optional[int] = None,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        async with self._bulk_summary_lock:
            return await self._perform_bulk_summary_import(limit=limit, context_length=context_length)

    async def _perform_bulk_summary_import(
        self,
        *,
        limit: Optional[int] = None,
        context_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        conn = self.ctx.metadata_store._conn
        if conn is None:
            return {"success": 0, "skipped": 0, "failed": 0, "candidates": 0, "message": "metadata store not ready"}

        resolved_context_length = (
            max(1, int(context_length))
            if context_length is not None
            else max(1, int(self.ctx.get_config("summarization.context_length", 50) or 50))
        )
        resolved_limit = max(1, int(limit)) if limit is not None else 500
        cursor = conn.cursor()
        cursor.execute(
            """
            WITH last_msgs AS (
                SELECT session_id, MAX(created_at) AS last_msg_created_at
                FROM transcript_messages
                GROUP BY session_id
            )
            SELECT s.session_id, s.metadata_json, lm.last_msg_created_at, st.last_message_created_at
            FROM transcript_sessions s
            JOIN last_msgs lm ON lm.session_id = s.session_id
            LEFT JOIN transcript_summary_state st ON st.session_id = s.session_id
            WHERE st.last_message_created_at IS NULL OR lm.last_msg_created_at > st.last_message_created_at
            ORDER BY lm.last_msg_created_at DESC
            LIMIT ?
            """,
            (resolved_limit,),
        )
        rows = cursor.fetchall()
        if not rows:
            return {"success": 0, "skipped": 0, "failed": 0, "candidates": 0}

        success_count = 0
        skipped_count = 0
        fail_count = 0
        for row in rows:
            session_id = str(row[0] or "").strip()
            if not session_id:
                skipped_count += 1
                continue
            metadata: Dict[str, Any] = {}
            raw_meta = row[1]
            if raw_meta:
                try:
                    metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else {}
                except Exception:
                    metadata = {}
            group_id = str(metadata.get("group_id", "") or "").strip() or None
            user_id = str(metadata.get("user_id", "") or "").strip() or None
            if not self.ctx.is_chat_enabled(stream_id=session_id, group_id=group_id, user_id=user_id):
                skipped_count += 1
                continue
            try:
                result = await self.summary_service.import_from_transcript(
                    session_id=session_id,
                    messages=[],
                    source=f"chat_summary:{session_id}",
                    context_length=resolved_context_length,
                )
                if bool(result.get("success")):
                    self.ctx.metadata_store.mark_transcript_summary_complete(
                        session_id=session_id,
                        last_message_created_at=row[2],
                        metadata={
                            "source": f"chat_summary:{session_id}",
                            "trigger": "bulk_summary_import",
                            "context_length": resolved_context_length,
                        },
                    )
                    success_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
                logger.warning("bulk summary import failed: session=%s", session_id, exc_info=True)

        return {
            "success": success_count,
            "skipped": skipped_count,
            "failed": fail_count,
            "candidates": len(rows),
        }

    async def _import_worker(self, worker_idx: int) -> None:
        while not self._stopping:
            task_id = ""
            try:
                task_id, payload = await self.import_queue.get()
                existing = self.ctx.metadata_store.get_async_task(task_id)
                if existing and existing.get("cancel_requested"):
                    self.ctx.metadata_store.update_async_task(
                        task_id=task_id,
                        status=TASK_STATUS_CANCELED,
                        finished_at=datetime.datetime.now().timestamp(),
                    )
                    self.import_queue.task_done()
                    continue

                now = datetime.datetime.now().timestamp()
                self.ctx.metadata_store.update_async_task(task_id=task_id, status=TASK_STATUS_RUNNING, started_at=now)
                mode = str(payload.get("mode", "text"))
                body = payload.get("payload")
                options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
                result = await self.import_service.run_import(mode=mode, payload=body, options=options)
                self.ctx.metadata_store.update_async_task(
                    task_id=task_id,
                    status=TASK_STATUS_SUCCEEDED,
                    result=result,
                    finished_at=datetime.datetime.now().timestamp(),
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if task_id:
                    self.ctx.metadata_store.update_async_task(
                        task_id=task_id,
                        status=TASK_STATUS_FAILED,
                        error_message=str(exc),
                        finished_at=datetime.datetime.now().timestamp(),
                    )
                logger.error("Import worker %s failed task %s: %s", worker_idx, task_id, exc, exc_info=True)
            finally:
                if task_id:
                    self.import_queue.task_done()

    async def _summary_worker(self, worker_idx: int) -> None:
        while not self._stopping:
            task_id = ""
            try:
                task_id, payload = await self.summary_queue.get()
                existing = self.ctx.metadata_store.get_async_task(task_id)
                if existing and existing.get("cancel_requested"):
                    self.ctx.metadata_store.update_async_task(
                        task_id=task_id,
                        status=TASK_STATUS_CANCELED,
                        finished_at=datetime.datetime.now().timestamp(),
                    )
                    self.summary_queue.task_done()
                    continue

                self.ctx.metadata_store.update_async_task(
                    task_id=task_id,
                    status=TASK_STATUS_RUNNING,
                    started_at=datetime.datetime.now().timestamp(),
                )
                session_id = str(payload.get("session_id", "")).strip() or uuid.uuid4().hex
                messages = payload.get("messages")
                if not isinstance(messages, list):
                    messages = []
                source = str(payload.get("source", f"summary:{session_id}"))
                context_length = int(payload.get("context_length", self.ctx.get_config("summarization.context_length", 50)))
                result = await self.summary_service.import_from_transcript(
                    session_id=session_id,
                    messages=messages,
                    source=source,
                    context_length=context_length,
                )
                status = TASK_STATUS_SUCCEEDED if result.get("success") else TASK_STATUS_FAILED
                if status == TASK_STATUS_SUCCEEDED:
                    self.ctx.metadata_store.mark_transcript_summary_complete(
                        session_id=session_id,
                        last_message_created_at=payload.get("last_message_created_at"),
                        task_id=task_id,
                        metadata={
                            "source": source,
                            "auto_import": bool(payload.get("auto_import")),
                            "context_length": context_length,
                        },
                    )
                self.ctx.metadata_store.update_async_task(
                    task_id=task_id,
                    status=status,
                    result=result,
                    error_message="" if result.get("success") else str(result.get("message", "")),
                    finished_at=datetime.datetime.now().timestamp(),
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if task_id:
                    self.ctx.metadata_store.update_async_task(
                        task_id=task_id,
                        status=TASK_STATUS_FAILED,
                        error_message=str(exc),
                        finished_at=datetime.datetime.now().timestamp(),
                    )
                logger.error("Summary worker %s failed task %s: %s", worker_idx, task_id, exc, exc_info=True)
            finally:
                if task_id:
                    if payload.get("auto_import"):
                        self._pending_auto_summary_sessions.discard(str(payload.get("session_id", "") or ""))
                    self.summary_queue.task_done()

    async def _auto_save_loop(self) -> None:
        while not self._stopping:
            try:
                interval_min = float(self.ctx.get_config("advanced.auto_save_interval_minutes", 5))
                await asyncio.sleep(max(30.0, interval_min * 60.0))
                if bool(self.ctx.get_config("advanced.enable_auto_save", True)):
                    await self.ctx.save_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Auto-save loop error: %s", exc)

    async def _memory_maintenance_loop(self) -> None:
        while not self._stopping:
            try:
                interval_h = float(self.ctx.get_config("memory.base_decay_interval_hours", 1.0))
                interval_s = max(60.0, interval_h * 3600.0)
                await asyncio.sleep(interval_s)
                if not bool(self.ctx.get_config("memory.enabled", True)):
                    continue

                half_life = float(self.ctx.get_config("memory.half_life_hours", 24.0))
                if half_life > 0:
                    factor = 0.5 ** (interval_h / half_life)
                    self.ctx.graph_store.decay(factor)

                prune_threshold = float(self.ctx.get_config("memory.prune_threshold", 0.1))
                low_edges = self.ctx.graph_store.get_low_weight_edges(prune_threshold)
                hashes_to_freeze: List[str] = []
                edges_to_deactivate = []
                for src, tgt in low_edges:
                    src_c = self.ctx.graph_store._canonicalize(src)  # noqa: SLF001
                    tgt_c = self.ctx.graph_store._canonicalize(tgt)  # noqa: SLF001
                    s_idx = self.ctx.graph_store._node_to_idx.get(src_c)  # noqa: SLF001
                    t_idx = self.ctx.graph_store._node_to_idx.get(tgt_c)  # noqa: SLF001
                    if s_idx is None or t_idx is None:
                        continue
                    edge_hashes = list(self.ctx.graph_store._edge_hash_map.get((s_idx, t_idx), set()))  # noqa: SLF001
                    if not edge_hashes:
                        continue
                    status = self.ctx.metadata_store.get_relation_status_batch(edge_hashes)
                    protected = any(
                        (v.get("is_pinned") or ((v.get("protected_until") or 0) > datetime.datetime.now().timestamp()))
                        for v in status.values()
                    )
                    if not protected:
                        hashes_to_freeze.extend(edge_hashes)
                        edges_to_deactivate.append((src, tgt))

                if hashes_to_freeze:
                    self.ctx.metadata_store.mark_relations_inactive(hashes_to_freeze)
                    self.ctx.graph_store.deactivate_edges(edges_to_deactivate)

                freeze_hours = float(self.ctx.get_config("memory.freeze_duration_hours", 24.0))
                cutoff = datetime.datetime.now().timestamp() - max(1.0, freeze_hours * 3600.0)
                expired = self.ctx.metadata_store.get_prune_candidates(cutoff)
                if expired:
                    cursor = self.ctx.metadata_store._conn.cursor()
                    placeholders = ",".join(["?"] * len(expired))
                    cursor.execute(
                        f"SELECT hash, subject, object FROM relations WHERE hash IN ({placeholders})",
                        expired,
                    )
                    ops = [(str(row[1]), str(row[2]), str(row[0])) for row in cursor.fetchall()]
                    if ops:
                        self.ctx.graph_store.prune_relation_hashes(ops)
                    self.ctx.metadata_store.backup_and_delete_relations(expired)
                await asyncio.to_thread(self.ctx.graph_store.save)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Memory maintenance loop error: %s", exc, exc_info=True)

    async def _paragraph_vector_backfill_loop(self) -> None:
        while not self._stopping:
            try:
                interval_s = int(self.ctx.get_config("retrieval.paragraph_vectorization.backfill_interval_seconds", 60))
                await asyncio.sleep(max(10, interval_s))
                if not bool(self.ctx.get_config("retrieval.paragraph_vectorization.backfill_enabled", True)):
                    continue
                service = getattr(self.ctx, "paragraph_vector_service", None)
                if service is None or not hasattr(service, "backfill_missing_vectors"):
                    continue

                batch_size = int(self.ctx.get_config("retrieval.paragraph_vectorization.backfill_batch_size", 50))
                scan_multiplier = int(
                    self.ctx.get_config("retrieval.paragraph_vectorization.backfill_scan_multiplier", 20)
                )
                result = await service.backfill_missing_vectors(
                    batch_size=max(1, batch_size),
                    scan_limit=max(1, batch_size) * max(1, scan_multiplier),
                )
                if (
                    int(result.get("written", 0) or 0) > 0
                    or int(result.get("already_exists", 0) or 0) > 0
                    or result.get("failed")
                ):
                    logger.info("paragraph vector backfill result: %s", result)
                    try:
                        await asyncio.to_thread(self.ctx.vector_store.save)
                    except Exception as exc:
                        logger.warning("Paragraph vector backfill save failed: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Paragraph vector backfill loop error: %s", exc, exc_info=True)

    async def _person_profile_refresh_loop(self) -> None:
        while not self._stopping:
            try:
                interval_min = int(self.ctx.get_config("person_profile.refresh_interval_minutes", 30))
                await asyncio.sleep(max(60, interval_min * 60))
                if not bool(self.ctx.get_config("person_profile.enabled", True)):
                    continue

                active_window_h = float(self.ctx.get_config("person_profile.active_window_hours", 72.0))
                active_after = datetime.datetime.now().timestamp() - max(1.0, active_window_h * 3600.0)
                limit = int(self.ctx.get_config("person_profile.max_refresh_per_cycle", 50))
                top_k = int(self.ctx.get_config("person_profile.top_k_evidence", 12))

                pids = self.ctx.metadata_store.get_active_person_ids(
                    active_after=active_after,
                    limit=limit,
                )
                for pid in pids:
                    try:
                        await self.person_service.query(
                            person_id=pid,
                            top_k=top_k,
                            force_refresh=False,
                            source_note="task:person_profile_refresh",
                        )
                    except Exception:
                        continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Person profile refresh loop error: %s", exc)

    async def _episode_generation_loop(self) -> None:
        while not self._stopping:
            try:
                interval_s = int(self.ctx.get_config("episode.generation_interval_seconds", 30))
                await asyncio.sleep(max(1, interval_s))
                if not bool(self.ctx.get_config("episode.enabled", True)):
                    continue
                if not bool(self.ctx.get_config("episode.generation_enabled", True)):
                    continue

                batch_size = int(self.ctx.get_config("episode.generation_batch_size", 20))
                max_retry = int(self.ctx.get_config("episode.max_retry", 3))
                rows = self.ctx.metadata_store.fetch_episode_source_rebuild_batch(
                    limit=max(1, batch_size),
                    max_retry=max(0, max_retry),
                )
                for row in rows:
                    source = str(row.get("source", "") or "").strip()
                    requested_at = row.get("requested_at")
                    if not source:
                        continue
                    if not self.ctx.metadata_store.mark_episode_source_running(
                        source,
                        requested_at=requested_at,
                    ):
                        continue
                    try:
                        await self.ctx.episode_service.rebuild_source(source)
                        self.ctx.metadata_store.mark_episode_source_done(
                            source,
                            requested_at=requested_at,
                        )
                    except Exception as exc:
                        self.ctx.metadata_store.mark_episode_source_failed(
                            source,
                            str(exc),
                            requested_at=requested_at,
                        )
                        logger.warning("Episode rebuild failed for source=%s: %s", source, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Episode generation loop error: %s", exc, exc_info=True)
