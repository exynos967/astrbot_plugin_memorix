"""
MemoryEngineFacade: Unified Domain Facade backed by ScopeRuntimeManager.
Provides clean lifecycle management, search, ingestion, and admin dispatching
over a single shared AppContext per scope (zero split-brain stores).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..amemorix.services.feedback_service import FeedbackService
from ..amemorix.services.fuzzy_modify_service import FuzzyModifyService
from ..app_context import ScopeRuntime, ScopeRuntimeManager
from ..services.admin_service import AdminService
from ..services.ingest_service import IngestService
from ..services.query_service import QueryService
from .host_shims import config_shim, install_host_shims, llm_shim

install_host_shims()


@dataclass
class MemoryItem:
    """标准记忆项实体，支持对象属性访问与字典索引。"""

    content: str
    score: float = 0.0
    source: str = ""
    timestamp: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    item_type: str = "paragraph"
    hash: str = ""

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.metadata.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.metadata.get(key, default)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        content = str(
            data.get("content")
            or data.get("text")
            or data.get("summary")
            or data.get("title")
            or ""
        )
        score = float(data.get("score", 0.0) or 0.0)
        source = str(data.get("source") or (data.get("metadata") or {}).get("source") or "")
        ts = data.get("timestamp")
        if ts is None:
            ts = data.get("event_time")
        if ts is None:
            ts = data.get("created_at")
        meta = dict(data.get("metadata") or {})
        item_type = str(
            data.get("item_type")
            or data.get("result_type")
            or data.get("type")
            or ("episode" if "episode_id" in data else "paragraph")
        )
        h = str(data.get("hash") or data.get("hash_value") or data.get("episode_id") or data.get("id") or "")
        return cls(
            content=content,
            score=score,
            source=source,
            timestamp=float(ts) if isinstance(ts, (int, float)) else None,
            metadata=meta,
            item_type=item_type,
            hash=h,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "item_type": self.item_type,
            "hash": self.hash,
        }


@dataclass
class IngestResult:
    """消息或事实摄入结果。"""

    success: bool
    hash: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "hash": self.hash,
            "error": self.error,
            "metadata": self.metadata,
        }


def _normalize_query_bound(raw: Optional[str | float | int]) -> Optional[str]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw)).strftime("%Y/%m/%d %H:%M")
    return str(raw).strip() or None


class MemoryEngineFacade:
    """统一基于 ScopeRuntimeManager 的领域门面，与插件领域服务共享同一作用域存储实例。"""

    def __init__(
        self,
        data_dir: Path | str,
        config: Optional[Dict[str, Any]] = None,
        provider_bridge: Optional[Any] = None,
        scope_key: str = "default",
        *,
        runtime_manager: Optional[ScopeRuntimeManager] = None,
        ingest_service: Optional[IngestService] = None,
        query_service: Optional[QueryService] = None,
        admin_service: Optional[AdminService] = None,
        owns_runtime_manager: Optional[bool] = None,
    ) -> None:
        self.scope_key = str(scope_key or "").strip() or "default"
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})
        self.provider_bridge = provider_bridge

        install_host_shims(
            config=self.config,
            provider_bridge=self.provider_bridge,
            scope_local=True,
        )

        if runtime_manager is None:
            self._runtime_manager = ScopeRuntimeManager(
                plugin_name="astrbot_plugin_memorix",
                plugin_config=self.config,
                exact_scope_dir=self.data_dir,
                provider_bridge=self.provider_bridge,
            )
            self._owns_runtime_manager = True if owns_runtime_manager is None else bool(owns_runtime_manager)
        else:
            self._runtime_manager = runtime_manager
            self._owns_runtime_manager = bool(owns_runtime_manager)

        self._ingest_service = ingest_service or IngestService(self._runtime_manager, self.config)
        self._query_service = query_service or QueryService(self._runtime_manager)
        self._owns_admin_service = admin_service is None
        if admin_service is not None:
            self._admin_service = admin_service
        else:
            feedback_svc = FeedbackService(
                self._runtime_manager,
                self.config,
                ingest_service=self._ingest_service,
            )
            fuzzy_svc = FuzzyModifyService(self._runtime_manager, self.config)
            self._admin_service = AdminService(
                self._runtime_manager,
                feedback_service=feedback_svc,
                fuzzy_modify_service=fuzzy_svc,
            )

        self._runtime: Optional[ScopeRuntime] = None
        self._initialized = False

    @property
    def runtime(self) -> Optional[ScopeRuntime]:
        return self._runtime

    @property
    def context(self) -> Any:
        return self._runtime.context if self._runtime is not None else None

    @property
    def kernel(self) -> Any:
        return self.context

    @property
    def metadata_store(self) -> Any:
        return getattr(self.context, "metadata_store", None)

    @property
    def vector_store(self) -> Any:
        return getattr(self.context, "vector_store", None)

    @property
    def paragraph_vector_store(self) -> Any:
        return getattr(self.context, "paragraph_vector_store", None)

    @property
    def graph_vector_store(self) -> Any:
        return getattr(self.context, "graph_vector_store", None)

    @property
    def graph_store(self) -> Any:
        return getattr(self.context, "graph_store", None)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        """获取或初始化当前作用域运行时，完成 Schema 29 迁移与后台自愈任务挂载。"""
        if self._initialized and self._runtime is not None:
            return
        self._runtime = await self._runtime_manager.get_runtime(self.scope_key)
        self.data_dir = Path(self._runtime.context.data_dir).resolve()
        self._initialized = True

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "search",
        chat_id: str = "",
        shared_chat_ids: Sequence[str] = (),
        person_id: str = "",
        time_start: Optional[str | float] = None,
        time_end: Optional[str | float] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
        **kwargs: Any,
    ) -> List[MemoryItem]:
        """统一检索入口：支持 search/time/hybrid/episode/aggregate 5 种模式。"""
        del shared_chat_ids
        if not self._initialized:
            await self.initialize()

        normalized_mode = str(mode or "search").strip().lower() or "search"
        stream_id = str(chat_id or kwargs.get("stream_id", "") or "").strip() or None
        source = str(kwargs.get("source", "") or "").strip() or None
        strict_source = bool(kwargs.get("strict_source", False))
        time_from = _normalize_query_bound(time_start)
        time_to = _normalize_query_bound(time_end)
        person = str(person_id or kwargs.get("person", "") or "").strip() or None

        ctx_cfg = self._runtime.context.config if self._runtime is not None else self.config
        with config_shim.use_scope_config(ctx_cfg), llm_shim.use_provider_bridge(self.provider_bridge):
            if normalized_mode == "time":
                res = await self._query_service.time_search(
                    scope_key=self.scope_key,
                    query=query,
                    time_from=time_from,
                    time_to=time_to,
                    person=person,
                    source=source,
                    top_k=limit,
                    stream_id=stream_id,
                    group_id=group_id or None,
                    user_id=user_id or None,
                    enforce_chat_filter=respect_filter,
                )
            elif normalized_mode in {"hybrid", "auto"}:
                res = await self._query_service.auto_search(
                    scope_key=self.scope_key,
                    query=query,
                    top_k=limit,
                    stream_id=stream_id,
                    group_id=group_id or None,
                    user_id=user_id or None,
                    source=source,
                    strict_source=strict_source,
                    enforce_chat_filter=respect_filter,
                )
            elif normalized_mode == "episode":
                res = await self._query_service.episode(
                    scope_key=self.scope_key,
                    query=query,
                    time_from=time_from,
                    time_to=time_to,
                    person=person,
                    source=source,
                    top_k=limit,
                    stream_id=stream_id,
                    group_id=group_id or None,
                    user_id=user_id or None,
                    enforce_chat_filter=respect_filter,
                )
            elif normalized_mode == "aggregate":
                res = await self._query_service.aggregate(
                    scope_key=self.scope_key,
                    query=query,
                    time_from=time_from,
                    time_to=time_to,
                    person=person,
                    source=source,
                    top_k=limit,
                    stream_id=stream_id,
                    group_id=group_id or None,
                    user_id=user_id or None,
                    enforce_chat_filter=respect_filter,
                )
            else:
                res = await self._query_service.search(
                    scope_key=self.scope_key,
                    query=query,
                    top_k=limit,
                    stream_id=stream_id,
                    group_id=group_id or None,
                    user_id=user_id or None,
                    source=source,
                    strict_source=strict_source,
                    enforce_chat_filter=respect_filter,
                )

        if not isinstance(res, dict):
            return []
        raw_hits = res.get("results")
        if raw_hits is None:
            raw_hits = res.get("mixed_results")
        if raw_hits is None:
            raw_hits = res.get("hits", [])
        return [MemoryItem.from_dict(h) if isinstance(h, dict) else h for h in (raw_hits or [])]

    async def ingest_message(
        self,
        sender_id: str,
        sender_name: str,
        content: str,
        role: str = "user",
        *,
        chat_id: str = "",
        timestamp: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> IngestResult:
        """将聊天对话或事实文本直接摄入当前作用域共享运行时。"""
        if not self._initialized:
            await self.initialize()

        external_id = str(kwargs.get("external_id") or f"msg_{uuid.uuid4().hex[:12]}")
        now = float(timestamp) if timestamp is not None else time.time()
        combined_text = f"{sender_name}: {content}" if sender_name and not content.startswith(sender_name) else content
        person_ids = [sender_id] if sender_id else []
        merged_meta = {"role": str(role or "user"), **(metadata or {})}

        try:
            ctx_cfg = self._runtime.context.config if self._runtime is not None else self.config
            with config_shim.use_scope_config(ctx_cfg), llm_shim.use_provider_bridge(self.provider_bridge):
                res = await self._ingest_service.ingest_text(
                    scope_key=self.scope_key,
                    external_id=external_id,
                    source_type="chat_stream",
                    text=combined_text,
                    chat_id=chat_id,
                    person_ids=person_ids,
                    participants=[sender_name] if sender_name else None,
                    timestamp=now,
                    metadata=merged_meta,
                    respect_filter=bool(kwargs.get("respect_filter", True)),
                    user_id=str(kwargs.get("user_id", sender_id) or ""),
                    group_id=str(kwargs.get("group_id", "") or ""),
                )
            item_hash = ""
            if isinstance(res, dict):
                stored_ids = res.get("stored_ids") or []
                item_hash = str(
                    res.get("paragraph_hash")
                    or (stored_ids[0] if stored_ids else "")
                    or ""
                )
            else:
                item_hash = str(res or "")
            return IngestResult(success=True, hash=item_hash, metadata=res if isinstance(res, dict) else {})
        except Exception as exc:
            return IngestResult(success=False, error=str(exc))

    async def dispatch_admin(
        self,
        command_name: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一派发 14+1 类 Admin 管理组件契约命令到当前作用域共享服务。"""
        if not self._initialized:
            await self.initialize()

        ctx_cfg = self._runtime.context.config if self._runtime is not None else self.config
        with config_shim.use_scope_config(ctx_cfg), llm_shim.use_provider_bridge(self.provider_bridge):
            return await self._admin_service.dispatch_contract(
                scope_key=self.scope_key,
                component_name=command_name,
                action=action,
                payload=params or {},
            )

    async def shutdown(self) -> None:
        """安全停止当前作用域后台自愈任务并刷盘 WAL 与双池向量索引。"""
        if not self._initialized and self._runtime is None:
            return
        if self._owns_admin_service:
            try:
                await self._admin_service.close()
            except Exception:
                pass
        if self._owns_runtime_manager:
            await self._runtime_manager.close_all(mark_closing=False)
        else:
            await self._runtime_manager.close_scope(self.scope_key)
        self._runtime = None
        self._initialized = False

    async def close(self) -> None:
        """幂等释放门面与底层作用域运行时资源。"""
        await self.shutdown()

