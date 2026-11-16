"""
AstrBot Memorix Plugin Local Storage Mixin.

维护 AstrBot 插件特有的本地表结构及兼容操作：
- transcript_sessions, transcript_messages, transcript_summary_state
- person_registry
- async_tasks
- episode_pending_paragraphs
- memory_operations, memory_projection_jobs & person_evidence
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..utils.hash import compute_hash
from .schema_compat import (
    ensure_person_registry_schema_compat,
    ensure_table_column,
    ensure_transcript_schema_compat,
    table_columns,
)

TABLES = {"paragraph": "paragraphs", "entity": "entities", "relation": "relations"}


class MetadataPluginLocalMixin:
    """维护 AstrBot 插件特有的本地表结构，确保与 upstream Schema 31 零冲突无损共存。"""

    _conn: sqlite3.Connection

    def _ensure_table_column(
        self,
        cursor: sqlite3.Cursor,
        *,
        table_name: str,
        column_name: str,
        add_column_sql: str,
    ) -> None:
        """Add a missing column for legacy DBs where CREATE TABLE IF NOT EXISTS is not enough."""
        ensure_table_column(
            cursor,
            table_name=table_name,
            column_name=column_name,
            add_column_sql=add_column_sql,
        )

    def _table_columns(self, cursor: sqlite3.Cursor, table_name: str) -> set[str]:
        """Return existing columns for a known SQLite table."""
        return table_columns(cursor, table_name)

    def _ensure_plugin_local_schema(self) -> None:
        """插件本地表与列补丁，每次 connect() 幂等执行。"""
        if not getattr(self, "_conn", None):
            return
        self._ensure_async_task_schema()
        self._ensure_transcript_schema()
        self._ensure_person_registry_schema()
        self._ensure_episode_pending_schema()
        self._ensure_projection_schema()
        self._ensure_graph_rename_schema()
        cursor = self._conn.cursor()
        self._ensure_table_column(
            cursor,
            table_name="episode_paragraphs",
            column_name="position",
            add_column_sql="ALTER TABLE episode_paragraphs ADD COLUMN position INTEGER DEFAULT 0",
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # 异步任务账本 (async_tasks)
    # ------------------------------------------------------------------

    def _ensure_async_task_schema(self) -> None:
        if not getattr(self, "_conn", None):
            return
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS async_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT,
                result_json TEXT,
                error_message TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                updated_at REAL NOT NULL,
                cancel_requested INTEGER DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_async_tasks_type_updated
            ON async_tasks(task_type, updated_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_async_tasks_status_updated
            ON async_tasks(status, updated_at DESC)
            """
        )
        self._conn.commit()

    def _async_task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "task_id": str(row["task_id"] or ""),
            "task_type": str(row["task_type"] or ""),
            "status": str(row["status"] or ""),
            "payload": self._json_loads(row["payload_json"], {}),
            "result": self._json_loads(row["result_json"], None),
            "error_message": str(row["error_message"] or ""),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
            "cancel_requested": bool(row["cancel_requested"]),
        }

    def create_async_task(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: Dict[str, Any],
        status: str = "queued",
    ) -> Dict[str, Any]:
        self._ensure_async_task_schema()
        token = str(task_id or "").strip()
        kind = str(task_type or "").strip()
        if not token:
            raise ValueError("task_id 不能为空")
        if not kind:
            raise ValueError("task_type 不能为空")

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO async_tasks (
                task_id, task_type, status, payload_json,
                created_at, updated_at, cancel_requested
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (token, kind, str(status or "queued"), self._json_dumps(payload or {}), now, now),
        )
        self._conn.commit()
        return self.get_async_task(token) or {
            "task_id": token,
            "task_type": kind,
            "status": str(status or "queued"),
            "payload": payload or {},
            "created_at": now,
            "updated_at": now,
            "cancel_requested": False,
        }

    def update_async_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        result: Any = None,
        error_message: Optional[str] = None,
        started_at: Optional[float] = None,
        finished_at: Optional[float] = None,
        cancel_requested: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        self._ensure_async_task_schema()
        token = str(task_id or "").strip()
        if not token:
            return None

        updates = ["updated_at = ?"]
        params: List[Any] = [datetime.now().timestamp()]
        if status is not None:
            updates.append("status = ?")
            params.append(str(status))
        if result is not None:
            updates.append("result_json = ?")
            params.append(self._json_dumps(result))
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(str(error_message))
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(float(started_at))
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(float(finished_at))
        if cancel_requested is not None:
            updates.append("cancel_requested = ?")
            params.append(1 if cancel_requested else 0)
        params.append(token)

        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE async_tasks SET {', '.join(updates)} WHERE task_id = ?",
            tuple(params),
        )
        self._conn.commit()
        return self.get_async_task(token)

    def get_async_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_async_task_schema()
        token = str(task_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM async_tasks WHERE task_id = ? LIMIT 1", (token,))
        row = cursor.fetchone()
        return self._async_task_row_to_dict(row) if row else None

    def list_async_tasks(
        self,
        *,
        task_type: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        self._ensure_async_task_schema()
        safe_limit = max(1, int(limit))
        conditions: List[str] = []
        params: List[Any] = []
        if task_type:
            conditions.append("task_type = ?")
            params.append(str(task_type))
        normalized_statuses = [str(x or "").strip() for x in (statuses or []) if str(x or "").strip()]
        if normalized_statuses:
            placeholders = ",".join(["?"] * len(normalized_statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(safe_limit)
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM async_tasks
            {where_sql}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._async_task_row_to_dict(row) for row in cursor.fetchall()]

    def get_async_task_summary(self, *, task_type: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_async_task_schema()
        conditions: List[str] = []
        params: List[Any] = []
        if task_type:
            conditions.append("task_type = ?")
            params.append(str(task_type))
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = self._conn.cursor()
        cursor.execute(
            f"""
            SELECT status, COUNT(*) AS cnt
            FROM async_tasks
            {where_sql}
            GROUP BY status
            """,
            tuple(params),
        )
        counts: Dict[str, int] = {
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "canceled": 0,
            "total": 0,
        }
        for row in cursor.fetchall():
            status = str(row["status"] or "").strip()
            count = int(row["cnt"] or 0)
            counts[status] = counts.get(status, 0) + count
            counts["total"] += count
        latest = self.list_async_tasks(task_type=task_type, limit=1)
        return {"counts": counts, "latest": latest[0] if latest else None}

    # ------------------------------------------------------------------
    # 会话转写 (transcript)
    # ------------------------------------------------------------------

    def _create_transcript_schema(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_sessions (
                session_id TEXT PRIMARY KEY,
                source TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                role TEXT,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES transcript_sessions(session_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_summary_state (
                session_id TEXT PRIMARY KEY,
                last_summary_at REAL,
                last_message_created_at REAL,
                last_task_id TEXT,
                summary_count INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES transcript_sessions(session_id) ON DELETE CASCADE
            )
            """
        )
        ensure_transcript_schema_compat(cursor)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transcript_messages_session_pos
            ON transcript_messages(session_id, position)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transcript_sessions_updated
            ON transcript_sessions(updated_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transcript_summary_state_updated
            ON transcript_summary_state(updated_at DESC)
            """
        )

    def _ensure_transcript_schema(self) -> None:
        if not getattr(self, "_conn", None):
            return
        cursor = self._conn.cursor()
        self._create_transcript_schema(cursor)
        self._conn.commit()

    def _transcript_session_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "session_id": str(row["session_id"] or ""),
            "source": str(row["source"] or ""),
            "metadata": self._json_loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_transcript_session(
        self,
        *,
        session_id: str,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            raise ValueError("session_id 不能为空")
        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO transcript_sessions (
                session_id, source, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                source = excluded.source,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                token,
                str(source or "").strip(),
                self._json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        self._conn.commit()
        cursor.execute("SELECT * FROM transcript_sessions WHERE session_id = ?", (token,))
        row = cursor.fetchone()
        return self._transcript_session_row_to_dict(row) if row else {
            "session_id": token,
            "source": str(source or "").strip(),
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    def get_transcript_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM transcript_sessions WHERE session_id = ? LIMIT 1", (token,))
        row = cursor.fetchone()
        return self._transcript_session_row_to_dict(row) if row else None

    def append_transcript_messages(
        self,
        *,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> int:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            raise ValueError("session_id 不能为空")
        if not isinstance(messages, list) or not messages:
            return 0

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(position), -1) FROM transcript_messages WHERE session_id = ?",
            (token,),
        )
        row = cursor.fetchone()
        max_position = row[0] if row and row[0] is not None else -1
        next_position = int(max_position) + 1
        now = datetime.now().timestamp()
        rows = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            role = str(item.get("role", "user") or "user").strip() or "user"
            try:
                created_at = float(item.get("created_at") or now)
            except (TypeError, ValueError):
                created_at = now
            metadata = {
                key: value
                for key, value in item.items()
                if key not in {"role", "content", "created_at"}
            }
            rows.append(
                (
                    token,
                    next_position,
                    role,
                    content,
                    self._json_dumps(metadata),
                    created_at,
                )
            )
            next_position += 1

        if not rows:
            return 0
        cursor.executemany(
            """
            INSERT INTO transcript_messages (
                session_id, position, role, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        cursor.execute(
            "UPDATE transcript_sessions SET updated_at = ? WHERE session_id = ?",
            (now, token),
        )
        self._conn.commit()
        return len(rows)

    def get_transcript_messages(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            return []
        safe_limit = max(1, int(limit or 50))
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM transcript_messages
                WHERE session_id = ?
                ORDER BY position DESC, message_id DESC
                LIMIT ?
            )
            ORDER BY position ASC, message_id ASC
            """,
            (token, safe_limit),
        )
        return [
            {
                "message_id": row["message_id"],
                "session_id": str(row["session_id"] or ""),
                "position": int(row["position"] or 0),
                "role": str(row["role"] or "user"),
                "content": str(row["content"] or ""),
                "metadata": self._json_loads(row["metadata_json"], {}),
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]

    def _transcript_summary_state_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        metadata = self._json_loads(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "session_id": str(row["session_id"] or ""),
            "last_summary_at": row["last_summary_at"],
            "last_message_created_at": row["last_message_created_at"],
            "last_task_id": str(row["last_task_id"] or ""),
            "summary_count": int(row["summary_count"] or 0),
            "metadata": metadata,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_transcript_summary_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM transcript_summary_state WHERE session_id = ? LIMIT 1",
            (token,),
        )
        row = cursor.fetchone()
        return self._transcript_summary_state_row_to_dict(row) if row else None

    def mark_transcript_summary_complete(
        self,
        *,
        session_id: str,
        last_message_created_at: Optional[float] = None,
        task_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_transcript_schema()
        token = str(session_id or "").strip()
        if not token:
            raise ValueError("session_id 不能为空")

        cursor = self._conn.cursor()
        resolved_last_message = last_message_created_at
        if resolved_last_message is None:
            cursor.execute(
                "SELECT MAX(created_at) FROM transcript_messages WHERE session_id = ?",
                (token,),
            )
            value = cursor.fetchone()[0]
            resolved_last_message = float(value) if value is not None else None

        now = datetime.now().timestamp()
        cursor.execute(
            """
            INSERT INTO transcript_summary_state (
                session_id, last_summary_at, last_message_created_at, last_task_id,
                summary_count, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_summary_at = excluded.last_summary_at,
                last_message_created_at = excluded.last_message_created_at,
                last_task_id = excluded.last_task_id,
                summary_count = transcript_summary_state.summary_count + 1,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                token,
                now,
                resolved_last_message,
                str(task_id or "").strip(),
                self._json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        self._conn.commit()
        state = self.get_transcript_summary_state(token)
        if state is None:
            raise RuntimeError("summary state 写入后读取失败")
        return state

    # ------------------------------------------------------------------
    # 人物花名册 (person_registry)
    # ------------------------------------------------------------------

    def _create_person_registry_schema(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS person_registry (
                person_id TEXT PRIMARY KEY,
                person_name TEXT,
                nickname TEXT,
                user_id TEXT,
                platform TEXT,
                group_nick_name TEXT,
                memory_points TEXT,
                last_know REAL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        ensure_person_registry_schema_compat(cursor)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_registry_updated
            ON person_registry(updated_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_registry_last_know
            ON person_registry(last_know DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_registry_name
            ON person_registry(person_name)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_registry_nickname
            ON person_registry(nickname)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_person_registry_user_id
            ON person_registry(user_id)
            """
        )

    def _commit_if_autocommit(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is None:
            return
        if getattr(conn, "_managed_transaction_depth", 0) > 0 or conn.in_transaction:
            return
        conn.commit()

    def _ensure_person_registry_schema(self) -> None:
        if not getattr(self, "_conn", None):
            return
        cursor = self._conn.cursor()
        self._create_person_registry_schema(cursor)
        self._commit_if_autocommit()

    @staticmethod
    def _extract_group_alias_names(raw_value: Any) -> List[str]:
        if not raw_value:
            return []
        values = raw_value
        if isinstance(raw_value, str):
            try:
                values = json.loads(raw_value)
            except Exception:
                values = [raw_value]
        if not isinstance(values, list):
            return []
        names: List[str] = []
        for item in values:
            if isinstance(item, dict):
                token = str(item.get("group_nick_name") or item.get("group_cardname") or "").strip()
            else:
                token = str(item or "").strip()
            if token and token not in names:
                names.append(token)
        return names

    def _person_registry_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        group_nicks = self._json_loads(row["group_nick_name"], [])
        if not isinstance(group_nicks, list):
            group_nicks = []
        memory_points = self._json_loads(row["memory_points"], [])
        if not isinstance(memory_points, list):
            memory_points = []
        metadata = self._json_loads(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            metadata = {}

        person_id = str(row["person_id"] or "").strip()
        person_name = str(row["person_name"] or "").strip()
        nickname = str(row["nickname"] or "").strip()
        user_id = str(row["user_id"] or "").strip()
        display_name = person_name or nickname or user_id or person_id
        group_alias_names = self._extract_group_alias_names(group_nicks)
        aliases: List[str] = []
        for item in [person_name, nickname, user_id, *group_alias_names]:
            if item and item not in aliases:
                aliases.append(item)

        return {
            "person_id": person_id,
            "display_name": display_name,
            "person_name": person_name,
            "nickname": nickname,
            "user_id": user_id,
            "platform": str(row["platform"] or "").strip(),
            "group_nick_name": group_nicks,
            "memory_points": memory_points,
            "metadata": metadata,
            "aliases": aliases,
            "last_know": row["last_know"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _person_json_field(self, value: Any) -> str:
        if value is None or value == "":
            return "[]"
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return self._json_dumps(parsed)
            except Exception:
                pass
            return self._json_dumps([value])
        if isinstance(value, list):
            return self._json_dumps(value)
        return self._json_dumps([value])

    def upsert_person_registry(
        self,
        person_id: str = "",
        person_name: str = "",
        nickname: str = "",
        user_id: str = "",
        platform: str = "",
        group_nick_name: Any = None,
        memory_points: Any = None,
        last_know: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_person_registry_schema()
        primary = str(person_id or "").strip()
        name = str(person_name or "").strip()
        nick = str(nickname or "").strip()
        uid = str(user_id or "").strip()
        plat = str(platform or "").strip()
        if not primary:
            seed = "|".join([plat, uid, name, nick]).strip("|")
            if not seed:
                raise ValueError("person_id 或人物标识不能为空")
            primary = compute_hash(seed)[:32]

        now = datetime.now().timestamp()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO person_registry (
                person_id, person_name, nickname, user_id, platform,
                group_nick_name, memory_points, last_know, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                person_name = excluded.person_name,
                nickname = excluded.nickname,
                user_id = excluded.user_id,
                platform = excluded.platform,
                group_nick_name = excluded.group_nick_name,
                memory_points = excluded.memory_points,
                last_know = COALESCE(excluded.last_know, person_registry.last_know),
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                primary,
                name,
                nick,
                uid,
                plat,
                self._person_json_field(group_nick_name),
                self._person_json_field(memory_points),
                float(last_know) if last_know is not None else None,
                self._json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        self._conn.commit()
        if primary and plat and uid and hasattr(self, "observe_person_identity"):
            alias_items = [x for x in [name, nick, *self._extract_group_alias_names(group_nick_name)] if x]
            try:
                self.observe_person_identity(
                    person_id=primary,
                    platform=plat,
                    user_id=uid,
                    aliases=alias_items,
                    preferred_name=name or nick or uid,
                    seen_at=float(last_know) if last_know is not None else now,
                )
            except Exception:
                pass
        record = self.get_person_registry(primary)
        if record is None:
            raise RuntimeError("人物注册记录写入后读取失败")
        return record

    def get_person_registry(self, person_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_person_registry_schema()
        pid = str(person_id or "").strip()
        if not pid:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, person_name, nickname, user_id, platform,
                   group_nick_name, memory_points, last_know, metadata_json,
                   created_at, updated_at
            FROM person_registry
            WHERE person_id = ?
            LIMIT 1
            """,
            (pid,),
        )
        row = cursor.fetchone()
        return self._person_registry_row_to_dict(row) if row else None

    def find_person_registry_ids(self, identifier: str) -> List[str]:
        self._ensure_person_registry_schema()
        value = str(identifier or "").strip()
        if not value:
            return []
        target = value.casefold()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, person_name, nickname, user_id, group_nick_name
            FROM person_registry
            WHERE LOWER(person_id) = LOWER(?)
               OR LOWER(person_name) = LOWER(?)
               OR LOWER(nickname) = LOWER(?)
               OR LOWER(user_id) = LOWER(?)
               OR group_nick_name LIKE ?
            ORDER BY last_know DESC, updated_at DESC
            """,
            (value, value, value, value, f"%{value}%"),
        )
        matches: List[str] = []
        for row in cursor.fetchall():
            pid = str(row["person_id"] or "").strip()
            if not pid or pid in matches:
                continue
            candidates = [
                pid,
                str(row["person_name"] or "").strip(),
                str(row["nickname"] or "").strip(),
                str(row["user_id"] or "").strip(),
                *self._extract_group_alias_names(self._json_loads(row["group_nick_name"], [])),
            ]
            if any(item and item.casefold() == target for item in candidates):
                matches.append(pid)
        return matches

    def resolve_person_registry(self, identifier: str) -> Optional[str]:
        matches = self.find_person_registry_ids(identifier)
        return matches[0] if len(matches) == 1 else None

    def list_person_registry(
        self,
        keyword: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        self._ensure_person_registry_schema()
        kw = str(keyword or "").strip()
        safe_page = max(1, int(page or 1))
        safe_size = max(1, min(200, int(page_size or 20)))
        offset = (safe_page - 1) * safe_size
        where_sql = ""
        params: List[Any] = []
        if kw:
            where_sql = (
                "WHERE person_id LIKE ? OR person_name LIKE ? OR nickname LIKE ? "
                "OR user_id LIKE ? OR platform LIKE ? OR group_nick_name LIKE ?"
            )
            like_kw = f"%{kw}%"
            params = [like_kw, like_kw, like_kw, like_kw, like_kw, like_kw]

        cursor = self._conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM person_registry {where_sql}", tuple(params))
        total = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            f"""
            SELECT person_id, person_name, nickname, user_id, platform,
                   group_nick_name, memory_points, last_know, metadata_json,
                   created_at, updated_at
            FROM person_registry
            {where_sql}
            ORDER BY COALESCE(last_know, updated_at) DESC, updated_at DESC, person_name ASC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [safe_size, offset]),
        )
        items = [self._person_registry_row_to_dict(row) for row in cursor.fetchall()]
        for item in items:
            latest = self.get_latest_person_profile_snapshot(item["person_id"]) if hasattr(self, "get_latest_person_profile_snapshot") else None
            override = self.get_person_profile_override(item["person_id"]) if hasattr(self, "get_person_profile_override") else None
            has_override = override is not None and bool(str(override.get("override_text", "")).strip())
            item["has_snapshot"] = latest is not None
            item["has_override"] = has_override
            item["latest_profile_updated_at"] = (
                override.get("updated_at") if has_override else latest.get("updated_at") if latest else None
            )
        return {
            "success": True,
            "keyword": kw,
            "page": safe_page,
            "page_size": safe_size,
            "total": total,
            "items": items,
        }

    def get_active_person_ids(
        self,
        active_after: Optional[float] = None,
        limit: int = 200,
    ) -> List[str]:
        cursor = self._conn.cursor()
        sql = """
            SELECT person_id, MAX(last_seen_at) AS last_seen
            FROM person_profile_active_persons
        """
        params: List[Any] = []
        if active_after is not None:
            sql += " WHERE last_seen_at >= ?"
            params.append(float(active_after))
        sql += """
            GROUP BY person_id
            ORDER BY last_seen DESC
            LIMIT ?
        """
        params.append(int(max(1, limit)))
        cursor.execute(sql, tuple(params))
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    # ------------------------------------------------------------------
    # 待处理情景队列 (episode_pending_paragraphs)
    # ------------------------------------------------------------------

    def _ensure_episode_pending_schema(self) -> None:
        if not getattr(self, "_conn", None):
            return
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS episode_pending_paragraphs (
                paragraph_hash TEXT PRIMARY KEY,
                source TEXT,
                created_at REAL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_pending_status_updated
            ON episode_pending_paragraphs(status, updated_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_pending_source_created
            ON episode_pending_paragraphs(source, created_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_pending_status_retry_updated
            ON episode_pending_paragraphs(status, retry_count, updated_at)
            """
        )
        self._commit_if_autocommit()

    def enqueue_episode_pending(self, paragraph_hash: str, source: str = "") -> None:
        self._ensure_episode_pending_schema()
        now = datetime.now().timestamp()
        self._conn.execute(
            """
            INSERT INTO episode_pending_paragraphs (paragraph_hash, source, created_at, status, updated_at)
            VALUES (?, ?, ?, 'pending', ?)
            ON CONFLICT(paragraph_hash) DO UPDATE SET
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (paragraph_hash, source, now, now),
        )
        self._conn.commit()

    def fetch_episode_pending_batch(self, limit: int = 20, max_retry: int = 3) -> List[Dict[str, Any]]:
        self._ensure_episode_pending_schema()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT paragraph_hash, source, created_at, status, retry_count, last_error, updated_at
            FROM episode_pending_paragraphs
            WHERE status IN ('pending', 'failed') AND retry_count <= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(0, int(max_retry)), max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_episode_pending_running(self, hashes: List[str]) -> None:
        if not hashes:
            return
        self._ensure_episode_pending_schema()
        now = datetime.now().timestamp()
        placeholders = ",".join(["?"] * len(hashes))
        self._conn.execute(
            f"UPDATE episode_pending_paragraphs SET status = 'running', updated_at = ? WHERE paragraph_hash IN ({placeholders})",
            [now, *hashes],
        )
        self._conn.commit()

    def mark_episode_pending_done(self, hashes: List[str]) -> None:
        if not hashes:
            return
        self._ensure_episode_pending_schema()
        placeholders = ",".join(["?"] * len(hashes))
        self._conn.execute(
            f"DELETE FROM episode_pending_paragraphs WHERE paragraph_hash IN ({placeholders})",
            hashes,
        )
        self._conn.commit()

    def mark_episode_pending_failed(self, hash_value: str, error: str = "") -> None:
        self._ensure_episode_pending_schema()
        now = datetime.now().timestamp()
        self._conn.execute(
            """
            UPDATE episode_pending_paragraphs
            SET status = 'failed', retry_count = retry_count + 1, last_error = ?, updated_at = ?
            WHERE paragraph_hash = ?
            """,
            (str(error)[:500], now, hash_value),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # 段落向量回填与投影 (projection & memory operations)
    # ------------------------------------------------------------------

    def _ensure_projection_schema(self) -> None:
        if not getattr(self, "_conn", None):
            return
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_projection_jobs (
                item_type TEXT NOT NULL,
                item_hash TEXT NOT NULL,
                item_key TEXT,
                revision INTEGER DEFAULT 1,
                attempts INTEGER DEFAULT 0,
                next_attempt_at REAL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY (item_type, item_hash)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_deletion_owners (
                item_type TEXT NOT NULL,
                item_hash TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                PRIMARY KEY (item_type, item_hash)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS paragraph_fact_evidence_backups (
                paragraph_hash TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        self._commit_if_autocommit()

    def _ensure_graph_rename_schema(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """确保图谱重命名派生索引暂存表存在 (幂等)。"""
        target = conn or getattr(self, "_conn", None)
        if target is None:
            return
        cursor = target.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_pending_renames (
                operation_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        if conn is None:
            self._commit_if_autocommit()

    def invalidate_person_evidence(self, paragraph_hashes: Sequence[str], *, conn: Any) -> None:
        people = set()
        for paragraph_hash in paragraph_hashes:
            paragraph = self.get_paragraph(paragraph_hash)
            if paragraph is None:
                continue
            metadata = paragraph.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = getattr(self, "_decode_metadata", lambda m: {})(metadata)
            raw_ids = metadata.get("person_ids", [])
            if isinstance(raw_ids, list):
                people.update(item.strip() for item in raw_ids if isinstance(item, str) and item.strip())
            person_id = metadata.get("person_id")
            if isinstance(person_id, str) and person_id.strip():
                people.add(person_id.strip())
        for person_id in people:
            if hasattr(self, "enqueue_person_profile_refresh"):
                self.enqueue_person_profile_refresh(person_id=person_id, reason="person_evidence_changed", conn=conn)

    def detach_paragraph_facts(self, paragraph_hashes: Sequence[str], *, recoverable: bool, conn: Any) -> None:
        self.invalidate_person_evidence(paragraph_hashes, conn=conn)
        for paragraph_hash in paragraph_hashes:
            snapshot = self.detach_fact_evidence_for_paragraphs(
                [paragraph_hash], reason="paragraph_deleted", conn=conn,
            )
            if recoverable and snapshot.get("evidence"):
                conn.execute(
                    "INSERT OR IGNORE INTO paragraph_fact_evidence_backups VALUES (?, ?)",
                    (paragraph_hash, json.dumps(snapshot, ensure_ascii=False)),
                )
            elif not recoverable:
                conn.execute("DELETE FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,))
            for claim in snapshot.get("claims", []):
                if claim.get("scope_type") == "person" and hasattr(self, "enqueue_person_profile_refresh"):
                    self.enqueue_person_profile_refresh(
                        person_id=claim["scope_id"], reason="fact_evidence_deleted", conn=conn,
                    )

    def restore_paragraph_facts(self, paragraph_hash: str, *, conn: Any) -> None:
        row = conn.execute(
            "SELECT snapshot_json FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,),
        ).fetchone()
        if row is not None:
            snapshot = json.loads(row[0])
            self.restore_fact_evidence_snapshot(snapshot, conn=conn)
            conn.execute("DELETE FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,))
            for claim in snapshot.get("claims", []):
                if claim.get("scope_type") == "person" and hasattr(self, "enqueue_person_profile_refresh"):
                    self.enqueue_person_profile_refresh(
                        person_id=claim["scope_id"], reason="fact_evidence_restored", conn=conn,
                    )
        self.invalidate_person_evidence([paragraph_hash], conn=conn)

    def list_paragraphs_for_vector_backfill(self, limit: int = 1000) -> List[Dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT hash, content, source, vector_index, created_at, updated_at
            FROM paragraphs
            WHERE (is_deleted IS NULL OR is_deleted = 0)
              AND (vector_index IS NULL OR vector_index < 0)
            ORDER BY COALESCE(updated_at, created_at, 0) ASC
            LIMIT ?
            """,
            (max(1, int(limit or 1000)),),
        )
        return [self._row_to_dict(row, "paragraph") for row in cursor.fetchall()]
