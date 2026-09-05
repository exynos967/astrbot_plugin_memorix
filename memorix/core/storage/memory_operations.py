"""按操作记录删除与恢复；SQLite 是记忆状态的唯一事实来源。"""

import base64
import time
import uuid

from .clear_state import clear_profile_state, restore_profile_state

TABLES = {"paragraph": "paragraphs", "entity": "entities", "relation": "relations"}


def _pack(row):
    return {
        key: {"bytes": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value
        for key, value in dict(row).items()
    }


def _unpack(row):
    return {
        key: base64.b64decode(value["bytes"], validate=True) if isinstance(value, dict) else value
        for key, value in row.items()
    }


class MemoryOperationsMixin:
    def enqueue_memory_projection(self, item_type, item_hash):
        if item_type not in TABLES:
            raise ValueError("invalid memory type")
        self._conn.execute(
            """INSERT INTO memory_projection_jobs (item_type, item_hash) VALUES (?, ?)
               ON CONFLICT(item_type, item_hash) DO UPDATE SET revision = revision + 1,
                 attempts = 0, next_attempt_at = 0, last_error = NULL""",
            (item_type, item_hash),
        )
        self._conn.commit()

    def _memory_delete_plan(self, mode, selectors):
        if mode not in {*TABLES, "source", "clear"}:
            raise ValueError("invalid delete mode")
        selected = {kind: {} for kind in TABLES}
        conn = self._conn
        if mode == "clear":
            for kind, table in TABLES.items():
                where = "" if kind == "relation" else " WHERE COALESCE(is_deleted, 0) = 0"
                selected[kind] = {row["hash"]: dict(row) for row in conn.execute(f"SELECT * FROM {table}{where}")}
        else:
            for selector in dict.fromkeys(selectors):
                if mode == "source":
                    rows = conn.execute(
                        "SELECT * FROM paragraphs WHERE source = ? AND COALESCE(is_deleted, 0) = 0", (selector,)
                    )
                    kind = "paragraph"
                else:
                    kind = mode
                    where = "hash = ?" if kind != "entity" else "(hash = ? OR LOWER(TRIM(name)) = LOWER(TRIM(?)))"
                    params = (selector,) if kind != "entity" else (selector, selector)
                    active = "" if kind == "relation" else " AND COALESCE(is_deleted, 0) = 0"
                    rows = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE {where}{active}", params)
                selected[kind].update({row["hash"]: dict(row) for row in rows})

        paragraphs = selected["paragraph"]
        # 删除段落只回收失去全部活跃证据的关系，保留共享关系。
        for hash_value in paragraphs:
            rows = conn.execute(
                """SELECT DISTINCT r.* FROM relations r LEFT JOIN paragraph_relations pr ON pr.relation_hash = r.hash
                   WHERE pr.paragraph_hash = ? OR r.source_paragraph = ?""",
                (hash_value, hash_value),
            )
            for row in rows.fetchall():
                evidence = {
                    r[0]
                    for r in conn.execute(
                        """SELECT p.hash FROM paragraphs p WHERE COALESCE(p.is_deleted, 0) = 0
                       AND (p.hash = ? OR p.hash IN
                         (SELECT paragraph_hash FROM paragraph_relations WHERE relation_hash = ?))""",
                        (row["source_paragraph"], row["hash"]),
                    )
                }
                if not evidence.difference(paragraphs):
                    selected["relation"][row["hash"]] = dict(row)
        for entity in selected["entity"].values():
            for row in conn.execute(
                "SELECT * FROM relations WHERE LOWER(subject) = LOWER(?) OR LOWER(object) = LOWER(?)",
                (entity["name"], entity["name"]),
            ):
                selected["relation"][row["hash"]] = dict(row)
        return selected

    def preview_memory_delete(self, mode, selectors):
        with self.transaction() as _conn:
            selected = self._memory_delete_plan(mode, selectors)
            return {
                "mode": mode,
                "counts": self._memory_delete_counts(mode, selected),
                "samples": {kind: list(rows)[:10] for kind, rows in selected.items()},
            }

    def _memory_delete_counts(self, mode, selected):
        counts = {kind: len(rows) for kind, rows in selected.items()}
        if mode == "clear":
            counts["facts"] = self._conn.execute(
                "SELECT COUNT(*) FROM fact_claims WHERE status IN ('active', 'conflicted')"
            ).fetchone()[0]
            for table in ("person_profile_overrides", "person_profile_alias_overrides", "person_profile_snapshots"):
                counts[table] = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts

    def delete_memories(self, mode, selectors, *, reason="", requested_by=""):
        with self.transaction(immediate=True) as conn:
            selected = self._memory_delete_plan(mode, selectors)
            if reason == "freeze_expired":
                selected["relation"] = {
                    h: row
                    for h, row in selected["relation"].items()
                    if row.get("is_inactive")
                    and not row.get("is_pinned")
                    and not row.get("is_permanent")
                    and (row.get("protected_until") or 0) <= time.time()
                }
                if not selected["relation"]:
                    return {"success": True, "deleted": {"paragraph": 0, "entity": 0, "relation": 0}}
            counts = self._memory_delete_counts(mode, selected)
            profile_state = clear_profile_state(self, conn) if mode == "clear" else None
            if not any(selected.values()) and not profile_state:
                raise ValueError("未找到可删除的记忆")
            items = []
            for kind, rows in selected.items():
                for hash_value, row in rows.items():
                    payload = {"row": _pack(row), "links": {}}
                    link_specs = {
                        "paragraph": [
                            ("paragraph_entities", "paragraph_hash"),
                            ("paragraph_relations", "paragraph_hash"),
                            ("external_memory_refs", "paragraph_hash"),
                        ],
                        "entity": [("paragraph_entities", "entity_hash")],
                        "relation": [
                            ("paragraph_relations", "relation_hash"),
                            ("paragraph_stale_relation_marks", "relation_hash"),
                        ],
                    }
                    for table, column in link_specs[kind]:
                        payload["links"][table] = [
                            _pack(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (hash_value,))
                        ]
                    items.append({"item_type": kind, "item_hash": hash_value, "payload": payload})
            if profile_state is not None:
                items.append({"item_type": "profile_state", "item_hash": "scope", "payload": profile_state})
            operation = self.create_delete_operation(
                mode=mode,
                selector=list(selectors),
                items=items,
                reason=reason,
                requested_by=requested_by,
                summary={"counts": counts},
                operation_id=uuid.uuid4().hex,
            )
            now = time.time()
            for item in items:
                kind, h = item["item_type"], item["item_hash"]
                if kind == "profile_state":
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO memory_deletion_owners VALUES (?, ?, ?)",
                    (kind, h, operation["operation_id"]),
                )
                if kind == "paragraph":
                    self.soft_delete_paragraphs([h])
                elif kind == "entity":
                    conn.execute("UPDATE entities SET is_deleted = 1, deleted_at = ? WHERE hash = ?", (now, h))
                    conn.execute("DELETE FROM paragraph_entities WHERE entity_hash = ?", (h,))
                elif self.backup_and_delete_relations([h]) != 1:
                    raise RuntimeError("relation backup failed")
                self.enqueue_memory_projection(kind, h)
            sources = [row.get("source") for row in selected["paragraph"].values()]
            self._enqueue_episode_source_rebuilds(sources, reason="memory_deleted")
            return {"success": True, "operation_id": operation["operation_id"], "deleted": counts}

    def _restore_memory_links(self, conn, links):
        allowed = {
            "paragraph_entities",
            "paragraph_relations",
            "external_memory_refs",
            "paragraph_stale_relation_marks",
        }
        for table, rows in links.items():
            if table not in allowed:
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for packed in rows:
                row = _unpack(packed)
                if (
                    "paragraph_hash" in row
                    and not conn.execute(
                        "SELECT 1 FROM paragraphs WHERE hash = ? AND COALESCE(is_deleted, 0) = 0",
                        (row["paragraph_hash"],),
                    ).fetchone()
                ):
                    continue
                if (
                    "entity_hash" in row
                    and not conn.execute(
                        "SELECT 1 FROM entities WHERE hash = ? AND COALESCE(is_deleted, 0) = 0",
                        (row["entity_hash"],),
                    ).fetchone()
                ):
                    continue
                if (
                    "relation_hash" in row
                    and not conn.execute("SELECT 1 FROM relations WHERE hash = ?", (row["relation_hash"],)).fetchone()
                ):
                    continue
                if (
                    row.get("task_id")
                    and not conn.execute(
                        "SELECT 1 FROM memory_feedback_tasks WHERE id = ?", (row["task_id"],)
                    ).fetchone()
                ):
                    row["task_id"] = None
                row = {key: value for key, value in row.items() if key in columns}
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                    tuple(row.values()),
                )

    def restore_memory_operation(self, operation_id):
        with self.transaction(immediate=True) as conn:
            operation = self.get_delete_operation(operation_id)
            if not operation:
                raise ValueError("删除操作不存在")
            if operation["status"] == "restored":
                return {"success": True, "operation_id": operation_id, "status": "restored", "restored": 0}
            if operation["status"] != "executed":
                raise ValueError("此操作不能恢复")
            restored, links, sources = 0, [], []
            skipped = []
            for item in operation.get("items", []):
                kind, h = item["item_type"], item["item_hash"]
                if kind == "profile_state":
                    continue
                owner = conn.execute(
                    "SELECT operation_id FROM memory_deletion_owners WHERE item_type = ? AND item_hash = ?", (kind, h)
                ).fetchone()
                if owner is None or owner[0] != operation_id:
                    skipped.append(h)
                    continue
                payload = item.get("payload") or {}
                row = _unpack(payload["row"])
                current = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE hash = ?", (h,)).fetchone()
                if kind == "relation":
                    if current is not None:
                        skipped.append(h)
                        continue
                    # 不连回另一个操作已删除的实体。
                    if conn.execute(
                        "SELECT 1 FROM entities WHERE LOWER(name) IN (LOWER(?), LOWER(?)) AND is_deleted = 1",
                        (row["subject"], row["object"]),
                    ).fetchone():
                        skipped.append(h)
                        continue
                    columns = {c[1] for c in conn.execute("PRAGMA table_info(relations)")}
                    row = {key: value for key, value in row.items() if key in columns}
                    conn.execute(
                        f"INSERT INTO relations ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                        tuple(row.values()),
                    )
                    conn.execute("DELETE FROM deleted_relations WHERE hash = ?", (h,))
                else:
                    if current is None or not current["is_deleted"]:
                        skipped.append(h)
                        continue
                    if kind == "paragraph":
                        self.restore_paragraph_by_hash(h)
                        sources.append(current["source"])
                    else:
                        conn.execute("UPDATE entities SET is_deleted = 0, deleted_at = NULL WHERE hash = ?", (h,))
                links.append(payload.get("links", {}))
                conn.execute("DELETE FROM memory_deletion_owners WHERE item_type = ? AND item_hash = ?", (kind, h))
                self.enqueue_memory_projection(kind, h)
                restored += 1
            for snapshot in links:
                self._restore_memory_links(conn, snapshot)
            for item in operation.get("items", []):
                if item["item_type"] == "profile_state":
                    restored += restore_profile_state(self, conn, item.get("payload") or {})
            self._enqueue_episode_source_rebuilds(sources, reason="memory_restored")
            result = {"success": True, "operation_id": operation_id, "restored": restored, "skipped": skipped}
            # 部分恢复保留快照，其他操作恢复后仍可重试。
            if not conn.execute(
                "SELECT 1 FROM memory_deletion_owners WHERE operation_id = ?", (operation_id,)
            ).fetchone():
                self.mark_delete_operation_restored(operation_id, summary={**operation.get("summary", {}), **result})
            return result

    def memory_deletion_owner(self, item_type, item_hash):
        row = self._conn.execute(
            "SELECT operation_id FROM memory_deletion_owners WHERE item_type = ? AND item_hash = ?",
            (item_type, item_hash),
        ).fetchone()
        return row[0] if row else None

    def purge_memory_operations(self, cutoff, limit=1000):
        purged = []
        with self.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT operation_id FROM delete_operations WHERE created_at < ? AND status != 'purged' ORDER BY created_at LIMIT ?",
                (cutoff, max(1, min(10000, int(limit)))),
            ).fetchall()
            for row in rows:
                operation_id = row[0]
                owners = conn.execute(
                    "SELECT * FROM memory_deletion_owners WHERE operation_id = ?", (operation_id,)
                ).fetchall()
                for owner in owners:
                    kind, h = owner["item_type"], owner["item_hash"]
                    if kind == "relation":
                        conn.execute("DELETE FROM deleted_relations WHERE hash = ?", (h,))
                    else:
                        conn.execute(f"DELETE FROM {TABLES[kind]} WHERE hash = ? AND is_deleted = 1", (h,))
                    if kind == "paragraph":
                        conn.execute("DELETE FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (h,))
                    self.enqueue_memory_projection(kind, h)
                conn.execute("DELETE FROM memory_deletion_owners WHERE operation_id = ?", (operation_id,))
                conn.execute("DELETE FROM delete_operation_items WHERE operation_id = ?", (operation_id,))
                conn.execute("UPDATE delete_operations SET status = 'purged' WHERE operation_id = ?", (operation_id,))
                purged.append(operation_id)
        return purged
