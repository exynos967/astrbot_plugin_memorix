"""AstrBot Plugin Pages bridge for the Memorix WebUI."""

from __future__ import annotations

import asyncio
import datetime
import importlib
import inspect
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from astrbot.api import logger

from ..amemorix.context import activate_context_shims
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
PLUGIN_PAGE_TICKET_UPLOAD_ROUTE = "webui/upload/<ticket_id>"
PLUGIN_PAGE_BUNDLE_DOWNLOAD_ROUTE = "webui/bundle-download"

UPLOAD_TICKET_TTL_SECONDS = 600.0
MAX_ACTIVE_UPLOAD_TICKETS = 256
MAX_BUNDLE_UPLOAD_BYTES = 256 * 1024 * 1024


def _make_json_response(json_response_fn: Any, payload: Dict[str, Any], *, status_code: int = 200) -> Any:
    if status_code == 200:
        return json_response_fn(payload)
    for kw in ("status_code", "status"):
        try:
            return json_response_fn(payload, **{kw: status_code})
        except TypeError:
            continue
    return json_response_fn(payload)


def _get_request_arg(request: Any, key: str, default: str = "") -> str:
    for attr in ("args", "query"):
        mapping = getattr(request, attr, None)
        if mapping is not None and hasattr(mapping, "get"):
            val = mapping.get(key)
            if val is not None:
                return str(val)
    return default


