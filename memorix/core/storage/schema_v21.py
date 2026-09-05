"""A_memorix schema 16-21 增量补丁（插件侧）。

开闭原则：
- 上游表/列升级集中在本模块，不改写 MetadataStore 的 CRUD 主路径。
- 插件本地表（transcript_* / person_registry / async_tasks / episode_pending_paragraphs）
  仍由 MetadataStore._ensure_plugin_local_schema 维护。
- 上游 schema 19 会 DROP episode_pending_paragraphs；插件保留该表作为兼容队列，
  只把 pending 来源复制进 episode_rebuild_sources，不删除旧表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable

import sqlite3

SCHEMA_VERSION = 21

FACT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fact_claims (
        claim_id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        value_text TEXT NOT NULL,
        value_normalized TEXT NOT NULL,
        polarity TEXT NOT NULL,
        cardinality TEXT NOT NULL,
        conflict_group TEXT NOT NULL,
        stability TEXT NOT NULL,
        profile_section TEXT NOT NULL,
        authority TEXT NOT NULL,
        status TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        valid_from REAL,
        valid_to REAL,
        first_observed_at REAL NOT NULL,
        last_confirmed_at REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_claims_scope_status
    ON fact_claims(scope_type, scope_id, status, profile_section, last_confirmed_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_claims_conflict
    ON fact_claims(scope_type, scope_id, conflict_group, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_evidence (
        claim_id TEXT NOT NULL,
        evidence_type TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        stance TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        observed_at REAL NOT NULL,
        metadata_json TEXT,
        PRIMARY KEY(claim_id, evidence_type, evidence_id, stance),
        FOREIGN KEY(claim_id) REFERENCES fact_claims(claim_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_evidence_claim
    ON fact_evidence(claim_id, observed_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_transitions (
        transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_claim_id TEXT,
        new_claim_id TEXT,
        transition_type TEXT NOT NULL,
        reason TEXT,
        evidence_type TEXT,
        evidence_id TEXT,
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_transitions_old
    ON fact_transitions(old_claim_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fact_transitions_new
    ON fact_transitions(new_claim_id, created_at DESC)
    """,
)


def _table_names(cursor: sqlite3.Cursor) -> set[str]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row[0]) for row in cursor.fetchall()}


def _columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cursor.fetchall()}


def _ensure_columns(cursor: sqlite3.Cursor, table: str, migrations: Dict[str, str]) -> None:
    if table not in _table_names(cursor):
        return
    existing = _columns(cursor, table)
    for column, sql in migrations.items():
        if column not in existing:
            cursor.execute(sql)


