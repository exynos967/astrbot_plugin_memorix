"""A_memorix schema 22 增量迁移，保留 AstrBot 的本地表。"""

import sqlite3

from .schema_v21 import apply_upstream_schema_v21

SCHEMA_VERSION = 22


def apply_upstream_schema_v22(conn: sqlite3.Connection, logger=None) -> None:
    version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0
    if int(version) < 21:
        apply_upstream_schema_v21(conn, logger)
    if int(version) < 22:
        conn.execute("UPDATE person_profile_snapshots SET expires_at = 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS person_profile_alias_overrides (
            person_id TEXT PRIMARY KEY,
            aliases_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            updated_by TEXT,
            source TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_person_profile_alias_overrides_updated
        ON person_profile_alias_overrides(updated_at DESC)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paragraph_fact_evidence_backups (
            paragraph_hash TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graph_pending_renames (
            operation_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL
        )
    """)
    conn.commit()
