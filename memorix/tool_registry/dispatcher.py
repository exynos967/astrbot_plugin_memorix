"""Declarative Open/Closed AdminContractDispatcher aligned with A_memorix v2.0.0 admin_contracts.py."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from ..core.runtime.admin_contracts import ADMIN_COMPONENT_SPECS

AdminHandler = Callable[..., Awaitable[dict[str, Any]]]
ArgNormalizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AdminToolRegistration:
    """Declarative metadata + dispatch target for an admin FunctionTool."""

    name: str
    admin_method: str
    allowed_actions: frozenset[str]
    extended_v2: bool = False
    arg_normalizer: Optional[ArgNormalizer] = None
    custom_handler: Optional[AdminHandler] = None


def _normalize_graph_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(kwargs)
    if "source_node" in normalized and "source" not in normalized:
        normalized["source"] = normalized.pop("source_node")
    if "target_node" in normalized and "target" not in normalized:
        normalized["target"] = normalized.pop("target_node")
    return normalized


class AdminContractDispatcher:
    """Extensible registry mapping A_memorix admin component names to AdminService handlers.

    Follows the Open/Closed Principle (OCP): new admin contracts or custom extensions
    can be registered via ``register()`` without modifying existing tool or service classes.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, AdminToolRegistration] = {}

    def register(self, spec: AdminToolRegistration) -> None:
        token = str(spec.name or "").strip()
        if not token:
            raise ValueError("AdminToolRegistration.name cannot be empty")
        self._registrations[token] = spec

    def get(self, name: str) -> Optional[AdminToolRegistration]:
        return self._registrations.get(str(name or "").strip())

    def list_registrations(self, *, include_extended_v2: bool = True) -> list[AdminToolRegistration]:
        if include_extended_v2:
            return list(self._registrations.values())
        return [spec for spec in self._registrations.values() if not spec.extended_v2]

    def component_names(self, *, include_extended_v2: bool = True) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.list_registrations(include_extended_v2=include_extended_v2))

    async def dispatch(
        self,
        plugin: Any,
        *,
        scope_key: str,
        component_name: str,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        spec = self.get(component_name)
        if spec is None:
            raise ValueError(f"Unsupported Memorix admin component: {component_name}")
        normalized_action = str(action or "").strip().lower()
        if not normalized_action:
            return {"success": False, "error": "action is required", "scope": scope_key}
        if spec.allowed_actions and normalized_action not in spec.allowed_actions:
            return {
                "success": False,
                "error": f"不支持的 {spec.name} action: {normalized_action}",
                "scope": scope_key,
                "action": normalized_action,
            }

        clean_kwargs = {
            str(k): v
            for k, v in dict(payload or {}).items()
            if str(k) not in {"scope_key", "action"}
        }
        if spec.arg_normalizer is not None:
            clean_kwargs = spec.arg_normalizer(clean_kwargs)

        if spec.custom_handler is not None:
            result = await spec.custom_handler(
                plugin,
                scope_key=scope_key,
                action=normalized_action,
                **clean_kwargs,
            )
        else:
            admin_service = getattr(plugin, "admin_service", None)
            handler = getattr(admin_service, spec.admin_method, None)
            if not callable(handler):
                raise RuntimeError(f"AdminService does not expose method: {spec.admin_method}")
            result = await handler(scope_key=scope_key, action=normalized_action, **clean_kwargs)

        if isinstance(result, dict):
            result.setdefault("scope", scope_key)
            result.setdefault("action", normalized_action)
        return result


def _upstream_actions(name: str, *astrbot_aliases: str) -> frozenset[str]:
    upstream = ADMIN_COMPONENT_SPECS[name].actions if name in ADMIN_COMPONENT_SPECS else frozenset()
    return frozenset({*upstream, *astrbot_aliases})


def build_default_admin_dispatcher() -> AdminContractDispatcher:
    dispatcher = AdminContractDispatcher()
    # Core 12 AstrBot admin tools (preserving backward-compatible order and action aliases)
    dispatcher.register(
        AdminToolRegistration(
            name="memory_graph_admin",
            admin_method="graph_admin",
            allowed_actions=_upstream_actions("memory_graph_admin"),
            arg_normalizer=_normalize_graph_args,
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_source_admin",
            admin_method="source_admin",
            allowed_actions=_upstream_actions("memory_source_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_episode_admin",
            admin_method="episode_admin",
            allowed_actions=_upstream_actions(
                "memory_episode_admin",
                "process_pending",
                "discard_migration_rebuilds",
            ),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_profile_admin",
            admin_method="profile_admin",
            allowed_actions=_upstream_actions("memory_profile_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_fact_admin",
            admin_method="fact_admin",
            allowed_actions=_upstream_actions("memory_fact_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_runtime_admin",
            admin_method="runtime_admin",
            allowed_actions=_upstream_actions("memory_runtime_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_import_admin",
            admin_method="import_admin",
            allowed_actions=_upstream_actions(
                "memory_import_admin",
                "settings",
                "path_aliases",
                "chunks",
                "resolve",
            ),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_tuning_admin",
            admin_method="tuning_admin",
            allowed_actions=_upstream_actions(
                "memory_tuning_admin",
                "settings",
                "profile",
                "apply",
                "rollback",
                "export_toml",
                "export",
                "create",
                "list",
                "get",
                "rounds",
                "apply_best_profile",
                "report",
            ),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_feedback_admin",
            admin_method="feedback_admin",
            allowed_actions=_upstream_actions(
                "memory_feedback_admin",
                "logs",
                "list_logs",
                "action_logs",
                "reconcile",
                "trigger_reconcile",
            ),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_v5_admin",
            admin_method="v5_admin",
            allowed_actions=_upstream_actions("memory_v5_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_delete_admin",
            admin_method="delete_admin",
            allowed_actions=_upstream_actions("memory_delete_admin"),
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_fuzzy_modify_admin",
            admin_method="fuzzy_modify_admin",
            allowed_actions=_upstream_actions("memory_fuzzy_modify_admin", "reconcile"),
        )
    )
    # Extended v2.0.0 admin contracts (remaining ADMIN_COMPONENT_SPECS + multimodal image memory)
    dispatcher.register(
        AdminToolRegistration(
            name="memory_bundle_admin",
            admin_method="bundle_admin",
            allowed_actions=_upstream_actions("memory_bundle_admin"),
            extended_v2=True,
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_correction_admin",
            admin_method="correction_admin",
            allowed_actions=_upstream_actions("memory_correction_admin", "reconcile"),
            extended_v2=True,
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_image_admin",
            admin_method="image_admin",
            allowed_actions=_upstream_actions(
                "memory_image_admin",
                "process_jobs",
                "claim_description_compensations",
                "complete_description_compensation",
            ),
            extended_v2=True,
        )
    )
    chat_migration_actions = _upstream_actions(
        "memory_chat_migration",
        "list_chats",
        "chats",
        "snapshot",
        "preview",
        "list",
        "list_jobs",
        "get",
        "get_job",
        "apply",
        "execute",
        "recover",
        "restore",
        "rollback",
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_chat_migration",
            admin_method="chat_migration_admin",
            allowed_actions=chat_migration_actions,
            extended_v2=True,
        )
    )
    dispatcher.register(
        AdminToolRegistration(
            name="memory_chat_migration_admin",
            admin_method="chat_migration_admin",
            allowed_actions=chat_migration_actions,
            extended_v2=True,
        )
    )
    return dispatcher


DEFAULT_ADMIN_DISPATCHER = build_default_admin_dispatcher()

__all__ = [
    "AdminContractDispatcher",
    "AdminToolRegistration",
    "DEFAULT_ADMIN_DISPATCHER",
    "build_default_admin_dispatcher",
]
