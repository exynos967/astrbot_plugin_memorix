"""核心表定义：新建与旧库逐列迁移共用，避免两份 schema 漂移。"""

import sqlite3

CORE_COLUMNS = {
    "paragraphs": {
        "hash": "TEXT PRIMARY KEY",
        "content": "TEXT NOT NULL",
        "vector_index": "INTEGER",
        "created_at": "REAL",
        "updated_at": "REAL",
        "metadata": "TEXT",
        "source": "TEXT",
        "word_count": "INTEGER",
        "event_time": "REAL",
        "event_time_start": "REAL",
        "event_time_end": "REAL",
        "time_granularity": "TEXT",
        "time_confidence": "REAL DEFAULT 1.0",
        "knowledge_type": "TEXT DEFAULT 'mixed'",
        "is_permanent": "BOOLEAN DEFAULT 0",
        "last_accessed": "REAL",
        "access_count": "INTEGER DEFAULT 0",
        "is_deleted": "INTEGER DEFAULT 0",
        "deleted_at": "REAL",
    },
    "entities": {
        "hash": "TEXT PRIMARY KEY",
        "name": "TEXT NOT NULL UNIQUE",
        "vector_index": "INTEGER",
        "appearance_count": "INTEGER DEFAULT 1",
        "created_at": "REAL",
        "metadata": "TEXT",
        "is_deleted": "INTEGER DEFAULT 0",
        "deleted_at": "REAL",
    },
    "relations": {
        "hash": "TEXT PRIMARY KEY",
        "subject": "TEXT NOT NULL",
        "predicate": "TEXT NOT NULL",
        "object": "TEXT NOT NULL",
        "vector_index": "INTEGER",
        "confidence": "REAL DEFAULT 1.0",
        "vector_state": "TEXT DEFAULT 'none'",
        "vector_updated_at": "REAL",
        "vector_error": "TEXT",
        "vector_retry_count": "INTEGER DEFAULT 0",
        "created_at": "REAL",
        "source_paragraph": "TEXT",
        "metadata": "TEXT",
        "is_permanent": "BOOLEAN DEFAULT 0",
        "last_accessed": "REAL",
        "access_count": "INTEGER DEFAULT 0",
        "is_inactive": "BOOLEAN DEFAULT 0",
        "inactive_since": "REAL",
        "is_pinned": "BOOLEAN DEFAULT 0",
        "protected_until": "REAL",
        "last_reinforced": "REAL",
    },
    "deleted_relations": {
        "hash": "TEXT PRIMARY KEY",
        "subject": "TEXT NOT NULL",
        "predicate": "TEXT NOT NULL",
        "object": "TEXT NOT NULL",
        "vector_index": "INTEGER",
        "confidence": "REAL DEFAULT 1.0",
        "vector_state": "TEXT DEFAULT 'none'",
        "vector_updated_at": "REAL",
        "vector_error": "TEXT",
        "vector_retry_count": "INTEGER DEFAULT 0",
        "created_at": "REAL",
        "source_paragraph": "TEXT",
        "metadata": "TEXT",
        "is_permanent": "BOOLEAN DEFAULT 0",
        "last_accessed": "REAL",
        "access_count": "INTEGER DEFAULT 0",
        "is_inactive": "BOOLEAN DEFAULT 0",
        "inactive_since": "REAL",
        "is_pinned": "BOOLEAN DEFAULT 0",
        "protected_until": "REAL",
        "last_reinforced": "REAL",
        "deleted_at": "REAL",
    },
}

CORE_CONSTRAINTS = {
    "paragraphs": [],
    "entities": [],
    "relations": ["UNIQUE(subject, predicate, object)"],
    "deleted_relations": [],
}


def create_core_tables(conn: sqlite3.Connection) -> None:
    for table, columns in CORE_COLUMNS.items():
        definitions = [f"{name} {definition}" for name, definition in columns.items()]
        definitions.extend(CORE_CONSTRAINTS[table])
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({','.join(definitions)})")


def ensure_legacy_core_columns(conn: sqlite3.Connection) -> None:
    create_core_tables(conn)
    for table, columns in CORE_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name in existing:
                continue
            if (
                "PRIMARY KEY" in definition
                or "UNIQUE" in definition
                or ("NOT NULL" in definition and "DEFAULT" not in definition)
            ):
                raise RuntimeError(f"无法推断旧表必填字段: {table}.{name}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
