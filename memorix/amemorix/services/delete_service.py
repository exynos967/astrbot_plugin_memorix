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

    async def paragraph(self, paragraph_spec: str) -> Dict[str, Any]:
        query = str(paragraph_spec or "").strip()
        if not query:
            raise ValueError("paragraph_spec is empty")

        target = None
        if self._looks_like_hash(query):
            target = self.ctx.metadata_store.get_paragraph(query)
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

        paragraph_hash = str(target["hash"])
        plan = self.ctx.metadata_store.delete_paragraph_atomic(paragraph_hash)
        relation_prune_ops = plan.get("relation_prune_ops", []) or []
        edges_to_remove = plan.get("edges_to_remove", []) or []

        if relation_prune_ops and hasattr(self.ctx.graph_store, "prune_relation_hashes"):
            self.ctx.graph_store.prune_relation_hashes(relation_prune_ops)
        elif edges_to_remove:
            self.ctx.graph_store.delete_edges(edges_to_remove)

        paragraph_ids = []
        relation_ids = []
        vid = plan.get("vector_id_to_remove")
        if vid:
            paragraph_ids.append(str(vid))
        for op in relation_prune_ops:
            if len(op) >= 3 and op[2]:
                relation_ids.append(str(op[2]))
        deleted_vectors = self._delete_vectors(
            paragraph_hashes=paragraph_ids,
            relation_hashes=relation_ids,
        )
        self.ctx.graph_store.save()
        return {
            "success": True,
            "paragraph_hash": paragraph_hash,
            "relation_prune_count": len(relation_prune_ops),
            "deleted_vectors": deleted_vectors,
        }

    async def entity(self, entity_name: str) -> Dict[str, Any]:
        target = str(entity_name or "").strip()
        if not target:
            raise ValueError("entity_name is empty")
        canonical = target.lower()
        if not self.ctx.graph_store.has_node(canonical):
            raise ValueError(f"entity not found: {canonical}")

        neighbors = self.ctx.graph_store.get_neighbors(canonical)
        rel_hashes = {
            str(r["hash"])
            for r in (
                self.ctx.metadata_store.get_relations(subject=canonical)
                + self.ctx.metadata_store.get_relations(object=canonical)
            )
            if r.get("hash")
        }

        self.ctx.graph_store.delete_nodes([canonical])
        self.ctx.metadata_store.delete_entity(canonical)
        entity_hash = compute_hash(canonical)
        deleted_vectors = self._delete_vectors(
            entity_hashes=[entity_hash],
            relation_hashes=list(rel_hashes),
        )
        self.ctx.graph_store.save()
        return {
            "success": True,
            "entity_name": canonical,
            "deleted_edges": len(neighbors),
            "deleted_vectors": deleted_vectors,
        }

    async def relation(self, relation_spec: str) -> Dict[str, Any]:
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

        # Keep relation restore path functional: delete into recycle-bin first.
        deleted_count = self.ctx.metadata_store.backup_and_delete_relations([rel_hash])
        if int(deleted_count) <= 0:
            raise RuntimeError("delete relation failed")

        subject = str(relation.get("subject", ""))
        obj = str(relation.get("object", ""))
        if hasattr(self.ctx.graph_store, "prune_relation_hashes"):
            self.ctx.graph_store.prune_relation_hashes([(subject, obj, rel_hash)])
        else:
            self.ctx.graph_store.delete_edges([(subject, obj)])

        deleted_vectors = self._delete_vectors(relation_hashes=[rel_hash])
        self.ctx.graph_store.save()
        return {
            "success": True,
            "relation_hash": rel_hash,
            "subject": subject,
            "predicate": str(relation.get("predicate", "")),
            "object": obj,
            "deleted_vectors": deleted_vectors,
        }

    async def clear(self) -> Dict[str, Any]:
        counts = {
            "paragraphs": self.ctx.metadata_store.count_paragraphs(),
            "relations": self.ctx.metadata_store.count_relations(),
            "entities": self.ctx.metadata_store.count_entities(),
            "vectors": self.ctx.vector_store.num_vectors,
        }
        self.ctx.vector_store.clear()
        if self._dual_pools_enabled():
            paragraph_store = getattr(self.ctx, "paragraph_vector_store", None)
            graph_vector_store = getattr(self.ctx, "graph_vector_store", None)
            if paragraph_store is not None:
                paragraph_store.clear()
                paragraph_store.save()
            if graph_vector_store is not None:
                graph_vector_store.clear()
                graph_vector_store.save()
        self.ctx.graph_store.clear()
        self.ctx.metadata_store.clear_all()
        self.ctx.vector_store.save()
        self.ctx.graph_store.save()
        return {"success": True, "deleted": counts}
