#!/usr/bin/env python3
"""离线 schema 迁移脚本：memorix metadata.db v8 -> 当前 SCHEMA_VERSION。

用途
----
正常启动会自动升级可识别的旧 metadata.db（包括 v8 和字段指纹匹配的无版本库）。
本脚本提供批量预检查、停机后手动升级和备份恢复，复用运行时迁移入口。
迁移前通过 SQLite Backup API 保存包含 WAL 数据的快照，失败时整体回滚。

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
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

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


def _restore_db(db_path: Path, backup_path: Path) -> None:
    if not backup_path.is_file():
        raise ValueError(f"备份文件不存在: {backup_path}")
    if backup_path.resolve() == db_path.resolve():
        raise ValueError("备份文件不能与恢复目标相同")
    with closing(sqlite3.connect(backup_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("备份完整性检查失败，未执行恢复")
        def progress(status, remaining, total):
            if status in {5, 6}:
                raise RuntimeError("恢复目标数据库正忙，请停止插件后重试")

        with closing(sqlite3.connect(db_path)) as target:
            source.backup(target, progress=progress)
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
    from memorix.core.storage.migration import inspect_database_file

    state = inspect_database_file(db_path) if db_path.exists() else None
    current = state.version if state else 0
    print(f"[信息] 当前 schema_version={current}, 目标={schema_version}, db={db_path}")
    if state is not None and state.kind == "current":
        print(f"[信息] 已是最新版本 (v{current})，无需迁移。")
        return 0
    if dry_run:
        print("[dry-run] 将执行：SQLite 一致性备份（旧库） -> 事务内升级/补列/回填 -> 完整性检查 -> 提交版本；不改动数据库")
        return 0
    store = MetadataStore(data_dir=str(db_path.parent), db_name=db_path.name)
    try:
        store.connect()
        report = store.migration_report or {}
        after = store.get_schema_version()
    finally:
        store.close()
    print(f"[完成] 迁移结束: v{current} -> v{after}")
    if report.get("backup_path"):
        print(f"[备份] {report['backup_path']}（如需回滚，请停机后使用 --restore）")
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
    parser.add_argument("--restore", type=Path, help="停止插件后，从指定备份恢复单个 --db 数据库")
    args = parser.parse_args()

    if args.restore is not None:
        if args.db is None:
            parser.error("--restore 必须配合 --db 使用")
        try:
            _restore_db(args.db, args.restore)
            return 0
        except Exception as exc:
            print(f"[错误] 恢复失败: {exc}", file=sys.stderr)
            return 3

    if args.db is not None:
        try:
            return _migrate(args.db, args.dry_run)
        except Exception as exc:
            print(f"[错误] 迁移失败: {exc}", file=sys.stderr)
            return 3

    return _migrate_all(args.plugin_data_dir, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
