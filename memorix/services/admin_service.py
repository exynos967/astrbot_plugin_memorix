"""Admin tool service for AstrBot LLM tools.

These methods mirror the A_memorix admin-tool surface where the embedded
AstrBot runtime has the corresponding capability. Unsupported upstream-only
features return an explicit payload instead of silently creating fake behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from ..amemorix.services import DeleteService, PersonProfileApiService
from ..amemorix.services import MemoryService as BaseMemoryService
from ..amemorix.services.import_task_manager import ImportTaskManager
from ..app_context import ScopeRuntimeManager
from ..core.utils.retrieval_tuning_manager import RetrievalTuningManager
from ..core.utils.runtime_self_check import ensure_runtime_self_check


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


class AdminService:
    def __init__(self, runtime_manager: ScopeRuntimeManager, *, feedback_service: Any = None):
        self.runtime_manager = runtime_manager
        self.feedback_service = feedback_service
        self._import_managers: dict[str, ImportTaskManager] = {}
        self._tuning_managers: dict[str, RetrievalTuningManager] = {}

    async def close(self) -> None:
        import_managers = list(self._import_managers.values())
        self._import_managers.clear()
        tuning_managers = list(self._tuning_managers.values())
        self._tuning_managers.clear()
        for manager in import_managers:
            await manager.stop()
        for manager in tuning_managers:
            await manager.shutdown()

    async def graph_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)

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
        if act == "create_node":
            name = str(kwargs.get("name") or kwargs.get("node") or kwargs.get("node_id") or "").strip()
            if not name:
                return self._err("node name 不能为空")
            added = ctx.graph_store.add_nodes([name])
            entity_hash = ctx.metadata_store.add_entity(name=name, metadata=kwargs.get("metadata") or {})
            await ctx.save_all()
            return {"success": True, "added_count": added, "node": {"name": name, "hash": entity_hash}}
        if act == "delete_node":
            name = str(kwargs.get("name") or kwargs.get("node") or kwargs.get("node_id") or kwargs.get("target") or "").strip()
            if not name:
                return self._err("node name 不能为空")
            return await DeleteService(ctx).entity(name)
        if act == "rename_node":
            return await self._rename_node(ctx, kwargs)
        if act == "create_edge":
            return await self._create_edge(ctx, kwargs)
        if act == "delete_edge":
            return await self._delete_edge(ctx, kwargs)
        if act == "update_edge_weight":
            return self._update_edge_weight(ctx, kwargs)
        return self._unsupported("graph", act)

    async def source_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
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
        if act == "process_pending":
            return {"success": True, **await self._process_episode_pending(ctx, kwargs)}
        return self._unsupported("episode", act)

    async def profile_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        service = PersonProfileApiService(ctx)
        act = self._action(action)
        if act == "query":
            return await service.query(
                person_id=str(kwargs.get("person_id", "") or ""),
                person_keyword=str(kwargs.get("person_keyword") or kwargs.get("keyword") or ""),
                top_k=self._int(kwargs.get("limit", kwargs.get("top_k")), 12, 1, 100),
                force_refresh=bool(kwargs.get("force_refresh", False)),
                source_note="astrbot:memory_profile_admin.query",
            )
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
            return {"success": True, "person_profile_count": int(stats.get("person_profile_count", 0) or 0)}
        return self._unsupported("profile", act)

    async def runtime_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        if act == "save":
            await ctx.save_all()
            return {"success": True, "saved": True, "data_dir": str(ctx.data_dir)}
        if act == "get_config":
            return {
                "success": True,
                "config": ctx.config,
                "data_dir": str(ctx.data_dir),
                "auto_save": bool(ctx.get_config("advanced.enable_auto_save", True)),
                "runtime_ready": True,
            }
        if act in {"self_check", "refresh_self_check"}:
            return {"success": True, "report": await ensure_runtime_self_check(ctx, force=True)}
        if act == "set_auto_save":
            enabled = bool(kwargs.get("enabled", False))
            ctx._runtime_auto_save = enabled
            return {"success": True, "auto_save": enabled}
        return self._unsupported("runtime", act)

    async def import_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
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

    async def tuning_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        manager = self._tuning_manager(scope_key, ctx)
        act = self._action(action)
        if act in {"settings", "get_settings"}:
            return {"success": True, "settings": manager.get_runtime_settings(), "profile": manager.get_profile_snapshot()}
        if act in {"profile", "get_profile"}:
            return {"success": True, "profile": manager.get_profile_snapshot()}
        if act in {"export_toml", "export"}:
            return {"success": True, "toml": manager.export_toml_snippet()}
        if act == "apply":
            profile = kwargs.get("profile") if isinstance(kwargs.get("profile"), dict) else kwargs
            return {"success": True, **await manager.apply_profile(profile, reason=str(kwargs.get("reason", "admin:apply") or "admin:apply"))}
        if act == "rollback":
            return {"success": True, **await manager.rollback_profile()}
        if act in {"create", "create_task"}:
            payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else kwargs
            return {"success": True, "task": await manager.create_task(payload)}
        if act == "list":
            items = await manager.list_tasks(limit=self._int(kwargs.get("limit"), 50, 1, 500))
            return {"success": True, "items": items, "count": len(items)}
        if act == "get":
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

    async def v5_admin(self, *, scope_key: str, action: str, **kwargs) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        ctx = runtime.context
        act = self._action(action)
        target = str(kwargs.get("target") or kwargs.get("query") or "").strip()
        limit = self._int(kwargs.get("limit"), 50, 1, 500)
        service = BaseMemoryService(ctx)

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

        if act == "preview":
            return self._delete_preview(ctx, mode, selector)
        if act == "execute":
            return await self._delete_execute(ctx, mode, selector)
        if act == "restore":
            target = str(selector.get("hash") or selector.get("target") or selector.get("query") or "").strip()
            restore_type = str(selector.get("restore_type") or selector.get("type") or mode or "relation")
            return await BaseMemoryService(ctx).restore(hash_value=target, restore_type=restore_type)
        if act in {"list_operations", "get_operation", "purge"}:
            return self._err(f"当前 AstrBot 内嵌运行时暂不支持 delete action: {act}")
        return self._unsupported("delete", act)

    async def _import_manager(self, scope_key: str, ctx: Any) -> ImportTaskManager:
        manager = self._import_managers.get(scope_key)
        if manager is None:
            manager = ImportTaskManager(ctx)
            await manager.start()
            self._import_managers[scope_key] = manager
        return manager

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

    async def _rename_node(self, ctx: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        old_name = str(kwargs.get("old_name") or kwargs.get("name") or kwargs.get("node") or "").strip()
        new_name = str(kwargs.get("new_name") or kwargs.get("target_name") or "").strip()
        if not old_name or not new_name:
            return self._err("old_name/new_name 不能为空")
        if not ctx.graph_store.has_node(old_name):
            return self._err(f"node 不存在: {old_name}")
        out_neighbors = ctx.graph_store.get_neighbors(old_name)
        in_neighbors = ctx.graph_store.get_in_neighbors(old_name)
        ctx.graph_store.add_nodes([new_name])
        for neighbor in out_neighbors:
            weight = ctx.graph_store.get_edge_weight(old_name, neighbor)
            if weight > 0:
                ctx.graph_store.add_edges([(new_name, neighbor)], weights=[weight])
        for neighbor in in_neighbors:
            weight = ctx.graph_store.get_edge_weight(neighbor, old_name)
            if weight > 0:
                ctx.graph_store.add_edges([(neighbor, new_name)], weights=[weight])
        deleted = ctx.graph_store.delete_nodes([old_name])
        ctx.metadata_store.add_entity(name=new_name)
        await ctx.save_all()
        return {"success": True, "old_name": old_name, "new_name": new_name, "deleted_old_nodes": deleted}

    async def _create_edge(self, ctx: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        subject = str(kwargs.get("subject") or kwargs.get("source") or "").strip()
        predicate = str(kwargs.get("predicate") or kwargs.get("label") or "关联").strip()
        obj = str(kwargs.get("object") or kwargs.get("target") or "").strip()
        if not subject or not obj:
            return self._err("subject/object 不能为空")
        confidence = float(kwargs.get("confidence", kwargs.get("weight", 1.0)) or 1.0)
        relation_hash = ctx.metadata_store.add_relation(
            subject=subject,
            predicate=predicate,
            obj=obj,
            confidence=confidence,
            source_paragraph=kwargs.get("source_paragraph"),
            metadata=kwargs.get("metadata") or {},
        )
        ctx.graph_store.add_edges([(subject, obj)], weights=[confidence], relation_hashes=[relation_hash])
        await ctx.save_all()
        return {"success": True, "edge": {"hash": relation_hash, "subject": subject, "predicate": predicate, "object": obj, "weight": confidence}}

    async def _delete_edge(self, ctx: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        relation_hash = str(kwargs.get("hash") or kwargs.get("relation_hash") or "").strip()
        if relation_hash:
            return await DeleteService(ctx).relation(relation_hash)
        subject = str(kwargs.get("subject") or kwargs.get("source") or "").strip()
        obj = str(kwargs.get("object") or kwargs.get("target") or "").strip()
        if not subject or not obj:
            return self._err("subject/object 不能为空")
        rels = ctx.metadata_store.get_relations(subject=subject, object=obj)
        deleted = []
        for rel in rels:
            rel_hash = str(rel.get("hash", "") or "")
            if rel_hash:
                deleted.append(await DeleteService(ctx).relation(rel_hash))
        return {"success": True, "deleted": len(deleted), "items": deleted}

    def _update_edge_weight(self, ctx: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        source = str(kwargs.get("subject") or kwargs.get("source") or "").strip()
        target = str(kwargs.get("object") or kwargs.get("target") or "").strip()
        if not source or not target:
            return self._err("source/target 不能为空")
        weight = float(kwargs.get("weight", kwargs.get("confidence", 1.0)) or 1.0)
        current = ctx.graph_store.get_edge_weight(source, target)
        new_weight = ctx.graph_store.update_edge_weight(source, target, weight - current)
        ctx.graph_store.save()
        return {"success": True, "source": source, "target": target, "new_weight": new_weight}

    async def _delete_sources(self, ctx: Any, sources: Iterable[str]) -> Dict[str, Any]:
        deleted = 0
        errors = []
        service = DeleteService(ctx)
        for source in sources:
            for para in ctx.metadata_store.get_paragraphs_by_source(str(source)):
                try:
                    await service.paragraph(str(para.get("hash") or ""))
                    deleted += 1
                except Exception as exc:
                    errors.append({"source": source, "paragraph_hash": para.get("hash"), "error": str(exc)})
        return {"success": not errors, "deleted_count": deleted, "errors": errors}

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
        if mode == "source":
            sources = self._tokens(selector.get("sources")) or [str(selector.get("source", "") or "")]
            count = sum(len(ctx.metadata_store.get_paragraphs_by_source(src)) for src in sources if src)
            return {"success": True, "mode": mode, "paragraph_count": count, "sources": sources}
        query = str(selector.get("query") or selector.get("target") or selector.get("hash") or "").strip()
        return {"success": True, "mode": mode, "selector": selector, "target": query, "dry_run": True}

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
