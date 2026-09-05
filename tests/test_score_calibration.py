from __future__ import annotations

from astrbot_plugin_memorix.memorix.core.retrieval.score_calibration import (
    fuse_score_maps,
    normalize_calibration_method,
)


def test_none_calibration_keeps_weighted_sum() -> None:
    finals, _calibrated = fuse_score_maps(
        {
            "semantic": {"a": 1.0, "b": 0.2},
            "sparse": {"a": 0.0, "b": 1.0},
            "graph": {"a": 0.0, "b": 0.0},
        },
        {"semantic": 0.65, "sparse": 0.20, "graph": 0.15},
        method="none",
    )
    assert finals["a"] == 0.65
    assert finals["b"] == 0.65 * 0.2 + 0.20


def test_normalize_calibration_method_rejects_unknown() -> None:
    try:
        normalize_calibration_method("not-a-method")
    except ValueError as exc:
        assert "不支持的分数校准方法" in str(exc)
    else:
        raise AssertionError("expected ValueError")
