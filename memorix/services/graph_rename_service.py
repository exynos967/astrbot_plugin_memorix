"""A_memorix 图谱重命名事务及 AstrBot 派生索引适配。"""

import json
from typing import Any, Dict, List, Optional

from ..amemorix.services.delete_service import DeleteService
from ..core.utils.hash import compute_hash


class GraphRenameService:
    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.metadata_store = ctx.metadata_store

    async def rename(self, old_name: str, new_name: str) -> Dict[str, Any]:
        await self.reconcile()
        if self.metadata_store._conn.execute("SELECT 1 FROM graph_pending_renames LIMIT 1").fetchone():
            return {"success": False, "error": "已有重命名的索引更新未完成，请修复 embedding/存储错误后重试"}
        result = self._rename_node(old_name, new_name)
        if not result.get("success") or not result.get("renamed"):
            return result
        return await self._project(result)

    async def reconcile(self) -> None:
        rows = self.metadata_store._conn.execute("SELECT payload_json FROM graph_pending_renames ORDER BY rowid").fetchall()
        for row in rows:
            await self._project(json.loads(row[0]))

    async def _project(self, result: Dict[str, Any]) -> Dict[str, Any]:
        old_name, new_name = result["old_name"], result["new_name"]
        try:
            DeleteService(self.ctx)._delete_vectors(
                entity_hashes=[result["old_entity_hash"]],
                relation_hashes=result["old_relation_hashes"],
            )
            self.ctx.graph_store.delete_nodes([old_name])
            self.ctx.graph_store.add_nodes([new_name])
            relations = self.metadata_store.get_relations(subject=new_name)
            relations += self.metadata_store.get_relations(object=new_name)
            edges = {(row["subject"], row["object"]) for row in relations}
            self.ctx.graph_store.delete_edges(list(edges))
            for relation in {row["hash"]: row for row in relations}.values():
                if not relation.get("is_inactive"):
                    self.ctx.graph_store.add_edges(
                        [(relation["subject"], relation["object"])],
                        weights=[float(relation["confidence"])], relation_hashes=[relation["hash"]],
                    )
            vector_errors = []
            dual = self.ctx._dual_vector_pools_enabled()
            entity_store = self.ctx.graph_vector_store if dual else self.ctx.vector_store
            entity_id = f"entity:{result['entity_hash']}" if dual else result["entity_hash"]
            if entity_id not in entity_store:
                embedding = await self.ctx.embedding_manager.encode(new_name)
                entity_store.add(embedding.reshape(1, -1), [entity_id])
            self.metadata_store.update_vector_index("entity", result["entity_hash"], 1)
            if self.ctx.get_config("retrieval.relation_vectorization.enabled", False):
                for relation_hash in dict.fromkeys(result["relation_hash_map"].values()):
                    relation = self.metadata_store.get_relation(relation_hash)
                    if relation is not None:
                        write = await self.ctx.relation_write_service.ensure_relation_vector(
                            hash_value=relation_hash, subject=relation["subject"],
                            predicate=relation["predicate"], obj=relation["object"],
                            typed_id=self.ctx._dual_vector_pools_enabled(),
                        )
                        if write.vector_state == "failed":
                            vector_errors.append(relation_hash)
            await self.ctx.save_all()
            result["projection"] = {"status": "ready"}
            result["vector_projection"] = {"status": "pending" if vector_errors else "ready", "failed_hashes": vector_errors}
            if not vector_errors:
                self.metadata_store._conn.execute(
                    "DELETE FROM graph_pending_renames WHERE operation_id = ?", (result["operation"]["operation_id"],),
                )
                self.metadata_store._conn.commit()
        except Exception as error:
            result["projection"] = {"status": "failed", "error": str(error)}
        return result

    def _rename_node(
        self,
        old_name: str,
        new_name: str,
        *,
        reason: str = "graph_rename_node",
        updated_by: str = "memory_graph_admin",
        record_operation: bool = True,
    ) -> Dict[str, Any]:
        assert self.metadata_store
        source = str(old_name or "").strip()
        target = str(new_name or "").strip()
        if not source or not target:
            return {"success": False, "error": "old_name/new_name 不能为空"}
        if source == target:
            result = {"success": True, "renamed": False, "old_name": source, "new_name": target}
            if not record_operation:
                return result
            operation = self.metadata_store.record_v5_operation(
                action="graph_rename_node",
                target=source,
                resolved_hashes=[compute_hash(source.lower())],
                reason=reason,
                updated_by=updated_by,
                result=result,
            )
            return {"operation": operation, **result}

        old_hash = compute_hash(self.metadata_store._canonicalize_name(source))
        target_hash = compute_hash(self.metadata_store._canonicalize_name(target))
        old_relation_hashes: List[str] = []
        relation_hash_map: Dict[str, str] = {}
        resolved_target_hash = target_hash
        old_entity_hash = old_hash
        operation: Optional[Dict[str, Any]] = None
        authoritative_result: Dict[str, Any] = {}
        try:
            with self.metadata_store.transaction(immediate=True) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM entities
                    WHERE hash = ?
                       OR LOWER(TRIM(name)) = LOWER(TRIM(?))
                    LIMIT 1
                    """,
                    (old_hash, source),
                )
                old_row = cursor.fetchone()
                if old_row is None:
                    return {"success": False, "error": "原节点不存在"}
                old_entity_hash = str(old_row["hash"] or "").strip()
                old_entity_name = str(old_row["name"] or "").strip()
                cursor.execute(
                    """
                    SELECT DISTINCT p.source
                    FROM paragraph_entities pe
                    JOIN paragraphs p ON p.hash = pe.paragraph_hash
                    WHERE pe.entity_hash = ?
                      AND p.source IS NOT NULL AND TRIM(p.source) != ''
                      AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                    """,
                    (old_entity_hash,),
                )
                episode_sources = [str(row["source"] or "").strip() for row in cursor.fetchall()]

                cursor.execute(
                    """
                    SELECT *
                    FROM entities
                    WHERE hash = ?
                       OR LOWER(TRIM(name)) = LOWER(TRIM(?))
                    LIMIT 1
                    """,
                    (target_hash, target),
                )
                target_row = cursor.fetchone()

                if target_row is not None and str(target_row["hash"] or "").strip() == old_entity_hash:
                    resolved_target_hash = old_entity_hash
                    cursor.execute(
                        """
                        UPDATE entities
                        SET name = ?, vector_index = NULL, is_deleted = 0, deleted_at = NULL
                        WHERE hash = ?
                        """,
                        (target, old_entity_hash),
                    )
                elif target_row is None:
                    cursor.execute(
                        """
                        INSERT INTO entities (
                            hash, name, vector_index, appearance_count, created_at, metadata, is_deleted, deleted_at
                        ) VALUES (?, ?, NULL, ?, ?, ?, 0, NULL)
                        """,
                        (
                            target_hash,
                            target,
                            old_row["appearance_count"],
                            old_row["created_at"],
                            old_row["metadata"],
                        ),
                    )
                    resolved_target_hash = target_hash
                else:
                    resolved_target_hash = str(target_row["hash"] or "").strip()
                    cursor.execute(
                        """
                        UPDATE entities
                        SET name = ?,
                            appearance_count = COALESCE(appearance_count, 0) + ?,
                            is_deleted = 0,
                            deleted_at = NULL
                        WHERE hash = ?
                        """,
                        (target, int(old_row["appearance_count"] or 0), resolved_target_hash),
                    )

                if resolved_target_hash != old_entity_hash:
                    cursor.execute(
                        """
                        INSERT INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                        SELECT paragraph_hash, ?, mention_count
                        FROM paragraph_entities
                        WHERE entity_hash = ?
                        ON CONFLICT(paragraph_hash, entity_hash) DO UPDATE SET
                            mention_count = paragraph_entities.mention_count + excluded.mention_count
                        """,
                        (resolved_target_hash, old_entity_hash),
                    )
                    cursor.execute("DELETE FROM paragraph_entities WHERE entity_hash = ?", (old_entity_hash,))

                cursor.execute(
                    """
                    SELECT *
                    FROM relations
                    WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
                       OR LOWER(TRIM(object)) = LOWER(TRIM(?))
                    """,
                    (old_entity_name, old_entity_name),
                )
                affected_relations = cursor.fetchall()
                for relation_row in affected_relations:
                    relation_data = dict(relation_row)
                    old_relation_hash = str(relation_data["hash"] or "").strip()
                    relation_subject = str(relation_data.get("subject", "") or "").strip()
                    relation_object = str(relation_data.get("object", "") or "").strip()
                    if relation_subject.lower() == old_entity_name.lower():
                        relation_subject = target
                    if relation_object.lower() == old_entity_name.lower():
                        relation_object = target
                    new_relation_hash = self.metadata_store.compute_relation_hash(
                        relation_subject,
                        str(relation_data.get("predicate", "") or "").strip(),
                        relation_object,
                    )
                    relation_data.update(
                        {
                            "hash": new_relation_hash,
                            "subject": relation_subject,
                            "object": relation_object,
                            "vector_index": None,
                            "vector_state": "none",
                            "vector_updated_at": None,
                            "vector_error": None,
                            "vector_retry_count": 0,
                        }
                    )
                    old_relation_hashes.append(old_relation_hash)
                    relation_hash_map[old_relation_hash] = new_relation_hash

                    if new_relation_hash == old_relation_hash:
                        cursor.execute(
                            """
                            UPDATE relations
                            SET subject = ?, object = ?, vector_index = NULL,
                                vector_state = 'none', vector_updated_at = NULL,
                                vector_error = NULL, vector_retry_count = 0
                            WHERE hash = ?
                            """,
                            (relation_subject, relation_object, old_relation_hash),
                        )
                        continue

                    columns = list(relation_data)
                    placeholders = ",".join("?" for _ in columns)
                    cursor.execute(
                        f"INSERT OR IGNORE INTO relations ({','.join(columns)}) VALUES ({placeholders})",
                        tuple(relation_data[column] for column in columns),
                    )
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                        SELECT paragraph_hash, ? FROM paragraph_relations WHERE relation_hash = ?
                        """,
                        (new_relation_hash, old_relation_hash),
                    )
                    cursor.execute("DELETE FROM relations WHERE hash = ?", (old_relation_hash,))

                    cursor.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'graph_edge_relation_map'"
                    )
                    if cursor.fetchone() is not None:
                        cursor.execute(
                            "UPDATE OR IGNORE graph_edge_relation_map SET relation_hash = ? WHERE relation_hash = ?",
                            (new_relation_hash, old_relation_hash),
                        )
                        cursor.execute(
                            "DELETE FROM graph_edge_relation_map WHERE relation_hash = ?",
                            (old_relation_hash,),
                        )

                if resolved_target_hash != old_entity_hash:
                    cursor.execute("DELETE FROM entities WHERE hash = ?", (old_entity_hash,))
                self.metadata_store.rebuild_relation_hash_aliases(conn=conn)
                authoritative_result = {
                    "success": True,
                    "renamed": True,
                    "old_name": source,
                    "new_name": target,
                    "entity_hash": resolved_target_hash,
                    "relation_hash_map": relation_hash_map,
                }
                if record_operation:
                    operation = self.metadata_store.record_v5_operation(
                        action="graph_rename_node",
                        target=source,
                        resolved_hashes=[resolved_target_hash, *list(dict.fromkeys(relation_hash_map.values()))],
                        reason=reason,
                        updated_by=updated_by,
                        result=authoritative_result,
                        conn=conn,
                    )
                    conn.execute(
                        "INSERT INTO graph_pending_renames VALUES (?, ?)",
                        (operation["operation_id"], json.dumps({
                            **authoritative_result, "operation": operation,
                            "old_entity_hash": old_entity_hash, "old_relation_hashes": old_relation_hashes,
                        }, ensure_ascii=False)),
                    )
        except Exception as exc:
            return {"success": False, "error": f"rename failed: {exc}"}

        self.metadata_store._enqueue_episode_source_rebuilds(episode_sources, reason="entity_renamed")
        return {
            **authoritative_result,
            "operation": operation,
            "old_entity_hash": old_entity_hash,
            "old_relation_hashes": old_relation_hashes,
        }
