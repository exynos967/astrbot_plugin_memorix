"""AstrBot Plugin Pages bridge for the Memorix WebUI."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from astrbot.api import logger

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

PLUGIN_PAGE_API_ROUTE = "webui/request"
PLUGIN_PAGE_UPLOAD_ROUTE = "webui/upload"


class _WebV1TaskManager:
    """Minimal v1 task manager for the Dashboard embedded WebUI."""

    def __init__(self, ctx: Any, *, import_task_manager: ImportTaskManager | None = None):
        self.ctx = ctx
        self.import_service = ImportService(ctx)
        self.summary_service = SummaryService(ctx)
        self.import_task_manager = import_task_manager or ImportTaskManager(ctx)
        self._native_import_task_ids: Set[str] = set()
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
        task = self.ctx.metadata_store.get_async_task(task_id)
        if task:
            return task

        # ImportTaskManager exposes an async get_task(), while the v1 router calls
        # this compatibility manager synchronously. Keep a narrow read-only bridge
        # for native import tasks created through enqueue_import_task().
        task_key = str(task_id or "")
        if task_key not in self._native_import_task_ids:
            return None
        native_tasks = getattr(self.import_task_manager, "_tasks", {})
        native_task = native_tasks.get(task_key) if isinstance(native_tasks, dict) else None
        if native_task is None:
            return None
        to_detail = getattr(native_task, "to_detail", None)
        if callable(to_detail):
            return to_detail(include_chunks=True)
        return native_task if isinstance(native_task, dict) else None

    async def _compat_import_task(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mode = str(payload.get("mode", "text") or "text").strip().lower()
        body = payload.get("payload")
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        import_options = {key: options[key] for key in ("source", "chat_log", "narrative_window_size", "narrative_overlap") if key in options}
        try:
            if mode == "text":
                content = body if isinstance(body, str) else str(body or "")
                if not content.strip():
                    raise ValueError("content 不能为空")
                return await self.import_task_manager.create_paste_task(
                    {
                        "content": content,
                        **import_options,
                        "name": str(options.get("name") or options.get("source") or "webui_text.txt"),
                        "knowledge_type": str(options.get("knowledge_type", "")),
                    }
                )
            if mode == "file" and isinstance(body, str):
                return await self.import_task_manager.create_raw_scan_task(
                    {
                        "alias": "raw",
                        **import_options,
                        "relative_path": body,
                        "glob": "*",
                        "recursive": True,
                        "knowledge_type": str(options.get("knowledge_type", "")),
                    }
                )
        except Exception:
            if self.import_task_manager.is_enabled():
                raise
        return None

    async def enqueue_import_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        compat_task = await self._compat_import_task(payload)
        if compat_task is not None:
            task_id = str(compat_task.get("task_id") or "")
            if task_id:
                self._native_import_task_ids.add(task_id)
            return compat_task

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

    async def enqueue_upload_task(self, files: list[Any], options: Dict[str, Any]) -> Dict[str, Any]:
        task = await self.import_task_manager.create_upload_task(files, options)
        self._native_import_task_ids.add(str(task["task_id"]))
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
class _EmbeddedWebUIApp:
    scope_key: str
    server: MemorixServer
    client: httpx.AsyncClient
    task_manager: _WebV1TaskManager
    import_task_manager: ImportTaskManager


class PluginPageWebUIBridge:
    """Expose the Memorix FastAPI WebUI API through AstrBot Plugin Pages.

    AstrBot Plugin Pages only proxy GET/POST requests to plugin Web APIs. The
    current A_memorix console still uses PUT/PATCH/DELETE, so the page sends a
    single AstrBot-proxied POST tunnel here and this bridge dispatches the original
    request to an in-process FastAPI app for the selected Memorix scope.
    """

    def __init__(
        self,
        *,
        runtime_manager: ScopeRuntimeManager,
        scope_resolver: Callable[[], str],
        admin_service: Any = None,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.scope_resolver = scope_resolver
        self.admin_service = admin_service
        self._apps: Dict[str, _EmbeddedWebUIApp] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    def register(self, context: Any, *, plugin_name: str) -> None:
        register_web_api = getattr(context, "register_web_api", None)
        if not callable(register_web_api):
            logger.warning("[memorix] AstrBot context does not support plugin Web APIs; embedded WebUI disabled")
            return

        register_web_api(
            f"/{plugin_name}/{PLUGIN_PAGE_API_ROUTE}",
            self.handle_request,
            ["POST"],
            "Memorix embedded WebUI request bridge",
        )
        register_web_api(
            f"/{plugin_name}/{PLUGIN_PAGE_UPLOAD_ROUTE}", self.handle_upload,
            ["POST"], "Memorix file import",
        )

    async def close(self) -> None:
        self._closing = True
        apps = list(self._apps.values())
        self._apps.clear()
        for app in apps:
            try:
                await app.client.aclose()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI client close failed: %s", exc)
            try:
                await app.import_task_manager.stop()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI import task manager stop failed: %s", exc)
            try:
                await app.task_manager.stop()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI task manager stop failed: %s", exc)

    async def handle_request(self):
        from astrbot.api.web import json_response, request

        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                raise ValueError("request payload must be a JSON object")

            method = str(payload.get("method") or "GET").upper()
            url = str(payload.get("url") or "").strip()
            body = payload.get("data")
            result = await self.dispatch(method=method, url=url, body=body)
            return json_response({"status": "ok", "data": result})
        except Exception as exc:
            logger.warning("[memorix] embedded WebUI request failed: %s", exc, exc_info=True)
            return json_response({"status": "error", "message": str(exc)})

    async def handle_upload(self):
        from astrbot.api.web import PluginUploadFile, error_response, json_response, request

        try:
            scope_key = self._resolve_scope_key(request.query.get("_scope", ""))
            app = await self._get_app(scope_key)
            max_size = app.import_task_manager.get_import_limits()["max_file_size_bytes"]
            content_length = request.headers.get("content-length")
            if content_length is None:
                return error_response("上传请求缺少 Content-Length", status_code=411)
            if int(content_length) < 0 or int(content_length) > max_size + 65536:
                return error_response("文件超过上传大小限制", status_code=413)
            files = await request.files()
            upload = files.get("file")
            if not isinstance(upload, PluginUploadFile):
                return error_response("请选择上传文件")
            options = {key: request.query.get(key) for key in
                       ("source", "input_mode", "knowledge_type", "chat_log", "narrative_window_size", "narrative_overlap")
                       if key in request.query}
            task = await app.task_manager.enqueue_upload_task([upload], options)
            return json_response({"status": "ok", "data": task})
        except (TypeError, ValueError) as exc:
            return error_response(str(exc))
        except Exception as exc:
            logger.warning("[memorix] file upload failed: %s", exc, exc_info=True)
            return error_response("上传失败，请检查插件日志", status_code=500)

    async def dispatch(self, *, method: str, url: str, body: Any = None) -> Any:
        method = self._normalize_method(method)
        target = self._normalize_url(url)
        if self._is_scope_options_request(target):
            return self._scope_options()

        target, scope_override = self._extract_scope_override(target)
        scope_key = self._resolve_scope_key(scope_override)
        app = await self._get_app(scope_key)

        request_kwargs: Dict[str, Any] = {}
        if method != "GET" and body is not None:
            request_kwargs["json"] = body

        response = await app.client.request(method, target, **request_kwargs)
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            message = response.text or response.reason_phrase or f"HTTP {response.status_code}"
            if "application/json" in content_type:
                try:
                    payload = response.json()
                    message = str(payload.get("detail") or payload.get("message") or payload)
                except Exception:
                    pass
            raise RuntimeError(message)

        if not response.content:
            return None
        if "application/json" in content_type:
            return response.json()
        return response.text

    def _resolve_scope_key(self, override: str = "") -> str:
        raw = str(override or "").strip()
        if not raw:
            raw = str(self.scope_resolver() or "").strip()
        return raw or "default"

    def _scope_options(self) -> Dict[str, Any]:
        current = self._resolve_scope_key()
        scope_keys: list[str] = []

        def add_scope(value: str) -> None:
            key = str(value or "").strip()
            if key and key not in scope_keys:
                scope_keys.append(key)

        add_scope(current)
        list_scope_keys = getattr(self.runtime_manager, "list_scope_keys", None)
        available_scopes = list_scope_keys() if callable(list_scope_keys) else self.runtime_manager.get_known_scopes()
        for key in available_scopes:
            add_scope(key)

        return {
            "current": current,
            "scopes": [
                {"value": key, "label": self._format_scope_label(key)}
                for key in scope_keys
            ],
        }

    @staticmethod
    def _format_scope_label(scope_key: str) -> str:
        key = str(scope_key or "").strip()
        parts = key.split(":")
        if len(parts) >= 3 and parts[1] == "group":
            return f"{parts[0]}:{':'.join(parts[2:])}"
        return key or "default"

    @staticmethod
    def _is_scope_options_request(target: str) -> bool:
        parts = urlsplit(target)
        return parts.path == "/api/scopes"

    @staticmethod
    def _extract_scope_override(target: str) -> tuple[str, str]:
        parts = urlsplit(target)
        scope_override = ""
        kept_params = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "_scope":
                scope_override = value
            else:
                kept_params.append((key, value))
        cleaned = urlunsplit(("", "", parts.path, urlencode(kept_params), parts.fragment))
        return cleaned, scope_override

    @staticmethod
    def _normalize_method(method: str) -> str:
        normalized = str(method or "GET").strip().upper()
        if normalized not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"unsupported WebUI method: {normalized}")
        return normalized

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            raise ValueError("missing WebUI request URL")
        parts = urlsplit(url)
        if parts.scheme or parts.netloc:
            raise ValueError("absolute WebUI request URL is not allowed")
        path = parts.path or "/"
        if any(segment == ".." for segment in path.split("/")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        if not path.startswith(("/api/", "/v1/", "/healthz", "/readyz")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        if path.startswith(("/api/plug/", "/api/plugin/")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        return urlunsplit(("", "", path, parts.query, ""))

    async def _get_app(self, scope_key: str) -> _EmbeddedWebUIApp:
        if self._closing:
            raise RuntimeError("Memorix WebUI bridge is closing")
        cached = self._apps.get(scope_key)
        if cached is not None:
            return cached

        async with self._lock:
            if self._closing:
                raise RuntimeError("Memorix WebUI bridge is closing")
            cached = self._apps.get(scope_key)
            if cached is not None:
                return cached
            app = await self._create_app(scope_key)
            self._apps[scope_key] = app
            return app

    async def _create_app(self, scope_key: str) -> _EmbeddedWebUIApp:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        server = MemorixServer(plugin_instance=runtime.context)
        app = server.app
        app.state.context = runtime.context
        app.state.scope_key = scope_key
        if self.admin_service is not None:
            app.state.admin_service = self.admin_service
        if not bool(getattr(app.state, "_memorix_v1_router_registered", False)):
            app.include_router(v1_router)
            app.state._memorix_v1_router_registered = True
        if not bool(getattr(app.state, "_memorix_import_guard_registered", False)):
            app.add_middleware(ImportWriteGuardMiddleware)
            app.state._memorix_import_guard_registered = True

        import_task_manager = ImportTaskManager(runtime.context)
        await import_task_manager.start()
        app.state.import_task_manager = import_task_manager

        task_manager = _WebV1TaskManager(runtime.context, import_task_manager=import_task_manager)
        await task_manager.start()
        app.state.task_manager = task_manager

        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://memorix.local")
        logger.info("[memorix] embedded WebUI app ready: scope=%s", scope_key)
        return _EmbeddedWebUIApp(
            scope_key=scope_key,
            server=server,
            client=client,
            task_manager=task_manager,
            import_task_manager=import_task_manager,
        )
