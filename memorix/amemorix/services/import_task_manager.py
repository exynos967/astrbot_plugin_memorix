"""Task manager for web import center (/v1/import/*)."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.strategies.chat_log import ChatLogStrategy
from ...core.utils.import_payloads import normalize_paragraph_import_item

from ..common.logging import get_logger
from ..context import AppContext

from .import_service import ImportService

logger = get_logger("A_Memorix.ImportTaskManager")

ALLOWED_FILE_SUFFIXES = {".txt", ".md", ".json"}
ALLOWED_SCAN_ALIASES = {"raw", "plugin_data"}
ALLOWED_KNOWLEDGE_TYPES = {"", "auto", "structured", "narrative", "factual", "mixed"}
TASK_TERMINAL = {"completed", "completed_with_errors", "cancelled", "failed"}


def _now() -> float:
    return time.time()


def _safe_filename(name: str) -> str:
    base = os.path.basename(str(name or "").strip())
    if not base:
        return f"unnamed_{uuid.uuid4().hex[:8]}.txt"
    return base


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


@dataclass
class ImportChunkRecord:
    chunk_id: str
    index: int
    chunk_type: str
    status: str = "queued"
    step: str = "queued"
    retryable: bool = False
    error: str = ""
    progress: float = 0.0
    failed_at: str = ""
    content_preview: str = ""
    updated_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "chunk_type": self.chunk_type,
            "status": self.status,
            "step": self.step,
            "retryable": self.retryable,
            "error": self.error,
            "progress": self.progress,
            "failed_at": self.failed_at,
            "content_preview": self.content_preview,
            "updated_at": self.updated_at,
        }


@dataclass
class ImportFileRecord:
    file_id: str
    name: str
    source_kind: str
    input_mode: str
    status: str = "queued"
    current_step: str = "queued"
    total_chunks: int = 0
    done_chunks: int = 0
    failed_chunks: int = 0
    cancelled_chunks: int = 0
    progress: float = 0.0
    error: str = ""
    chunks: List[ImportChunkRecord] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    temp_path: str = ""
    source_path: str = ""
    inline_content: str = ""
    default_knowledge_type: str = ""
    retry_mode: str = ""
    retry_chunk_indexes: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    default_source: str = ""

    def to_dict(self, include_chunks: bool = False) -> Dict[str, Any]:
        data = {
            "file_id": self.file_id,
            "name": self.name,
            "source_kind": self.source_kind,
            "input_mode": self.input_mode,
            "status": self.status,
            "current_step": self.current_step,
            "total_chunks": self.total_chunks,
            "done_chunks": self.done_chunks,
            "failed_chunks": self.failed_chunks,
            "cancelled_chunks": self.cancelled_chunks,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_path": self.source_path,
            "default_knowledge_type": self.default_knowledge_type,
            "warnings": list(self.warnings),
            "retry_mode": self.retry_mode,
            "retry_chunk_indexes": list(self.retry_chunk_indexes),
        }
        if include_chunks:
            data["chunks"] = [item.to_dict() for item in self.chunks]
        return data


@dataclass
class ImportTaskRecord:
    task_id: str
    source: str
    params: Dict[str, Any]
    status: str = "queued"
    current_step: str = "queued"
    total_chunks: int = 0
    done_chunks: int = 0
    failed_chunks: int = 0
    cancelled_chunks: int = 0
    progress: float = 0.0
    error: str = ""
    files: List[ImportFileRecord] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = field(default_factory=_now)
    retry_parent_task_id: str = ""
    retry_summary: Dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "status": self.status,
            "current_step": self.current_step,
            "total_chunks": self.total_chunks,
            "done_chunks": self.done_chunks,
            "failed_chunks": self.failed_chunks,
            "cancelled_chunks": self.cancelled_chunks,
            "progress": self.progress,
            "error": self.error,
            "file_count": len(self.files),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
            "task_kind": str(self.params.get("task_kind") or self.source),
            "retry_parent_task_id": self.retry_parent_task_id,
            "retry_summary": dict(self.retry_summary),
        }

    def to_detail(self, include_chunks: bool = False) -> Dict[str, Any]:
        data = self.to_summary()
        data["params"] = dict(self.params)
        data["files"] = [item.to_dict(include_chunks=include_chunks) for item in self.files]
        return data


class ImportTaskManager:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.import_service = ImportService(ctx)

        self._tasks: Dict[str, ImportTaskRecord] = {}
        self._task_order: deque[str] = deque()
        self._queue: deque[str] = deque()
        self._active_task_id: Optional[str] = None

        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._last_cleanup_at = 0.0

        self._temp_root = (self.ctx.data_dir / "web_import_tmp").resolve()
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def _cfg(self, key: str, default: Any) -> Any:
        return self.ctx.get_config(key, default)

    def is_enabled(self) -> bool:
        return True

    def _queue_limit(self) -> int:
        return max(1, _coerce_int(self._cfg("web.import.max_queue_size", 20), 20))

    def _max_files(self) -> int:
        return max(1, _coerce_int(self._cfg("web.import.max_files_per_task", 200), 200))

    def _max_file_size_bytes(self) -> int:
        mb = max(1, _coerce_int(self._cfg("web.import.max_file_size_mb", 20), 20))
        return mb * 1024 * 1024

    def get_import_limits(self) -> Dict[str, Any]:
        return {"max_file_size_bytes": self._max_file_size_bytes(), "suffixes": sorted(ALLOWED_FILE_SUFFIXES),
                "task_retention_seconds": 86400}

    def _cleanup_expired_tasks_locked(self) -> None:
        now = _now()
        if now - self._last_cleanup_at < 60:
            return
        self._last_cleanup_at = now
        cutoff = now - 86400
        expired = {key for key, task in self._tasks.items() if task.status in TASK_TERMINAL and task.updated_at < cutoff}
        protected = {Path(file.temp_path).resolve().parent for key, task in self._tasks.items() if key not in expired
                     for file in task.files if file.temp_path}
        for directory in self._temp_root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != self._temp_root:
                continue
            if len(directory.name) != 32 or any(ch not in "0123456789abcdef" for ch in directory.name):
                continue
            if directory in protected or (directory.name not in expired and directory.stat().st_mtime >= cutoff):
                continue
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            if not any(directory.iterdir()):
                directory.rmdir()
        for key in expired:
            self._tasks.pop(key, None)
        self._task_order = deque(key for key in self._task_order if key not in expired)

    def _max_paste_chars(self) -> int:
        return max(1000, _coerce_int(self._cfg("web.import.max_paste_chars", 200000), 200000))

    def _normalize_knowledge_type(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text not in ALLOWED_KNOWLEDGE_TYPES:
            raise ValueError("knowledge_type 仅支持 auto/structured/narrative/factual/mixed")
        if text == "auto":
            return ""
        return text

    def is_write_blocked(self) -> bool:
        if not self._active_task_id:
            return False
        task = self._tasks.get(self._active_task_id)
        if not task:
            return False
        return task.status in {"preparing", "running", "cancel_requested"}

    async def start(self) -> None:
        async with self._lock:
            self._cleanup_expired_tasks_locked()
            self._stopping = False
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._worker_loop(), name="import-task-worker")

    async def stop(self) -> None:
        self._stopping = True
        async with self._lock:
            worker = self._worker_task
            self._worker_task = None
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        async with self._lock:
            for task in self._tasks.values():
                if task.status not in TASK_TERMINAL:
                    self._mark_task_cancelled(task, "插件已停止，请重新提交任务")
            self._queue.clear()
            self._active_task_id = None
            self._cleanup_expired_tasks_locked()

    async def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            await self.start()

    def _pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status not in TASK_TERMINAL)

    def _default_aliases(self) -> Dict[str, str]:
        return {
            "raw": str((self.ctx.data_dir / "raw").resolve()),
            "plugin_data": str(self.ctx.data_dir.resolve()),
        }

    def get_path_aliases(self) -> Dict[str, str]:
        result = self._default_aliases()
        configured = self._cfg("web.import.path_aliases", {})
        if isinstance(configured, dict):
            for alias, raw in configured.items():
                key = str(alias or "").strip()
                val = str(raw or "").strip()
                if not key or not val:
                    continue
                path = Path(val).expanduser()
                if not path.is_absolute():
                    path = (self.ctx.data_dir.resolve() / path).resolve()
                else:
                    path = path.resolve()
                result[key] = str(path)
        return {k: v for k, v in result.items() if k in ALLOWED_SCAN_ALIASES}

    def resolve_path_alias(self, alias: str, relative_path: str = "", *, must_exist: bool = False) -> Path:
        aliases = self.get_path_aliases()
        key = str(alias or "").strip()
        if key not in ALLOWED_SCAN_ALIASES:
            raise ValueError(f"不允许的路径别名: {key}")
        if key not in aliases:
            raise ValueError(f"未知路径别名: {key}")
        root = Path(aliases[key]).resolve()
        rel = str(relative_path or "").strip().replace("\\", "/")
        if rel.startswith(("/", "\\", "//")):
            raise ValueError("relative_path 不能为绝对路径")
        if ":" in rel:
            raise ValueError("relative_path 不允许包含盘符")
        candidate = (root / rel).resolve() if rel else root
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("路径越界：relative_path 超出白名单目录") from exc
        if must_exist and not candidate.exists():
            raise ValueError(f"路径不存在: {candidate}")
        return candidate

    async def resolve_path_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        alias = str(payload.get("alias") or "").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        must_exist = _coerce_bool(payload.get("must_exist"), True)
        resolved = self.resolve_path_alias(alias, relative_path, must_exist=must_exist)
        return {
            "alias": alias,
            "relative_path": relative_path,
            "resolved_path": str(resolved),
            "exists": resolved.exists(),
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
        }

    def _normalize_params(self, payload: Dict[str, Any], *, task_kind: str) -> Dict[str, Any]:
        mode = str(payload.get("input_mode", "text") or "text").strip().lower()
        if mode not in {"text", "json"}:
            raise ValueError("input_mode 必须为 text 或 json")
        knowledge_type = self._normalize_knowledge_type(payload.get("knowledge_type", ""))
        window_size = int(payload.get("narrative_window_size", 1600))
        overlap = int(payload.get("narrative_overlap", 400))
        if window_size < 200 or window_size > 100000 or not 0 <= overlap < window_size:
            raise ValueError("narrative_window_size 必须为 200-100000，overlap 必须小于窗口且非负")
        return {
            "task_kind": task_kind,
            "source": str(payload.get("source") or "").strip()[:512],
            "input_mode": mode,
            "knowledge_type": knowledge_type,
            "chat_log": _coerce_bool(payload.get("chat_log"), False),
            "narrative_window_size": window_size,
            "narrative_overlap": overlap,
            "glob": str(payload.get("glob", "*") or "*").strip() or "*",
            "recursive": _coerce_bool(payload.get("recursive"), True),
        }

    async def create_upload_task(self, files: List[Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_enabled():
            raise ValueError("导入功能未启用")
        if not files:
            raise ValueError("至少需要上传一个文件")
        params = self._normalize_params(payload or {}, task_kind="upload")
        if len(files) > self._max_files():
            raise ValueError(f"单任务文件数超过上限: {self._max_files()}")

        async with self._lock:
            if self._pending_count() >= self._queue_limit():
                raise ValueError("导入任务队列已满，请稍后重试")
            task = ImportTaskRecord(task_id=uuid.uuid4().hex, source="upload", params=params)
            task_dir = self._temp_root / task.task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            try:
                for idx, uploaded in enumerate(files):
                    name = _safe_filename(getattr(uploaded, "filename", f"file_{idx}.txt"))
                    suffix = Path(name).suffix.lower()
                    if suffix not in ALLOWED_FILE_SUFFIXES:
                        raise ValueError(f"不支持的文件后缀: {suffix or '(none)'}")
                    content = await uploaded.read(self._max_file_size_bytes() + 1)
                    if len(content) > self._max_file_size_bytes():
                        raise ValueError(f"文件超过大小限制({self._cfg('web.import.max_file_size_mb', 20)}MB): {name}")
                    file_id = uuid.uuid4().hex
                    temp_path = task_dir / f"{file_id}_{name}"
                    temp_path.write_bytes(content)
                    mode = "json" if suffix == ".json" else params["input_mode"]
                    task.files.append(
                        ImportFileRecord(
                            file_id=file_id,
                            name=name,
                            source_kind="upload",
                            input_mode=mode,
                            temp_path=str(temp_path),
                            default_knowledge_type=str(params.get("knowledge_type", "")),
                        default_source=str(params.get("source", "")),
                        )
                    )
            except BaseException:
                for path in task_dir.iterdir():
                    if path.is_file():
                        path.unlink()
                task_dir.rmdir()
                raise
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)
        await self._ensure_worker()
        return task.to_summary()

    async def create_paste_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_enabled():
            raise ValueError("导入功能未启用")
        payload = payload or {}
        params = self._normalize_params(payload, task_kind="paste")
        params["input_mode"] = "text"
        content = str(payload.get("content") or "")
        if not content.strip():
            raise ValueError("content 不能为空")
        if len(content) > self._max_paste_chars():
            raise ValueError(f"粘贴内容超过限制: {self._max_paste_chars()} 字符")
        name = _safe_filename(payload.get("name") or f"paste_{int(_now())}.txt")

        async with self._lock:
            if self._pending_count() >= self._queue_limit():
                raise ValueError("导入任务队列已满，请稍后重试")
            task = ImportTaskRecord(task_id=uuid.uuid4().hex, source="paste", params=params)
            task.files.append(
                ImportFileRecord(
                    file_id=uuid.uuid4().hex,
                    name=name,
                    source_kind="paste",
                    input_mode="text",
                    inline_content=content,
                    default_knowledge_type=str(params.get("knowledge_type", "")),
                        default_source=str(params.get("source", "")),
                )
            )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)
        await self._ensure_worker()
        return task.to_summary()

    def _scan_files(self, base: Path, pattern: str, recursive: bool) -> List[Path]:
        if base.is_file():
            return [base.resolve()] if base.suffix.lower() in ALLOWED_FILE_SUFFIXES else []
        iterator = base.rglob(pattern) if recursive else base.glob(pattern)
        out = [p.resolve() for p in iterator if p.is_file() and p.suffix.lower() in ALLOWED_FILE_SUFFIXES]
        return sorted(out)

    async def create_raw_scan_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_enabled():
            raise ValueError("导入功能未启用")
        payload = payload or {}
        params = self._normalize_params(payload, task_kind="raw_scan")
        alias = str(payload.get("alias") or "raw").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        scan_root = self.resolve_path_alias(alias, relative_path, must_exist=True)
        files = self._scan_files(scan_root, params["glob"], params["recursive"])
        if not files:
            raise ValueError("未找到可导入文件")
        if len(files) > self._max_files():
            raise ValueError(f"单任务文件数超过上限: {self._max_files()}")

        async with self._lock:
            if self._pending_count() >= self._queue_limit():
                raise ValueError("导入任务队列已满，请稍后重试")
            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="raw_scan",
                params={"alias": alias, "relative_path": relative_path, **params},
            )
            for path in files:
                mode = "json" if path.suffix.lower() == ".json" else params["input_mode"]
                task.files.append(
                    ImportFileRecord(
                        file_id=uuid.uuid4().hex,
                        name=path.name,
                        source_kind="raw_scan",
                        input_mode=mode,
                        source_path=str(path),
                        default_knowledge_type=str(params.get("knowledge_type", "")),
                        default_source=str(params.get("source", "")),
                    )
                )
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)
        await self._ensure_worker()
        return task.to_summary()

    async def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(200, int(limit)))
        async with self._lock:
            ids = list(self._task_order)[:lim]
            return [self._tasks[i].to_summary() for i in ids if i in self._tasks]

    async def get_task(self, task_id: str, include_chunks: bool = False) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.to_detail(include_chunks=include_chunks) if task else None

    async def get_chunks(self, task_id: str, file_id: str, offset: int = 0, limit: int = 100) -> Optional[Dict[str, Any]]:
        off = max(0, int(offset))
        lim = max(1, min(500, int(limit)))
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return None
            items = file_record.chunks[off : off + lim]
            return {
                "task_id": task_id,
                "file_id": file_id,
                "offset": off,
                "limit": lim,
                "total": len(file_record.chunks),
                "items": [item.to_dict() for item in items],
            }

    async def cancel_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in TASK_TERMINAL:
                return task.to_summary()
            task.status = "cancel_requested"
            task.current_step = "cancel_requested"
            task.updated_at = _now()
            return task.to_summary()

    async def retry_failed(self, task_id: str, overrides: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        async with self._lock:
            base = self._tasks.get(task_id)
            if not base:
                return None
            if base.status not in {"completed_with_errors", "failed", "cancelled"}:
                raise ValueError("仅失败/部分失败/已取消任务支持重试")

            retry_files: List[ImportFileRecord] = []
            failed_chunks = 0
            for item in base.files:
                idxs = [c.index for c in item.chunks if c.status == "failed" and c.retryable]
                if idxs:
                    failed_chunks += len(idxs)
                    retry_files.append(
                        ImportFileRecord(
                            file_id=uuid.uuid4().hex,
                            name=item.name,
                            source_kind=item.source_kind,
                            input_mode=item.input_mode,
                            temp_path=item.temp_path,
                            source_path=item.source_path,
                            inline_content=item.inline_content,
                            default_knowledge_type=item.default_knowledge_type,
                            default_source=item.default_source,
                            retry_mode="chunk",
                            retry_chunk_indexes=sorted(set(idxs)),
                        )
                    )
                elif item.status == "failed":
                    retry_files.append(
                        ImportFileRecord(
                            file_id=uuid.uuid4().hex,
                            name=item.name,
                            source_kind=item.source_kind,
                            input_mode=item.input_mode,
                            temp_path=item.temp_path,
                            source_path=item.source_path,
                            inline_content=item.inline_content,
                            default_knowledge_type=item.default_knowledge_type,
                            default_source=item.default_source,
                            retry_mode="file",
                        )
                    )
            if not retry_files:
                raise ValueError("未找到可重试失败项")
            if self._pending_count() >= self._queue_limit():
                raise ValueError("导入任务队列已满，请稍后重试")

            params = dict(base.params)
            override_input_mode: Optional[str] = None
            override_knowledge_type: Optional[str] = None
            for key, value in (overrides or {}).items():
                if value is not None:
                    if key == "knowledge_type":
                        normalized = self._normalize_knowledge_type(value)
                        params[key] = normalized
                        override_knowledge_type = normalized
                    elif key == "input_mode":
                        mode = str(value).strip().lower()
                        if mode not in {"text", "json"}:
                            raise ValueError("input_mode 必须为 text 或 json")
                        params[key] = mode
                        override_input_mode = mode
                    else:
                        params[key] = value
            if override_input_mode is not None:
                for item in retry_files:
                    item.input_mode = override_input_mode
            if override_knowledge_type is not None:
                for item in retry_files:
                    item.default_knowledge_type = override_knowledge_type
            params["task_kind"] = "retry_failed"
            params = self._normalize_params(params, task_kind="retry_failed")
            if any(params[key] != base.params.get(key, params[key]) for key in ("input_mode", "chat_log", "narrative_window_size", "narrative_overlap")):
                for item in retry_files:
                    item.retry_mode = "file"
                    item.retry_chunk_indexes = []

            task = ImportTaskRecord(
                task_id=uuid.uuid4().hex,
                source="retry",
                params=params,
                retry_parent_task_id=base.task_id,
                retry_summary={
                    "retry_mode": "chunk_first",
                    "failed_chunks": failed_chunks,
                    "retry_files": len(retry_files),
                },
            )
            task.files = retry_files
            self._tasks[task.task_id] = task
            self._task_order.appendleft(task.task_id)
            self._queue.append(task.task_id)
        await self._ensure_worker()
        return task.to_summary()

    async def _worker_loop(self) -> None:
        while not self._stopping:
            task_id = ""
            async with self._lock:
                try:
                    self._cleanup_expired_tasks_locked()
                except OSError as exc:
                    logger.warning("导入临时文件回收失败: %s", exc)
                if self._queue:
                    task_id = self._queue.popleft()
                    self._active_task_id = task_id
            if not task_id:
                await asyncio.sleep(0.2)
                continue

            try:
                await self._process_task(task_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Import worker failed task=%s: %s", task_id, exc, exc_info=True)
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task:
                        task.status = "failed"
                        task.current_step = "failed"
                        task.error = str(exc)
                        task.finished_at = _now()
                        task.updated_at = _now()
            finally:
                async with self._lock:
                    if self._active_task_id == task_id:
                        self._active_task_id = None

    async def _process_task(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status == "cancel_requested":
                self._mark_task_cancelled(task, "任务已取消")
                return
            task.status = "preparing"
            task.current_step = "preparing"
            task.started_at = _now()
            task.updated_at = _now()

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status == "cancel_requested":
                self._mark_task_cancelled(task, "任务已取消")
                return
            task.status = "running"
            task.current_step = "running"
            task.updated_at = _now()

        for file_record in list(task.files):
            if await self._is_cancel_requested(task_id):
                await self._cancel_file(task_id, file_record.file_id, "任务已取消")
                continue
            try:
                await self._process_file(task_id, file_record)
            except Exception as exc:
                await self._fail_file(task_id, file_record.file_id, str(exc))

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status == "cancel_requested":
                self._mark_task_cancelled(task, "任务已取消")
            elif task.failed_chunks > 0:
                task.status = "completed_with_errors"
                task.current_step = "completed"
            else:
                task.status = "completed"
                task.current_step = "completed"
            task.finished_at = _now()
            task.updated_at = _now()

        try:
            await asyncio.gather(
                asyncio.to_thread(self.ctx.vector_store.save),
                asyncio.to_thread(self.ctx.graph_store.save),
            )
        except Exception as exc:
            logger.warning("save after task failed task=%s err=%s", task_id, exc)

    async def _process_file(self, task_id: str, file_record: ImportFileRecord) -> None:
        content = self._load_content(file_record)
        if not content.strip():
            raise ValueError("文件内容为空")

        await self._set_file_state(task_id, file_record.file_id, "splitting", "splitting")
        units = self._build_units(content, file_record, self._tasks[task_id].params)
        await self._register_chunks(task_id, file_record.file_id, units)
        await self._set_file_state(task_id, file_record.file_id, "writing", "writing")

        for idx, unit in enumerate(units):
            chunk_id = f"{file_record.file_id}_{unit.get('index', idx)}"
            if await self._is_cancel_requested(task_id):
                await self._cancel_chunk(task_id, file_record.file_id, chunk_id, "任务已取消")
                continue
            await self._set_chunk_state(task_id, file_record.file_id, chunk_id, "writing", "writing", 0.7)
            try:
                await self._persist_unit(file_record, unit)
                await self._complete_chunk(task_id, file_record.file_id, chunk_id)
            except Exception as exc:
                await self._fail_chunk(task_id, file_record.file_id, chunk_id, str(exc))

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            cur = next((f for f in task.files if f.file_id == file_record.file_id), None)
            if not cur:
                return
            if cur.failed_chunks > 0:
                cur.status = "failed"
                cur.current_step = "failed"
                if not cur.error:
                    cur.error = f"存在失败分块: {cur.failed_chunks}"
            elif task.status == "cancel_requested":
                cur.status = "cancelled"
                cur.current_step = "cancelled"
            else:
                cur.status = "completed"
                cur.current_step = "completed"
            cur.updated_at = _now()
            self._recompute_task(task)

    def _load_content(self, file_record: ImportFileRecord) -> str:
        if file_record.inline_content:
            return file_record.inline_content
        if file_record.temp_path:
            path = Path(file_record.temp_path)
            if not path.exists():
                raise FileNotFoundError(f"临时文件不存在: {path}")
            return path.read_text(encoding="utf-8", errors="replace")
        if file_record.source_path:
            path = Path(file_record.source_path)
            if not path.exists():
                raise FileNotFoundError(f"源文件不存在: {path}")
            if path.stat().st_size > self._max_file_size_bytes():
                raise ValueError(f"文件超过大小限制({self._cfg('web.import.max_file_size_mb', 20)}MB): {path.name}")
            return path.read_text(encoding="utf-8", errors="replace")
        raise ValueError("无法读取文件内容")

    def _split_text(self, text: str, max_len: int = 700) -> List[str]:
        out: List[str] = []
        for para in str(text or "").replace("\r\n", "\n").split("\n\n"):
            line = para.strip()
            if not line:
                continue
            if len(line) <= max_len:
                out.append(line)
            else:
                for i in range(0, len(line), max_len):
                    out.append(line[i : i + max_len])
        return out or [str(text or "").strip()]

    def _build_units(self, content: str, file_record: ImportFileRecord, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        params = params or {}
        if file_record.input_mode != "json":
            if params.get("chat_log"):
                strategy = ChatLogStrategy(file_record.name, params["narrative_window_size"], params["narrative_overlap"])
                chunks = [chunk.chunk.text for chunk in strategy.split(content)]
                file_record.warnings = [strategy.split_warning] if strategy.split_warning else []
                if strategy.oversized_message_count:
                    file_record.warnings.append(f"保留了 {strategy.oversized_message_count} 条超过窗口的完整消息")
            else:
                chunks = self._split_text(content)
            payloads: List[Dict[str, Any]] = []
            for index, chunk in enumerate(chunks):
                if file_record.retry_mode == "chunk" and index not in file_record.retry_chunk_indexes:
                    continue
                para_payload: Dict[str, Any] = {"content": chunk}
                if file_record.default_knowledge_type:
                    para_payload["knowledge_type"] = file_record.default_knowledge_type
                payloads.append({"kind": "paragraph", "payload": para_payload, "index": index})
            return payloads

        data = json.loads(content)
        units: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            for p in data.get("paragraphs", []):
                if isinstance(p, str):
                    units.append({"kind": "paragraph", "payload": {"content": p}})
                elif isinstance(p, dict):
                    units.append({"kind": "paragraph", "payload": p})
            for rel in data.get("relations", []):
                if isinstance(rel, dict):
                    units.append({"kind": "relation", "payload": rel})
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    units.append({"kind": "paragraph", "payload": {"content": item}})
                elif isinstance(item, dict) and {"subject", "predicate", "object"} <= set(item.keys()):
                    units.append({"kind": "relation", "payload": item})
                elif isinstance(item, dict):
                    units.append({"kind": "paragraph", "payload": item})
        if not units:
            units.append({"kind": "json_blob", "payload": data})

        for index, unit in enumerate(units):
            unit["index"] = index
        if file_record.retry_mode == "chunk" and file_record.retry_chunk_indexes:
            keep = set(file_record.retry_chunk_indexes)
            units = [v for i, v in enumerate(units) if i in keep]
        return units

    async def _persist_unit(self, file_record: ImportFileRecord, unit: Dict[str, Any]) -> None:
        kind = str(unit.get("kind", "")).strip()
        payload = unit.get("payload")
        if kind == "paragraph":
            data = payload if isinstance(payload, dict) else {"content": str(payload or "")}
            raw_knowledge_type = data.get("knowledge_type")
            knowledge_type = (
                str(raw_knowledge_type).strip()
                if raw_knowledge_type is not None and str(raw_knowledge_type).strip()
                else str(file_record.default_knowledge_type or "")
            )
            await self.import_service.import_paragraph(
                **{key: value for key, value in normalize_paragraph_import_item(
                    {**data, "knowledge_type": knowledge_type or None},
                    default_source=file_record.default_source or f"{file_record.source_kind}:{file_record.name}",
                ).items() if key in {"content", "source", "knowledge_type", "time_meta", "person_ids"}},
            )
            return
        if kind == "relation":
            data = payload if isinstance(payload, dict) else {}
            await self.import_service.import_relation(
                subject=str(data.get("subject", "")),
                predicate=str(data.get("predicate", "")),
                obj=str(data.get("object", "")),
                confidence=float(data.get("confidence", 1.0)),
                source_paragraph=str(data.get("source_paragraph", "")),
            )
            return
        await self.import_service.import_json(payload)

    async def _register_chunks(self, task_id: str, file_id: str, units: List[Dict[str, Any]]) -> None:
        chunks: List[ImportChunkRecord] = []
        for i, unit in enumerate(units):
            payload = unit.get("payload")
            if isinstance(payload, dict):
                preview = str(payload.get("content", "") or payload.get("subject", ""))[:120]
            else:
                preview = str(payload or "")[:120]
            chunks.append(
                ImportChunkRecord(
                    chunk_id=f"{file_id}_{unit.get('index', i)}",
                    index=int(unit.get("index", i)),
                    chunk_type=str(unit.get("kind", "unknown")),
                    content_preview=preview,
                )
            )

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            file_record.chunks = chunks
            file_record.total_chunks = len(chunks)
            file_record.done_chunks = 0
            file_record.failed_chunks = 0
            file_record.cancelled_chunks = 0
            file_record.progress = 0.0 if chunks else 1.0
            file_record.updated_at = _now()
            self._recompute_task(task)

    async def _set_file_state(self, task_id: str, file_id: str, status: str, step: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            file_record.status = status
            file_record.current_step = step
            file_record.updated_at = _now()
            task.updated_at = _now()

    async def _fail_file(self, task_id: str, file_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            file_record.status = "failed"
            file_record.current_step = "failed"
            file_record.error = str(error)
            file_record.updated_at = _now()
            task.updated_at = _now()
            self._recompute_task(task)

    async def _cancel_file(self, task_id: str, file_id: str, reason: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            file_record.status = "cancelled"
            file_record.current_step = "cancelled"
            file_record.error = reason
            for chunk in file_record.chunks:
                if chunk.status in {"completed", "failed", "cancelled"}:
                    continue
                chunk.status = "cancelled"
                chunk.step = "cancelled"
                chunk.retryable = False
                chunk.error = reason
                chunk.progress = 1.0
                chunk.updated_at = _now()
                file_record.cancelled_chunks += 1
            file_record.progress = self._ratio(file_record.done_chunks + file_record.failed_chunks + file_record.cancelled_chunks, file_record.total_chunks)
            file_record.updated_at = _now()
            task.updated_at = _now()
            self._recompute_task(task)

    async def _set_chunk_state(self, task_id: str, file_id: str, chunk_id: str, status: str, step: str, progress: float) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            chunk = next((c for c in file_record.chunks if c.chunk_id == chunk_id), None)
            if not chunk:
                return
            chunk.status = status
            chunk.step = step
            chunk.progress = max(0.0, min(1.0, float(progress)))
            chunk.updated_at = _now()

    async def _complete_chunk(self, task_id: str, file_id: str, chunk_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            chunk = next((c for c in file_record.chunks if c.chunk_id == chunk_id), None)
            if not chunk or chunk.status == "completed":
                return
            chunk.status = "completed"
            chunk.step = "completed"
            chunk.retryable = False
            chunk.error = ""
            chunk.progress = 1.0
            chunk.updated_at = _now()
            file_record.done_chunks += 1
            file_record.progress = self._ratio(file_record.done_chunks + file_record.failed_chunks + file_record.cancelled_chunks, file_record.total_chunks)
            file_record.updated_at = _now()
            self._recompute_task(task)

    async def _fail_chunk(self, task_id: str, file_id: str, chunk_id: str, error: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            chunk = next((c for c in file_record.chunks if c.chunk_id == chunk_id), None)
            if not chunk or chunk.status == "failed":
                return
            chunk.status = "failed"
            chunk.step = "failed"
            chunk.failed_at = "writing"
            chunk.retryable = bool(file_record.input_mode == "text")
            chunk.error = str(error)
            chunk.progress = 1.0
            chunk.updated_at = _now()
            file_record.failed_chunks += 1
            if not file_record.error:
                file_record.error = str(error)
            file_record.progress = self._ratio(file_record.done_chunks + file_record.failed_chunks + file_record.cancelled_chunks, file_record.total_chunks)
            file_record.updated_at = _now()
            self._recompute_task(task)

    async def _cancel_chunk(self, task_id: str, file_id: str, chunk_id: str, reason: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            file_record = next((f for f in task.files if f.file_id == file_id), None)
            if not file_record:
                return
            chunk = next((c for c in file_record.chunks if c.chunk_id == chunk_id), None)
            if not chunk or chunk.status == "cancelled":
                return
            chunk.status = "cancelled"
            chunk.step = "cancelled"
            chunk.retryable = False
            chunk.error = reason
            chunk.progress = 1.0
            chunk.updated_at = _now()
            file_record.cancelled_chunks += 1
            file_record.progress = self._ratio(file_record.done_chunks + file_record.failed_chunks + file_record.cancelled_chunks, file_record.total_chunks)
            file_record.updated_at = _now()
            self._recompute_task(task)

    async def _is_cancel_requested(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return True
            return task.status == "cancel_requested"

    def _ratio(self, done: int, total: int) -> float:
        if total <= 0:
            return 1.0
        return max(0.0, min(1.0, float(done) / float(total)))

    def _recompute_task(self, task: ImportTaskRecord) -> None:
        total = sum(item.total_chunks for item in task.files)
        done = sum(item.done_chunks for item in task.files)
        failed = sum(item.failed_chunks for item in task.files)
        cancelled = sum(item.cancelled_chunks for item in task.files)
        task.total_chunks = total
        task.done_chunks = done
        task.failed_chunks = failed
        task.cancelled_chunks = cancelled
        task.progress = self._ratio(done + failed + cancelled, total)
        task.updated_at = _now()

    def _mark_task_cancelled(self, task: ImportTaskRecord, reason: str) -> None:
        for file_record in task.files:
            if file_record.status in {"completed", "failed", "cancelled"}:
                continue
            file_record.status = "cancelled"
            file_record.current_step = "cancelled"
            file_record.error = reason
            for chunk in file_record.chunks:
                if chunk.status in {"completed", "failed", "cancelled"}:
                    continue
                chunk.status = "cancelled"
                chunk.step = "cancelled"
                chunk.retryable = False
                chunk.error = reason
                chunk.progress = 1.0
                chunk.updated_at = _now()
                file_record.cancelled_chunks += 1
            file_record.progress = self._ratio(file_record.done_chunks + file_record.failed_chunks + file_record.cancelled_chunks, file_record.total_chunks)
            file_record.updated_at = _now()
        task.status = "cancelled"
        task.current_step = "cancelled"
        task.finished_at = _now()
        task.updated_at = _now()
        self._recompute_task(task)
