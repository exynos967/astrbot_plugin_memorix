"""可重试的记忆派生索引同步；重试时重新读取当前状态。"""

import time

from ..core.storage.memory_operations import TABLES


class MemoryProjectionService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.store = ctx.metadata_store

    async def reconcile(self, limit=20):
        completed = 0
        result = {"completed": 0, "pending": 0}
        for _ in range(max(1, int(limit))):
            async with self.ctx.graph_mutation_lock:
                result = await self.reconcile_locked(limit=1)
            completed += result["completed"]
            if not result["processed"]:
                break
        return {**result, "completed": completed}

    async def reconcile_locked(self, limit=100, *, operation_id=None):
        rows = self.store._conn.execute(
            """SELECT j.* FROM memory_projection_jobs j WHERE next_attempt_at <= ?
               AND (? IS NULL OR EXISTS (SELECT 1 FROM delete_operation_items i
                   WHERE i.operation_id = ? AND i.item_type = j.item_type AND i.item_hash = j.item_hash))
               ORDER BY next_attempt_at, j.rowid LIMIT ?""",
            (time.time(), operation_id, operation_id, max(1, int(limit))),
        ).fetchall()
        completed = 0
        for job in rows:
            try:
                await self._project(job["item_type"], job["item_hash"], job["item_key"])
                self.store._conn.execute(
                    "DELETE FROM memory_projection_jobs WHERE item_type = ? AND item_hash = ? AND revision = ?",
                    (job["item_type"], job["item_hash"], job["revision"]),
                )
                completed += 1
            except Exception as exc:
                self.store._conn.execute(
                    """UPDATE memory_projection_jobs SET attempts = attempts + 1, last_error = ?, next_attempt_at = ?
                       WHERE item_type = ? AND item_hash = ? AND revision = ?""",
                    (
                        str(exc)[:500],
                        time.time() + min(300, 2 ** min(job["attempts"] + 1, 8)),
                        job["item_type"],
                        job["item_hash"],
                        job["revision"],
                    ),
                )
            self.store._conn.commit()
        pending = self.store._conn.execute("SELECT COUNT(*) FROM memory_projection_jobs").fetchone()[0]
        failures = [
            dict(row)
            for row in self.store._conn.execute(
                "SELECT item_type, item_hash, attempts, last_error FROM memory_projection_jobs WHERE last_error IS NOT NULL LIMIT 10",
            )
        ]
        return {"completed": completed, "pending": pending, "processed": len(rows), "failures": failures}

    def _read(self, kind, hash_value):
        row = self.store._conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE hash = ?", (hash_value,)).fetchone()
        return dict(row) if row else None

    async def _project(self, kind, hash_value, item_key=None):
        from ..amemorix.services.delete_service import DeleteService

        ctx = self.ctx
        row = self._read(kind, hash_value)
        live = row is not None and not row.get("is_deleted") and not row.get("is_inactive")
        if live:
            if kind == "entity":
                ctx.graph_store.add_nodes([row["name"]])
            elif kind == "relation":
                subject, obj = row["subject"], row["object"]
                if hash_value not in ctx.graph_store.get_relation_hashes_for_edge(subject, obj):
                    current = ctx.graph_store.get_edge_weight(subject, obj)
                    weight = max(current, float(row["confidence"]))
                    with ctx.graph_store.batch_update():
                        ctx.graph_store.add_edges([(subject, obj)], weights=[weight], relation_hashes=[hash_value])
            ctx.graph_store.save()
            dual = ctx._dual_vector_pools_enabled()
            vector_store = (
                (ctx.paragraph_vector_store if kind == "paragraph" else ctx.graph_vector_store)
                if dual
                else ctx.vector_store
            )
            vector_id = f"{kind}:{hash_value}" if dual and kind != "paragraph" else hash_value
            vector_enabled = kind != "relation" or ctx.get_config("retrieval.relation_vectorization.enabled", False)
            if vector_enabled and vector_id not in vector_store:
                text = (
                    row["content"]
                    if kind == "paragraph"
                    else row["name"]
                    if kind == "entity"
                    else f"{row['subject']} {row['predicate']} {row['object']}"
                )
                embedding = await ctx.embedding_manager.encode(text)
                # 导入可在模型调用期间重新写入记录，不能发布旧内容的向量。
                latest = self._read(kind, hash_value)
                if latest != row:
                    raise RuntimeError("memory changed during projection; retry")
                if vector_id not in vector_store:
                    vector_store.add(embedding.reshape(1, -1), [vector_id])
            if vector_enabled:
                vector_store.save()
                self.store.update_vector_index(kind, hash_value, 1)
        else:
            DeleteService(ctx)._delete_vectors(**{f"{kind}_hashes": [hash_value]})
            if row is not None:
                self.store.update_vector_index(kind, hash_value, None)
            if kind == "entity" and (row is not None or item_key):
                ctx.graph_store.delete_nodes([row["name"] if row else item_key])
            elif kind == "relation":
                ops = [
                    (s, o, hash_value)
                    for s, o, hashes in ctx.graph_store.iter_edge_hash_entries()
                    if hash_value in hashes
                ]
                ctx.graph_store.prune_relation_hashes(ops)
        ctx.graph_store.save()
