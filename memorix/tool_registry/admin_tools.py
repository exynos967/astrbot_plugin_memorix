"""Declarative Admin FunctionTools backed by AdminContractDispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from pydantic import Field
from pydantic.dataclasses import dataclass

from .base import MemorixToolBase
from .dispatcher import DEFAULT_ADMIN_DISPATCHER
from .formatting import _tool_result

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.tool import ToolExecResult
    from astrbot.core.astr_agent_context import AstrAgentContext


def _admin_parameters(actions: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": f"管理动作：{actions}。"},
            "target": {"type": "string", "description": "目标标识、hash 或查询文本，可选。"},
            "query": {"type": "string", "description": "查询文本，可选。"},
            "limit": {"type": "integer", "description": "返回条数，默认按动作决定。"},
            "source": {"type": "string", "description": "来源批次/source，可选。"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "来源批次列表，可选。"},
            "node": {"type": "string", "description": "节点名，可选。"},
            "node_id": {"type": "string", "description": "节点 ID，可选。"},
            "name": {"type": "string", "description": "名称，可选。"},
            "old_name": {"type": "string", "description": "重命名前节点名。"},
            "new_name": {"type": "string", "description": "重命名后节点名。"},
            "source_node": {"type": "string", "description": "图边源节点，兼容字段。"},
            "subject": {"type": "string", "description": "关系主体/source。"},
            "predicate": {"type": "string", "description": "关系谓词/标签。"},
            "object": {"type": "string", "description": "关系客体/target。"},
            "target_node": {"type": "string", "description": "图边目标节点，兼容字段。"},
            "weight": {"type": "number", "description": "边权重/置信度。"},
            "confidence": {"type": "number", "description": "关系置信度。"},
            "hash": {"type": "string", "description": "段落/关系/实体 hash。"},
            "relation_hash": {"type": "string", "description": "关系 hash。"},
            "mode": {"type": "string", "description": "delete_admin/bundle_admin 的模式。"},
            "selector": {"type": "object", "description": "选择器对象。"},
            "restore_type": {"type": "string", "description": "恢复类型：relation/entity。"},
            "person_id": {"type": "string", "description": "人物 ID。"},
            "aliases": {"type": "array", "items": {"type": "string"}, "description": "人工确认的完整人物别名集合。"},
            "identifier": {"type": "string", "description": "身份解析标识符。"},
            "platform": {"type": "string", "description": "消息平台标识。"},
            "user_id": {"type": "string", "description": "平台用户 ID。"},
            "group_id": {"type": "string", "description": "平台群组 ID。"},
            "person_keyword": {"type": "string", "description": "人物关键词。"},
            "keyword": {"type": "string", "description": "关键词。"},
            "override_text": {"type": "string", "description": "人物画像手工覆盖内容。"},
            "text": {"type": "string", "description": "文本内容。"},
            "task_id": {"type": "string", "description": "导入/调优/反馈任务 ID。"},
            "file_id": {"type": "string", "description": "导入任务文件 ID。"},
            "content": {"type": "string", "description": "粘贴导入文本。"},
            "chat_log": {"type": "boolean", "description": "按时间和发送者消息边界切分聊天记录。"},
            "narrative_window_size": {"type": "integer", "minimum": 200, "maximum": 100000},
            "narrative_overlap": {"type": "integer", "minimum": 0},
            "alias": {"type": "string", "description": "导入扫描路径别名。"},
            "relative_path": {"type": "string", "description": "导入扫描相对路径。"},
            "include_chunks": {"type": "boolean", "description": "是否包含导入 chunks。"},
            "include_paragraphs": {"type": "boolean", "description": "是否包含 Episode 段落。"},
            "dry_run": {"type": "boolean", "description": "是否仅预演不执行。"},
            "enabled": {"type": "boolean", "description": "开关值。"},
            "hours": {"type": "number", "description": "保护时长。"},
            "strength": {"type": "number", "description": "强化/弱化强度。"},
            "reason": {"type": "string", "description": "操作原因。"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }


def _correction_parameters(*, include_plan: bool = True) -> dict:
    actions = ["preview", "plan", "execute", "get", "list", "rollback", "reconcile"] if include_plan else [
        "preview",
        "execute",
        "get",
        "list",
        "rollback",
        "reconcile",
    ]
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": actions,
                "description": "操作类型",
            },
            "request_text": {"type": "string", "description": "preview/plan: 用户的自然语言修改请求"},
            "plan_id": {"type": "string", "description": "execute/get/rollback: 计划 id"},
            "person_id": {"type": "string"},
            "person_keyword": {"type": "string"},
            "chat_id": {"type": "string"},
            "limit": {"type": "integer"},
            "confirmed": {"type": "boolean"},
            "status": {"type": "string"},
            "requested_by": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }


@dataclass
class MemorixAdminToolBase(MemorixToolBase):
    admin_method: str = Field(default="", repr=False)

    def _require_admin(self, event: AstrMessageEvent) -> None:
        is_admin = getattr(event, "is_admin", None)
        allowed = bool(is_admin()) if callable(is_admin) else str(getattr(event, "role", "member")) == "admin"
        if not allowed:
            raise PermissionError("Memorix admin tools require AstrBot admin permission.")

    async def _call_admin(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        scope_key = "unknown"
        action = str(kwargs.get("action", "") or "").strip().lower()
        try:
            event = self._event(context)
            self._require_admin(event)
            kwargs.pop("scope_key", None)
            scope_key = self._scope_key(event)
            action = str(kwargs.pop("action", "") or "").strip().lower()
            if not action:
                return _tool_result({"success": False, "error": "action is required", "scope": scope_key})
            if DEFAULT_ADMIN_DISPATCHER.get(self.name) is not None:
                data = await DEFAULT_ADMIN_DISPATCHER.dispatch(
                    self.plugin,
                    scope_key=scope_key,
                    component_name=self.name,
                    action=action,
                    payload=kwargs,
                )
            else:
                service_method = getattr(self.plugin.admin_service, self.admin_method)
                data = await service_method(scope_key=scope_key, action=action, **kwargs)
                if isinstance(data, dict):
                    data.setdefault("scope", scope_key)
                    data.setdefault("action", action)
            return _tool_result(data)
        except PermissionError as exc:
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key, "action": action})
        except Exception as exc:
            logger.warning("[memorix] %s tool failed: %s", self.name, exc, exc_info=True)
            return _tool_result({"success": False, "error": str(exc), "scope": scope_key, "action": action})


@dataclass
class MemorixGraphAdminTool(MemorixAdminToolBase):
    name: str = "memory_graph_admin"
    description: str = "长期记忆图谱管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "graph_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "get_graph/search/node_detail/edge_detail/create_node/delete_node/rename_node/create_edge/delete_edge/update_edge_weight"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixSourceAdminTool(MemorixAdminToolBase):
    name: str = "memory_source_admin"
    description: str = "长期记忆来源批次管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "source_admin"
    parameters: dict = Field(default_factory=lambda: _admin_parameters("list/delete/batch_delete"))

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixEpisodeAdminTool(MemorixAdminToolBase):
    name: str = "memory_episode_admin"
    description: str = "Episode 管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "episode_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "query/list/get/status/rebuild/process_pending/process_sources/discard_migration_backfill"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixProfileAdminTool(MemorixAdminToolBase):
    name: str = "memory_profile_admin"
    description: str = "人物画像管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "profile_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "query/list/status/evidence/correct_evidence/process_pending/observe_identity/resolve_identity/set_override/delete_override/get_aliases/set_aliases/delete_aliases"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixRuntimeAdminTool(MemorixAdminToolBase):
    name: str = "memory_runtime_admin"
    description: str = "长期记忆运行时管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "runtime_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "save/get_config/self_check/refresh_self_check/recover_embedding/rebuild_all_vectors/paragraph_backfill_once/set_auto_save"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixImportAdminTool(MemorixAdminToolBase):
    name: str = "memory_import_admin"
    description: str = "长期记忆导入管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "import_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "settings/get_guide/path_aliases/resolve_path/create_paste/create_raw_scan/list/get/chunks/cancel/retry_failed"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixTuningAdminTool(MemorixAdminToolBase):
    name: str = "memory_tuning_admin"
    description: str = "长期记忆检索调优管理接口；仅 AstrBot 管理员可用。可读取/应用/回滚检索参数快照，创建调优任务并查看轮次与报告。"
    admin_method: str = "tuning_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters(
            "settings/get_profile/apply/apply_profile/rollback/rollback_profile/export_toml/export_profile/create/create_task/list/list_tasks/get/get_task/rounds/get_rounds/cancel/apply_best/report/get_report"
        )
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixFeedbackAdminTool(MemorixAdminToolBase):
    name: str = "memory_feedback_admin"
    description: str = "长期记忆反馈纠错管理接口；仅 AstrBot 管理员可用。可列出/查看反馈纠错任务、回滚已应用的纠错、查看操作日志、手动触发 reconcile。"
    admin_method: str = "feedback_admin"
    parameters: dict = Field(default_factory=lambda: _admin_parameters("list/get/rollback/logs/list_logs/reconcile/trigger_reconcile"))

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixV5AdminTool(MemorixAdminToolBase):
    name: str = "memory_v5_admin"
    description: str = "长期记忆 V5 管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "v5_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters("status/recycle_bin/restore/reinforce/weaken/remember_forever/forget")
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixDeleteAdminTool(MemorixAdminToolBase):
    name: str = "memory_delete_admin"
    description: str = "长期记忆删除管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "delete_admin"
    parameters: dict = Field(
        default_factory=lambda: _admin_parameters("preview/execute/restore/list_operations/get_operation/purge")
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixFuzzyModifyAdminTool(MemorixAdminToolBase):
    name: str = "memory_fuzzy_modify_admin"
    description: str = "记忆模糊修正管理：基于自然语言请求预览/执行/回滚对记忆的模糊修改计划。"
    admin_method: str = "fuzzy_modify_admin"
    parameters: dict = Field(default_factory=lambda: _correction_parameters(include_plan=True))

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixFactAdminTool(MemorixAdminToolBase):
    name: str = "memory_fact_admin"
    description: str = "管理当前记忆作用域内的结构化事实，支持显式修正、撤回和恢复；仅 AstrBot 管理员可用。"
    admin_method: str = "fact_admin"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "list", "create", "update", "retract", "restore"]},
                "claim_id": {"type": "string", "description": "get/update/retract/restore 必填。"},
                "scope_type": {"type": "string", "enum": ["person", "chat"]},
                "scope_id": {"type": "string", "description": "当前记忆库内的人物或聊天 ID；list/create 必填。"},
                "fact_key": {"type": "string"},
                "value_text": {"type": "string"},
                "polarity": {"type": "string", "enum": ["positive", "negative"]},
                "cardinality": {"type": "string", "enum": ["single", "set"]},
                "stability": {"type": "string", "enum": ["stable", "temporal", "uncertain"]},
                "authority": {"type": "string", "enum": ["manual", "direct_user", "imported", "summary_derived"]},
                "profile_section": {
                    "type": "string",
                    "enum": [
                        "identity_settings",
                        "relationship_settings",
                        "stable_facts",
                        "interaction_preferences",
                        "recent_interactions",
                        "uncertain_notes",
                    ],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "valid_from": {"type": ["number", "null"]},
                "valid_to": {"type": ["number", "null"]},
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["active", "conflicted", "superseded", "retracted"]},
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                "reason": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixBundleAdminTool(MemorixAdminToolBase):
    name: str = "memory_bundle_admin"
    description: str = "A_Memorix 知识包（.amembundle）导出、校验、安装、列表与安全卸载管理接口；仅 AstrBot 管理员可用。"
    admin_method: str = "bundle_admin"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["export", "inspect", "import", "list", "resolve_file", "uninstall"],
                    "description": "知识包管理操作。",
                },
                "path": {"type": "string", "description": "inspect/import 时的知识包文件名或受控路径。"},
                "file_name": {"type": "string", "description": "resolve_file 时的文件名。"},
                "installation_id": {"type": "string", "description": "uninstall 时的安装记录 ID。"},
                "content_level": {"type": "string", "enum": ["knowledge", "full"]},
                "mode": {"type": "string", "enum": ["merge", "restore"]},
                "scope_type": {"type": "string", "enum": ["global", "chat"]},
                "chat_id": {"type": "string"},
                "selector": {"type": "object"},
                "package": {"type": "object"},
                "include_images": {"type": "boolean"},
                "include_image_related": {"type": "boolean"},
                "include_vectors": {"type": "boolean"},
                "preview": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixCorrectionAdminTool(MemorixAdminToolBase):
    name: str = "memory_correction_admin"
    description: str = "A_Memorix v2.0 记忆修正管理接口（对齐 upstream memory_correction_admin 契约）；仅 AstrBot 管理员可用。"
    admin_method: str = "correction_admin"
    parameters: dict = Field(default_factory=lambda: _correction_parameters(include_plan=True))

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


@dataclass
class MemorixImageAdminTool(MemorixAdminToolBase):
    name: str = "memory_image_admin"
    description: str = "多模态图片记忆管理接口（状态探测、图片入库、观察标注、语义关联与相似检索）；仅 AstrBot 管理员可用。"
    admin_method: str = "image_admin"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "jobs",
                        "retry_failed_jobs",
                        "ingest",
                        "search",
                        "get",
                        "list",
                        "list_chats",
                        "delete_occurrence",
                        "observe",
                        "link",
                        "unlink",
                        "describe",
                        "process_jobs",
                    ],
                },
                "image_base64": {"type": "string", "description": "Base64 编码的静态图片内容（PNG/JPEG/WEBP/BMP）。"},
                "asset_id": {"type": "string"},
                "occurrence_id": {"type": "string"},
                "content_hash": {"type": "string"},
                "source_kind": {"type": "string"},
                "external_ref": {"type": "string"},
                "scope_type": {"type": "string"},
                "chat_id": {"type": "string"},
                "chat_ids": {"type": "array", "items": {"type": "string"}},
                "message_id": {"type": "string"},
                "user_statement": {"type": "string"},
                "text": {"type": "string"},
                "confirm_status": {"type": "string"},
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "link_kind": {"type": "string"},
                "link_id": {"type": "string"},
                "candidate_limit": {"type": "integer"},
                "similarity_threshold": {"type": "number"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "status": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        return await self._call_admin(context, **kwargs)


__all__ = [
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
    "MemorixProfileAdminTool",
    "MemorixRuntimeAdminTool",
    "MemorixSourceAdminTool",
    "MemorixTuningAdminTool",
    "MemorixV5AdminTool",
    "_admin_parameters",
]
