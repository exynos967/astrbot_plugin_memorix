#!/usr/bin/env python3
"""离线迁移脚本：单池向量 -> 双池（paragraph / graph 分离）。

用途
----
memorix vendored core 升级支持双池向量检索后，运行时默认仍以 ``mode=single`` 启动。
若用户希望启用 dual 模式，需要先运行本脚本，把历史单池向量按 ID 前缀分流到
``data_dir/vectors/paragraph`` 与 ``data_dir/vectors/graph`` 两个子池，并写
``dual_ready.json`` manifest。manifest 就绪后，运行时才会按 dual 模式工作。

分流规则（与写入端约定一致）
----------------------------
* 无前缀（裸 hash）          -> paragraph 池
* ``relation:`` 前缀          -> graph 池
* ``entity:`` 前缀            -> graph 池

幂等
----
* 不删除单池原数据（``data_dir/vectors/vectors.bin`` 等保留），
  dual 回退 single 时无数据损失。
* 已存在 ``dual_ready.json`` 时默认跳过，除非 ``--force`` 强制重写。

用法
----
    uv run python scripts/migrate_vectors_to_dual_pools.py --data-dir /path/to/scope
    uv run python scripts/migrate_vectors_to_dual_pools.py --data-dir /path/to/scope --dry-run
    uv run python scripts/migrate_vectors_to_dual_pools.py --data-dir /path/to/scope --force
    uv run python scripts/migrate_vectors_to_dual_pools.py --data-dir /path/to/scope --batch-size 2048
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# 模块导入：复用 vendored VectorStore，不引入任何外部运行时依赖。
# --------------------------------------------------------------------------- #

def _resolve_vector_store():
    """延迟导入 vendored VectorStore 与量化枚举。"""

    # 直接运行本脚本时（python scripts/xxx.py），Python 仅把脚本目录加进 sys.path，
    # 不含插件包根（memorix 的父目录）。此处显式注入包根，使 `import memorix` 可解析。
    _pkg_root = str(Path(__file__).resolve().parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)

    try:
        # 与 scripts/migrate_schema_v8_to_v13.py 同样的导入路径，
        # memorix/__init__.py 会把包根注入 sys.path。
        from memorix.core.storage.vector_store import VectorStore  # type: ignore
        from memorix.core.utils.quantization import QuantizationType  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(
            f"[错误] 无法导入 vendored VectorStore: {exc}\n"
            "请在插件包根目录下运行本脚本（astrbot_plugin_memorix/）。",
            file=sys.stderr,
        )
        sys.exit(2)
    return VectorStore, QuantizationType


def _resolve_logger():
    """延迟导入统一 logger，失败时回落到标准 logging。"""

    try:
        from memorix.amemorix.common.logging import get_logger  # type: ignore
        return get_logger("A_Memorix.MigrateDualPools")
    except Exception:  # noqa: BLE001
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        return logging.getLogger("A_Memorix.MigrateDualPools")


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

GRAPH_PREFIXES = ("relation:", "entity:")


def _classify(vector_id: str) -> str:
    """按 ID 前缀分流：返回 'paragraph' 或 'graph'。"""

    text = str(vector_id or "").strip()
    for prefix in GRAPH_PREFIXES:
        if text.startswith(prefix):
            return "graph"
    return "paragraph"


def _read_dimension(vectors_dir: Path) -> int:
    """从单池 ``vectors_metadata.pkl`` 读取 dimension。"""

    meta_path = vectors_dir / "vectors_metadata.pkl"
    if not meta_path.exists():
        return 0
    try:
        with meta_path.open("rb") as f:
            meta = pickle.load(f)
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(meta, dict):
        try:
            dim = int(meta.get("dimension", 0) or 0)
            if dim > 0:
                return dim
        except (TypeError, ValueError):
            return 0
    return 0


def _read_known_hashes(vectors_dir: Path) -> List[str]:
    """从单池 ``vectors_metadata.pkl`` 读取 known_hashes 列表，避免私有访问。"""

    meta_path = vectors_dir / "vectors_metadata.pkl"
    if not meta_path.exists():
        return []
    try:
        with meta_path.open("rb") as f:
            meta = pickle.load(f)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(meta, dict):
        return []
    raw = meta.get("known_hashes") or meta.get("ids") or []
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _dual_ready_manifest(vectors_dir: Path) -> Path:
    return vectors_dir / "dual_ready.json"


def _is_already_migrated(vectors_dir: Path) -> bool:
    """检查双池 ready manifest 是否已就绪。"""

    manifest = _dual_ready_manifest(vectors_dir)
    if not manifest.exists():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and payload.get("status") == "ready"


def _reset_pool_dir(pool_dir: Path) -> None:
    """``--force`` 时清空目标子池向量文件，避免 add 重复写入导致 ID 冲突。"""

    if not pool_dir.exists():
        return
    for fname in (
        "vectors.bin",
        "vectors_ids.bin",
        "vectors_metadata.pkl",
        "vectors.index",
    ):
        (pool_dir / fname).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def _migrate(
    data_dir: Path,
    *,
    scope: Optional[str],
    dry_run: bool,
    force: bool,
    batch_size: int,
) -> int:
    logger = _resolve_logger()
    VectorStore, QuantizationType = _resolve_vector_store()

    vectors_dir = data_dir / "vectors"
    scope_label = scope or data_dir.name
    logger.info("scope=%s data_dir=%s vectors_dir=%s", scope_label, data_dir, vectors_dir)

    if not vectors_dir.exists():
        logger.warning("scope=%s 未找到 vectors 目录，无需迁移。", scope_label)
        return 0

    dimension = _read_dimension(vectors_dir)
    if dimension <= 0:
        logger.error(
            "scope=%s 无法从 vectors_metadata.pkl 解析 dimension，请确认单池已初始化。",
            scope_label,
        )
        return 3

    # 幂等检查
    if _is_already_migrated(vectors_dir):
        if not force:
            logger.info(
                "scope=%s 双池已就绪（dual_ready.json 存在），跳过迁移。使用 --force 强制重写。",
                scope_label,
            )
            return 0
        logger.warning("scope=%s --force 模式：清空已有双池向量文件后重写。", scope_label)

    paragraph_dir = vectors_dir / "paragraph"
    graph_dir = vectors_dir / "graph"

    # 单池：读取所有已知 ID（不含已删除）。
    known_hashes = _read_known_hashes(vectors_dir)
    if not known_hashes:
        logger.info("scope=%s 单池无向量记录，跳过迁移。", scope_label)
        return 0

    # 分流统计
    paragraph_ids: List[str] = []
    graph_ids: List[str] = []
    for vid in known_hashes:
        if _classify(vid) == "graph":
            graph_ids.append(vid)
        else:
            paragraph_ids.append(vid)

    logger.info(
        "scope=%s dimension=%s total=%s paragraph=%s graph=%s",
        scope_label, dimension, len(known_hashes), len(paragraph_ids), len(graph_ids),
    )

    if dry_run:
        logger.info("[dry-run] 不写入双池，仅打印分流统计。")
        return 0

    # 准备双池目录
    paragraph_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)

    if force:
        _reset_pool_dir(paragraph_dir)
        _reset_pool_dir(graph_dir)

    quantization = QuantizationType.INT8

    # 打开单池（仅读，不动原数据）
    source_store = VectorStore(
        dimension=dimension,
        quantization_type=quantization,
        data_dir=vectors_dir,
    )
    source_store.load()

    migrated_count: Dict[str, int] = {"paragraph": 0, "graph": 0}

    # 段落池写入
    if paragraph_ids:
        paragraph_store = VectorStore(
            dimension=dimension,
            quantization_type=quantization,
            data_dir=paragraph_dir,
        )
        # 复用其 load() 以便 --force 后从空池起步；首次迁移目录为空时 load() 等价 no-op
        if not force:
            paragraph_store.load()
        migrated_count["paragraph"] = _copy_vectors(
            source=source_store,
            target=paragraph_store,
            ids=paragraph_ids,
            batch_size=batch_size,
            label="paragraph",
            logger=logger,
            scope_label=scope_label,
        )
        paragraph_store.save()

    # 图谱池写入
    if graph_ids:
        graph_store = VectorStore(
            dimension=dimension,
            quantization_type=quantization,
            data_dir=graph_dir,
        )
        if not force:
            graph_store.load()
        migrated_count["graph"] = _copy_vectors(
            source=source_store,
            target=graph_store,
            ids=graph_ids,
            batch_size=batch_size,
            label="graph",
            logger=logger,
            scope_label=scope_label,
        )
        graph_store.save()

    # 写 ready manifest
    manifest = {
        "status": "ready",
        "dimension": dimension,
        "created_at": int(time.time()),
        "migrated_count": migrated_count,
    }
    _dual_ready_manifest(vectors_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "scope=%s 迁移完成：paragraph=%s graph=%s；单池原数据保留，可安全回退 single。",
        scope_label,
        migrated_count["paragraph"],
        migrated_count["graph"],
    )
    return 0


def _copy_vectors(
    *,
    source: Any,
    target: Any,
    ids: List[str],
    batch_size: int,
    label: str,
    logger,
    scope_label: str,
) -> int:
    """从 source 按批次读取并写入 target，返回实际写入条数。"""

    safe_batch = max(1, int(batch_size or 1024))
    total_written = 0

    import numpy as np  # 延迟导入，避免脚本顶部强依赖 numpy

    for batch in source.iter_vectors_by_ids(ids, batch_size=safe_batch):
        if not batch:
            continue
        batch_ids = list(batch.keys())
        # iter_vectors_by_ids 已按 batch_size 切片，直接构造 (N, D) 矩阵写入。
        matrix = np.asarray(
            [batch[key] for key in batch_ids],
            dtype=np.float32,
        )

        written = target.add(vectors=matrix, ids=batch_ids)
        total_written += int(written)
        logger.debug(
            "scope=%s pool=%s 写入本批=%s 累计=%s",
            scope_label, label, written, total_written,
        )

    return total_written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Memorix 单池向量离线迁移到双池（paragraph / graph 分离）。",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="scope 数据目录（包含 vectors/ 子目录）。",
    )
    parser.add_argument(
        "--scope",
        default=None,
        help="scope_key，仅用于日志标注，可选。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印分流统计，不写入双池。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="已存在双池时强制重写（默认跳过已迁移）。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1024,
        help="单次读写批次大小，默认 1024。",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    if not data_dir.exists():
        print(f"[错误] data-dir 不存在: {data_dir}", file=sys.stderr)
        return 2

    return _migrate(
        data_dir=data_dir,
        scope=args.scope,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        batch_size=int(args.batch_size or 1024),
    )


if __name__ == "__main__":
    raise SystemExit(main())