def apply_upstream_schema_v21(conn: sqlite3.Connection, logger: Any = None) -> Dict[str, int]:
    """幂等补齐 schema 16-21 的上游表与列。"""
    cursor = conn.cursor()
    tables = _table_names(cursor)
    added_columns = 0

    if "paragraphs" in tables:
        before = _columns(cursor, "paragraphs")
        _ensure_columns(
            cursor,
            "paragraphs",
            {
                "expires_at": "ALTER TABLE paragraphs ADD COLUMN expires_at REAL",
                "deletion_reason": "ALTER TABLE paragraphs ADD COLUMN deletion_reason TEXT",
            },
        )
        added_columns += len(_columns(cursor, "paragraphs") - before)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paragraphs_expiration
            ON paragraphs(is_deleted, expires_at)
            """
        )

    relation_columns = {
        "retention_strength": "ALTER TABLE {table} ADD COLUMN retention_strength REAL NOT NULL DEFAULT 1.0",
        "retention_anchor_at": "ALTER TABLE {table} ADD COLUMN retention_anchor_at REAL",
        "next_lifecycle_at": "ALTER TABLE {table} ADD COLUMN next_lifecycle_at REAL",
        "reinforcement_count": "ALTER TABLE {table} ADD COLUMN reinforcement_count INTEGER NOT NULL DEFAULT 0",
        "lifecycle_revision": "ALTER TABLE {table} ADD COLUMN lifecycle_revision INTEGER NOT NULL DEFAULT 0",
        "inactive_reason": "ALTER TABLE {table} ADD COLUMN inactive_reason TEXT",
        "last_access_reinforced_at": "ALTER TABLE {table} ADD COLUMN last_access_reinforced_at REAL",
    }
    now = datetime.now().timestamp()
    for table in ("relations", "deleted_relations"):
        if table not in tables:
            continue
        before = _columns(cursor, table)
        _ensure_columns(
            cursor,
            table,
            {name: sql.format(table=table) for name, sql in relation_columns.items()},
        )
        added_columns += len(_columns(cursor, table) - before)
        cursor.execute(
            f"""
            UPDATE {table}
            SET retention_strength = MIN(1.0, MAX(0.0, COALESCE(retention_strength, 1.0))),
                retention_anchor_at = COALESCE(retention_anchor_at, ?),
                reinforcement_count = COALESCE(reinforcement_count, 0),
                lifecycle_revision = COALESCE(lifecycle_revision, 0)
            """,
            (now,),
        )
    if "relations" in tables:
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relations_lifecycle_due
            ON relations(is_inactive, next_lifecycle_at, hash)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relations_inactive_reason
            ON relations(is_inactive, inactive_reason, inactive_since)
            """
        )

    if "episodes" in tables:
        _ensure_columns(
            cursor,
            "episodes",
            {"input_fingerprint": "ALTER TABLE episodes ADD COLUMN input_fingerprint TEXT"},
        )

    if "person_profile_snapshots" in tables:
        _ensure_columns(
            cursor,
            "person_profile_snapshots",
            {
                "evidence_fingerprint": "ALTER TABLE person_profile_snapshots ADD COLUMN evidence_fingerprint TEXT",
                "fact_claim_ids_json": "ALTER TABLE person_profile_snapshots ADD COLUMN fact_claim_ids_json TEXT",
            },
        )

    if "episode_rebuild_sources" in tables:
        _ensure_columns(
            cursor,
            "episode_rebuild_sources",
            {
                "desired_revision": (
                    "ALTER TABLE episode_rebuild_sources "
                    "ADD COLUMN desired_revision INTEGER NOT NULL DEFAULT 1"
                ),
                "built_revision": (
                    "ALTER TABLE episode_rebuild_sources "
                    "ADD COLUMN built_revision INTEGER NOT NULL DEFAULT 0"
                ),
                "claimed_revision": "ALTER TABLE episode_rebuild_sources ADD COLUMN claimed_revision INTEGER",
                "dirty_start": "ALTER TABLE episode_rebuild_sources ADD COLUMN dirty_start REAL",
                "dirty_end": "ALTER TABLE episode_rebuild_sources ADD COLUMN dirty_end REAL",
                "first_requested_at": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN first_requested_at REAL"
                ),
                "ready_at": "ALTER TABLE episode_rebuild_sources ADD COLUMN ready_at REAL",
                "lease_token": "ALTER TABLE episode_rebuild_sources ADD COLUMN lease_token TEXT",
                "lease_until": "ALTER TABLE episode_rebuild_sources ADD COLUMN lease_until REAL",
                "next_attempt_at": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN next_attempt_at REAL"
                ),
                "built_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN built_generation_hash TEXT"
                ),
                "claimed_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN claimed_generation_hash TEXT"
                ),
                "retry_revision": "ALTER TABLE episode_rebuild_sources ADD COLUMN retry_revision INTEGER",
                "retry_generation_hash": (
                    "ALTER TABLE episode_rebuild_sources ADD COLUMN retry_generation_hash TEXT"
                ),
            },
        )
        cursor.execute(
            """
            UPDATE episode_rebuild_sources
            SET desired_revision = MAX(1, COALESCE(desired_revision, 1)),
                built_revision = CASE
                    WHEN status = 'done' THEN MAX(1, COALESCE(desired_revision, 1))
                    ELSE MIN(COALESCE(built_revision, 0), MAX(0, COALESCE(desired_revision, 1) - 1))
                END,
                first_requested_at = COALESCE(first_requested_at, requested_at, ?),
                ready_at = COALESCE(ready_at, requested_at, ?),
                next_attempt_at = COALESCE(next_attempt_at, requested_at, ?)
            """,
            (now, now, now),
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_claim
            ON episode_rebuild_sources(lease_until, next_attempt_at, ready_at, first_requested_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_rebuild_revision
            ON episode_rebuild_sources(desired_revision, built_revision)
            """
        )
        if "episode_pending_paragraphs" in tables:
            cursor.execute(
                """
                INSERT INTO episode_rebuild_sources (
                    source, status, retry_count, last_error, reason,
                    requested_at, updated_at, desired_revision, built_revision,
                    first_requested_at, ready_at, next_attempt_at
                )
                SELECT source, 'pending', 0, NULL, 'schema_21_pending_copy',
                       MIN(updated_at), ?, 1, 0, MIN(updated_at), MIN(updated_at), MIN(updated_at)
                FROM episode_pending_paragraphs
                WHERE source IS NOT NULL AND TRIM(source) != ''
                GROUP BY source
                ON CONFLICT(source) DO NOTHING
                """,
                (now,),
            )

    if "delete_operations" in tables:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_cleanup_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                expected_state_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                lease_token TEXT,
                lease_until REAL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                UNIQUE(operation_id, resource_type, resource_id, action),
                FOREIGN KEY (operation_id) REFERENCES delete_operations(operation_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_storage_cleanup_ready
            ON storage_cleanup_jobs(status, next_attempt_at, lease_until, created_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_storage_cleanup_operation
            ON storage_cleanup_jobs(operation_id, status, job_id)
            """
        )

    for statement in FACT_SCHEMA_STATEMENTS:
        cursor.execute(statement)

    conn.commit()
    if logger is not None:
        logger.info("applied upstream schema v21 patch: added_columns=%s", added_columns)
    return {"added_columns": added_columns}


def expected_v21_tables() -> Iterable[str]:
    return (
        "fact_claims",
        "fact_evidence",
        "fact_transitions",
    )
