#!/usr/bin/env python3
"""离线 schema 迁移脚本：memorix metadata.db v8 -> 当前 SCHEMA_VERSION。

用途
----
插件 vendored core 升级后，``metadata_store.SCHEMA_VERSION`` 会随内核 schema 演进。
新版运行时自动迁移要求 ``schema_version >= RUNTIME_AUTO_MIGRATION_MIN_SCHEMA_VERSION (9)``，
因此历史 v8 库首次启动会被 ``_assert_schema_compatible`` 直接拒绝。本脚本提供离线兜底：
直接复用新版 ``MetadataStore._migrate_schema()``（幂等全量 DDL）把老库推到当前版本。

何时用
------
* 用户不信任运行时自动迁移，想手动升级后再启动；
* 运行时迁移因环境问题失败，需要离线修复。

用法
----
    uv run python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db
    uv run python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db --dry-run
    uv run python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db --restore /path/to/metadata.db.v8.bak

注意：本脚本需在插件代码已升级到目标版本后运行，目标版本取当前 ``SCHEMA_VERSION``。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _resolve_metadata_store():
    """延迟导入新版 MetadataStore（Phase 1 core 升级后才可用）。"""

    # 直接运行本脚本时（python scripts/xxx.py），Python 仅把脚本目录加进 sys.path，
    # 不含插件包根（memorix 的父目录）。此处显式注入包根，使 `import memorix` 可解析。
    pkg_root = str(Path(__file__).resolve().parent.parent)
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

    try:
        # 插件包内绝对导入（memorix/__init__.py 已注入 sys.path）
        from memorix.core.storage.metadata_store import SCHEMA_VERSION, MetadataStore  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(
            f"[错误] 无法导入新版 MetadataStore: {exc}\n"
            "请确认插件 vendored core 已升级到目标版本。",
            file=sys.stderr,
        )
        sys.exit(2)
    return MetadataStore, SCHEMA_VERSION


def _read_current_version(db_path: Path) -> int:
    """直连读取当前 schema_version，缺失 schema_migrations 视为 0。"""

    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cursor.fetchone() is None:
            return 0
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def _backup_db(db_path: Path) -> Path:
    backup_path = db_path.with_suffix(db_path.suffix + f".v8.bak.{int(time.time())}")
    shutil.copy2(str(db_path), str(backup_path))
    return backup_path


def _restore_db(db_path: Path, backup_path: Path) -> None:
    if not backup_path.exists():
        print(f"[错误] 备份文件不存在: {backup_path}", file=sys.stderr)
        sys.exit(2)
    shutil.copy2(str(backup_path), str(db_path))
    print(f"[完成] 已从备份恢复: {backup_path} -> {db_path}")


def _migrate(db_path: Path, dry_run: bool) -> int:
    MetadataStore, schema_version = _resolve_metadata_store()
    current = _read_current_version(db_path)
    print(f"[信息] 当前 schema_version={current}, 目标={schema_version}, db={db_path}")

    if current == 0 and not db_path.exists():
        print(f"[信息] 数据库不存在，将创建全新 v{schema_version} 库。")
    elif current >= schema_version:
        print(f"[信息] 已是最新版本 (v{current})，无需迁移。")
        return 0

    if dry_run:
        print("[dry-run] 将执行：备份 -> _migrate_schema() -> rebuild_relation_hash_aliases() "
              f"-> normalize_paragraph_knowledge_types() -> set_schema_version({schema_version})")
        return 0

    backup_path: Path | None = None
    if db_path.exists():
        backup_path = _backup_db(db_path)
        print(f"[备份] {backup_path}")

    store = MetadataStore(data_dir=str(db_path.parent), db_name=db_path.name)
    # enforce_schema=False 避免老库在 _assert_schema_compatible 直接抛错，
    # 改由本脚本显式调用 _migrate_schema 推进版本。
    store.connect(enforce_schema=False)
    try:
        store._migrate_schema()
        alias_result: Dict[str, Any] = store.rebuild_relation_hash_aliases()
        kt_result: Dict[str, Any] = store.normalize_paragraph_knowledge_types()
        store.set_schema_version(schema_version)
        if store._conn is not None:
            store._conn.commit()
    finally:
        store.close()

    after = _read_current_version(db_path)
    print(
        f"[完成] 迁移结束: v{current} -> v{after}, "
        f"alias_inserted={int(alias_result.get('inserted', 0) or 0)}, "
        f"knowledge_normalized={int(kt_result.get('normalized', 0) or 0)}"
    )
    if backup_path is not None:
        print(f"[提示] 旧库已备份于: {backup_path}（如需回滚使用 --restore）")
    return 0 if after == schema_version else 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Memorix metadata.db schema 离线迁移 v8 -> 当前 SCHEMA_VERSION",
    )
    parser.add_argument("--db", required=True, type=Path, help="metadata.db 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的步骤，不改动数据库")
    parser.add_argument("--restore", type=Path, help="从指定备份恢复数据库")
    args = parser.parse_args()

    if args.restore is not None:
        _restore_db(args.db, args.restore)
        return 0

    return _migrate(args.db, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
