"""AstrBot 会话记忆在线迁移服务（基于上游 Schema 31 ChatMemoryMigration 账本）。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from ..core.runtime.services.chat_migration_service import ChatMemoryMigration
from ..core.runtime.services.search_hit_processing_service import MemorySearchHitProcessingService
from ..core.utils.chat_migration_gate import get_chat_migration_gate, settled_thread


@asynccontextmanager
async def _pause_migration_chats(session_ids: Set[str]) -> AsyncIterator[None]:
    clean_ids = {str(item or "").strip() for item in session_ids if str(item or "").strip()}
    if not clean_ids:
        yield
        return
    gate = get_chat_migration_gate()
    await gate.pause(clean_ids)
    try:
        yield
    finally:
        gate.resume(clean_ids)


def _now() -> float:
    return time.time()


def _is_archive_token(value: str) -> bool:
    return str(value or "").strip().startswith("migration_archive:")


def _extract_chat_id_from_source(source: str) -> str:
    clean = str(source or "").strip()
    if not clean or _is_archive_token(clean):
        return ""
    if clean.startswith("chat_summary:"):
        return clean.split(":", 1)[1].strip()
    if clean.startswith("chat:"):
        parts = clean.split(":", 2)
        if len(parts) == 3:
            return parts[2].strip()
        if len(parts) == 2:
            return parts[1].strip()
    if clean.startswith("person_fact:"):
        rest = clean.split(":", 1)[1].strip()
        if ":" in rest:
            candidate = rest.split(":", 1)[0].strip()
            return candidate
    return ""


class AstrBotChatMigrationService:
    """面向 AstrBot WebUI 与管理契约的在线会话记忆迁移编排服务。"""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._ensure_job_table()

    def _ensure_job_table(self) -> None:
        conn = getattr(self.ctx.metadata_store, "_conn", None)
        if conn is None:
            return
        with self.ctx.metadata_store.transaction(immediate=True) as tx:
            tx.execute(
                """
                CREATE TABLE IF NOT EXISTS astrbot_chat_migration_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_chat_id TEXT NOT NULL,
                    target_chat_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    migrate_transcript INTEGER NOT NULL DEFAULT 1,
                    expected_fingerprint TEXT NOT NULL DEFAULT '',
                    target_has_existing_data INTEGER NOT NULL DEFAULT 0,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    applied_json TEXT NOT NULL DEFAULT '{}',
                    restored_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL DEFAULT 'webui',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            tx.execute(
                "CREATE INDEX IF NOT EXISTS idx_astrbot_chat_migration_jobs_updated "
                "ON astrbot_chat_migration_jobs(updated_at DESC)"
            )

    @staticmethod
    def _parse_json_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if not raw or not isinstance(raw, str):
            return {}
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _serialize_job_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        status = str(row.get("status") or "").strip().lower()
        snapshot = self._parse_json_dict(row.get("snapshot_json"))
        applied = self._parse_json_dict(row.get("applied_json"))
        restored = self._parse_json_dict(row.get("restored_json"))
        target_has_existing = bool(int(row.get("target_has_existing_data") or 0))
        return {
            "job_id": str(row.get("job_id") or ""),
            "source_chat_id": str(row.get("source_chat_id") or ""),
            "target_chat_id": str(row.get("target_chat_id") or ""),
            "status": status,
            "migrate_transcript": bool(int(row.get("migrate_transcript") or 0)),
            "expected_fingerprint": str(row.get("expected_fingerprint") or ""),
            "target_has_existing_data": target_has_existing,
            "snapshot": snapshot,
            "source_counts": snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {},
            "target_counts": snapshot.get("target") if isinstance(snapshot.get("target"), dict) else {},
            "transcript_counts": snapshot.get("transcript") if isinstance(snapshot.get("transcript"), dict) else {},
            "applied_result": applied,
            "restored_result": restored,
            "note": str(row.get("note") or ""),
            "requested_by": str(row.get("requested_by") or "webui"),
            "error": str(row.get("error") or ""),
            "created_at": float(row.get("created_at") or 0.0),
            "updated_at": float(row.get("updated_at") or 0.0),
            "can_execute": status in {"previewed", "failed"},
            "can_recover": status == "completed",
        }

    def _transcript_counts_sync(self, chat_id: str) -> Dict[str, int]:
        clean = str(chat_id or "").strip()
        if not clean:
            return {"messages": 0, "has_session": 0}
        msg_rows = self.ctx.metadata_store.query(
            "SELECT COUNT(*) AS cnt FROM transcript_messages WHERE session_id = ?",
            (clean,),
        )
        sess_rows = self.ctx.metadata_store.query(
            "SELECT COUNT(*) AS cnt FROM transcript_sessions WHERE session_id = ?",
            (clean,),
        )
        return {
            "messages": int((msg_rows[0].get("cnt") if msg_rows else 0) or 0),
            "has_session": int((sess_rows[0].get("cnt") if sess_rows else 0) or 0),
        }

    @staticmethod
    def _counts_total(counts: Dict[str, Any]) -> int:
        total = 0
        for key, value in (counts or {}).items():
            if key in {"chat_id", "fingerprint"}:
                continue
            if isinstance(value, (int, float)):
                total += int(value)
        return total

    def _list_chats_sync(self, *, query: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        store = self.ctx.metadata_store
        entries: Dict[str, Dict[str, Any]] = {}

        def _ensure(chat_id: str) -> Optional[Dict[str, Any]]:
            cid = str(chat_id or "").strip()
            if not cid or _is_archive_token(cid):
                return None
            item = entries.get(cid)
            if item is None:
                item = {
                    "chat_id": cid,
                    "display_name": cid,
                    "platform": "",
                    "group_id": "",
                    "group_name": "",
                    "user_id": "",
                    "sender_name": "",
                    "unified_msg_origin": "",
                    "is_group": False,
                    "transcript_message_count": 0,
                    "paragraph_count": 0,
                    "fact_claim_count": 0,
                    "episode_count": 0,
                    "image_occurrence_count": 0,
                    "image_observation_count": 0,
                    "last_active_at": 0.0,
                }
                entries[cid] = item
            return item

        for row in store.query(
            """
            SELECT s.session_id, s.updated_at, s.metadata_json,
                   (SELECT COUNT(*) FROM transcript_messages m WHERE m.session_id = s.session_id) AS msg_cnt
            FROM transcript_sessions s
            ORDER BY s.updated_at DESC
            """
        ):
            item = _ensure(str(row.get("session_id") or ""))
            if item is None:
                continue
            meta = self._parse_json_dict(row.get("metadata_json"))
            item["transcript_message_count"] = max(item["transcript_message_count"], int(row.get("msg_cnt") or 0))
            item["last_active_at"] = max(item["last_active_at"], float(row.get("updated_at") or 0.0))
            for field in ("platform", "group_id", "group_name", "user_id", "sender_name", "unified_msg_origin"):
                val = str(meta.get(field) or "").strip()
                if val and not item[field]:
                    item[field] = val

        for row in store.query(
            "SELECT source, metadata, created_at FROM paragraphs WHERE COALESCE(is_deleted, 0) = 0"
        ):
            src = str(row.get("source") or "").strip()
            if _is_archive_token(src):
                continue
            meta = self._parse_json_dict(row.get("metadata"))
            meta_src = str(meta.get("source") or "").strip()
            if _is_archive_token(meta_src):
                continue
            kind, scope_ids = MemorySearchHitProcessingService._paragraph_scope_identity(row)
            if kind == "chat" and scope_ids:
                owner_chat_ids = sorted(str(cid).strip() for cid in scope_ids if str(cid).strip())
            elif kind not in {"archive", "global"}:
                fallback_cid = str(meta.get("chat_id") or "").strip() or _extract_chat_id_from_source(src)
                owner_chat_ids = [fallback_cid] if fallback_cid else []
            else:
                owner_chat_ids = []
            for cid in owner_chat_ids:
                item = _ensure(cid)
                if item is None:
                    continue
                item["paragraph_count"] += 1
                item["last_active_at"] = max(item["last_active_at"], float(row.get("created_at") or 0.0))
                for field in ("platform", "group_id", "group_name", "sender_name"):
                    val = str(meta.get(field) or "").strip()
                    if val and not item[field]:
                        item[field] = val

        for row in store.query(
            "SELECT scope_id, COUNT(*) AS cnt, MAX(updated_at) AS last_ts "
            "FROM fact_claims WHERE scope_type = 'chat' AND status = 'active' GROUP BY scope_id"
        ):
            item = _ensure(str(row.get("scope_id") or ""))
            if item is not None:
                item["fact_claim_count"] = int(row.get("cnt") or 0)
                item["last_active_at"] = max(item["last_active_at"], float(row.get("last_ts") or 0.0))

        for row in store.query("SELECT source, created_at FROM episodes"):
            src = str(row.get("source") or "").strip()
            if _is_archive_token(src):
                continue
            item = _ensure(_extract_chat_id_from_source(src))
            if item is not None:
                item["episode_count"] += 1
                item["last_active_at"] = max(item["last_active_at"], float(row.get("created_at") or 0.0))

        for row in store.query(
            """
            SELECT occ.chat_id AS chat_id,
                   COUNT(DISTINCT occ.occurrence_id) AS occ_cnt,
                   COUNT(CASE WHEN COALESCE(obs.superseded_by, '') = '' THEN obs.observation_id END) AS obs_cnt,
                   MAX(COALESCE(occ.occurred_at, occ.updated_at, occ.created_at)) AS last_ts
            FROM image_occurrences occ
            LEFT JOIN image_observations obs ON obs.occurrence_id = occ.occurrence_id
            WHERE occ.status = 'active'
              AND occ.scope_type = 'chat'
              AND occ.chat_id IS NOT NULL
              AND occ.chat_id != ''
            GROUP BY occ.chat_id
            """
        ):
            item = _ensure(str(row.get("chat_id") or ""))
            if item is not None:
                item["image_occurrence_count"] = int(row.get("occ_cnt") or 0)
                item["image_observation_count"] = int(row.get("obs_cnt") or 0)
                item["last_active_at"] = max(item["last_active_at"], float(row.get("last_ts") or 0.0))

        keyword = str(query or "").strip().lower()
        results: List[Dict[str, Any]] = []
        for item in entries.values():
            is_group = bool(item["group_id"]) or ("group" in item["chat_id"].lower())
            item["is_group"] = is_group
            if item["group_name"]:
                item["display_name"] = f"{item['group_name']} ({item['chat_id']})"
            elif item["group_id"]:
                item["display_name"] = f"群聊 {item['group_id']} ({item['chat_id']})"
            elif item["sender_name"]:
                item["display_name"] = f"{item['sender_name']} ({item['chat_id']})"
            elif item["user_id"]:
                item["display_name"] = f"私聊 {item['user_id']} ({item['chat_id']})"
            item["total_records"] = (
                int(item["paragraph_count"])
                + int(item["fact_claim_count"])
                + int(item["episode_count"])
                + max(int(item["image_occurrence_count"]), int(item["image_observation_count"]))
                + int(item["transcript_message_count"])
            )
            if item["total_records"] <= 0:
                continue
            if keyword:
                haystack = " ".join(
                    str(item.get(k) or "")
                    for k in ("chat_id", "display_name", "platform", "group_id", "group_name", "user_id", "sender_name", "unified_msg_origin")
                ).lower()
                if keyword not in haystack:
                    continue
            results.append(item)

        results.sort(key=lambda r: (float(r.get("last_active_at") or 0.0), int(r.get("total_records") or 0)), reverse=True)
        return results[: max(1, min(500, int(limit or 100)))]

    def _snapshot_sync(self, source_chat_id: str, target_chat_id: str) -> Dict[str, Any]:
        src = str(source_chat_id or "").strip()
        dst = str(target_chat_id or "").strip()
        if not src or not dst:
            raise ValueError("source_chat_id 和 target_chat_id 不能为空")
        if src == dst:
            raise ValueError("源会话与目标会话不能相同")
        migration = ChatMemoryMigration(self.ctx.metadata_store)
        raw_snap = migration.snapshot(src, dst)
        raw_counts = raw_snap.get("counts") if isinstance(raw_snap.get("counts"), dict) else {}
        paragraphs = raw_snap.get("paragraphs") if isinstance(raw_snap.get("paragraphs"), list) else []
        images = raw_snap.get("images") if isinstance(raw_snap.get("images"), list) else []
        facts = raw_snap.get("facts") if isinstance(raw_snap.get("facts"), list) else []
        episodes = raw_snap.get("episodes") if isinstance(raw_snap.get("episodes"), list) else []
        obs_rows = raw_snap.get("image_observations") if isinstance(raw_snap.get("image_observations"), list) else []
        occ_chat_map = {
            str(row.get("occurrence_id") or ""): str(row.get("chat_id") or "")
            for row in images
            if isinstance(row, dict)
        }
        src_obs = sum(1 for row in obs_rows if isinstance(row, dict) and occ_chat_map.get(str(row.get("occurrence_id") or "")) == src)
        dst_obs = sum(1 for row in obs_rows if isinstance(row, dict) and occ_chat_map.get(str(row.get("occurrence_id") or "")) == dst)

        logical_payload = {
            "paragraphs": [
                {
                    "hash": item["row"].get("hash"),
                    "content": item["row"].get("content"),
                    "source": item["row"].get("source"),
                    "metadata": item["row"].get("metadata"),
                    "scopes": item.get("scopes"),
                }
                for item in paragraphs
                if isinstance(item, dict) and isinstance(item.get("row"), dict)
            ],
            "facts": [
                {
                    "claim_id": row.get("claim_id"),
                    "scope_id": row.get("scope_id"),
                    "fact_key": row.get("fact_key"),
                    "value_text": row.get("value_text"),
                    "status": row.get("status"),
                }
                for row in facts
                if isinstance(row, dict)
            ],
            "episodes": [
                {"episode_id": row.get("episode_id"), "source": row.get("source"), "summary": row.get("summary")}
                for row in episodes
                if isinstance(row, dict)
            ],
            "images": [
                {"occurrence_id": row.get("occurrence_id"), "chat_id": row.get("chat_id"), "asset_id": row.get("asset_id")}
                for row in images
                if isinstance(row, dict)
            ],
        }
        logical_fp = hashlib.sha256(
            json.dumps(logical_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        source_counts = {
            "paragraphs": int(raw_counts.get("source") or 0),
            "facts": int(raw_counts.get("source_facts") or 0),
            "episodes": int(raw_counts.get("source_episodes") or 0),
            "image_occurrences": int(raw_counts.get("source_images") or 0),
            "image_observations": int(src_obs),
        }
        target_counts = {
            "paragraphs": int(raw_counts.get("target") or 0),
            "facts": int(raw_counts.get("target_facts") or 0),
            "episodes": int(raw_counts.get("target_episodes") or 0),
            "image_occurrences": int(raw_counts.get("target_images") or 0),
            "image_observations": int(dst_obs),
        }
        src_tr = self._transcript_counts_sync(src)
        dst_tr = self._transcript_counts_sync(dst)
        target_mem_total = self._counts_total(target_counts)
        return {
            "fingerprint": str(raw_snap.get("fingerprint") or ""),
            "logical_fingerprint": logical_fp,
            "counts": raw_counts,
            "source": source_counts,
            "target": target_counts,
            "transcript": {"source": src_tr, "target": dst_tr},
            "target_has_existing_data": bool(target_mem_total > 0 or dst_tr.get("messages", 0) > 0),
            "source_has_data": bool(
                self._counts_total(source_counts) > 0 or src_tr.get("messages", 0) > 0
            ),
        }

    def _migrate_transcript_sync(self, job_id: str, source_chat_id: str, target_chat_id: str) -> Dict[str, Any]:
        store = self.ctx.metadata_store
        archive_session_id = f"migration_archive:{job_id}:{target_chat_id}"
        now_ts = _now()
        with store.transaction(immediate=True) as conn:
            src_sess = conn.execute(
                "SELECT metadata_json, created_at, updated_at FROM transcript_sessions WHERE session_id = ?",
                (source_chat_id,),
            ).fetchone()
            dst_sess = conn.execute(
                "SELECT metadata_json, created_at, updated_at FROM transcript_sessions WHERE session_id = ?",
                (target_chat_id,),
            ).fetchone()

            target_msg_rows = conn.execute(
                "SELECT message_id FROM transcript_messages WHERE session_id = ? ORDER BY message_id ASC",
                (target_chat_id,),
            ).fetchall()
            archived_target_ids = [int(r[0]) for r in target_msg_rows]
            if archived_target_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO transcript_sessions(session_id, metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        archive_session_id,
                        (dst_sess[0] if dst_sess is not None else None) or "{}",
                        float((dst_sess[1] if dst_sess is not None else None) or now_ts),
                        now_ts,
                    ),
                )
                conn.execute(
                    "UPDATE transcript_messages SET session_id = ? WHERE session_id = ?",
                    (archive_session_id, target_chat_id),
                )

            source_msg_rows = conn.execute(
                "SELECT message_id FROM transcript_messages WHERE session_id = ? ORDER BY message_id ASC",
                (source_chat_id,),
            ).fetchall()
            moved_source_ids = [int(r[0]) for r in source_msg_rows]
            if src_sess is not None or moved_source_ids:
                if dst_sess is None:
                    conn.execute(
                        "INSERT OR IGNORE INTO transcript_sessions(session_id, metadata_json, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            target_chat_id,
                            (src_sess[0] if src_sess is not None else None) or "{}",
                            float((src_sess[1] if src_sess is not None else None) or now_ts),
                            now_ts,
                        ),
                    )
                elif moved_source_ids:
                    conn.execute(
                        "UPDATE transcript_sessions SET updated_at = ? WHERE session_id = ?",
                        (now_ts, target_chat_id),
                    )
            if moved_source_ids:
                conn.execute(
                    "UPDATE transcript_messages SET session_id = ? WHERE session_id = ?",
                    (target_chat_id, source_chat_id),
                )
        return {
            "archive_session_id": archive_session_id,
            "archived_target_message_ids": archived_target_ids,
            "moved_source_message_ids": moved_source_ids,
            "moved_count": len(moved_source_ids),
            "archived_target_count": len(archived_target_ids),
        }

    def _restore_transcript_sync(
        self,
        source_chat_id: str,
        target_chat_id: str,
        transcript_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        moved_ids = [int(x) for x in (transcript_info.get("moved_source_message_ids") or [])]
        archived_ids = [int(x) for x in (transcript_info.get("archived_target_message_ids") or [])]
        archive_session_id = str(transcript_info.get("archive_session_id") or "")
        if not moved_ids and not archived_ids:
            return {"restored_source_messages": 0, "restored_target_messages": 0}
        store = self.ctx.metadata_store
        now_ts = _now()
        with store.transaction(immediate=True) as conn:
            if moved_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO transcript_sessions(session_id, metadata_json, created_at, updated_at) "
                    "VALUES (?, '{}', ?, ?)",
                    (source_chat_id, now_ts, now_ts),
                )
                placeholders = ",".join("?" for _ in moved_ids)
                conn.execute(
                    f"UPDATE transcript_messages SET session_id = ? WHERE session_id = ? AND message_id IN ({placeholders})",
                    (source_chat_id, target_chat_id, *moved_ids),
                )
            if archived_ids and archive_session_id:
                conn.execute(
                    "INSERT OR IGNORE INTO transcript_sessions(session_id, metadata_json, created_at, updated_at) "
                    "VALUES (?, '{}', ?, ?)",
                    (target_chat_id, now_ts, now_ts),
                )
                placeholders = ",".join("?" for _ in archived_ids)
                conn.execute(
                    f"UPDATE transcript_messages SET session_id = ? WHERE session_id = ? AND message_id IN ({placeholders})",
                    (target_chat_id, archive_session_id, *archived_ids),
                )
        return {
            "restored_source_messages": len(moved_ids),
            "restored_target_messages": len(archived_ids),
        }

    async def list_chats(self, *, query: str = "", limit: int = 100) -> Dict[str, Any]:
        items = await settled_thread(self._list_chats_sync, query=query, limit=limit)
        return {"success": True, "items": items, "count": len(items)}

    async def snapshot(self, *, source_chat_id: str, target_chat_id: str) -> Dict[str, Any]:
        snap = await settled_thread(self._snapshot_sync, source_chat_id, target_chat_id)
        return {"success": True, **snap}

    async def preview(
        self,
        *,
        source_chat_id: str,
        target_chat_id: str,
        migrate_transcript: bool = True,
        requested_by: str = "webui",
        note: str = "",
    ) -> Dict[str, Any]:
        src = str(source_chat_id or "").strip()
        dst = str(target_chat_id or "").strip()
        snap = await settled_thread(self._snapshot_sync, src, dst)
        job_id = f"cmig_{uuid.uuid4().hex[:16]}"
        now_ts = _now()
        target_has_existing = int(bool(snap.get("target_has_existing_data")))

        def _insert_job() -> Dict[str, Any]:
            with self.ctx.metadata_store.transaction(immediate=True) as conn:
                conn.execute(
                    """
                    INSERT INTO astrbot_chat_migration_jobs(
                        job_id, source_chat_id, target_chat_id, status, migrate_transcript,
                        expected_fingerprint, target_has_existing_data, snapshot_json,
                        applied_json, restored_json, note, requested_by, error, created_at, updated_at
                    ) VALUES (?, ?, ?, 'previewed', ?, ?, ?, ?, '{}', '{}', ?, ?, '', ?, ?)
                    """,
                    (
                        job_id,
                        src,
                        dst,
                        1 if migrate_transcript else 0,
                        str(snap.get("fingerprint") or ""),
                        target_has_existing,
                        json.dumps(snap, ensure_ascii=False),
                        str(note or "").strip(),
                        str(requested_by or "webui").strip() or "webui",
                        now_ts,
                        now_ts,
                    ),
                )
            rows = self.ctx.metadata_store.query(
                "SELECT * FROM astrbot_chat_migration_jobs WHERE job_id = ?",
                (job_id,),
            )
            return self._serialize_job_row(rows[0])

        job = await settled_thread(_insert_job)
        return {"success": True, "job": job}

    async def list_jobs(self, *, limit: int = 50, status: str = "") -> Dict[str, Any]:
        safe_limit = max(1, min(200, int(limit or 50)))
        clean_status = str(status or "").strip().lower()

        def _query_jobs() -> List[Dict[str, Any]]:
            if clean_status:
                rows = self.ctx.metadata_store.query(
                    "SELECT * FROM astrbot_chat_migration_jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (clean_status, safe_limit),
                )
            else:
                rows = self.ctx.metadata_store.query(
                    "SELECT * FROM astrbot_chat_migration_jobs ORDER BY updated_at DESC LIMIT ?",
                    (safe_limit,),
                )
            return [self._serialize_job_row(row) for row in rows]

        items = await settled_thread(_query_jobs)
        return {"success": True, "items": items, "count": len(items)}

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        clean_id = str(job_id or "").strip()
        if not clean_id:
            raise ValueError("job_id 不能为空")

        def _fetch() -> Optional[Dict[str, Any]]:
            rows = self.ctx.metadata_store.query(
                "SELECT * FROM astrbot_chat_migration_jobs WHERE job_id = ?",
                (clean_id,),
            )
            return self._serialize_job_row(rows[0]) if rows else None

        job = await settled_thread(_fetch)
        if job is None:
            return {"success": False, "error": f"迁移任务不存在: {clean_id}"}
        return {"success": True, "job": job}

    async def apply(
        self,
        *,
        job_id: str,
        source_chat_id: str,
        target_chat_id: str,
        expected_fingerprint: str,
    ) -> Dict[str, Any]:
        src = str(source_chat_id or "").strip()
        dst = str(target_chat_id or "").strip()
        jid = str(job_id or "").strip()
        expected = str(expected_fingerprint or "").strip()
        if not jid or not src or not dst or not expected:
            raise ValueError("job_id、source_chat_id、target_chat_id 与 expected_fingerprint 均不能为空")

        async with _pause_migration_chats({src, dst}):
            result = await settled_thread(
                ChatMemoryMigration(self.ctx.metadata_store).apply,
                jid,
                src,
                dst,
                expected,
            )
            await self._persist_stores()
        return {"success": True, **result}

    async def execute(
        self,
        *,
        job_id: str,
        confirmed: bool = False,
        refresh_fingerprint: bool = False,
    ) -> Dict[str, Any]:
        clean_id = str(job_id or "").strip()
        if not clean_id:
            raise ValueError("job_id 不能为空")
        job_resp = await self.get_job(clean_id)
        if not job_resp.get("success"):
            return job_resp
        job = job_resp["job"]
        if job["status"] == "completed":
            return {"success": True, "job": job, "already_completed": True}
        if job["status"] not in {"previewed", "failed"}:
            return {"success": False, "error": f"当前任务状态不允许执行: {job['status']}", "job": job}
        if job["target_has_existing_data"] and not confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "error": "目标会话已存在记忆或聊天记录，执行迁移将归档目标会话现有数据，请勾选确认后重试",
                "job": job,
            }

        src = job["source_chat_id"]
        dst = job["target_chat_id"]

        try:
            async with _pause_migration_chats({src, dst}):
                expected_fp = job["expected_fingerprint"]
                saved_logical_fp = str((job.get("snapshot") or {}).get("logical_fingerprint") or "")
                fresh_snap = await settled_thread(self._snapshot_sync, src, dst)
                if refresh_fingerprint or not expected_fp:
                    expected_fp = str(fresh_snap.get("fingerprint") or "")
                elif fresh_snap.get("fingerprint") != expected_fp and saved_logical_fp and fresh_snap.get("logical_fingerprint") == saved_logical_fp:
                    expected_fp = str(fresh_snap.get("fingerprint") or "")
                apply_result = await settled_thread(
                    ChatMemoryMigration(self.ctx.metadata_store).apply,
                    clean_id,
                    src,
                    dst,
                    expected_fp,
                )
                if job["migrate_transcript"]:
                    transcript_res = await settled_thread(self._migrate_transcript_sync, clean_id, src, dst)
                    apply_result = {**apply_result, "transcript_migration": transcript_res}
                await self._persist_stores()

            def _mark_completed() -> Dict[str, Any]:
                now_ts = _now()
                with self.ctx.metadata_store.transaction(immediate=True) as conn:
                    conn.execute(
                        """
                        UPDATE astrbot_chat_migration_jobs
                        SET status = 'completed', expected_fingerprint = ?, applied_json = ?, error = '', updated_at = ?
                        WHERE job_id = ?
                        """,
                        (expected_fp, json.dumps(apply_result, ensure_ascii=False), now_ts, clean_id),
                    )
                rows = self.ctx.metadata_store.query(
                    "SELECT * FROM astrbot_chat_migration_jobs WHERE job_id = ?",
                    (clean_id,),
                )
                return self._serialize_job_row(rows[0])

            updated_job = await settled_thread(_mark_completed)
            return {"success": True, "job": updated_job, "result": apply_result}
        except Exception as exc:
            err_msg = str(exc)

            def _mark_failed() -> Dict[str, Any]:
                now_ts = _now()
                with self.ctx.metadata_store.transaction(immediate=True) as conn:
                    conn.execute(
                        "UPDATE astrbot_chat_migration_jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ?",
                        (err_msg, now_ts, clean_id),
                    )
                rows = self.ctx.metadata_store.query(
                    "SELECT * FROM astrbot_chat_migration_jobs WHERE job_id = ?",
                    (clean_id,),
                )
                return self._serialize_job_row(rows[0])

            failed_job = await settled_thread(_mark_failed)
            return {"success": False, "error": err_msg, "job": failed_job}

    async def recover(self, *, job_id: str) -> Dict[str, Any]:
        clean_id = str(job_id or "").strip()
        if not clean_id:
            raise ValueError("job_id 不能为空")

        job_resp = await self.get_job(clean_id)
        job = job_resp.get("job") if job_resp.get("success") else None
        pause_chats = (
            {str(job["source_chat_id"]), str(job["target_chat_id"])}
            if isinstance(job, dict) and job.get("source_chat_id") and job.get("target_chat_id")
            else set()
        )
        try:
            async with _pause_migration_chats(pause_chats):
                restore_res = await self._run_restore_core(clean_id, job)
        except Exception as exc:
            return {"success": False, "error": str(exc), **({"job": job} if job else {})}

        if job is None:
            return {"success": True, **restore_res}

        def _mark_restored() -> Dict[str, Any]:
            now_ts = _now()
            with self.ctx.metadata_store.transaction(immediate=True) as conn:
                conn.execute(
                    """
                    UPDATE astrbot_chat_migration_jobs
                    SET status = 'restored', restored_json = ?, error = '', updated_at = ?
                    WHERE job_id = ?
                    """,
                    (json.dumps(restore_res, ensure_ascii=False), now_ts, clean_id),
                )
            rows = self.ctx.metadata_store.query(
                "SELECT * FROM astrbot_chat_migration_jobs WHERE job_id = ?",
                (clean_id,),
            )
            return self._serialize_job_row(rows[0])

        updated_job = await settled_thread(_mark_restored)
        return {"success": True, "job": updated_job, "result": restore_res}

    async def _run_restore_core(self, job_id: str, job: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        restore_res = await settled_thread(ChatMemoryMigration(self.ctx.metadata_store).restore, job_id)
        if isinstance(job, dict):
            applied = job.get("applied_result") if isinstance(job.get("applied_result"), dict) else {}
            transcript_info = applied.get("transcript_migration")
            if isinstance(transcript_info, dict):
                tr_restore = await settled_thread(
                    self._restore_transcript_sync,
                    str(job["source_chat_id"]),
                    str(job["target_chat_id"]),
                    transcript_info,
                )
                restore_res = {**restore_res, "transcript_restore": tr_restore}
        await self._persist_stores()
        return restore_res

    async def _persist_stores(self) -> None:
        dedup_cache = getattr(self.ctx, "_request_dedup_cache", None)
        if isinstance(dedup_cache, dict):
            dedup_cache.clear()
        save_all = getattr(self.ctx, "save_all", None)
        if callable(save_all):
            await save_all()
