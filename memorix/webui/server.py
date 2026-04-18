"""Embedded WebUI server manager."""

from __future__ import annotations

import asyncio
import datetime
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from astrbot.api import logger

from ..amemorix.auth import BearerAuthMiddleware
from ..amemorix.common import register_lifecycle_handler
from ..amemorix.import_write_guard import ImportWriteGuardMiddleware
from ..amemorix.routers.v1_router import router as v1_router
from ..amemorix.services import ImportService, SummaryService
from ..amemorix.services.import_task_manager import ImportTaskManager
from ..app_context import ScopeRuntimeManager
from .routes_compat import MemorixServer

TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"


class _WebV1TaskManager:
    """Minimal v1 task manager for WebUI loop.

    This avoids cross-event-loop usage of runtime.task_manager while preserving
    legacy /v1/import/tasks and /v1/summary/tasks compatibility.
    """

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.import_service = ImportService(ctx)
        self.summary_service = SummaryService(ctx)
        self._jobs: Set[asyncio.Task] = set()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        jobs = list(self._jobs)
        self._jobs.clear()
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.ctx.metadata_store.get_async_task(task_id)

    async def enqueue_import_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        task = self.ctx.metadata_store.create_async_task(task_id=task_id, task_type="import", payload=payload)
        job = asyncio.create_task(self._run_import(task_id, payload), name=f"webui-import-{task_id[:8]}")
        self._jobs.add(job)
        job.add_done_callback(lambda t: self._jobs.discard(t))
        return task

    async def enqueue_summary_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        task = self.ctx.metadata_store.create_async_task(task_id=task_id, task_type="summary", payload=payload)
        job = asyncio.create_task(self._run_summary(task_id, payload), name=f"webui-summary-{task_id[:8]}")
        self._jobs.add(job)
        job.add_done_callback(lambda t: self._jobs.discard(t))
        return task

    async def _run_import(self, task_id: str, payload: Dict[str, Any]) -> None:
        try:
            existing = self.ctx.metadata_store.get_async_task(task_id)
            if existing and existing.get("cancel_requested"):
                self.ctx.metadata_store.update_async_task(
                    task_id=task_id,
                    status=TASK_STATUS_CANCELED,
                    finished_at=datetime.datetime.now().timestamp(),
                )
                return

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
            raise
        except Exception as exc:
            self.ctx.metadata_store.update_async_task(
                task_id=task_id,
                status=TASK_STATUS_FAILED,
                error_message=str(exc),
                finished_at=datetime.datetime.now().timestamp(),
            )
            logger.error("webui import task failed task=%s err=%s", task_id, exc, exc_info=True)

    async def _run_summary(self, task_id: str, payload: Dict[str, Any]) -> None:
        try:
            existing = self.ctx.metadata_store.get_async_task(task_id)
            if existing and existing.get("cancel_requested"):
                self.ctx.metadata_store.update_async_task(
                    task_id=task_id,
                    status=TASK_STATUS_CANCELED,
                    finished_at=datetime.datetime.now().timestamp(),
                )
                return

            self.ctx.metadata_store.update_async_task(
                task_id=task_id,
                status=TASK_STATUS_RUNNING,
                started_at=datetime.datetime.now().timestamp(),
            )
            session_id = str(payload.get("session_id", "")).strip() or uuid.uuid4().hex
            messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
            source = str(payload.get("source", f"chat_summary:{session_id}"))
            context_length = int(payload.get("context_length", self.ctx.get_config("summarization.context_length", 50)))
            persist_messages = bool(payload.get("persist_messages", False))
            result = await self.summary_service.import_from_transcript(
                session_id=session_id,
                messages=messages,
                source=source,
                context_length=context_length,
                persist_messages=persist_messages,
            )
            status = TASK_STATUS_SUCCEEDED if result.get("success") else TASK_STATUS_FAILED
            self.ctx.metadata_store.update_async_task(
                task_id=task_id,
                status=status,
                result=result,
                error_message="" if result.get("success") else str(result.get("message", "")),
                finished_at=datetime.datetime.now().timestamp(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.ctx.metadata_store.update_async_task(
                task_id=task_id,
                status=TASK_STATUS_FAILED,
                error_message=str(exc),
                finished_at=datetime.datetime.now().timestamp(),
            )
            logger.error("webui summary task failed task=%s err=%s", task_id, exc, exc_info=True)


@dataclass(slots=True)
class WebUIServerState:
    scope_key: str = ""
    host: str = ""
    port: int = 0
    url: str = ""


class EmbeddedWebUIServer:
    def __init__(self, runtime_manager: ScopeRuntimeManager, plugin_config: Dict[str, Any]):
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self._server: Optional[MemorixServer] = None
        self.state = WebUIServerState()

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.plugin_config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @staticmethod
    def _is_port_available(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return True
            except OSError:
                return False

    @staticmethod
    def _display_host(host: str) -> str:
        normalized = str(host or "").strip().lower()
        if normalized in {"0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return str(host or "").strip() or "127.0.0.1"

    def _pick_port(self, host: str, start_port: int, tries: int) -> int:
        for idx in range(max(1, int(tries))):
            candidate = int(start_port) + idx
            if self._is_port_available(host, candidate):
                if idx > 0:
                    logger.warning(
                        "webui default port busy, fallback selected: host=%s from=%s to=%s",
                        host,
                        start_port,
                        candidate,
                    )
                return candidate
        raise RuntimeError(f"no available port from {start_port} after {tries} tries")

    async def start(self, *, scope_key: str) -> WebUIServerState:
        enabled = bool(self._cfg("webui.enabled", True))
        if not enabled:
            logger.info("webui disabled by config")
            return self.state

        if self._server is not None:
            logger.info("webui already running: url=%s scope=%s", self.state.url, self.state.scope_key)
            return self.state

        runtime = await self.runtime_manager.get_runtime(scope_key)

        host = str(self._cfg("webui.host", "0.0.0.0") or "0.0.0.0")
        start_port = int(self._cfg("webui.port", 8092) or 8092)
        max_tries = int(self._cfg("webui.port_fallback_max_tries", 20) or 20)
        port = self._pick_port(host, start_port, max_tries)
        display_host = self._display_host(host)

        server = MemorixServer(plugin_instance=runtime.context, host=host, port=port)
        app = server.app
        app.state.context = runtime.context

        if not bool(getattr(app.state, "_memorix_v1_router_registered", False)):
            app.include_router(v1_router)
            app.state._memorix_v1_router_registered = True

        auth_enabled = bool(self._cfg("webui.auth.enabled", False))
        if auth_enabled:
            app.add_middleware(BearerAuthMiddleware)

        if not bool(getattr(app.state, "_memorix_import_guard_registered", False)):
            app.add_middleware(ImportWriteGuardMiddleware)
            app.state._memorix_import_guard_registered = True

        if not bool(getattr(app.state, "_memorix_import_hooks_registered", False)):

            async def _startup_managers() -> None:
                legacy = _WebV1TaskManager(runtime.context)
                await legacy.start()
                app.state.task_manager = legacy

                import_manager = ImportTaskManager(runtime.context)
                await import_manager.start()
                app.state.import_task_manager = import_manager

            async def _shutdown_managers() -> None:
                manager = getattr(app.state, "import_task_manager", None)
                if manager is not None:
                    try:
                        await manager.stop()
                    except Exception as exc:
                        logger.warning("webui import task manager stop failed: %s", exc)

                legacy = getattr(app.state, "task_manager", None)
                if legacy is not None and hasattr(legacy, "stop"):
                    try:
                        await legacy.stop()
                    except Exception as exc:
                        logger.warning("webui legacy task manager stop failed: %s", exc)

            register_lifecycle_handler(app, "startup", _startup_managers)
            register_lifecycle_handler(app, "shutdown", _shutdown_managers)
            app.state._memorix_import_hooks_registered = True

        raw_startup_timeout = self._cfg("webui.startup_timeout_seconds", self._cfg("webui.startup_timeout", 8.0))
        try:
            startup_timeout = max(0.5, float(raw_startup_timeout))
        except (TypeError, ValueError):
            startup_timeout = 8.0

        await asyncio.to_thread(server.start, True, startup_timeout)
        self._server = server
        self.state = WebUIServerState(
            scope_key=scope_key,
            host=host,
            port=port,
            url=f"http://{display_host}:{port}",
        )
        logger.info(
            "webui started: bind=%s:%s url=%s scope=%s auth_enabled=%s",
            host,
            port,
            self.state.url,
            self.state.scope_key,
            auth_enabled,
        )
        return self.state

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.stop()
        finally:
            logger.info("webui stopped: url=%s scope=%s", self.state.url, self.state.scope_key)
            self._server = None
            self.state = WebUIServerState()
