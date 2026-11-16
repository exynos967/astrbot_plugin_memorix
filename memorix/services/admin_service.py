"""Admin tool service for AstrBot LLM tools.

These methods mirror the A_memorix admin-tool surface where the embedded
AstrBot runtime has the corresponding capability. Unsupported upstream-only
features return an explicit payload instead of silently creating fake behavior.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from ..amemorix.context import activate_context_shims
from ..amemorix.services import DeleteService, PersonProfileApiService
from ..amemorix.services import MemoryService as BaseMemoryService
from ..amemorix.services.import_task_manager import ImportTaskManager
from ..amemorix.settings import mask_sensitive
from ..app_context import ScopeRuntimeManager
from ..core.runtime.admin_contracts import dispatch_admin_command, parse_admin_command
from ..core.runtime.services.bundle_admin_service import MemoryBundleAdminService
from ..core.runtime.services.image_service import MemoryImageService
from ..core.utils.retrieval_tuning_manager import RetrievalTuningManager
from ..core.utils.runtime_self_check import ensure_runtime_self_check
from ..core.utils.summary_identity import observe_identity
from ..engine.host_shims.embedding_shim import (
    is_image_memory_enabled,
    resolve_image_memory_status,
)
from .chat_migration_service import AstrBotChatMigrationService
from .fact_admin_service import FactAdminService, resolve_canonical_person_id
from .graph_mutation_service import GraphMutationService


class _TuningPluginAdapter:
    """把 AppContext 适配成 RetrievalTuningManager 期望的 plugin 句柄。

    RetrievalTuningManager 原为单 ctx 内核设计，期望 plugin 持 config 与各 store；
    封装层等价物是 AppContext，本适配器桥接二者，并在 runtime 重建后刷新 ctx 引用。
    """

    def __init__(self, ctx: Any) -> None:
        self.bind(ctx)

    def bind(self, ctx: Any) -> None:
        self._ctx = ctx
        # config 须为可写 dict，apply_profile 通过 _nested_set 直接改它以影响该 scope 检索。
        self.config = ctx.config
        self._plugin_config = None

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._ctx.get_config(key, default)

    @property
    def metadata_store(self) -> Any:
        return self._ctx.metadata_store

    @property
    def vector_store(self) -> Any:
        return self._ctx.vector_store

    # 双池向量存储透传：ctx 暂未挂载时降级为 None，供内核检索调谐探测。
    @property
    def paragraph_vector_store(self) -> Any:
        return getattr(self._ctx, "paragraph_vector_store", None)

    @property
    def graph_vector_store(self) -> Any:
        return getattr(self._ctx, "graph_vector_store", None)

    def _dual_vector_pools_enabled(self) -> bool:
        # 双保险：ctx 暴露方法则调用，否则回退读 _dual_vector_pools_ready 字段。
        checker = getattr(self._ctx, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        return bool(getattr(self._ctx, "_dual_vector_pools_ready", False))

    @property
    def graph_store(self) -> Any:
        return self._ctx.graph_store

    @property
    def embedding_manager(self) -> Any:
        return self._ctx.embedding_manager

    @property
    def sparse_index(self) -> Any:
        return self._ctx.sparse_index

    def is_runtime_ready(self) -> bool:
        return True


class _GraphRebuildAdapter:
    """为 MemoryBundleAdminService 提供与 SDKMemoryKernel._graph_admin_service 等价的图重建能力。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def _rebuild_graph_from_metadata(self) -> Dict[str, int]:
        metadata_store = self._ctx.metadata_store
        graph_store = self._ctx.graph_store
        entity_rows = metadata_store.query(
            "SELECT name FROM entities WHERE is_deleted IS NULL OR is_deleted = 0 ORDER BY name ASC"
        )
        raw_relation_rows = metadata_store.query(
            "SELECT subject, object, confidence, hash FROM relations WHERE is_inactive IS NULL OR is_inactive = 0"
        )
        relation_rows = [
            row
            for row in raw_relation_rows
            if str(row.get("subject", "") or "").strip() and str(row.get("object", "") or "").strip()
        ]
        names = list(
            dict.fromkeys(
                [str(row.get("name", "") or "").strip() for row in entity_rows if str(row.get("name", "") or "").strip()]
                + [str(row.get("subject", "") or "").strip() for row in relation_rows]
                + [str(row.get("object", "") or "").strip() for row in relation_rows]
            )
        )
        graph_store.clear()
        if names:
            graph_store.add_nodes(names)
        if relation_rows:
            graph_store.add_edges(
                [
                    (str(row.get("subject", "") or "").strip(), str(row.get("object", "") or "").strip())
                    for row in relation_rows
                ],
                relation_hashes=[str(row.get("hash", "") or "") for row in relation_rows],
            )
        return {"node_count": int(graph_store.num_nodes), "edge_count": int(graph_store.num_edges)}


