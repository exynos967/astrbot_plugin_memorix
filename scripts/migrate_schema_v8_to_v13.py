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
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py --dry-run
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py --plugin-data-dir /path/to/data/plugin_data/astrbot_plugin_memorix
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db --dry-run
    uv run --no-project python scripts/migrate_schema_v8_to_v13.py --db /path/to/metadata.db --restore /path/to/metadata.db.v8.bak

注意：本脚本需在插件代码已升级到目标版本后运行，目标版本取当前 ``SCHEMA_VERSION``。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict

PLUGIN_NAME = "astrbot_plugin_memorix"


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


def _scope_label(scope_dir: Path) -> str:
    manifest = scope_dir / ".scope.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            scope_key = str(payload.get("scope_key", "") or "").strip()
            if scope_key:
                return scope_key
        except Exception:  # noqa: BLE001
            pass
    return scope_dir.name


def _candidate_plugin_data_dirs(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit.expanduser()]

    cwd = Path.cwd()
    script_root = Path(__file__).resolve().parent.parent
    candidates = [
        cwd / "data" / "plugin_data" / PLUGIN_NAME,
        script_root / "data" / "plugin_data" / PLUGIN_NAME,
    ]

    for base in (cwd, script_root):
        for parent in (base, *base.parents):
            if parent.name == "data":
                candidates.append(parent / "plugin_data" / PLUGIN_NAME)
            candidates.append(parent / "data" / "plugin_data" / PLUGIN_NAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        token = str(resolved)
        if token not in seen:
            unique.append(resolved)
            seen.add(token)
    return unique


def _discover_scope_dbs(plugin_data_dir: Path | None) -> list[tuple[str, Path]]:
    discovered: list[tuple[str, Path]] = []
    for candidate in _candidate_plugin_data_dirs(plugin_data_dir):
        scopes_dir = candidate / "scopes"
        if not scopes_dir.exists():
            continue
        for scope_dir in sorted(scopes_dir.iterdir(), key=lambda path: path.name):
            if not scope_dir.is_dir():
                continue
            db_path = scope_dir / "metadata" / "metadata.db"
            if db_path.exists():
                discovered.append((_scope_label(scope_dir), db_path))
        if discovered:
            print(f"[信息] 自动发现 scope 数据目录: {candidate}")
            break
    return discovered


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


def _migrate_all(plugin_data_dir: Path | None, dry_run: bool) -> int:
    scope_dbs = _discover_scope_dbs(plugin_data_dir)
    if not scope_dbs:
        searched = ", ".join(str(path) for path in _candidate_plugin_data_dirs(plugin_data_dir)[:5])
        print(
            "[信息] 未发现可迁移的 scope metadata.db，已跳过。"
            f" 搜索路径示例: {searched}"
        )
        return 0

    print(f"[信息] 发现 {len(scope_dbs)} 个 scope metadata.db，开始批量迁移。")
    failures: list[tuple[str, Path, int]] = []
    for index, (scope_key, db_path) in enumerate(scope_dbs, start=1):
        print(f"[scope {index}/{len(scope_dbs)}] {scope_key}: {db_path}")
        try:
            code = _migrate(db_path, dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"[错误] scope={scope_key} 迁移异常: {exc}", file=sys.stderr)
            code = 3
        if code != 0:
            failures.append((scope_key, db_path, code))

    if failures:
        print(f"[错误] {len(failures)} 个 scope 迁移失败：", file=sys.stderr)
        for scope_key, db_path, code in failures:
            print(f"  - scope={scope_key} code={code} db={db_path}", file=sys.stderr)
        return 3
    print("[完成] 所有 scope metadata.db 均已处理。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Memorix metadata.db schema 离线迁移 v8 -> 当前 SCHEMA_VERSION",
    )
    parser.add_argument("--db", type=Path, help="metadata.db 文件路径；不填则自动扫描所有 scope")
    parser.add_argument(
        "--plugin-data-dir",
        type=Path,
        help="插件数据目录，默认自动查找 data/plugin_data/astrbot_plugin_memorix",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的步骤，不改动数据库")
    parser.add_argument("--restore", type=Path, help="从指定备份恢复单个 --db 数据库")
    args = parser.parse_args()

    if args.restore is not None:
        if args.db is None:
            parser.error("--restore 必须配合 --db 使用")
        _restore_db(args.db, args.restore)
        return 0

    if args.db is not None:
        return _migrate(args.db, args.dry_run)

    return _migrate_all(args.plugin_data_dir, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
