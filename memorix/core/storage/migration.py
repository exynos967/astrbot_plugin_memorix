"""启动与离线命令共用的 metadata 自动迁移边界。"""

import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...amemorix.common.logging import get_logger
from .core_schema import CORE_COLUMNS
from .schema import SCHEMA_VERSION

logger = get_logger("Memorix.Migration")


@dataclass(frozen=True)
class DatabaseState:
    version: int
    kind: str


def inspect_database(conn: sqlite3.Connection) -> DatabaseState:
    objects = conn.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if not objects:
        return DatabaseState(0, "empty")
    tables = {row[0] for row in objects if row[1] == "table"}
    version = 0
    if "schema_migrations" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        if not {"version", "applied_at"} <= columns:
            raise RuntimeError("无法识别 schema_migrations 结构，未执行自动迁移")
        raw = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if raw is not None and (not isinstance(raw, int) or raw < 0):
            raise RuntimeError(f"无效的数据库版本: {raw!r}")
        version = int(raw or 0)
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"数据库版本较新: current={version}, expected={SCHEMA_VERSION}；请升级插件，禁止自动降级")
    if version == SCHEMA_VERSION:
        validate_schema_shape(conn)
        return DatabaseState(version, "current")

    required = {
        "paragraphs": {"hash", "content"},
        "relations": {"hash", "subject", "predicate", "object"},
    }
    # 无版本信息时要求更多历史字段，不能把任意同名表当作 Memorix 数据。
    if version == 0:
        required["paragraphs"] |= {"vector_index", "created_at", "metadata", "source", "word_count"}
        required["relations"] |= {"vector_index", "confidence", "created_at", "metadata", "source_paragraph"}
    for table, fields in required.items():
        rows = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        valid = fields <= rows.keys() and {name for name, row in rows.items() if row[5]} == {"hash"}
        if not valid or str(rows["hash"][2]).upper() != "TEXT":
            raise RuntimeError(f"无法识别旧库结构 {table}: current={version}, expected={SCHEMA_VERSION}；未执行迁移")
    for table, fields in {
        "entities": {"hash", "name"},
        "deleted_relations": {"hash", "subject", "predicate", "object"},
    }.items():
        if table in tables:
            columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            if not fields <= columns.keys() or {name for name, row in columns.items() if row[5]} != {"hash"}:
                raise RuntimeError(f"无法识别旧库结构 {table}；未执行迁移")
    return DatabaseState(version, "legacy" if version == 0 else "upgrade")


def inspect_database_file(path: Path) -> DatabaseState:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return inspect_database(conn)


def backup_database(path: Path, *, source_version: int) -> Path:
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{path.name}.v{source_version}-to-v{SCHEMA_VERSION}.{time.strftime('%Y%m%d-%H%M%S')}.{uuid.uuid4().hex[:12]}.sqlite3"
    backup_path = directory / name
    backup_path.touch(exist_ok=False)
    stalled_at = time.monotonic()

    def progress(status: int, remaining: int, total: int) -> None:
        nonlocal stalled_at
        if status not in {5, 6}:  # SQLITE_BUSY / SQLITE_LOCKED，兼容 Python 3.10。
            stalled_at = time.monotonic()
        elif time.monotonic() - stalled_at > 30:
            raise TimeoutError("数据库备份等待锁超时")

    try:
        # 迁移连接持有 BEGIN IMMEDIATE。另开只读连接取得提交态快照，
        # 避免在持写事务的同一连接上调用 backup 导致等待自身锁。
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup_path)) as target:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("数据库备份完整性检查失败")
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def validate_schema_shape(conn: sqlite3.Connection) -> None:
    required = {table: set(columns) for table, columns in CORE_COLUMNS.items()}
    required.update(
        {
            "episode_rebuild_sources": {"source", "desired_revision", "built_revision", "lease_token", "lease_until"},
            "episode_paragraphs": {"episode_id", "paragraph_hash", "position"},
            "paragraph_stale_relation_marks": {
                "paragraph_hash",
                "relation_hash",
                "query_tool_id",
                "task_id",
                "source_type",
                "source_id",
                "source_operation_id",
            },
            "fact_claims": {"claim_id", "scope_type", "scope_id", "status", "value_text"},
            "person_profile_alias_overrides": {"person_id", "aliases_json"},
            "memory_projection_jobs": {"item_type", "item_hash", "revision", "item_key"},
            "memory_deletion_owners": {"item_type", "item_hash", "operation_id"},
            "transcript_messages": {"message_id", "session_id", "position", "content"},
            "person_registry": {"person_id", "person_name", "metadata_json"},
            "async_tasks": {"task_id", "task_type", "status"},
        }
    )
    for table, expected in required.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if missing := expected - present:
            raise RuntimeError(f"数据库缺少字段: {table}: {', '.join(sorted(missing))}")


def validate_migrated_schema(conn: sqlite3.Connection) -> None:
    validate_schema_shape(conn)
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("迁移后数据库完整性检查失败")
    if row := conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError(f"迁移后存在无效关联: table={row[0]}, rowid={row[1]}；未自动删除旧数据")


def migrate_metadata(store: Any) -> dict[str, Any] | None:
    state = inspect_database(store._conn)
    if state.kind == "current":
        return None
    if store._conn.in_transaction:
        raise RuntimeError("自动迁移必须在后台任务启动前、无活动事务时执行")
    backup_path = None
    try:
        with store.transaction(immediate=True) as conn:
            # 数据库写锁负责跨连接/进程串行化，等待期间其他实例可能已完成迁移。
            state = inspect_database(conn)
            if state.kind == "current":
                return None
            if state.kind != "empty":
                backup_path = backup_database(store._db_path, source_version=state.version)
                logger.info("metadata 迁移备份已保存: %s", backup_path)
                store._migrate_schema()
            else:
                store._initialize_tables()
            store._ensure_plugin_local_schema()
            aliases = store.rebuild_relation_hash_aliases()
            knowledge = store.normalize_paragraph_knowledge_types()
            if state.version < 22:
                store.backfill_person_fact_claims()
            validate_migrated_schema(conn)
            store.set_schema_version(SCHEMA_VERSION)
        report = {
            "from_version": state.version,
            "to_version": SCHEMA_VERSION,
            "backup_path": str(backup_path) if backup_path else None,
            "aliases": aliases,
            "knowledge_types": knowledge,
        }
        logger.info("metadata 自动迁移完成: %s -> %s", state.version, SCHEMA_VERSION)
        return report
    except Exception as exc:
        location = f"；备份: {backup_path}" if backup_path else ""
        raise RuntimeError(f"metadata 自动迁移失败，事务已回滚{location}；原因: {exc}") from exc
