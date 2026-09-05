"""AstrBot Memorix 自有存储版本与增量迁移。"""

import sqlite3

from .schema_v22 import apply_upstream_schema_v22

SCHEMA_VERSION = 23


def apply_schema(conn: sqlite3.Connection, logger=None) -> None:
    apply_upstream_schema_v22(conn, logger)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_projection_jobs (
            item_type TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            item_key TEXT,
            revision INTEGER NOT NULL DEFAULT 1,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_error TEXT,
            PRIMARY KEY (item_type, item_hash)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_deletion_owners (
            item_type TEXT NOT NULL,
            item_hash TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            PRIMARY KEY (item_type, item_hash),
            FOREIGN KEY (operation_id) REFERENCES delete_operations(operation_id)
        )
    """)
    # 仅监听内容和生存状态，向量索引回写不会再次触发同步。
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_projection_jobs)")}
    if "item_key" not in columns:
        conn.execute("ALTER TABLE memory_projection_jobs ADD COLUMN item_key TEXT")
    watched = {
        "paragraph": ("paragraphs", "content, is_deleted", "hash"),
        "entity": ("entities", "name, is_deleted", "name"),
        "relation": ("relations", "subject, predicate, object, confidence, is_inactive", "hash"),
    }
    for kind, (table, fields, identity) in watched.items():
        for event in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if event == "DELETE" else "NEW"
            clause = f"UPDATE OF {fields}" if event == "UPDATE" else event
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS memorix_project_{kind}_{event.lower()}
                AFTER {clause} ON {table} BEGIN
                    INSERT INTO memory_projection_jobs (item_type, item_hash, item_key)
                    VALUES ('{kind}', {ref}.hash, {ref}.{identity})
                    ON CONFLICT(item_type, item_hash) DO UPDATE SET
                        revision = revision + 1, item_key = excluded.item_key,
                        attempts = 0, next_attempt_at = 0, last_error = NULL;
                END
            """)
        live = "1" if kind == "relation" else "COALESCE(NEW.is_deleted, 0) = 0"
        for event in ("INSERT", "UPDATE"):
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS memorix_owner_{kind}_{event.lower()}
                AFTER {event} ON {table} WHEN {live} BEGIN
                    DELETE FROM memory_deletion_owners WHERE item_type = '{kind}' AND item_hash = NEW.hash;
                END
            """)
    conn.commit()
