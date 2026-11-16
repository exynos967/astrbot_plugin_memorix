"""Modular tool registry and declarative AdminContractDispatcher for Memorix."""

from __future__ import annotations

from typing import Any

from astrbot.api import FunctionTool

from .admin_tools import (
    MemorixAdminToolBase,
    MemorixBundleAdminTool,
    MemorixCorrectionAdminTool,
    MemorixDeleteAdminTool,
    MemorixEpisodeAdminTool,
    MemorixFactAdminTool,
    MemorixFeedbackAdminTool,
    MemorixFuzzyModifyAdminTool,
    MemorixGraphAdminTool,
    MemorixImageAdminTool,
    MemorixImportAdminTool,
    MemorixProfileAdminTool,
    MemorixRuntimeAdminTool,
    MemorixSourceAdminTool,
    MemorixTuningAdminTool,
    MemorixV5AdminTool,
    _admin_parameters,
)
from .base import MemorixToolBase
from .core_tools import (
    MemorixIngestSummaryTool,
    MemorixIngestTextTool,
    MemorixMaintainTool,
    MemorixPersonProfileTool,
    MemorixSearchTool,
    MemorixStatsTool,
)
from .dispatcher import (
    DEFAULT_ADMIN_DISPATCHER,
    AdminContractDispatcher,
    AdminToolRegistration,
    build_default_admin_dispatcher,
)
from .formatting import (
    _format_search_result_for_llm,
    _hit_time_text,
    _search_hit_items,
    _to_float_or_none,
    _to_int,
    _tool_result,
    _truncate_text,
)


def _extended_v2_tools_enabled(plugin: Any) -> bool:
    raw_cfg = getattr(plugin, "config", None)
    if not isinstance(raw_cfg, dict):
        return False
    integration_cfg = raw_cfg.get("integration")
    if not isinstance(integration_cfg, dict):
        return False
    return bool(
        integration_cfg.get("enable_v2_extended_tools", False)
        or integration_cfg.get("enable_extended_v2_admin_tools", False)
    )


def build_memorix_tools(
    plugin: Any,
    *,
    include_extended_v2_tools: bool | None = None,
) -> list[FunctionTool[Any]]:
    """Build the AstrBot FunctionTool suite for a MemorixPlugin instance.

    By default, builds the 18 backward-compatible core tools unless
    ``integration.enable_v2_extended_tools`` is True (or ``include_extended_v2_tools=True``),
    which appends ``memory_bundle_admin``, ``memory_correction_admin``, and ``memory_image_admin``.
    """
    tools: list[FunctionTool[Any]] = [
        MemorixSearchTool(plugin=plugin),
        MemorixIngestSummaryTool(plugin=plugin),
        MemorixIngestTextTool(plugin=plugin),
        MemorixPersonProfileTool(plugin=plugin),
        MemorixMaintainTool(plugin=plugin),
        MemorixStatsTool(plugin=plugin),
        MemorixGraphAdminTool(plugin=plugin),
        MemorixSourceAdminTool(plugin=plugin),
        MemorixEpisodeAdminTool(plugin=plugin),
        MemorixProfileAdminTool(plugin=plugin),
        MemorixFactAdminTool(plugin=plugin),
        MemorixRuntimeAdminTool(plugin=plugin),
        MemorixImportAdminTool(plugin=plugin),
        MemorixTuningAdminTool(plugin=plugin),
        MemorixFeedbackAdminTool(plugin=plugin),
        MemorixV5AdminTool(plugin=plugin),
        MemorixDeleteAdminTool(plugin=plugin),
        MemorixFuzzyModifyAdminTool(plugin=plugin),
    ]
    enable_extended = (
        _extended_v2_tools_enabled(plugin)
        if include_extended_v2_tools is None
        else bool(include_extended_v2_tools)
    )
    if enable_extended:
        tools.extend(
            [
                MemorixBundleAdminTool(plugin=plugin),
                MemorixCorrectionAdminTool(plugin=plugin),
                MemorixImageAdminTool(plugin=plugin),
            ]
        )
    return tools


def build_all_v2_memorix_tools(plugin: Any) -> list[FunctionTool[Any]]:
    """Build the full A_memorix v2.0.0 FunctionTool suite including bundle, correction, and image admin tools."""
    return build_memorix_tools(plugin, include_extended_v2_tools=True)


__all__ = [
    "AdminContractDispatcher",
    "AdminToolRegistration",
    "DEFAULT_ADMIN_DISPATCHER",
    "MemorixAdminToolBase",
    "MemorixBundleAdminTool",
    "MemorixCorrectionAdminTool",
    "MemorixDeleteAdminTool",
    "MemorixEpisodeAdminTool",
    "MemorixFactAdminTool",
    "MemorixFeedbackAdminTool",
    "MemorixFuzzyModifyAdminTool",
    "MemorixGraphAdminTool",
    "MemorixImageAdminTool",
    "MemorixImportAdminTool",
    "MemorixIngestSummaryTool",
    "MemorixIngestTextTool",
    "MemorixMaintainTool",
    "MemorixPersonProfileTool",
    "MemorixRuntimeAdminTool",
    "MemorixSearchTool",
    "MemorixSourceAdminTool",
    "MemorixStatsTool",
    "MemorixToolBase",
    "MemorixTuningAdminTool",
    "MemorixV5AdminTool",
    "_admin_parameters",
    "_format_search_result_for_llm",
    "_hit_time_text",
    "_search_hit_items",
    "_to_float_or_none",
    "_to_int",
    "_tool_result",
    "_truncate_text",
    "build_all_v2_memorix_tools",
    "build_default_admin_dispatcher",
    "build_memorix_tools",
]
