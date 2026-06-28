from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from astrbot_plugin_memorix.memorix.amemorix.bootstrap import _dual_vector_ready
from astrbot_plugin_memorix.memorix.amemorix.context import AppContext
from astrbot_plugin_memorix.memorix.amemorix.settings import DEFAULT_CONFIG


def test_dual_vector_ready_false_on_empty_dir(tmp_path: Path) -> None:
    """空目录无 ready manifest，应返回 False。"""
    assert _dual_vector_ready(tmp_path, expected_dimension=128) is False


def test_dual_vector_ready_true_when_manifest_and_subdirs_present(tmp_path: Path) -> None:
    """manifest status=ready + 维度匹配 + paragraph/graph 子目录存在 → True。"""
    vectors_dir = tmp_path / "vectors"
    (vectors_dir / "paragraph").mkdir(parents=True)
    (vectors_dir / "graph").mkdir(parents=True)
    (vectors_dir / "dual_ready.json").write_text(
        json.dumps({"status": "ready", "dimension": 128}),
        encoding="utf-8",
    )
    assert _dual_vector_ready(tmp_path, expected_dimension=128) is True


def test_dual_vector_ready_false_on_dimension_mismatch(tmp_path: Path) -> None:
    """维度不匹配应返回 False。"""
    vectors_dir = tmp_path / "vectors"
    (vectors_dir / "paragraph").mkdir(parents=True)
    (vectors_dir / "graph").mkdir(parents=True)
    (vectors_dir / "dual_ready.json").write_text(
        json.dumps({"status": "ready", "dimension": 256}),
        encoding="utf-8",
    )
    assert _dual_vector_ready(tmp_path, expected_dimension=128) is False


def test_dual_vector_ready_false_when_status_not_ready(tmp_path: Path) -> None:
    """status 字段非 ready 应返回 False。"""
    vectors_dir = tmp_path / "vectors"
    (vectors_dir / "paragraph").mkdir(parents=True)
    (vectors_dir / "graph").mkdir(parents=True)
    (vectors_dir / "dual_ready.json").write_text(
        json.dumps({"status": "pending", "dimension": 128}),
        encoding="utf-8",
    )
    assert _dual_vector_ready(tmp_path, expected_dimension=128) is False


def test_default_config_dual_pool_defaults_to_single() -> None:
    """DEFAULT_CONFIG 默认 single 模式，runtime 就绪标志为 False。"""
    assert DEFAULT_CONFIG["retrieval"]["vector_pools"]["mode"] == "single"
    assert DEFAULT_CONFIG["runtime"]["vector_pools_ready"] is False


def test_appcontext_has_dual_pool_fields_and_method() -> None:
    """AppContext dataclass 应含双池字段，并暴露 _dual_vector_pools_enabled 方法。"""
    field_names = {field.name for field in dataclasses.fields(AppContext)}
    assert "paragraph_vector_store" in field_names
    assert "graph_vector_store" in field_names
    assert "_dual_vector_pools_ready" in field_names
    assert callable(getattr(AppContext, "_dual_vector_pools_enabled", None))


def test_appcontext_dual_pool_enabled_reflects_ready_flag() -> None:
    """_dual_vector_pools_enabled 应直接反映 _dual_vector_pools_ready 标志。"""
    # 构造最小 AppContext 实例仅用于方法行为校验，绕过 __init__ 必填字段。
    ctx = AppContext.__new__(AppContext)
    ctx._dual_vector_pools_ready = False
    assert ctx._dual_vector_pools_enabled() is False
    ctx._dual_vector_pools_ready = True
    assert ctx._dual_vector_pools_enabled() is True
