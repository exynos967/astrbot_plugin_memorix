"""Query orchestration service."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ...core.utils.aggregate_query_service import AggregateQueryService
from ...core.utils.search_execution_service import (
    SearchExecutionRequest,
    SearchExecutionService,
)
from ...core.utils.time_parser import extract_query_time_intent, parse_query_time_range
from ..common.logging import get_logger
from ..context import AppContext

logger = get_logger("A_Memorix.QueryService")


def _is_migration_archived_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    src = str(item.get("source") or "").strip()
    if src.startswith("migration_archive:"):
        return True
    meta = item.get("metadata")
    if isinstance(meta, str) and meta.strip():
        try:
            meta = json.loads(meta)
        except Exception:
            meta = None
    if isinstance(meta, dict):
        if str(meta.get("source") or "").strip().startswith("migration_archive:"):
            return True
        if str(meta.get("scope_type") or "").strip().lower() == "archive":
            return True
    return False


class QueryService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    def _filter_archived_results(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not items:
            return []
        pre_filtered = [item for item in items if not _is_migration_archived_item(item)]
        if not pre_filtered:
            return []

        metadata_store = getattr(self.ctx, "metadata_store", None)
        if metadata_store is None:
            return pre_filtered

        para_hashes = [
            str(item.get("hash") or "").strip()
            for item in pre_filtered
            if isinstance(item, dict)
            and str(item.get("type") or "paragraph").strip().lower() == "paragraph"
            and str(item.get("hash") or "").strip()
        ]
        archived_hashes: set[str] = set()
        if para_hashes:
            batch_getter = getattr(metadata_store, "get_paragraphs_by_hashes", None)
            single_getter = getattr(metadata_store, "get_paragraph", None)
            if callable(batch_getter):
                rows_by_hash = batch_getter(para_hashes) or {}
                for h, db_row in rows_by_hash.items():
                    if _is_migration_archived_item(db_row):
                        archived_hashes.add(str(h or "").strip())
            elif callable(single_getter):
                for h in dict.fromkeys(para_hashes):
                    db_row = single_getter(h)
                    if _is_migration_archived_item(db_row):
                        archived_hashes.add(h)

        if not archived_hashes:
            return pre_filtered
        return [
            item
            for item in pre_filtered
            if not (
                str(item.get("type") or "paragraph").strip().lower() == "paragraph"
                and str(item.get("hash") or "").strip() in archived_hashes
            )
        ]

    def _plugin_config(self) -> Dict[str, Any]:
        cfg = dict(self.ctx.config)
        cfg["plugin_instance"] = self.ctx
        cfg["graph_store"] = self.ctx.graph_store
        cfg["metadata_store"] = self.ctx.metadata_store
        return cfg

    async def _execute_request(
        self,
        *,
        caller: str,
        query_type: str,
        query: str,
        top_k: Optional[int] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        person: Optional[str] = None,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
        strict_source: bool = False,
        enforce_chat_filter: bool = False,
        route_reason: str = "",
        matched_time: str = "",
    ) -> Dict[str, Any]:
        req = SearchExecutionRequest(
            caller=caller,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            query_type=query_type,
            query=str(query or ""),
            top_k=top_k,
            time_from=time_from,
            time_to=time_to,
            person=person,
            source=source,
            strict_source=bool(strict_source),
            use_threshold=True,
            enable_ppr=bool(self.ctx.get_config("retrieval.enable_ppr", True)),
        )
        result = await SearchExecutionService.execute(
            retriever=self.ctx.retriever,
            threshold_filter=self.ctx.threshold_filter,
            plugin_config=self._plugin_config(),
            request=req,
            enforce_chat_filter=bool(enforce_chat_filter),
            reinforce_access=True,
        )
        if not result.success:
            raise ValueError(result.error)
        serialized_results = self._filter_archived_results(
            SearchExecutionService.to_serializable_results(result.results)
        )
        payload = {
            "query_type": result.query_type,
            "query": result.query,
            "top_k": result.top_k,
            "count": len(serialized_results),
            "elapsed_ms": result.elapsed_ms,
            "results": serialized_results,
        }
        if result.chat_filtered:
            payload["filtered"] = True
        if result.query_type in {"time", "hybrid"}:
            payload["time_from"] = result.time_from
            payload["time_to"] = result.time_to
        if route_reason:
            payload["route_reason"] = route_reason
        if matched_time:
            payload["matched_time"] = matched_time
        return payload

    async def search(
        self,
        *,
        query: str,
        top_k: Optional[int] = None,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
        strict_source: bool = False,
        enforce_chat_filter: bool = False,
    ) -> Dict[str, Any]:
        return await self._execute_request(
            caller="v1.search",
            query_type="search",
            query=query,
            top_k=top_k,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            source=source,
            strict_source=strict_source,
            enforce_chat_filter=enforce_chat_filter,
        )

    async def time_search(
        self,
        *,
        query: str = "",
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        top_k: Optional[int] = None,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        enforce_chat_filter: bool = False,
    ) -> Dict[str, Any]:
        parse_query_time_range(time_from, time_to)
        return await self._execute_request(
            caller="v1.time",
            query_type="time",
            query=query,
            top_k=top_k,
            time_from=time_from,
            time_to=time_to,
            person=person,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            source=source,
            enforce_chat_filter=enforce_chat_filter,
        )

    async def auto_search(
        self,
        *,
        query: str,
        top_k: Optional[int] = None,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
        strict_source: bool = False,
        enforce_chat_filter: bool = False,
    ) -> Dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            raise ValueError("query is empty")

        if not bool(self.ctx.get_config("retrieval.auto_route.enabled", True)):
            return await self.search(
                query=text,
                top_k=top_k,
                stream_id=stream_id,
                group_id=group_id,
                user_id=user_id,
                source=source,
                strict_source=strict_source,
                enforce_chat_filter=enforce_chat_filter,
            )

        if bool(self.ctx.get_config("retrieval.auto_route.enable_time_intent", True)):
            intent = extract_query_time_intent(text)
            if intent is not None:
                routed_query = intent.cleaned_query if intent.query_type == "hybrid" else ""
                try:
                    return await self._execute_request(
                        caller="v1.auto_search",
                        query_type=intent.query_type,
                        query=routed_query,
                        top_k=top_k,
                        time_from=intent.time_from,
                        time_to=intent.time_to,
                        stream_id=stream_id,
                        group_id=group_id,
                        user_id=user_id,
                        source=source,
                        strict_source=strict_source,
                        enforce_chat_filter=enforce_chat_filter,
                        route_reason="time_intent",
                        matched_time=intent.matched_text,
                    )
                except ValueError as exc:
                    logger.debug("auto search time intent fallback to semantic search: %s", exc)

        return await self.search(
            query=text,
            top_k=top_k,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            source=source,
            strict_source=strict_source,
            enforce_chat_filter=enforce_chat_filter,
        )

    async def entity(self, *, entity_name: str) -> Dict[str, Any]:
        target = str(entity_name or "").strip()
        if not target:
            raise ValueError("entity_name is empty")
        if not self.ctx.graph_store.has_node(target):
            raise ValueError(f"entity not found: {target}")
        neighbors = self.ctx.graph_store.get_neighbors(target)
        paragraphs = [
            item for item in self.ctx.metadata_store.get_paragraphs_by_entity(target) if not _is_migration_archived_item(item)
        ]
        relations = self.ctx.metadata_store.get_relations(subject=target) + self.ctx.metadata_store.get_relations(object=target)
        return {
            "entity_name": target,
            "neighbors": neighbors,
            "paragraphs": paragraphs,
            "relations": relations,
        }

    async def relation(self, *, subject: str = "", predicate: str = "", obj: str = "") -> Dict[str, Any]:
        rels = self.ctx.metadata_store.get_relations(
            subject=subject or None,
            predicate=predicate or None,
            object=obj or None,
        )
        return {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "count": len(rels),
            "relations": rels,
        }

    async def episode(
        self,
        *,
        query: str = "",
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        top_k: Optional[int] = None,
        include_paragraphs: bool = False,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        enforce_chat_filter: bool = False,
    ) -> Dict[str, Any]:
        safe_top_k = max(1, min(50, int(top_k or self.ctx.get_config("retrieval.temporal.default_top_k", 10))))
        checker = getattr(self.ctx, "is_chat_enabled", None)
        if enforce_chat_filter and callable(checker) and not checker(
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "query_type": "episode",
                "query": query,
                "time_from": time_from,
                "time_to": time_to,
                "top_k": safe_top_k,
                "count": 0,
                "results": [],
                "filtered": True,
            }

        ts_from, ts_to = parse_query_time_range(time_from, time_to) if (time_from or time_to) else (None, None)
        results = [
            item
            for item in await self.ctx.episode_retrieval_service.query(
                query=query,
                top_k=safe_top_k,
                time_from=ts_from,
                time_to=ts_to,
                person=person,
                source=source,
                include_paragraphs=include_paragraphs,
            )
            if not _is_migration_archived_item(item)
        ]
        return {
            "query_type": "episode",
            "query": query,
            "time_from": time_from,
            "time_to": time_to,
            "top_k": safe_top_k,
            "count": len(results),
            "results": results,
        }

    async def aggregate(
        self,
        *,
        query: str = "",
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        top_k: Optional[int] = None,
        mix: bool = True,
        mix_top_k: Optional[int] = None,
        stream_id: Optional[str] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        enforce_chat_filter: bool = False,
    ) -> Dict[str, Any]:
        safe_top_k = max(1, min(50, int(top_k or self.ctx.get_config("retrieval.temporal.default_top_k", 10))))
        checker = getattr(self.ctx, "is_chat_enabled", None)
        if enforce_chat_filter and callable(checker) and not checker(
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
        ):
            return {
                "success": True,
                "query_type": "aggregate",
                "query": query,
                "top_k": safe_top_k,
                "mix": bool(mix),
                "mix_top_k": max(1, int(mix_top_k if mix_top_k is not None else safe_top_k)),
                "count": 0,
                "results": [],
                "mixed_results": [],
                "filtered": True,
            }

        async def _search_runner() -> Dict[str, Any]:
            payload = await self.search(
                query=query,
                top_k=safe_top_k,
                stream_id=stream_id,
                group_id=group_id,
                user_id=user_id,
                source=source,
                enforce_chat_filter=enforce_chat_filter,
            )
            payload["success"] = True
            return payload

        async def _time_runner() -> Dict[str, Any]:
            payload = await self.time_search(
                query=query,
                time_from=time_from,
                time_to=time_to,
                person=person,
                source=source,
                top_k=safe_top_k,
                stream_id=stream_id,
                group_id=group_id,
                user_id=user_id,
                enforce_chat_filter=enforce_chat_filter,
            )
            payload["success"] = True
            return payload

        async def _episode_runner() -> Dict[str, Any]:
            payload = await self.episode(
                query=query,
                time_from=time_from,
                time_to=time_to,
                person=person,
                source=source,
                top_k=safe_top_k,
                stream_id=stream_id,
                group_id=group_id,
                user_id=user_id,
                enforce_chat_filter=enforce_chat_filter,
            )
            payload["success"] = True
            return payload

        return await AggregateQueryService(self.ctx).execute(
            query=query,
            top_k=safe_top_k,
            mix=bool(mix),
            mix_top_k=mix_top_k,
            time_from=time_from,
            time_to=time_to,
            search_runner=_search_runner,
            time_runner=_time_runner if (time_from or time_to) else None,
            episode_runner=_episode_runner,
        )

    async def stats(self) -> Dict[str, Any]:
        vector_stats = {"num_vectors": self.ctx.vector_store.num_vectors, "dimension": self.ctx.vector_store.dimension}
        graph_stats = {"num_nodes": self.ctx.graph_store.num_nodes, "num_edges": self.ctx.graph_store.num_edges}
        metadata_stats = self.ctx.metadata_store.get_statistics()
        return {
            "vector_store": vector_stats,
            "graph_store": graph_stats,
            "metadata_store": metadata_stats,
            "retriever": self.ctx.retriever.get_statistics(),
            "sparse": self.ctx.sparse_index.stats() if self.ctx.sparse_index is not None else None,
        }