class _BundleAndImageKernelAdapter:
    """将 AppContext 适配为 MemoryBundleAdminService 与 MemoryImageService 所需的内核接口。"""

    def __init__(
        self,
        ctx: Any,
        *,
        storage_cleanup_lock: asyncio.Lock,
        import_task_manager: Any = None,
        image_memory_runtime: Any = None,
    ) -> None:
        self._ctx = ctx
        self.data_dir = Path(ctx.data_dir)
        self.metadata_store = ctx.metadata_store
        self.vector_store = ctx.vector_store
        self.paragraph_vector_store = getattr(ctx, "paragraph_vector_store", None) or ctx.vector_store
        self.graph_vector_store = getattr(ctx, "graph_vector_store", None) or ctx.vector_store
        self.graph_store = ctx.graph_store
        self.embedding_manager = ctx.embedding_manager
        self.import_task_manager = import_task_manager
        self.image_memory_runtime = image_memory_runtime
        self._storage_cleanup_lock = storage_cleanup_lock
        self.relation_vectors_enabled = bool(ctx.get_config("retrieval.relation_vectorization.enabled", False))
        self._graph_admin_service = _GraphRebuildAdapter(ctx)

    async def initialize(self) -> None:
        return None

    def _dual_vector_pools_enabled(self) -> bool:
        checker = getattr(self._ctx, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        return bool(getattr(self._ctx, "_dual_vector_pools_ready", False))

    def _paragraph_store(self) -> Any:
        return self.paragraph_vector_store if self._dual_vector_pools_enabled() else self.vector_store

    def _graph_vector_store(self) -> Any:
        return self.graph_vector_store if self._dual_vector_pools_enabled() else self.vector_store

    def _graph_vector_id(self, kind: str, item_hash: str) -> str:
        token = str(item_hash or "").strip()
        if self._dual_vector_pools_enabled():
            return f"{kind}:{token}"
        return token

    def _current_embedding_fingerprint_for_validation(self, dimension: Optional[int] = None) -> Optional[Dict[str, Any]]:
        getter = getattr(self.embedding_manager, "get_embedding_fingerprint", None)
        if not callable(getter):
            return None
        dim = int(dimension or getattr(self._paragraph_store(), "dimension", 0) or 1024)
        fp = getter(dimension=dim)
        return fp if isinstance(fp, dict) and fp.get("hash") else None

    def _stored_embedding_fingerprint(self, store: Any = None) -> Optional[Dict[str, Any]]:
        target = store or self._paragraph_store()
        store_dir = getattr(target, "data_dir", None)
        if store_dir is not None:
            meta_path = Path(store_dir) / "vectors_metadata.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(meta, dict) and isinstance(meta.get("embedding_fingerprint"), dict):
                        return meta["embedding_fingerprint"]
                except Exception:
                    pass
        return self._current_embedding_fingerprint_for_validation(getattr(target, "dimension", None))

    async def _ensure_vector_for_text(self, *, item_hash: str, text: str, vector_store: Any) -> bool:
        token = str(item_hash or "").strip()
        content = str(text or "").strip()
        if not token or not content or vector_store is None or self.embedding_manager is None:
            return False
        if token in vector_store:
            return False
        encode_single = getattr(self.embedding_manager, "encode_single", None)
        encode_fn = getattr(self.embedding_manager, "encode", None)
        if callable(encode_single):
            vec = await encode_single(content)
        elif callable(encode_fn):
            vec = await encode_fn(content)
        else:
            batch = await self.embedding_manager.encode_batch([content], show_progress=False)
            vec = batch[0] if len(batch) > 0 else None
        if vec is None:
            return False
        import numpy as np

        arr = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        vector_store.add(arr, [token])
        return True

    async def _ensure_paragraph_vector(self, paragraph: Dict[str, Any]) -> bool:
        return await self._ensure_vector_for_text(
            item_hash=str(paragraph.get("hash", "") or ""),
            text=str(paragraph.get("content", "") or ""),
            vector_store=self._paragraph_store(),
        )

    async def _ensure_entity_vector(self, entity: Dict[str, Any]) -> bool:
        raw_hash = str(entity.get("hash", "") or "")
        return await self._ensure_vector_for_text(
            item_hash=self._graph_vector_id("entity", raw_hash),
            text=str(entity.get("name", "") or ""),
            vector_store=self._graph_vector_store(),
        )

    async def _ensure_relation_vector(self, relation: Dict[str, Any]) -> bool:
        raw_hash = str(relation.get("hash", "") or "")
        rel_text = " ".join(
            part
            for part in (
                str(relation.get("subject", "") or "").strip(),
                str(relation.get("predicate", "") or "").strip(),
                str(relation.get("object", "") or "").strip(),
            )
            if part
        )
        return await self._ensure_vector_for_text(
            item_hash=self._graph_vector_id("relation", raw_hash),
            text=rel_text,
            vector_store=self._graph_vector_store(),
        )

    def _persist(self, *, force_vectors: bool = False) -> None:
        del force_vectors
        if self.graph_store is not None:
            self.graph_store.save()
        p_store = self._paragraph_store()
        g_store = self._graph_vector_store()
        fp = self._current_embedding_fingerprint_for_validation()
        seen_store_ids: set[int] = set()
        for store in (p_store, g_store):
            if store is None or id(store) in seen_store_ids:
                continue
            seen_store_ids.add(id(store))
            try:
                if fp is not None:
                    store.save(embedding_fingerprint=fp)
                else:
                    store.save()
            except TypeError:
                store.save()


class _AdminContractKernelProxy:
    """把 admin_contracts.py 的 memory_*_admin 方法调用桥接到指定 scope_key 的 AdminService。"""

    def __init__(self, service: "AdminService", scope_key: str) -> None:
        self._service = service
        self._scope_key = scope_key

    def __getattr__(self, name: str) -> Any:
        if name.startswith("memory_") and name.endswith("_admin"):
            short_name = name.removeprefix("memory_")
            target = getattr(self._service, short_name, None)
            if callable(target):
                async def _bound(*, action: str, **kwargs: Any) -> Dict[str, Any]:
                    return await target(scope_key=self._scope_key, action=action, **kwargs)
                return _bound
        raise AttributeError(name)


class AdminService:
    def __init__(
        self,
        runtime_manager: ScopeRuntimeManager,
        *,
        feedback_service: Any = None,
        fuzzy_modify_service: Any = None,
    ):
        self.runtime_manager = runtime_manager
        self.feedback_service = feedback_service
        self.fuzzy_modify_service = fuzzy_modify_service
        self._import_managers: dict[str, ImportTaskManager] = {}
        self._tuning_managers: dict[str, RetrievalTuningManager] = {}
        self._cleanup_locks: dict[str, asyncio.Lock] = {}
        self._image_runtimes: dict[str, Any] = {}

    async def close(self) -> None:
        import_managers = list(self._import_managers.values())
        self._import_managers.clear()
        tuning_managers = list(self._tuning_managers.values())
        self._tuning_managers.clear()
        self._cleanup_locks.clear()
        self._image_runtimes.clear()
        for manager in import_managers:
            await manager.stop()
        for manager in tuning_managers:
            await manager.shutdown()

    async def graph_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act == "get_graph":
                return {"success": True, **self._serialize_graph(ctx, limit=self._int(kwargs.get("limit"), 200, 1, 1000))}
            if act == "search":
                return self._search_graph(ctx, str(kwargs.get("query", "") or ""), self._int(kwargs.get("limit"), 50, 1, 200))
            if act == "node_detail":
                return self._node_detail(ctx, str(kwargs.get("node_id") or kwargs.get("node") or ""), kwargs)
            if act == "edge_detail":
                return self._edge_detail(
                    ctx,
                    str(kwargs.get("source") or kwargs.get("subject") or ""),
                    str(kwargs.get("target") or kwargs.get("object") or ""),
                    kwargs,
                )
            if act in {"create_node", "delete_node", "rename_node", "create_edge", "delete_edge", "update_edge_weight"}:
                return await GraphMutationService(ctx).execute(act, **kwargs)
            return self._unsupported("graph", act)

    async def source_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act == "list":
                items = ctx.metadata_store.get_all_sources()
                return {"success": True, "items": items, "count": len(items)}
            if act == "delete":
                source = str(kwargs.get("source", "") or "").strip()
                if not source:
                    return self._err("source 不能为空")
                return await self._delete_sources(ctx, [source])
            if act == "batch_delete":
                sources = self._tokens(kwargs.get("sources"))
                if not sources:
                    source = str(kwargs.get("source", "") or "").strip()
                    if source:
                        sources = [source]
                if not sources:
                    return self._err("sources 不能为空")
                return await self._delete_sources(ctx, sources)
            return self._unsupported("source", act)

    async def episode_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act in {"query", "list"}:
                items = ctx.metadata_store.query_episodes(
                    query=str(kwargs.get("query", "") or ""),
                    time_from=self._float_or_none(kwargs.get("time_start", kwargs.get("time_from"))),
                    time_to=self._float_or_none(kwargs.get("time_end", kwargs.get("time_to"))),
                    person=str(kwargs.get("person_id") or kwargs.get("person") or "") or None,
                    source=str(kwargs.get("source") or "") or None,
                    limit=self._int(kwargs.get("limit"), 20, 1, 200),
                )
                return {"success": True, "items": items, "count": len(items)}
            if act == "get":
                episode_id = str(kwargs.get("episode_id", "") or "").strip()
                episode = ctx.metadata_store.get_episode_by_id(episode_id) if episode_id else None
                if episode is None:
                    return self._err("episode 不存在")
                if bool(kwargs.get("include_paragraphs", True)):
                    episode["paragraphs"] = ctx.metadata_store.get_episode_paragraphs(
                        episode_id,
                        limit=self._int(kwargs.get("paragraph_limit"), 100, 1, 500),
                    )
                return {"success": True, "episode": episode}
            if act == "status":
                summary = ctx.metadata_store.get_episode_source_rebuild_summary(
                    failed_limit=self._int(kwargs.get("limit"), 20, 1, 200)
                )
                pending_rows = ctx.metadata_store.fetch_episode_pending_batch(limit=1, max_retry=self._int(kwargs.get("max_retry"), 3, 0, 50))
                return {"success": True, **summary, "has_pending_paragraphs": bool(pending_rows)}
            if act == "discard_migration_backfill":
                result = await asyncio.to_thread(
                    ctx.metadata_store.discard_migration_episode_rebuilds,
                    dry_run=bool(kwargs.get("dry_run", True)),
                )
                return {"success": True, **result}
            if act == "rebuild":
                sources = self._tokens(kwargs.get("sources"))
                source = str(kwargs.get("source", "") or "").strip()
                if source:
                    sources.append(source)
                if not sources and bool(kwargs.get("all", False)):
                    sources = [str(row.get("source", "") or "") for row in ctx.metadata_store.get_all_sources()]
                sources = list(dict.fromkeys([item for item in sources if item]))
                if not sources:
                    return self._err("未提供可重建的 source")
                results = []
                failures = []
                for src in sources:
                    try:
                        results.append(await ctx.episode_service.rebuild_source(src))
                    except Exception as exc:
                        failures.append({"source": src, "error": str(exc)})
                return {"success": not failures, "items": results, "failures": failures, "count": len(results)}
            if act in {"process_pending", "process_sources"}:
                return {"success": True, **await self._process_episode_pending(ctx, kwargs)}
            return self._unsupported("episode", act)

    async def fact_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await FactAdminService(runtime.context).memory_fact_admin(action=action, **kwargs)

    async def profile_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        service = PersonProfileApiService(ctx)
        act = self._action(action)
        with activate_context_shims(ctx):
            if act == "resolve_identity":
                identifier = str(kwargs.get("identifier") or kwargs.get("person_keyword") or kwargs.get("keyword") or "").strip()
                if not identifier:
                    return self._err("identifier 不能为空")
                person_id = ctx.person_profile_service.resolve_person_id(identifier, metadata_store=ctx.metadata_store)
                return {"success": True, "person_id": person_id}
            if act == "observe_identity":
                platform = str(kwargs.get("platform", "") or "").strip()
                user_id = str(kwargs.get("user_id", "") or "").strip()
                aliases = self._tokens(kwargs.get("aliases"))
                if not platform or not user_id:
                    return self._err("platform 和 user_id 不能为空")
                try:
                    identity = observe_identity(
                        ctx.metadata_store,
                        platform=platform,
                        user_id=user_id,
                        aliases=aliases,
                        group_id=str(kwargs.get("group_id", "") or ""),
                    )
                    return {"success": True, **identity}
                except Exception as exc:
                    return self._err(str(exc))
            if act in {"get_aliases", "set_aliases", "delete_aliases"}:
                person_id = str(kwargs.get("person_id", "") or "").strip()
                if not person_id:
                    return self._err("person_id 不能为空")
                try:
                    person_id = resolve_canonical_person_id(ctx, person_id)
                    with ctx.metadata_store.transaction(immediate=True) as conn:
                        if act == "set_aliases":
                            ctx.metadata_store.set_person_profile_alias_override(
                                person_id=person_id, aliases=kwargs.get("aliases"),
                                updated_by="astrbot_admin", source="astrbot", conn=conn,
                            )
                        elif act == "delete_aliases":
                            ctx.metadata_store.delete_person_profile_alias_override(person_id, conn=conn)
                        if act != "get_aliases":
                            ctx.metadata_store.enqueue_person_profile_refresh(
                                person_id=person_id, reason="aliases_changed", conn=conn,
                            )
                    return {"success": True, **ctx.person_profile_service.get_person_alias_details(person_id)}
                except (TypeError, ValueError) as error:
                    return self._err(str(error))
            if act == "query":
                return await service.query(
                    person_id=str(kwargs.get("person_id", "") or ""),
                    person_keyword=str(kwargs.get("person_keyword") or kwargs.get("keyword") or ""),
                    top_k=self._int(kwargs.get("limit", kwargs.get("top_k")), 12, 1, 100),
                    force_refresh=bool(kwargs.get("force_refresh", False)),
                    source_note="astrbot:memory_profile_admin.query",
                )
            if act == "evidence":
                profile = await service.query(
                    person_id=str(kwargs.get("person_id", "") or ""),
                    person_keyword=str(kwargs.get("person_keyword") or kwargs.get("keyword") or ""),
                    top_k=self._int(kwargs.get("limit", kwargs.get("top_k")), 12, 1, 100),
                    force_refresh=bool(kwargs.get("force_refresh", False)),
                    source_note="astrbot:memory_profile_admin.evidence",
                )
                evidence = list(profile.get("evidence") or []) if isinstance(profile, dict) else []
                return {
                    "success": bool(profile.get("success", False)) if isinstance(profile, dict) else False,
                    "person_id": str((profile or {}).get("person_id", "") or kwargs.get("person_id", "")),
                    "evidence": evidence,
                    "evidence_count": len(evidence),
                    "raw_profile": profile,
                }
            if act == "correct_evidence":
                evidence_type = str(kwargs.get("evidence_type", "") or "").strip().lower()
                hash_value = str(kwargs.get("hash") or kwargs.get("hash_value") or "").strip()
                if evidence_type not in {"relation", "paragraph", "person_fact", "chat_summary"}:
                    return self._err("不支持的画像证据类型")
                if not hash_value:
                    return self._err("画像证据 hash 不能为空")
                delete_mode = "relation" if evidence_type == "relation" else "paragraph"
                delete_result = await self.delete_admin(
                    scope_key=scope_key,
                    action="execute",
                    mode=delete_mode,
                    selector={"hash": hash_value},
                )
                return {
                    "success": bool(delete_result.get("success", False)),
                    "delete_result": delete_result,
                    "error": str(delete_result.get("error", "") or ""),
                }
            if act == "process_pending":
                limit = self._int(kwargs.get("limit"), 10, 1, 100)
                requests = ctx.metadata_store.fetch_person_profile_refresh_batch(limit=limit)
                refreshed = 0
                for req in requests:
                    pid = str(req.get("person_id", "") or "").strip()
                    if not pid:
                        continue
                    res = await service.query(person_id=pid, top_k=12, force_refresh=True, source_note="astrbot:process_pending")
                    if bool(res.get("success")):
                        ctx.metadata_store.mark_person_profile_refresh_done(pid)
                        refreshed += 1
                return {"success": True, "processed": len(requests), "refreshed": refreshed}
            if act == "list":
                return await service.list_registry(
                    keyword=str(kwargs.get("keyword") or kwargs.get("query") or ""),
                    page=self._int(kwargs.get("page"), 1, 1, 100000),
                    page_size=self._int(kwargs.get("page_size", kwargs.get("limit")), 20, 1, 200),
                )
            if act == "set_override":
                return await service.set_override(
                    person_id=str(kwargs.get("person_id", "") or ""),
                    override_text=str(kwargs.get("override_text") or kwargs.get("text") or ""),
                    updated_by=str(kwargs.get("updated_by") or "memory_profile_admin"),
                )
            if act == "delete_override":
                return await service.delete_override(person_id=str(kwargs.get("person_id", "") or ""))
            if act == "status":
                stats = ctx.metadata_store.get_statistics()
                summary = ctx.metadata_store.get_person_profile_refresh_summary(
                    failed_limit=self._int(kwargs.get("limit"), 20, 1, 200)
                )
                return {
                    "success": True,
                    "person_profile_count": int(stats.get("person_profile_count", 0) or 0),
                    **summary,
                }
            return self._unsupported("profile", act)

    async def runtime_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act == "save":
                await ctx.save_all()
                img_rt = getattr(ctx, "image_memory_runtime", None) or self._image_runtimes.get(scope_key)
                if img_rt is not None and getattr(img_rt, "vector_store", None) is not None:
                    persist_fn = getattr(img_rt, "persist_vector_store", None)
                    if callable(persist_fn):
                        await asyncio.to_thread(persist_fn, img_rt.vector_store, getattr(img_rt, "fingerprint", {}) or {})
                    elif callable(getattr(img_rt.vector_store, "save", None)):
                        await asyncio.to_thread(img_rt.vector_store.save)
                return {"success": True, "saved": True, "data_dir": str(ctx.data_dir)}
            if act == "get_config":
                raw_config = ctx.config if isinstance(ctx.config, dict) else {}
                return {
                    "success": True,
                    "config": mask_sensitive(raw_config),
                    "data_dir": str(ctx.data_dir),
                    "auto_save": bool(ctx.get_config("advanced.enable_auto_save", True)),
                    "runtime_ready": True,
                }
            if act in {"self_check", "refresh_self_check", "recover_embedding"}:
                return {"success": True, "report": await ensure_runtime_self_check(ctx, force=True)}
            if act == "set_auto_save":
                enabled = bool(kwargs.get("enabled", False))
                ctx._runtime_auto_save = enabled
                return {"success": True, "auto_save": enabled}
            if act == "rebuild_all_vectors":
                raw_limit = kwargs.get("limit")
                adapter = await self._bundle_kernel_adapter(scope_key, ctx, include_image_runtime=False)
                configured_mode = str(ctx.get_config("retrieval.vector_pools.mode", "dual") or "dual").strip().lower()
                promote_dual = (
                    configured_mode == "dual"
                    and raw_limit in {None, ""}
                    and getattr(ctx, "paragraph_vector_store", None) is not None
                    and getattr(ctx, "graph_vector_store", None) is not None
                )
                p_store = ctx.paragraph_vector_store if promote_dual else adapter._paragraph_store()
                g_store = ctx.graph_vector_store if promote_dual else adapter._graph_vector_store()
                for store in {p_store, g_store}:
                    if store is not None and hasattr(store, "clear"):
                        store.clear()
                if raw_limit not in {None, ""}:
                    limit = self._int(raw_limit, 5000, 1, 1_000_000)
                    p_rows = ctx.metadata_store.query(
                        "SELECT hash, content FROM paragraphs WHERE COALESCE(is_deleted, 0) = 0 ORDER BY created_at ASC LIMIT ?",
                        (limit,),
                    )
                else:
                    p_rows = ctx.metadata_store.query(
                        "SELECT hash, content FROM paragraphs WHERE COALESCE(is_deleted, 0) = 0 ORDER BY created_at ASC"
                    )
                e_rows = ctx.metadata_store.query(
                    "SELECT hash, name FROM entities WHERE COALESCE(is_deleted, 0) = 0 ORDER BY name ASC"
                )
                r_rows = (
                    ctx.metadata_store.query(
                        "SELECT hash, subject, predicate, object FROM relations WHERE COALESCE(is_inactive, 0) = 0"
                    )
                    if adapter.relation_vectors_enabled
                    else []
                )
                generated_paragraphs = 0
                for row in p_rows:
                    if await adapter._ensure_vector_for_text(
                        item_hash=str(row.get("hash", "") or ""),
                        text=str(row.get("content", "") or ""),
                        vector_store=p_store,
                    ):
                        generated_paragraphs += 1
                generated_entities = 0
                for row in e_rows:
                    raw_hash = str(row.get("hash", "") or "").strip()
                    item_id = f"entity:{raw_hash}" if (promote_dual or adapter._dual_vector_pools_enabled()) else raw_hash
                    if await adapter._ensure_vector_for_text(
                        item_hash=item_id,
                        text=str(row.get("name", "") or ""),
                        vector_store=g_store,
                    ):
                        generated_entities += 1
                generated_relations = 0
                for row in r_rows:
                    raw_hash = str(row.get("hash", "") or "").strip()
                    item_id = f"relation:{raw_hash}" if (promote_dual or adapter._dual_vector_pools_enabled()) else raw_hash
                    rel_text = " ".join(
                        part
                        for part in (
                            str(row.get("subject", "") or "").strip(),
                            str(row.get("predicate", "") or "").strip(),
                            str(row.get("object", "") or "").strip(),
                        )
                        if part
                    )
                    if await adapter._ensure_vector_for_text(
                        item_hash=item_id,
                        text=rel_text,
                        vector_store=g_store,
                    ):
                        generated_relations += 1

                dual_activated = False
                if promote_dual and p_store is not None and g_store is not None:
                    dim = int(getattr(p_store, "dimension", 0) or getattr(g_store, "dimension", 0) or 1024)
                    fp = adapter._current_embedding_fingerprint_for_validation(dim)
                    if ctx.graph_store is not None:
                        ctx.graph_store.save()
                    for store in (p_store, g_store):
                        try:
                            if fp is not None:
                                store.save(embedding_fingerprint=fp)
                            else:
                                store.save()
                        except TypeError:
                            store.save()
                    manifest_payload: Dict[str, Any] = {
                        "status": "ready",
                        "version": 1,
                        "mode": "dual",
                        "dimension": dim,
                        "created_at": time.time(),
                        "paragraph_vectors": int(getattr(p_store, "num_vectors", generated_paragraphs)),
                        "graph_vectors": int(getattr(g_store, "num_vectors", generated_entities + generated_relations)),
                        "stats": {
                            "paragraphs": {"done": generated_paragraphs, "failed": 0},
                            "entities": {"done": generated_entities, "failed": 0},
                            "relations": {"done": generated_relations, "failed": 0},
                        },
                    }
                    if fp is not None:
                        manifest_payload["embedding_fingerprint"] = fp
                    manifest_path = Path(ctx.data_dir) / "vectors" / "dual_ready.json"
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_manifest = manifest_path.with_suffix(".json.tmp")
                    tmp_manifest.write_text(
                        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    tmp_manifest.replace(manifest_path)
                    activator = getattr(ctx, "activate_dual_vector_pools", None)
                    if callable(activator):
                        activator()
                    else:
                        ctx._dual_vector_pools_ready = True
                    dual_activated = True
                else:
                    adapter._persist(force_vectors=True)
                    ctx._runtime_self_check_report = None

                total_processed = len(p_rows) + len(e_rows) + len(r_rows)
                total_generated = generated_paragraphs + generated_entities + generated_relations
                return {
                    "success": True,
                    "processed": total_processed,
                    "generated": total_generated,
                    "paragraph_processed": len(p_rows),
                    "paragraph_generated": generated_paragraphs,
                    "entity_processed": len(e_rows),
                    "entity_generated": generated_entities,
                    "relation_processed": len(r_rows),
                    "relation_generated": generated_relations,
                    "dual_activated": dual_activated,
                    "effective_mode": "dual" if (dual_activated or adapter._dual_vector_pools_enabled()) else "single",
                }
            if act == "paragraph_backfill_once":
                limit = self._int(kwargs.get("limit"), 100, 1, 5000)
                adapter = await self._bundle_kernel_adapter(scope_key, ctx, include_image_runtime=False)
                p_store = adapter._paragraph_store()
                all_rows = ctx.metadata_store.query(
                    "SELECT hash, content FROM paragraphs WHERE COALESCE(is_deleted, 0) = 0 ORDER BY created_at ASC"
                )
                missing_rows = [
                    row
                    for row in all_rows
                    if str(row.get("hash", "") or "").strip()
                    and str(row.get("content", "") or "").strip()
                    and (p_store is None or str(row.get("hash", "") or "").strip() not in p_store)
                ][:limit]
                generated = 0
                for row in missing_rows:
                    if await adapter._ensure_paragraph_vector(row):
                        generated += 1
                if generated > 0:
                    adapter._persist(force_vectors=True)
                return {"success": True, "processed": len(missing_rows), "generated": generated}
            return self._unsupported("runtime", act)

    async def import_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            manager = await self._import_manager(scope_key, runtime.context)
            act = self._action(action)
            if act in {"settings", "get_settings", "get_guide"}:
                return {"success": True, "settings": self._import_settings(manager), "path_aliases": manager.get_path_aliases()}
            if act in {"path_aliases", "get_path_aliases"}:
                return {"success": True, "path_aliases": manager.get_path_aliases()}
            if act in {"resolve_path", "resolve"}:
                return {"success": True, **await manager.resolve_path_request(kwargs)}
            if act == "create_paste":
                return {"success": True, "task": await manager.create_paste_task(kwargs)}
            if act == "create_raw_scan":
                return {"success": True, "task": await manager.create_raw_scan_task(kwargs)}
            if act == "list":
                items = await manager.list_tasks(limit=self._int(kwargs.get("limit"), 50, 1, 200))
                return {"success": True, "items": items, "count": len(items)}
            if act == "get":
                task = await manager.get_task(str(kwargs.get("task_id", "") or ""), include_chunks=bool(kwargs.get("include_chunks", False)))
                return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
            if act in {"chunks", "get_chunks"}:
                payload = await manager.get_chunks(
                    str(kwargs.get("task_id", "") or ""),
                    str(kwargs.get("file_id", "") or ""),
                    offset=self._int(kwargs.get("offset"), 0, 0, 1000000),
                    limit=self._int(kwargs.get("limit"), 50, 1, 500),
                )
                return {"success": payload is not None, **(payload or {}), "error": "" if payload is not None else "任务或文件不存在"}
            if act == "cancel":
                task = await manager.cancel_task(str(kwargs.get("task_id", "") or ""))
                return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
            if act == "retry_failed":
                overrides = kwargs.get("overrides") if isinstance(kwargs.get("overrides"), dict) else kwargs
                task = await manager.retry_failed(str(kwargs.get("task_id", "") or ""), overrides=overrides)
                return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
            if act in {"create_upload", "create_lpmm_openie", "create_lpmm_convert", "create_temporal_backfill", "create_maibot_migration"}:
                return self._err(f"当前 AstrBot 内嵌运行时暂不支持 import action: {act}")
            return self._unsupported("import", act)

    async def bundle_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        with activate_context_shims(ctx):
            adapter = await self._bundle_kernel_adapter(scope_key, ctx, include_image_runtime=True)
            try:
                return await MemoryBundleAdminService(adapter).memory_bundle_admin(action=action, **kwargs)
            except Exception as exc:
                return self._err(str(exc))

    async def image_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            status_meta = resolve_image_memory_status(config=ctx.config)
            emb_configured = bool(status_meta["embedding_configured"])
            req_embedding = bool(status_meta["require_embedding"])
            image_runtime = await self._image_runtime(scope_key, ctx)
            if image_runtime is None:
                if act == "status":
                    stats = (
                        ctx.metadata_store.image_memory_stats()
                        if getattr(ctx, "metadata_store", None) is not None
                        else {}
                    )
                    return {
                        "success": True,
                        **stats,
                        **status_meta,
                    }
                return self._err(str(status_meta.get("message") or "图片记忆未启用"))
            if act == "process_jobs" and not emb_configured:
                return {
                    "success": True,
                    "claimed": 0,
                    "processed": 0,
                    "failed": 0,
                    "status": "unconfigured",
                    "embedding_configured": False,
                    "require_embedding": req_embedding,
                    "message": "未配置多模态图片向量嵌入模型，跳过后台图片向量计算",
                }
            if act in {"search", "get", "read"} and kwargs.get("chat_ids") is None:
                chat_id = str(kwargs.get("chat_id") or "").strip()
                plugin_cfg = getattr(self.runtime_manager, "plugin_config", None)
                plugin_scope_cfg = plugin_cfg.get("scope", {}) if isinstance(plugin_cfg, dict) and isinstance(plugin_cfg.get("scope"), dict) else {}
                scope_mode = str(ctx.get_config("scope.mode", "") or plugin_scope_cfg.get("mode", "") or "").strip().lower()
                img_scope_mode = str(ctx.get_config("image_memory.scope_mode", "") or "").strip().lower()
                if chat_id and scope_mode != "platform_global" and img_scope_mode != "global":
                    kwargs["chat_ids"] = [chat_id]
                elif act == "read":
                    kwargs["chat_ids"] = None
            if act == "ingest" and not str(kwargs.get("external_ref") or "").strip():
                src_kind = str(kwargs.get("source_kind") or "chat").strip() or "chat"
                c_id = str(kwargs.get("chat_id") or scope_key or "global").strip()
                m_id = str(kwargs.get("message_id") or "").strip()
                comp = str(kwargs.get("component_path") or "0").strip()
                if m_id:
                    kwargs["external_ref"] = f"astrbot:{src_kind}:{c_id}:{m_id}:{comp}"
                else:
                    import hashlib as _hl

                    raw_b64 = str(kwargs.get("image_base64") or "").encode("utf-8")
                    digest_prefix = _hl.sha256(raw_b64).hexdigest()[:16]
                    kwargs["external_ref"] = f"astrbot:{src_kind}:{c_id}:sha:{digest_prefix}:{comp}"
            adapter = await self._bundle_kernel_adapter(
                scope_key,
                ctx,
                include_image_runtime=True,
                include_import_manager=False,
            )
            try:
                result = await MemoryImageService(adapter).execute(action=act, **kwargs)
                if isinstance(result, dict):
                    result.setdefault("embedding_configured", emb_configured)
                    result.setdefault("require_embedding", req_embedding)
                    if act == "status":
                        result.setdefault("enabled", True)
                        result.setdefault("configured_enabled", bool(status_meta["configured_enabled"]))
                        result.setdefault("paused_by_missing_embedding", False)
                        result.setdefault("reason", str(status_meta["reason"]))
                        result.setdefault("message", str(status_meta["message"]))
                    if (
                        act == "search"
                        and not emb_configured
                        and str(result.get("status") or "").strip().lower() == "unavailable"
                        and isinstance(result.get("hits"), list)
                    ):
                        result["success"] = True
                        result["error"] = ""
                    else:
                        result.setdefault("success", True)
                return result
            except Exception as exc:
                return self._err(str(exc))

    async def chat_migration_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            service = AstrBotChatMigrationService(ctx)
            try:
                if act in {"list_chats", "chats"}:
                    return await service.list_chats(
                        query=str(kwargs.get("query") or kwargs.get("keyword") or ""),
                        limit=self._int(kwargs.get("limit"), 100, 1, 500),
                    )
                if act == "snapshot":
                    src = str(kwargs.get("source_chat_id") or kwargs.get("source") or "").strip()
                    dst = str(kwargs.get("target_chat_id") or kwargs.get("target") or "").strip()
                    return await service.snapshot(source_chat_id=src, target_chat_id=dst)
                if act == "preview":
                    src = str(kwargs.get("source_chat_id") or kwargs.get("source") or "").strip()
                    dst = str(kwargs.get("target_chat_id") or kwargs.get("target") or "").strip()
                    migrate_tr = bool(kwargs.get("migrate_transcript", True))
                    return await service.preview(
                        source_chat_id=src,
                        target_chat_id=dst,
                        migrate_transcript=migrate_tr,
                        requested_by=str(kwargs.get("requested_by") or "admin"),
                        note=str(kwargs.get("note") or ""),
                    )
                if act in {"list", "list_jobs"}:
                    return await service.list_jobs(
                        limit=self._int(kwargs.get("limit"), 50, 1, 200),
                        status=str(kwargs.get("status") or ""),
                    )
                if act in {"get", "get_job"}:
                    return await service.get_job(str(kwargs.get("job_id") or ""))
                if act == "apply":
                    return await service.apply(
                        job_id=str(kwargs.get("job_id") or ""),
                        source_chat_id=str(kwargs.get("source_chat_id") or kwargs.get("source") or ""),
                        target_chat_id=str(kwargs.get("target_chat_id") or kwargs.get("target") or ""),
                        expected_fingerprint=str(kwargs.get("expected_fingerprint") or kwargs.get("expected") or ""),
                    )
                if act == "execute":
                    confirmed = bool(kwargs.get("confirmed") or kwargs.get("confirm_overwrite"))
                    return await service.execute(
                        job_id=str(kwargs.get("job_id") or ""),
                        confirmed=confirmed,
                        refresh_fingerprint=bool(kwargs.get("refresh_fingerprint", False)),
                    )
                if act in {"recover", "restore", "rollback"}:
                    return await service.recover(job_id=str(kwargs.get("job_id") or ""))
                return self._unsupported("chat_migration", act)
            except Exception as exc:
                return self._err(str(exc))

    async def tuning_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        with activate_context_shims(ctx):
            manager = self._tuning_manager(scope_key, ctx)
            act = self._action(action)
            if act in {"settings", "get_settings"}:
                return {"success": True, "settings": manager.get_runtime_settings(), "profile": manager.get_profile_snapshot()}
            if act in {"profile", "get_profile"}:
                return {"success": True, "profile": manager.get_profile_snapshot()}
            if act in {"export_toml", "export", "export_profile"}:
                return {"success": True, "toml": manager.export_toml_snippet()}
            if act in {"apply", "apply_profile"}:
                profile = kwargs.get("profile") if isinstance(kwargs.get("profile"), dict) else kwargs
                return {"success": True, **await manager.apply_profile(profile, reason=str(kwargs.get("reason", "admin:apply") or "admin:apply"))}
            if act in {"rollback", "rollback_profile"}:
                return {"success": True, **await manager.rollback_profile()}
            if act in {"create", "create_task"}:
                payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else kwargs
                return {"success": True, "task": await manager.create_task(payload)}
            if act in {"list", "list_tasks"}:
                items = await manager.list_tasks(limit=self._int(kwargs.get("limit"), 50, 1, 500))
                return {"success": True, "items": items, "count": len(items)}
            if act in {"get", "get_task"}:
                task = await manager.get_task(str(kwargs.get("task_id", "") or ""), include_rounds=bool(kwargs.get("include_rounds", False)))
                return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
            if act in {"rounds", "get_rounds"}:
                payload = await manager.get_rounds(
                    str(kwargs.get("task_id", "") or ""),
                    offset=self._int(kwargs.get("offset"), 0, 0, 1000000),
                    limit=self._int(kwargs.get("limit"), 50, 1, 500),
                )
                return {"success": payload is not None, **(payload or {}), "error": "" if payload is not None else "任务不存在"}
            if act == "cancel":
                task = await manager.cancel_task(str(kwargs.get("task_id", "") or ""))
                return {"success": task is not None, "task": task, "error": "" if task is not None else "任务不存在"}
            if act in {"apply_best", "apply_best_profile"}:
                return {"success": True, **await manager.apply_best(str(kwargs.get("task_id", "") or ""))}
            if act in {"report", "get_report"}:
                fmt = str(kwargs.get("format", kwargs.get("fmt", "md")) or "md").strip().lower()
                payload = await manager.get_report(str(kwargs.get("task_id", "") or ""), fmt=fmt)
                return {"success": payload is not None, **(payload or {}), "error": "" if payload is not None else "任务不存在"}
            return self._unsupported("tuning", act)

    def _tuning_manager(self, scope_key: str, ctx: Any) -> RetrievalTuningManager:
        manager = self._tuning_managers.get(scope_key)
        if manager is None:
            adapter = _TuningPluginAdapter(ctx)
            manager = RetrievalTuningManager(adapter, ctx_provider=lambda: adapter._ctx)
            self._tuning_managers[scope_key] = manager
        else:
            manager.plugin.bind(ctx)
        return manager

    async def feedback_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        if self.feedback_service is None:
            return self._err("反馈纠错服务未启用")
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act == "list":
                return await self.feedback_service.list_feedback_tasks(
                    ctx,
                    limit=self._int(kwargs.get("limit"), 50, 1, 500),
                    statuses=self._tokens(kwargs.get("status") or kwargs.get("statuses")),
                    rollback_statuses=self._tokens(kwargs.get("rollback_status") or kwargs.get("rollback_statuses")),
                    query=str(kwargs.get("query", "") or "").strip(),
                )
            if act == "get":
                return await self.feedback_service.get_feedback_task(ctx, int(kwargs.get("task_id", 0) or 0))
            if act == "rollback":
                return await self.feedback_service.rollback_feedback_task(
                    ctx,
                    task_id=int(kwargs.get("task_id", 0) or 0),
                    requested_by=str(kwargs.get("requested_by", "") or "").strip(),
                    reason=str(kwargs.get("reason", "") or "").strip(),
                )
            if act in {"logs", "list_logs", "action_logs"}:
                task_id = int(kwargs.get("task_id", 0) or 0)
                if task_id <= 0:
                    return self._err("task_id 不能为空")
                return {"success": True, "items": ctx.metadata_store.list_feedback_action_logs(task_id), "task_id": task_id}
            if act in {"reconcile", "trigger_reconcile"}:
                return {"success": True, "result": await self.feedback_service.trigger_reconcile(scope_key)}
            return self._unsupported("feedback", act)

    async def fuzzy_modify_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        # 模糊修改服务调度入口，镜像 feedback_admin 的 ctx 获取与 action 归一化方式
        if self.fuzzy_modify_service is None:
            return self._err("模糊修改服务未启用")
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        with activate_context_shims(ctx):
            if act in {"preview", "plan"}:
                call_kwargs = dict(kwargs)
                target_scope = str(
                    call_kwargs.pop("scope", "")
                    or ("person_profile" if (call_kwargs.get("person_id") or call_kwargs.get("person_keyword")) else "memory")
                ).strip()
                return await self.fuzzy_modify_service.preview(ctx, scope=target_scope, **call_kwargs)
            if act == "execute":
                return await self.fuzzy_modify_service.execute(ctx, **kwargs)
            if act == "get":
                return await self.fuzzy_modify_service.get(ctx, **kwargs)
            if act == "list":
                call_kwargs = dict(kwargs)
                list_scope = str(call_kwargs.pop("scope", "") or "").strip()
                return await self.fuzzy_modify_service.list(ctx, scope=list_scope, **call_kwargs)
            if act == "rollback":
                return await self.fuzzy_modify_service.rollback(ctx, **kwargs)
            if act == "reconcile":
                return await self.fuzzy_modify_service.reconcile(ctx)
            return self._unsupported("fuzzy_modify", act)

    async def correction_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        return await self.fuzzy_modify_admin(scope_key=scope_key, action=action, **kwargs)

    async def dispatch_contract(
        self,
        *,
        scope_key: str,
        component_name: str,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按 A_memorix v2.0.0 admin_contracts.py 统一解析并分发管理契约组件。"""
        clean_payload = {
            str(k): v
            for k, v in dict(payload or {}).items()
            if str(k) not in {"scope_key", "action"}
        }
        normalized_component = str(component_name or "").strip().lower()
        if normalized_component in {"memory_chat_migration", "memory_chat_migration_admin"}:
            return await self.chat_migration_admin(scope_key=scope_key, action=action, **clean_payload)
        command = parse_admin_command(
            component_name,
            {"action": action, **clean_payload},
        )
        return await dispatch_admin_command(_AdminContractKernelProxy(self, scope_key), command)

    async def v5_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        target = str(kwargs.get("target") or kwargs.get("query") or "").strip()
        limit = self._int(kwargs.get("limit"), 50, 1, 500)
        service = BaseMemoryService(ctx)
        with activate_context_shims(ctx):
            if act == "status":
                payload = await service.status()
                if target:
                    payload["target"] = target
                    payload["resolved_relation_hashes"] = await service._resolve_relations(target)
                return {"success": True, **payload}
            if act == "recycle_bin":
                items = ctx.metadata_store.get_deleted_relations(limit) + [
                    {**item, "type": "entity"} for item in ctx.metadata_store.get_deleted_entities(limit)
                ]
                return {"success": True, "items": items[:limit], "count": min(len(items), limit)}
            if act == "restore":
                restore_type = str(kwargs.get("restore_type") or kwargs.get("type") or "relation")
                return await service.restore(hash_value=target, restore_type=restore_type)
            if act == "reinforce":
                return await service.reinforce(query_or_hash=target)
            if act == "remember_forever":
                return await service.protect(query_or_hash=target, hours=0)
            if act == "forget":
                return await self._forget_relations(ctx, target)
            if act == "weaken":
                return await self._weaken_relations(ctx, target, float(kwargs.get("strength", 1.0) or 1.0))
            return self._unsupported("v5", act)

    async def delete_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        mode = str(kwargs.get("mode", "") or "").strip().lower()
        selector = kwargs.get("selector") if isinstance(kwargs.get("selector"), dict) else kwargs
        with activate_context_shims(ctx):
            if act == "preview":
                return self._delete_preview(ctx, mode, selector)
            if act == "execute":
                return await self._delete_execute(ctx, mode, selector)
            if act == "restore":
                operation_id = str(kwargs.get("operation_id") or selector.get("operation_id") or "").strip()
                if operation_id:
                    return await DeleteService(ctx).restore(operation_id)
                target = str(selector.get("hash") or selector.get("target") or selector.get("query") or "").strip()
                restore_type = str(selector.get("restore_type") or selector.get("type") or mode or "relation")
                return await BaseMemoryService(ctx).restore(hash_value=target, restore_type=restore_type)
            if act == "list_operations":
                items = ctx.metadata_store.list_delete_operations(
                    limit=self._int(kwargs.get("limit"), 50, 1, 200),
                    mode=str(kwargs.get("mode") or selector.get("mode") or ""),
                )
                return {"success": True, "items": items, "count": len(items)}
            if act == "get_operation":
                operation_id = str(
                    kwargs.get("operation_id") or selector.get("operation_id") or selector.get("target") or ""
                ).strip()
                operation = ctx.metadata_store.get_delete_operation(operation_id)
                if operation is None:
                    return self._err("delete operation 不存在")
                return {"success": True, "operation": operation}
            if act == "purge":
                hours = self._float_or_none(kwargs.get("older_than_hours")) or 24.0
                cutoff = time.time() - max(0.0, float(hours)) * 3600.0
                from .memory_projection_service import MemoryProjectionService

                async with ctx.graph_mutation_lock:
                    purged = ctx.metadata_store.purge_memory_operations(
                        cutoff, limit=self._int(kwargs.get("limit"), 1000, 1, 10000),
                    )
                    projection = await MemoryProjectionService(ctx).reconcile_locked()
                return {"success": True, "purged": purged, "count": len(purged), "cutoff_time": cutoff, "projection": projection}

            return self._unsupported("delete", act)

    async def _import_manager(self, scope_key: str, ctx: Any) -> ImportTaskManager:
        manager = self._import_managers.get(scope_key)
        if manager is None:
            manager = ImportTaskManager(ctx)
            await manager.start()
            self._import_managers[scope_key] = manager
        return manager

    def _cleanup_lock(self, scope_key: str) -> asyncio.Lock:
        lock = self._cleanup_locks.get(scope_key)
        if lock is None:
            lock = asyncio.Lock()
            self._cleanup_locks[scope_key] = lock
        return lock

    async def _image_runtime(self, scope_key: str, ctx: Any) -> Any:
        if not is_image_memory_enabled(config=ctx.config):
            return None
        existing_ctx_rt = getattr(ctx, "image_memory_runtime", None)
        if existing_ctx_rt is not None:
            return existing_ctx_rt
        raw_cfg = ctx.get_config("image_memory", {})
        image_cfg = dict(raw_cfg) if isinstance(raw_cfg, dict) else {}
        image_cfg["enabled"] = True
        cached = self._image_runtimes.get(scope_key)
        if cached is not None and getattr(cached, "metadata_store", None) is ctx.metadata_store:
            return cached
        from ..core.image import ImageAssetStore, ImageMemoryRuntime
        from ..core.image.embedder import HostImageEmbedder

        data_root = Path(ctx.data_dir)
        asset_store = ImageAssetStore(
            data_root / "images" / "assets",
            max_bytes=int(image_cfg.get("max_bytes", 10 * 1024 * 1024) or 10 * 1024 * 1024),
            max_pixels=int(image_cfg.get("max_pixels", 40_000_000) or 40_000_000),
        )

        def _persist_image_vectors(store: Any, fingerprint: Dict[str, Any]) -> None:
            store.save(embedding_fingerprint=fingerprint)

        runtime = ImageMemoryRuntime(
            metadata_store=ctx.metadata_store,
            asset_store=asset_store,
            embedder=HostImageEmbedder(
                task_name=str(image_cfg.get("task_name", "image_embedding") or "image_embedding"),
                preprocess_version=str(image_cfg.get("preprocess_version", "identity_v1") or "identity_v1"),
                probe_retry_seconds=float(image_cfg.get("probe_retry_seconds", 60.0) or 60.0),
            ),
            vector_root=data_root / "images" / "vectors",
            config=image_cfg,
            persist_vector_store=_persist_image_vectors,
        )
        await runtime.recover_assets()
        self._image_runtimes[scope_key] = runtime
        try:
            ctx.image_memory_runtime = runtime
        except Exception:
            pass
        return runtime

    async def _bundle_kernel_adapter(
        self,
        scope_key: str,
        ctx: Any,
        *,
        include_image_runtime: bool = True,
        include_import_manager: bool = True,
    ) -> _BundleAndImageKernelAdapter:
        import_mgr = await self._import_manager(scope_key, ctx) if include_import_manager else None
        img_rt = await self._image_runtime(scope_key, ctx) if include_image_runtime else None
        return _BundleAndImageKernelAdapter(
            ctx,
            storage_cleanup_lock=self._cleanup_lock(scope_key),
            import_task_manager=import_mgr,
            image_memory_runtime=img_rt,
        )

    def _serialize_graph(self, ctx: Any, *, limit: int) -> Dict[str, Any]:
        nodes = ctx.graph_store.get_nodes()[:limit]
        node_set = set(nodes)
        edges = []
        for source in nodes:
            for target in ctx.graph_store.get_neighbors(source):
                if target not in node_set:
                    continue
                hashes = list(ctx.graph_store.get_relation_hashes_for_edge(source, target))
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "weight": ctx.graph_store.get_edge_weight(source, target),
                        "relation_hashes": hashes,
                    }
                )
                if len(edges) >= limit:
                    break
            if len(edges) >= limit:
                break
        return {"nodes": nodes, "edges": edges, "node_count": ctx.graph_store.num_nodes, "edge_count": ctx.graph_store.num_edges}

    def _search_graph(self, ctx: Any, query: str, limit: int) -> Dict[str, Any]:
        q = query.strip().lower()
        nodes = [node for node in ctx.graph_store.get_nodes() if not q or q in str(node).lower()][:limit]
        rels = ctx.metadata_store.get_relations(subject=query) + ctx.metadata_store.get_relations(object=query) if query else []
        return {"success": True, "nodes": nodes, "relations": rels[:limit], "count": len(nodes) + min(len(rels), limit)}

    def _node_detail(self, ctx: Any, node: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        name = node.strip()
        if not name:
            return self._err("node_id 不能为空")
        resolved = ctx.graph_store.find_node(name) or name
        relations = ctx.metadata_store.get_relations(subject=resolved) + ctx.metadata_store.get_relations(object=resolved)
        paragraphs = ctx.metadata_store.get_paragraphs_by_entity(resolved)
        return {
            "success": True,
            "node": resolved,
            "exists": ctx.graph_store.has_node(resolved),
            "neighbors": ctx.graph_store.get_neighbors(resolved),
            "in_neighbors": ctx.graph_store.get_in_neighbors(resolved),
            "relations": relations[: self._int(kwargs.get("relation_limit"), 20, 1, 200)],
            "paragraphs": paragraphs[: self._int(kwargs.get("paragraph_limit"), 20, 1, 200)],
        }

    def _edge_detail(self, ctx: Any, source: str, target: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        s, t = source.strip(), target.strip()
        if not s or not t:
            return self._err("source/target 不能为空")
        rels = ctx.metadata_store.get_relations(subject=s, object=t)
        paragraphs = []
        for rel in rels:
            rel_hash = str(rel.get("hash", "") or "")
            if rel_hash:
                paragraphs.extend(ctx.metadata_store.get_paragraphs_by_relation(rel_hash))
        return {
            "success": True,
            "source": s,
            "target": t,
            "weight": ctx.graph_store.get_edge_weight(s, t),
            "relation_hashes": list(ctx.graph_store.get_relation_hashes_for_edge(s, t)),
            "relations": rels,
            "paragraphs": paragraphs[: self._int(kwargs.get("paragraph_limit"), 20, 1, 200)],
        }





    async def _delete_sources(self, ctx: Any, sources: Iterable[str]) -> Dict[str, Any]:
        result = await DeleteService(ctx).source(list(sources))
        return {**result, "deleted_count": result["deleted"]["paragraph"], "errors": []}


    async def _process_episode_pending(self, ctx: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        limit = self._int(kwargs.get("limit"), 20, 1, 200)
        max_retry = self._int(kwargs.get("max_retry"), 3, 0, 50)
        rows = ctx.metadata_store.fetch_episode_pending_batch(limit=limit, max_retry=max_retry)
        hashes = [str(row.get("paragraph_hash") or "") for row in rows if row.get("paragraph_hash")]
        if not hashes:
            return {"processed": 0, "episode_count": 0, "failed_hashes": {}}
        ctx.metadata_store.mark_episode_pending_running(hashes)
        result = await ctx.episode_service.process_pending_rows(rows)
        done = list(result.get("done_hashes") or [])
        failed = dict(result.get("failed_hashes") or {})
        ctx.metadata_store.mark_episode_pending_done(done)
        for h, err in failed.items():
            ctx.metadata_store.mark_episode_pending_failed(h, err)
        return {"processed": len(rows), **result}

    async def _forget_relations(self, ctx: Any, target: str) -> Dict[str, Any]:
        hashes = await BaseMemoryService(ctx)._resolve_relations(target)
        if not hashes:
            return self._err("未命中可删除关系")
        items = []
        for h in hashes:
            try:
                items.append(await DeleteService(ctx).relation(h))
            except Exception as exc:
                items.append({"success": False, "hash": h, "error": str(exc)})
        return {"success": all(bool(item.get("success")) for item in items), "items": items, "count": len(items)}

    async def _weaken_relations(self, ctx: Any, target: str, strength: float) -> Dict[str, Any]:
        hashes = await BaseMemoryService(ctx)._resolve_relations(target)
        if not hashes:
            return self._err("未命中可弱化关系")
        rels = [ctx.metadata_store.get_relation(h) for h in hashes]
        changed = 0
        for rel in rels:
            if not rel:
                continue
            ctx.graph_store.update_edge_weight(str(rel.get("subject")), str(rel.get("object")), -abs(strength))
            changed += 1
        ctx.graph_store.save()
        return {"success": True, "count": changed, "hashes": hashes}

    def _delete_preview(self, ctx: Any, mode: str, selector: Dict[str, Any]) -> Dict[str, Any]:
        service = DeleteService(ctx)
        query = str(selector.get("query") or selector.get("target") or selector.get("hash") or "").strip()
        if mode == "source":
            selectors = self._tokens(selector.get("sources")) or [str(selector.get("source", "") or "")]
        elif mode == "paragraph":
            selectors = [service.resolve_paragraph(query)]
        elif mode == "relation":
            selectors = [service.resolve_relation(query)]
        elif mode == "entity":
            selectors = [str(selector.get("entity_name") or selector.get("name") or query)]
        else:
            selectors = []
        return {"success": True, "dry_run": True, **ctx.metadata_store.preview_memory_delete(mode, selectors)}

    async def _delete_execute(self, ctx: Any, mode: str, selector: Dict[str, Any]) -> Dict[str, Any]:
        service = DeleteService(ctx)
        query = str(selector.get("query") or selector.get("target") or selector.get("hash") or "").strip()
        if mode == "paragraph":
            return await service.paragraph(query)
        if mode == "entity":
            return await service.entity(str(selector.get("entity_name") or selector.get("name") or query))
        if mode == "relation":
            return await service.relation(query)
        if mode == "source":
            sources = self._tokens(selector.get("sources")) or [str(selector.get("source", "") or "")]
            return await self._delete_sources(ctx, sources)
        if mode == "clear":
            return await service.clear()
        return self._err(f"不支持的 delete mode: {mode}")

    @staticmethod
    def _import_settings(manager: ImportTaskManager) -> Dict[str, Any]:
        return {
            "enabled": manager.is_enabled(),
            "path_aliases": manager.get_path_aliases(),
        }

    @staticmethod
    def _action(action: str) -> str:
        return str(action or "").strip().lower()

    @staticmethod
    def _err(message: str) -> Dict[str, Any]:
        return {"success": False, "error": message}

    @staticmethod
    def _unsupported(domain: str, action: str) -> Dict[str, Any]:
        return {"success": False, "error": f"不支持的 {domain} action: {action}"}

    @staticmethod
    def _tokens(raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, (list, tuple, set)):
            return [str(item).strip() for item in raw if str(item).strip()]
        text = str(raw or "").strip()
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    @staticmethod
    def _int(raw: Any, default: int, min_value: int, max_value: int) -> int:
        try:
            value = int(raw)
        except Exception:
            value = default
        return max(min_value, min(max_value, value))

    @staticmethod
    def _float_or_none(raw: Any) -> Optional[float]:
        if raw in {None, ""}:
            return None
        try:
            return float(raw)
        except Exception:
            return None
