"""SQLite schema compatibility helpers for legacy metadata.db upgrades."""

import sqlite3
from typing import Any, Mapping


def table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    """Return existing columns for a known SQLite table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {str(row[1]) for row in cursor.fetchall()}


def ensure_table_column(
    cursor: sqlite3.Cursor,
    *,
    table_name: str,
    column_name: str,
    add_column_sql: str,
    logger: Any = None,
) -> None:
    """Add a missing column for legacy DBs where CREATE TABLE IF NOT EXISTS is not enough."""
    columns = table_columns(cursor, table_name)
    if not columns or column_name in columns:
        return
    try:
        cursor.execute(add_column_sql)
        if logger:
            logger.info("Schema兼容迁移完成：已添加 %s.%s", table_name, column_name)
    except sqlite3.OperationalError as e:
        if logger:
            logger.warning("Schema兼容迁移失败（%s.%s）: %s", table_name, column_name, e)
        raise


def ensure_table_columns(
    cursor: sqlite3.Cursor,
    *,
    table_name: str,
    columns: Mapping[str, str],
    logger: Any = None,
) -> None:
    """Add a group of missing columns to one legacy table."""
    for column_name, add_column_sql in columns.items():
        ensure_table_column(
            cursor,
            table_name=table_name,
            column_name=column_name,
            add_column_sql=add_column_sql,
            logger=logger,
        )


def backfill_column_from_legacy(
    cursor: sqlite3.Cursor,
    *,
    table_name: str,
    target_column: str,
    legacy_column: str,
    logger: Any = None,
) -> None:
    """Copy data from an old column name into a new column when both exist."""
    columns = table_columns(cursor, table_name)
    if target_column not in columns or legacy_column not in columns:
        return
    cursor.execute(
        f"""
        UPDATE {table_name}
        SET {target_column} = {legacy_column}
        WHERE ({target_column} IS NULL OR TRIM({target_column}) = '')
          AND {legacy_column} IS NOT NULL
          AND TRIM({legacy_column}) != ''
        """
    )
    if cursor.rowcount > 0 and logger:
        logger.info(
            "Schema兼容迁移完成：已从 %s.%s 回填 %s.%s（%s 行）",
            table_name,
            legacy_column,
            table_name,
            target_column,
            cursor.rowcount,
        )


def backfill_transcript_message_positions(cursor: sqlite3.Cursor, *, logger: Any = None) -> None:
    """Populate transcript message positions for pre-position legacy rows."""
    columns = table_columns(cursor, "transcript_messages")
    if "position" not in columns or "message_id" not in columns or "session_id" not in columns:
        return
    cursor.execute(
        """
        UPDATE transcript_messages
        SET position = (
            SELECT COUNT(*) - 1
            FROM transcript_messages AS previous
            WHERE previous.session_id = transcript_messages.session_id
              AND previous.message_id <= transcript_messages.message_id
        )
        WHERE position IS NULL OR position = 0
        """
    )
    if cursor.rowcount > 0 and logger:
        logger.info("Schema兼容迁移完成：已回填 transcript_messages.position（%s 行）", cursor.rowcount)


def ensure_transcript_schema_compat(cursor: sqlite3.Cursor, *, logger: Any = None) -> None:
    """Patch transcript tables created by pre-0.9 schemas."""
    ensure_table_columns(
        cursor,
        table_name="transcript_sessions",
        columns={
            "source": "ALTER TABLE transcript_sessions ADD COLUMN source TEXT",
            "metadata_json": "ALTER TABLE transcript_sessions ADD COLUMN metadata_json TEXT",
            "created_at": "ALTER TABLE transcript_sessions ADD COLUMN created_at REAL",
            "updated_at": "ALTER TABLE transcript_sessions ADD COLUMN updated_at REAL",
        },
        logger=logger,
    )
    backfill_column_from_legacy(
        cursor,
        table_name="transcript_sessions",
        target_column="metadata_json",
        legacy_column="metadata",
        logger=logger,
    )

    message_columns_before = table_columns(cursor, "transcript_messages")
    ensure_table_columns(
        cursor,
        table_name="transcript_messages",
        columns={
            "position": "ALTER TABLE transcript_messages ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
            "metadata_json": "ALTER TABLE transcript_messages ADD COLUMN metadata_json TEXT",
            "created_at": "ALTER TABLE transcript_messages ADD COLUMN created_at REAL",
        },
        logger=logger,
    )
    backfill_column_from_legacy(
        cursor,
        table_name="transcript_messages",
        target_column="metadata_json",
        legacy_column="metadata",
        logger=logger,
    )
    if "position" not in message_columns_before:
        backfill_transcript_message_positions(cursor, logger=logger)

    ensure_table_columns(
        cursor,
        table_name="transcript_summary_state",
        columns={
            "last_summary_at": "ALTER TABLE transcript_summary_state ADD COLUMN last_summary_at REAL",
            "last_message_created_at": "ALTER TABLE transcript_summary_state ADD COLUMN last_message_created_at REAL",
            "last_task_id": "ALTER TABLE transcript_summary_state ADD COLUMN last_task_id TEXT",
            "summary_count": "ALTER TABLE transcript_summary_state ADD COLUMN summary_count INTEGER NOT NULL DEFAULT 0",
            "metadata_json": "ALTER TABLE transcript_summary_state ADD COLUMN metadata_json TEXT",
            "created_at": "ALTER TABLE transcript_summary_state ADD COLUMN created_at REAL",
            "updated_at": "ALTER TABLE transcript_summary_state ADD COLUMN updated_at REAL",
        },
        logger=logger,
    )
    state_columns = table_columns(cursor, "transcript_summary_state")
    if "summary_count" in state_columns:
        cursor.execute(
            """
            UPDATE transcript_summary_state
            SET summary_count = 0
            WHERE summary_count IS NULL
            """
        )
    if {"created_at", "updated_at", "last_summary_at", "last_message_created_at"} <= state_columns:
        cursor.execute(
            """
            UPDATE transcript_summary_state
            SET created_at = COALESCE(created_at, updated_at, last_summary_at, last_message_created_at)
            WHERE created_at IS NULL
            """
        )


def ensure_person_registry_schema_compat(cursor: sqlite3.Cursor, *, logger: Any = None) -> None:
    """Patch person_registry tables created by pre-metadata_json schemas."""
    ensure_table_columns(
        cursor,
        table_name="person_registry",
        columns={
            "person_name": "ALTER TABLE person_registry ADD COLUMN person_name TEXT",
            "nickname": "ALTER TABLE person_registry ADD COLUMN nickname TEXT",
            "user_id": "ALTER TABLE person_registry ADD COLUMN user_id TEXT",
            "platform": "ALTER TABLE person_registry ADD COLUMN platform TEXT",
            "group_nick_name": "ALTER TABLE person_registry ADD COLUMN group_nick_name TEXT",
            "memory_points": "ALTER TABLE person_registry ADD COLUMN memory_points TEXT",
            "last_know": "ALTER TABLE person_registry ADD COLUMN last_know REAL",
            "metadata_json": "ALTER TABLE person_registry ADD COLUMN metadata_json TEXT",
            "created_at": "ALTER TABLE person_registry ADD COLUMN created_at REAL",
            "updated_at": "ALTER TABLE person_registry ADD COLUMN updated_at REAL",
        },
        logger=logger,
    )
    backfill_column_from_legacy(
        cursor,
        table_name="person_registry",
        target_column="metadata_json",
        legacy_column="metadata",
        logger=logger,
    )
