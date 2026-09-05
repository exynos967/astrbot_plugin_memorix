"""Delete orchestration service."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Sequence

from ...core.utils.hash import compute_hash, normalize_text
from ..context import AppContext


def _unique_tokens(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


class DeleteService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    @staticmethod
    def _looks_like_hash(text: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(text or "").strip()))

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    def _dual_pools_enabled(self) -> bool:
        checker = getattr(self.ctx, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        return bool(getattr(self.ctx, "_dual_vector_pools_ready", False))

    def _delete_vectors(
        self,
        *,
        paragraph_hashes: Sequence[str] = (),
        entity_hashes: Sequence[str] = (),
        relation_hashes: Sequence[str] = (),
    ) -> int:
        deleted = 0
        legacy_ids = _unique_tokens([*paragraph_hashes, *entity_hashes, *relation_hashes])
        if legacy_ids:
            deleted += int(self.ctx.vector_store.delete(legacy_ids) or 0)
            self.ctx.vector_store.save()
        if not self._dual_pools_enabled():
            return deleted
        paragraph_ids = _unique_tokens(paragraph_hashes)
        paragraph_store = getattr(self.ctx, "paragraph_vector_store", None)
        if paragraph_store is not None and paragraph_ids:
            deleted += int(paragraph_store.delete(paragraph_ids) or 0)
            paragraph_store.save()
        graph_ids = [
            self._graph_vector_id("entity", hash_value) for hash_value in _unique_tokens(entity_hashes)
        ]
        graph_ids.extend(
            self._graph_vector_id("relation", hash_value) for hash_value in _unique_tokens(relation_hashes)
        )
        graph_store = getattr(self.ctx, "graph_vector_store", None)
        if graph_store is not None and graph_ids:
            deleted += int(graph_store.delete(graph_ids) or 0)
            graph_store.save()
        return deleted

    def resolve_paragraph(self, paragraph_spec: str) -> str:
        query = str(paragraph_spec or "").strip()
        if not query:
            raise ValueError("paragraph_spec is empty")

        target = None
        if self._looks_like_hash(query):
            target = self.ctx.metadata_store.get_paragraph(query.lower())
            if not target:
                raise ValueError(f"paragraph not found: {query}")
        else:
            matches = self.ctx.metadata_store.search_paragraphs_by_content(query)
            if not matches:
                raise ValueError("paragraph not found")
            if len(matches) > 1:
                query_norm = normalize_text(query)
                exact = [m for m in matches if normalize_text(str(m.get("content", ""))) == query_norm]
                if len(exact) != 1:
                    raise ValueError("multiple paragraphs matched, use hash")
                target = exact[0]
            else:
                target = matches[0]

        return str(target["hash"])

    async def paragraph(self, paragraph_spec: str) -> Dict[str, Any]:
        paragraph_hash = self.resolve_paragraph(paragraph_spec)
        return {**await self.execute("paragraph", [paragraph_hash]), "paragraph_hash": paragraph_hash}

    async def entity(self, entity_name: str) -> Dict[str, Any]:
        return {**await self.execute("entity", [entity_name]), "entity_name": entity_name}

    def resolve_relation(self, relation_spec: str) -> str:
        query = str(relation_spec or "").strip()
        if not query:
            raise ValueError("relation_spec is empty")

        relation = None
        if self._looks_like_hash(query):
            rel_hash = query.lower()
            relation = self.ctx.metadata_store.get_relation(rel_hash)
            if not relation:
                raise ValueError(f"relation not found: {rel_hash}")
        else:
            if "|" in query:
                parts = [p.strip() for p in query.split("|")]
                if len(parts) != 3:
                    raise ValueError("relation format should be subject|predicate|object")
                s, p, o = parts
            else:
                parts = query.split(maxsplit=2)
                if len(parts) != 3:
                    raise ValueError("relation format should be subject predicate object")
                s, p, o = parts
            rel_hash = compute_hash(f"{s.lower()}|{p.lower()}|{o.lower()}")
            relation = self.ctx.metadata_store.get_relation(rel_hash)
            if not relation:
                raise ValueError("relation not found")

        return rel_hash

    async def relation(self, relation_spec: str) -> Dict[str, Any]:
        rel_hash = self.resolve_relation(relation_spec)
        return {**await self.execute("relation", [rel_hash]), "relation_hash": rel_hash}

    async def clear(self) -> Dict[str, Any]:
        return await self.execute("clear", [])

    async def source(self, sources: Sequence[str]) -> Dict[str, Any]:
        return await self.execute("source", sources)

    async def execute(self, mode: str, selectors: Sequence[str], *, reason="", requested_by="") -> Dict[str, Any]:
        from ...services.memory_projection_service import MemoryProjectionService

        async with self.ctx.graph_mutation_lock:
            result = self.ctx.metadata_store.delete_memories(mode, selectors, reason=reason, requested_by=requested_by)
            result["projection"] = await MemoryProjectionService(self.ctx).reconcile_locked(operation_id=result.get("operation_id"))
            return result

    async def restore(self, operation_id: str) -> Dict[str, Any]:
        from ...services.memory_projection_service import MemoryProjectionService

        async with self.ctx.graph_mutation_lock:
            result = self.ctx.metadata_store.restore_memory_operation(operation_id)
            result["projection"] = await MemoryProjectionService(self.ctx).reconcile_locked(limit=10, operation_id=operation_id)
            return result