async def _read_upload_bounded(upload: Any, max_bytes: int) -> bytes:
    reader = getattr(upload, "read", None)
    if not callable(reader):
        raise ValueError("无效的上传文件对象")
    try:
        chunk = reader(max_bytes + 1)
    except TypeError:
        chunk = reader()
    if inspect.isawaitable(chunk):
        chunk = await chunk
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        return bytes(chunk)
    return str(chunk or "").encode("utf-8")


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
        # this compatibility manager synchronously. Read directly from _tasks so both
        # bridge-enqueued tasks and AdminService.import_admin tasks are queryable.
        task_key = str(task_id or "").strip()
        if not task_key:
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
        import_options = {
            key: options[key]
            for key in ("source", "input_mode", "chat_log", "narrative_window_size", "narrative_overlap")
            if key in options
        }
        try:
            if mode == "text":
                if isinstance(body, (dict, list)):
                    content = json.dumps(body, ensure_ascii=False)
                else:
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
            with activate_context_shims(self.ctx):
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
            with activate_context_shims(self.ctx):
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
    owns_import_task_manager: bool = True


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
        self._upload_tickets: Dict[str, Dict[str, Any]] = {}
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
        register_web_api(
            f"/{plugin_name}/{PLUGIN_PAGE_TICKET_UPLOAD_ROUTE}",
            self.handle_ticket_upload,
            ["POST"],
            "Memorix ticket-based file and bundle upload",
        )
        register_web_api(
            f"/{plugin_name}/{PLUGIN_PAGE_BUNDLE_DOWNLOAD_ROUTE}",
            self.handle_bundle_download,
            ["GET"],
            "Memorix bundle download",
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
            if app.owns_import_task_manager:
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
            with activate_context_shims(app.task_manager.ctx):
                task = await app.task_manager.enqueue_upload_task([upload], options)
            return json_response({"status": "ok", "data": task})
        except (TypeError, ValueError) as exc:
            return error_response(str(exc))
        except Exception as exc:
            logger.warning("[memorix] file upload failed: %s", exc, exc_info=True)
            return error_response("上传失败，请检查插件日志", status_code=500)

    def _evict_expired_tickets(self, now: Optional[float] = None) -> None:
        current = time.monotonic() if now is None else float(now)
        expired = [
            tid
            for tid, entry in self._upload_tickets.items()
            if float(entry.get("expires_at", 0.0)) <= current
        ]
        for tid in expired:
            self._upload_tickets.pop(tid, None)

    def _resolve_scope_from_url(self, url: str) -> str:
        normalized = self._normalize_url(url)
        _, scope_override = self._extract_scope_override(normalized)
        return self._resolve_scope_key(scope_override)

    def create_upload_ticket(self, *, scope_key: str, body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("upload ticket payload must be a JSON object")
        kind = str(body.get("kind") or "").strip().lower()
        if kind not in {"import", "bundle"}:
            raise ValueError(f"unsupported upload ticket kind: {kind or '<empty>'}")
        raw_options = body.get("options")
        if raw_options is None:
            options: Dict[str, Any] = {}
        elif isinstance(raw_options, dict):
            options = dict(raw_options)
        else:
            raise ValueError("upload ticket options must be a JSON object")

        now = time.monotonic()
        self._evict_expired_tickets(now)
        while len(self._upload_tickets) >= MAX_ACTIVE_UPLOAD_TICKETS:
            oldest_key = min(
                self._upload_tickets,
                key=lambda key: float(self._upload_tickets[key].get("expires_at", 0.0)),
            )
            self._upload_tickets.pop(oldest_key, None)

        ticket_id = uuid.uuid4().hex
        self._upload_tickets[ticket_id] = {
            "ticket_id": ticket_id,
            "scope_key": str(scope_key or "").strip() or "default",
            "kind": kind,
            "options": options,
            "expires_at": now + UPLOAD_TICKET_TTL_SECONDS,
        }
        return {"success": True, "ticket_id": ticket_id}

    def _consume_upload_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        key = str(ticket_id or "").strip()
        ticket = self._upload_tickets.pop(key, None) if key else None
        self._evict_expired_tickets(now)
        if ticket is None or float(ticket.get("expires_at", 0.0)) <= now:
            return None
        return ticket

    async def handle_ticket_upload(self, ticket_id: str = ""):
        web_api = importlib.import_module("astrbot.api.web")

        json_response = web_api.json_response
        request = web_api.request
        upload_cls = getattr(web_api, "PluginUploadFile", None)

        try:
            resolved_ticket_id = str(
                ticket_id
                or (getattr(request, "path_params", None) or {}).get("ticket_id", "")
                or ""
            ).strip()
            if not resolved_ticket_id:
                raw_path = str(getattr(request, "path", "") or "")
                if "/webui/upload/" in raw_path:
                    resolved_ticket_id = raw_path.rsplit("/webui/upload/", 1)[-1].strip("/")

            ticket = self._consume_upload_ticket(resolved_ticket_id)
            if ticket is None:
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": "上传凭证无效或已过期"},
                    status_code=400,
                )

            scope_key = str(ticket["scope_key"])
            kind = str(ticket["kind"])
            options = dict(ticket.get("options") or {})
            app = await self._get_app(scope_key)

            headers = getattr(request, "headers", None)
            content_length = headers.get("content-length") if headers is not None and hasattr(headers, "get") else None

            files = await request.files()
            upload = files.get("file") if hasattr(files, "get") else None
            if upload is None or (
                upload_cls is not None
                and not isinstance(upload, upload_cls)
                and not callable(getattr(upload, "read", None))
            ):
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": "请选择上传文件"},
                    status_code=400,
                )

            if kind == "import":
                limits = app.import_task_manager.get_import_limits()
                max_size = int(limits.get("max_file_size_bytes", 20 * 1024 * 1024))
                allowed_exts = {
                    str(ext).lower()
                    for ext in (limits.get("allowed_extensions") or [".txt", ".md", ".json", ".jsonl"])
                }
                if content_length is not None:
                    parsed_len = int(content_length)
                    if parsed_len < 0 or parsed_len > max_size + 65536:
                        return _make_json_response(
                            json_response,
                            {"status": "error", "message": "文件超过上传大小限制"},
                            status_code=413,
                        )

                raw_filename = str(getattr(upload, "filename", "") or "")
                suffix = Path(raw_filename).suffix.lower()
                custom_name = str(options.get("name") or "").strip()
                if custom_name:
                    custom_suffix = Path(custom_name).suffix.lower()
                    if not custom_suffix and suffix:
                        custom_name = f"{custom_name}{suffix}"
                        custom_suffix = suffix
                    if not suffix:
                        suffix = custom_suffix
                if allowed_exts and suffix not in allowed_exts:
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": f"不支持的文件后缀: {suffix or '(none)'}"},
                        status_code=400,
                    )

                upload_target = upload
                if custom_name and Path(custom_name).suffix.lower() in allowed_exts:
                    class _NamedUploadProxy:
                        def __init__(self, inner: Any, filename: str) -> None:
                            self._inner = inner
                            self.filename = filename

                        def read(self, *args: Any, **kwargs: Any) -> Any:
                            return self._inner.read(*args, **kwargs)

                    upload_target = _NamedUploadProxy(upload, custom_name)

                with activate_context_shims(app.server.plugin):
                    task = await app.task_manager.enqueue_upload_task([upload_target], options)
                return _make_json_response(json_response, {"status": "ok", "data": task})

            if kind == "bundle":
                raw_filename = Path(str(getattr(upload, "filename", "") or "")).name
                if not raw_filename.lower().endswith(".amembundle"):
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": "记忆包必须使用 .amembundle 扩展名"},
                        status_code=400,
                    )
                if content_length is not None:
                    parsed_len = int(content_length)
                    if parsed_len < 0 or parsed_len > MAX_BUNDLE_UPLOAD_BYTES + 65536:
                        return _make_json_response(
                            json_response,
                            {"status": "error", "message": "记忆包超过 256 MiB 上传大小限制"},
                            status_code=413,
                        )

                raw_bytes = await _read_upload_bounded(upload, MAX_BUNDLE_UPLOAD_BYTES)
                if not raw_bytes:
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": "上传的记忆包文件为空"},
                        status_code=400,
                    )
                if len(raw_bytes) > MAX_BUNDLE_UPLOAD_BYTES:
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": "记忆包超过 256 MiB 上传大小限制"},
                        status_code=413,
                    )

                stem = raw_filename[: -len(".amembundle")]
                safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "bundle"
                saved_name = f"{safe_stem}-{uuid.uuid4().hex[:8]}.amembundle"
                bundles_dir = (Path(app.server.plugin.data_dir) / "imports" / "bundles").resolve()
                bundles_dir.mkdir(parents=True, exist_ok=True)
                saved_path = (bundles_dir / saved_name).resolve()
                if saved_path.parent != bundles_dir:
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": "记忆包目标路径越界"},
                        status_code=400,
                    )

                tmp_path = bundles_dir / f".{saved_name}.{uuid.uuid4().hex[:8]}.tmp"
                try:
                    tmp_path.write_bytes(raw_bytes)
                    os.replace(tmp_path, saved_path)
                finally:
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)

                admin_svc = self.admin_service
                temp_admin_svc = False
                if admin_svc is None:
                    from ..services.admin_service import AdminService

                    admin_svc = AdminService(self.runtime_manager)
                    temp_admin_svc = True

                try:
                    with activate_context_shims(app.server.plugin):
                        inspect_res = await admin_svc.bundle_admin(
                            scope_key=scope_key,
                            action="inspect",
                            path=saved_name,
                        )
                except Exception as exc:
                    saved_path.unlink(missing_ok=True)
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": str(exc)},
                        status_code=400,
                    )
                finally:
                    if temp_admin_svc:
                        await admin_svc.close()

                if not isinstance(inspect_res, dict) or not inspect_res.get("success"):
                    saved_path.unlink(missing_ok=True)
                    err_msg = str((inspect_res or {}).get("error") or "记忆包校验失败")
                    return _make_json_response(
                        json_response,
                        {"status": "error", "message": err_msg},
                        status_code=400,
                    )

                return _make_json_response(
                    json_response,
                    {
                        "status": "ok",
                        "data": {
                            "success": True,
                            "path": saved_name,
                            "file_name": saved_name,
                            "inspect": inspect_res,
                        },
                    },
                )

            return _make_json_response(
                json_response,
                {"status": "error", "message": f"不支持的上传类型: {kind}"},
                status_code=400,
            )
        except (TypeError, ValueError) as exc:
            return _make_json_response(
                json_response,
                {"status": "error", "message": str(exc)},
                status_code=400,
            )
        except Exception as exc:
            logger.warning("[memorix] ticket upload failed: %s", exc, exc_info=True)
            return _make_json_response(
                json_response,
                {"status": "error", "message": "上传失败，请检查插件日志"},
                status_code=500,
            )

    async def handle_bundle_download(self):
        web_api = importlib.import_module("astrbot.api.web")

        json_response = web_api.json_response
        request = web_api.request
        file_resp_fn = getattr(web_api, "file_response", None)

        try:
            raw_scope = _get_request_arg(request, "_scope", "")
            raw_file_name = _get_request_arg(request, "file_name", "").strip()
            if (
                not raw_file_name
                or "/" in raw_file_name
                or "\\" in raw_file_name
                or raw_file_name in {".", ".."}
                or Path(raw_file_name).name != raw_file_name
                or not raw_file_name.lower().endswith(".amembundle")
            ):
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": "非法的记忆包文件名"},
                    status_code=400,
                )

            scope_key = self._resolve_scope_key(raw_scope)
            app = await self._get_app(scope_key)
            bundles_dir = (Path(app.server.plugin.data_dir) / "imports" / "bundles").resolve()
            bundles_dir.mkdir(parents=True, exist_ok=True)
            candidate = bundles_dir / raw_file_name
            if candidate.is_symlink():
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": "不允许下载符号链接文件"},
                    status_code=400,
                )
            resolved_path = candidate.resolve()
            if resolved_path.parent != bundles_dir or resolved_path.suffix.lower() != ".amembundle":
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": "记忆包路径超出允许目录"},
                    status_code=400,
                )
            if not resolved_path.is_file():
                return _make_json_response(
                    json_response,
                    {"status": "error", "message": f"记忆包不存在: {raw_file_name}"},
                    status_code=404,
                )

            if callable(file_resp_fn):
                return file_resp_fn(str(resolved_path), filename=resolved_path.name)

            from fastapi.responses import FileResponse

            return FileResponse(
                str(resolved_path),
                filename=resolved_path.name,
                media_type="application/octet-stream",
            )
        except Exception as exc:
            logger.warning("[memorix] bundle download failed: %s", exc, exc_info=True)
            return _make_json_response(
                json_response,
                {"status": "error", "message": str(exc)},
                status_code=500,
            )

    async def dispatch(self, *, method: str, url: str, body: Any = None) -> Any:
        method = self._normalize_method(method)
        target = self._normalize_url(url)
        if self._is_scope_options_request(target):
            return self._scope_options()
        if urlsplit(target).path == "/v1/upload-tickets":
            if method != "POST":
                raise ValueError("upload-tickets only supports POST")
            scope_key = self._resolve_scope_from_url(url)
            return self.create_upload_ticket(scope_key=scope_key, body=body)

        target, scope_override = self._extract_scope_override(target)
        scope_key = self._resolve_scope_key(scope_override)
        app = await self._get_app(scope_key)

        request_kwargs: Dict[str, Any] = {}
        if method != "GET" and body is not None:
            request_kwargs["json"] = body

        with activate_context_shims(app.task_manager.ctx):
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
        with activate_context_shims(runtime.context):
            server = MemorixServer(plugin_instance=runtime.context)
            app = server.app
            app.state.context = runtime.context
            app.state.scope_key = scope_key
            app.state.webui_bridge = self
            if self.admin_service is not None:
                app.state.admin_service = self.admin_service
            if not bool(getattr(app.state, "_memorix_v1_router_registered", False)):
                app.include_router(v1_router)
                app.state._memorix_v1_router_registered = True
            if not bool(getattr(app.state, "_memorix_import_guard_registered", False)):
                app.add_middleware(ImportWriteGuardMiddleware)
                app.state._memorix_import_guard_registered = True

            shared_import_getter = getattr(self.admin_service, "_import_manager", None)
            if callable(shared_import_getter):
                import_task_manager = await shared_import_getter(scope_key, runtime.context)
                owns_import_task_manager = False
            else:
                import_task_manager = ImportTaskManager(runtime.context)
                await import_task_manager.start()
                owns_import_task_manager = True
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
                owns_import_task_manager=owns_import_task_manager,
            )
