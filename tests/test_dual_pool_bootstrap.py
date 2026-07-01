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


def test_default_config_dual_pool_defaults_align_with_maibot() -> None:
    """DEFAULT_CONFIG 默认 dual，与上游 A_memorix 对齐；runtime 就绪标志为 False。"""
    vector_pools = DEFAULT_CONFIG["retrieval"]["vector_pools"]
    assert vector_pools["mode"] == "dual"
    assert vector_pools["relation_evidence_weight"] == 1.0
    assert vector_pools["entity_evidence_weight"] == 0.55
    assert vector_pools["relation_intent"]["graph_top_k"] == 80
    assert vector_pools["relation_intent"]["semantic_weight"] == 0.45
    assert vector_pools["relation_intent"]["sparse_weight"] == 0.15
    assert vector_pools["relation_intent"]["graph_weight"] == 0.40
    assert vector_pools["relation_intent"]["return_relation_items"] is False
    relation_vectorization = DEFAULT_CONFIG["retrieval"]["relation_vectorization"]
    assert relation_vectorization["enabled"] is False
    assert relation_vectorization["backfill_enabled"] is False
    assert relation_vectorization["write_on_import"] is True
    assert DEFAULT_CONFIG["runtime"]["vector_pools_ready"] is False


def test_conf_schema_exposes_dual_pool_config() -> None:
    """AstrBot Dashboard schema 应暴露 dual-pool 配置树。"""
    schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    vector_pools = schema["retrieval"]["items"]["vector_pools"]
    assert vector_pools["type"] == "object"
    assert vector_pools["items"]["mode"]["default"] == "dual"
    assert vector_pools["items"]["mode"]["options"] == ["single", "dual"]
    assert vector_pools["items"]["relation_evidence_weight"]["default"] == 1.0
    assert vector_pools["items"]["entity_evidence_weight"]["default"] == 0.55
    relation_intent = vector_pools["items"]["relation_intent"]["items"]
    assert relation_intent["graph_top_k"]["default"] == 80
    assert relation_intent["semantic_weight"]["default"] == 0.45
    assert relation_intent["sparse_weight"]["default"] == 0.15
    assert relation_intent["graph_weight"]["default"] == 0.4
    assert relation_intent["return_relation_items"]["default"] is False


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
