"""工具与 WebUI 共用的图谱写入边界。"""

import math
from typing import Any

from ..amemorix.services.delete_service import DeleteService
from .graph_rename_service import GraphRenameService


class GraphMutationService:
    def __init__(self, ctx: Any):
        self.ctx = ctx

    @staticmethod
    def _weight(value: Any) -> float:
        weight = float(value)
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("weight/confidence 必须是 0 到 1 之间的有限数值")
        return weight

    async def execute(self, action: str, **kwargs) -> dict:
        try:
            if action in {"delete_node", "delete_edge"}:
                return await self._execute(action, kwargs)
            async with self.ctx.graph_mutation_lock:
                return await self._execute(action, kwargs)
        except (TypeError, ValueError) as error:
            return {"success": False, "error": str(error)}

    async def _execute(self, action: str, kwargs: dict) -> dict:
        ctx = self.ctx
        if action == "rename_node":
            return await GraphRenameService(ctx).rename(
                str(kwargs.get("old_name") or kwargs.get("name") or kwargs.get("node") or "").strip(),
                str(kwargs.get("new_name") or kwargs.get("target_name") or "").strip(),
            )
        if action in {"create_node", "delete_node"}:
            name = str(kwargs.get("name") or kwargs.get("node") or kwargs.get("node_id") or kwargs.get("target") or "").strip()
            if not name:
                raise ValueError("node name 不能为空")
            if action == "delete_node":
                return await DeleteService(ctx).entity(name)
            entity_hash = ctx.metadata_store.add_entity(name=name, metadata=kwargs.get("metadata") or {})
            added = ctx.graph_store.add_nodes([name])
            await ctx.save_all()
            return {"success": True, "added_count": added, "node": {"name": name, "hash": entity_hash}}
        relation_hash = str(kwargs.get("hash") or kwargs.get("relation_hash") or "").strip()
        if action == "delete_edge" and relation_hash:
            return await DeleteService(ctx).relation(relation_hash)
        subject = str(kwargs.get("subject") or kwargs.get("source") or "").strip()
        obj = str(kwargs.get("object") or kwargs.get("target") or "").strip()
        if not subject or not obj:
            raise ValueError("subject/object 不能为空")
        if action == "delete_edge":
            relations = ctx.metadata_store.get_relations(subject=subject, object=obj)
            if relations:
                return await DeleteService(ctx).execute("relation", [row["hash"] for row in relations])
            items = []
            if not relations:
                async with ctx.graph_mutation_lock:
                    if ctx.metadata_store.get_relations(subject=subject, object=obj):
                        raise ValueError("关系状态已变化，请重试")
                    ctx.graph_store.delete_edges([(subject, obj)])
                    await ctx.save_all()
            return {"success": True, "deleted": len(items), "items": items}
        if action == "create_edge":
            weight = self._weight(kwargs.get("confidence", kwargs.get("weight", 1.0)))
            predicate = str(kwargs.get("predicate") or kwargs.get("label") or "关联").strip()
            ctx.metadata_store.add_entity(subject)
            ctx.metadata_store.add_entity(obj)
            relation_hash = ctx.metadata_store.add_relation(
                subject, predicate, obj, confidence=weight,
                source_paragraph=kwargs.get("source_paragraph"), metadata=kwargs.get("metadata") or {},
            )
            row = ctx.metadata_store.get_relation(relation_hash)
            if row is None:
                raise RuntimeError("关系写入后未找到 metadata 记录")
            weight = float(row["confidence"])
            if relation_hash not in ctx.graph_store.get_relation_hashes_for_edge(subject, obj):
                ctx.graph_store.add_edges([(subject, obj)], weights=[weight], relation_hashes=[relation_hash])
            if ctx.get_config("retrieval.relation_vectorization.enabled", False):
                await ctx.relation_write_service.ensure_relation_vector(
                    hash_value=relation_hash, subject=subject, predicate=predicate, obj=obj,
                    typed_id=ctx._dual_vector_pools_enabled(),
                )
            await ctx.save_all()
            return {"success": True, "edge": {"hash": relation_hash, "subject": subject, "predicate": predicate, "object": obj, "weight": weight}}
        if action == "update_edge_weight":
            weight = self._weight(kwargs.get("weight", kwargs.get("confidence", 1.0)))
            relations = ctx.metadata_store.get_relations(subject=subject, object=obj)
            if not relations:
                raise ValueError("未找到对应的 metadata 关系")
            with ctx.metadata_store.transaction(immediate=True) as conn:
                for row in relations:
                    conn.execute("UPDATE relations SET confidence = ? WHERE hash = ?", (weight, row["hash"]))
            current = ctx.graph_store.get_edge_weight(subject, obj)
            ctx.graph_store.update_edge_weight(subject, obj, weight - current, min_weight=0.0)
            await ctx.save_all()
            return {"success": True, "source": subject, "target": obj, "new_weight": weight}
        raise ValueError(f"不支持的 graph action: {action}")
