"""Application context and runtime shared state."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional, Tuple

from ..core.embedding.api_adapter import EmbeddingAPIAdapter
from ..core.retrieval import DualPathRetriever, DynamicThresholdFilter, SparseBM25Index
from ..core.storage import GraphStore, MetadataStore, VectorStore
from ..core.utils.episode_retrieval_service import EpisodeRetrievalService
from ..core.utils.episode_service import EpisodeService
from ..core.utils.paragraph_vector_service import ParagraphVectorWriteService
from ..core.utils.person_profile_service import PersonProfileService
from ..core.utils.relation_write_service import RelationWriteService
from .common.logging import get_logger
from .settings import AppSettings

logger = get_logger("A_Memorix.AppContext")


def activate_context_shims(ctx: Any) -> contextlib.AbstractContextManager[Any]:
    """安全激活 ctx 的 scope_shims；兼容单测中的轻量桩对象。"""
    scope_shims = getattr(ctx, "scope_shims", None)
    if callable(scope_shims):
        return scope_shims()
    return contextlib.nullcontext(ctx)


@dataclass
class AppContext:
    settings: AppSettings
    vector_store: VectorStore
    graph_store: GraphStore
    metadata_store: MetadataStore
    embedding_manager: EmbeddingAPIAdapter
    sparse_index: Optional[SparseBM25Index]
    retriever: DualPathRetriever
    threshold_filter: DynamicThresholdFilter
    person_profile_service: PersonProfileService
    paragraph_vector_service: ParagraphVectorWriteService
    relation_write_service: RelationWriteService
    episode_service: EpisodeService
    episode_retrieval_service: EpisodeRetrievalService
    llm_client: Optional[Any]
    data_dir: Path
    config: Dict[str, Any]
    provider_bridge: Optional[Any] = None
    _runtime_self_check_report: Optional[Dict[str, Any]] = None
    _runtime_auto_save: Optional[bool] = None
    _request_dedup_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _request_dedup_inflight: Dict[str, asyncio.Future] = field(default_factory=dict)
    _request_dedup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    graph_mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 双池子存储：始终构造，ready 标志决定是否启用双池检索/写入路径。
    paragraph_vector_store: Optional[VectorStore] = None
    graph_vector_store: Optional[VectorStore] = None
    _dual_vector_pools_ready: bool = False

    @contextlib.contextmanager
    def scope_shims(self) -> Iterator["AppContext"]:
        """为当前协程激活本作用域隔离的 config 与 provider_bridge 上下文。"""
        from ..engine.host_shims import config_shim, llm_shim

        with config_shim.use_scope_config(self.config), llm_shim.use_provider_bridge(self.provider_bridge):
            yield self

    def get_config(self, key: str, default: Any = None) -> Any:
        current: Any = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default

        if key == "advanced.enable_auto_save" and self._runtime_auto_save is not None:
            return bool(self._runtime_auto_save)
        return current

    def is_chat_enabled(
        self,
        stream_id: Optional[str],
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        filter_config = self.get_config("filter", {}) or {}
        if not bool(filter_config.get("enabled", True)):
            return True

        mode = str(filter_config.get("mode", "blacklist")).strip().lower()
        chats = filter_config.get("chats", [])
        if not chats:
            return mode == "blacklist"

        sid = str(stream_id or "")
        gid = str(group_id or "")
        uid = str(user_id or "")
        matched = False

        for pattern in chats:
            item = str(pattern or "").strip()
            if not item:
                continue
            if ":" in item:
                prefix, value = item.split(":", 1)
                prefix = prefix.lower().strip()
                value = value.strip()
                if prefix == "group" and value == gid:
                    matched = True
                elif prefix in {"user", "private"} and value == uid:
                    matched = True
                elif prefix == "stream" and value == sid:
                    matched = True
            else:
                if item in {sid, gid, uid}:
                    matched = True
            if matched:
                break

        if mode == "blacklist":
            return not matched
        return matched

    def _dedup_enabled(self) -> bool:
        return bool(self.get_config("routing.enable_request_dedup", True))

    def _dedup_ttl(self) -> float:
        try:
            ttl = float(self.get_config("routing.request_dedup_ttl_seconds", 2))
        except (TypeError, ValueError):
            ttl = 2.0
        return max(0.1, ttl)

    async def execute_request_with_dedup(
        self,
        request_key: str,
        executor: Callable[[], Awaitable[Any]],
    ) -> Tuple[bool, Any]:
        if not self._dedup_enabled():
            return False, await executor()

        wait_future: Optional[asyncio.Future] = None
        is_owner = False
        now_ts = time.time()

        async with self._request_dedup_lock:
            stale = [
                key
                for key, entry in self._request_dedup_cache.items()
                if float(entry.get("expires_at", 0.0)) <= now_ts
            ]
            for key in stale:
                self._request_dedup_cache.pop(key, None)

            cached = self._request_dedup_cache.get(request_key)
            if cached and float(cached.get("expires_at", 0.0)) > now_ts:
                return True, cached.get("result")

            inflight = self._request_dedup_inflight.get(request_key)
            if inflight is not None:
                wait_future = inflight
            else:
                wait_future = asyncio.get_running_loop().create_future()
                self._request_dedup_inflight[request_key] = wait_future
                is_owner = True

        if not is_owner and wait_future is not None:
            return True, await wait_future

        assert wait_future is not None
        try:
            result = await executor()
            expires_at = time.time() + self._dedup_ttl()
            async with self._request_dedup_lock:
                self._request_dedup_cache[request_key] = {"result": result, "expires_at": expires_at}
                inflight = self._request_dedup_inflight.pop(request_key, None)
                if inflight is not None and not inflight.done():
                    inflight.set_result(result)
            return False, result
        except Exception as exc:
            async with self._request_dedup_lock:
                inflight = self._request_dedup_inflight.pop(request_key, None)
                if inflight is not None and not inflight.done():
                    inflight.set_exception(exc)
            raise

    async def reinforce_access(self, relation_hashes: list[str]) -> None:
        if not relation_hashes:
            return
        try:
            self.metadata_store.reinforce_relations(relation_hashes)
        except Exception as exc:
            logger.warning("Failed to reinforce relation access: %s", exc)

    def is_runtime_ready(self) -> bool:
        return True

    def persist_vector_store(
        self,
        store: Optional[VectorStore],
        embedding_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        """与 KernelRuntimeFacade 对齐，连同模型指纹持久化指定 VectorStore。"""
        if store is None:
            return
        fingerprint = embedding_fingerprint
        if fingerprint is None:
            fp_getter = getattr(self.embedding_manager, "get_embedding_fingerprint", None)
            dim = getattr(store, "dimension", None)
            if callable(fp_getter) and dim is not None:
                try:
                    fingerprint = fp_getter(dimension=int(dim))
                except Exception:
                    fingerprint = None
        if fingerprint is not None:
            try:
                store.save(embedding_fingerprint=fingerprint)
                return
            except TypeError:
                pass
        store.save()

    def enqueue_paragraph_vector_backfill(self, paragraph_hash: str, *, error: str = "") -> None:
        enqueuer = getattr(self.metadata_store, "enqueue_paragraph_vector_backfill", None)
        if callable(enqueuer):
            enqueuer(paragraph_hash, error=error)

    def record_person_evidence_written(self, person_ids: Any, *, reason: str = "") -> None:
        """与 KernelRuntimeFacade 对齐，在摘要/WebUI 导入写入人物证据后合并画像刷新请求。"""
        if not bool(self.get_config("person_profile.enabled", True)):
            return
        enqueuer = getattr(self.metadata_store, "enqueue_person_profile_refresh", None)
        if not callable(enqueuer):
            return
        clean_reason = str(reason or "").strip() or "memory_ingest"
        for raw_pid in list(dict.fromkeys(person_ids or [])):
            token = str(raw_pid or "").strip()
            if token:
                enqueuer(person_id=token, reason=clean_reason)

    async def save_all(self) -> None:
        from ..core.utils.async_work import cancellation_safe_to_thread

        if self._dual_vector_pools_ready:
            # 双池就绪：并行存主向量池 + 段落池 + 图谱池 + 图结构（连同模型指纹）。
            await asyncio.gather(
                cancellation_safe_to_thread(self.persist_vector_store, self.vector_store),
                cancellation_safe_to_thread(self.graph_store.save),
                cancellation_safe_to_thread(self.persist_vector_store, self.paragraph_vector_store),
                cancellation_safe_to_thread(self.persist_vector_store, self.graph_vector_store),
            )
            return
        await asyncio.gather(
            cancellation_safe_to_thread(self.persist_vector_store, self.vector_store),
            cancellation_safe_to_thread(self.graph_store.save),
        )

    def _dual_vector_pools_enabled(self) -> bool:
        """双池检索/写入路径是否就绪可用。"""
        return self._dual_vector_pools_ready

    async def close(self) -> None:
        try:
            if self.sparse_index is not None:
                self.sparse_index.unload()
        except Exception:
            pass

        try:
            await self.save_all()
        except Exception as exc:
            logger.warning("Save on close failed: %s", exc)

        try:
            closer = getattr(self.embedding_manager, "close", None)
            if callable(closer):
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:
            logger.warning("Embedding close failed: %s", exc)

        try:
            self.metadata_store.close()
        except Exception as exc:
            logger.warning("Metadata close failed: %s", exc)
