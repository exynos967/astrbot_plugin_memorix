"""
ScopeEngineManager: Multi-scope Dynamic Isolation & Lifecycle Management.
Ensures secure isolation of memory data directories across chat groups, users, and platforms.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .facade import MemoryEngineFacade


class ScopeEngineManager:
    """管理多作用域 MemoryEngineFacade 实例生命周期与目录安全隔离。"""

    def __init__(
        self,
        base_data_dir: Optional[Path | str] = None,
        config: Optional[Dict[str, Any]] = None,
        provider_bridge: Optional[Any] = None,
        *,
        runtime_manager: Optional[Any] = None,
        ingest_service: Optional[Any] = None,
        query_service: Optional[Any] = None,
        admin_service: Optional[Any] = None,
    ) -> None:
        self._explicit_base_data_dir = (
            Path(base_data_dir).resolve()
            if base_data_dir is not None and str(base_data_dir).strip()
            else None
        )
        if self._explicit_base_data_dir is not None:
            self._explicit_base_data_dir.mkdir(parents=True, exist_ok=True)
        self.config = dict(config or {})
        self.provider_bridge = provider_bridge
        self._engines: Dict[str, MemoryEngineFacade] = {}
        self._lock = asyncio.Lock()

        if runtime_manager is not None:
            self._runtime_manager = runtime_manager
            self._owns_runtime_manager = False
        else:
            from ..app_context import ScopeRuntimeManager

            self._runtime_manager = ScopeRuntimeManager(
                plugin_name="astrbot_plugin_memorix",
                plugin_config=self.config,
                base_data_dir=self._explicit_base_data_dir,
                provider_bridge=self.provider_bridge,
            )
            self._owns_runtime_manager = True

        if ingest_service is not None:
            self._ingest_service = ingest_service
        else:
            from ..services.ingest_service import IngestService

            self._ingest_service = IngestService(self._runtime_manager, self.config)

        if query_service is not None:
            self._query_service = query_service
        else:
            from ..services.query_service import QueryService

            self._query_service = QueryService(self._runtime_manager)

        self._owns_admin_service = admin_service is None
        if admin_service is not None:
            self._admin_service = admin_service
        else:
            from ..amemorix.services.feedback_service import FeedbackService
            from ..amemorix.services.fuzzy_modify_service import FuzzyModifyService
            from ..services.admin_service import AdminService

            feedback_svc = FeedbackService(
                self._runtime_manager,
                self.config,
                ingest_service=self._ingest_service,
            )
            fuzzy_svc = FuzzyModifyService(self._runtime_manager, self.config)
            self._admin_service = AdminService(
                self._runtime_manager,
                feedback_service=feedback_svc,
                fuzzy_modify_service=fuzzy_svc,
            )

    @property
    def base_data_dir(self) -> Path:
        resolver = getattr(self._runtime_manager, "_resolve_plugin_data_dir", None)
        if callable(resolver):
            return Path(resolver()).resolve()
        if self._explicit_base_data_dir is not None:
            return self._explicit_base_data_dir
        from ..app_context import resolve_plugin_data_dir

        return resolve_plugin_data_dir(plugin_config=self.config)

    @property
    def runtime_manager(self) -> Any:
        return self._runtime_manager

    @staticmethod
    def _normalize_scope_key(scope_key: str) -> str:
        raw = str(scope_key or "").strip()
        return raw or "default"

    @classmethod
    def sanitize_scope_key(cls, scope_key: str) -> str:
        """净化 scope_key，复用 ScopeRuntimeManager 统一安全规则。"""
        from ..app_context import ScopeRuntimeManager

        return ScopeRuntimeManager._sanitize_scope_dirname(cls._normalize_scope_key(scope_key))

    def get_scope_dir(self, scope_key: str) -> Path:
        """委托 ScopeRuntimeManager._scope_dir 计算作用域物理目录，确保所有配置模式下 100% 同源。"""
        canonical_key = self._normalize_scope_key(scope_key)
        return Path(self._runtime_manager._scope_dir(canonical_key)).resolve()

    async def get_engine(self, scope_key: str = "default") -> MemoryEngineFacade:
        """获取或惰性初始化指定 scope_key 对应的 MemoryEngineFacade。并发安全。"""
        canonical_key = self._normalize_scope_key(scope_key)

        if canonical_key in self._engines:
            return self._engines[canonical_key]

        async with self._lock:
            if canonical_key in self._engines:
                return self._engines[canonical_key]

            scope_dir = self.get_scope_dir(canonical_key)
            facade = MemoryEngineFacade(
                data_dir=scope_dir,
                config=self.config,
                provider_bridge=self.provider_bridge,
                scope_key=canonical_key,
                runtime_manager=self._runtime_manager,
                ingest_service=self._ingest_service,
                query_service=self._query_service,
                admin_service=self._admin_service,
                owns_runtime_manager=False,
            )
            await facade.initialize()
            self._engines[canonical_key] = facade
            return facade

    async def shutdown_engine(self, scope_key: str) -> None:
        """平稳关闭并卸载指定作用域的引擎。"""
        canonical_key = self._normalize_scope_key(scope_key)
        async with self._lock:
            facade = self._engines.pop(canonical_key, None)
        if facade:
            await facade.close()

    async def shutdown_all(self) -> None:
        """平稳关闭所有活跃作用域的记忆引擎，按顺序刷盘持久化并释放连接。"""
        async with self._lock:
            facades = list(self._engines.items())
            self._engines.clear()

        for canonical_key, facade in facades:
            try:
                await facade.close()
            except Exception as exc:
                import logging

                logging.getLogger("ScopeEngineManager").warning(
                    "关闭作用域 [%s] 发生异常: %s", canonical_key, exc
                )

        if self._owns_admin_service:
            try:
                await self._admin_service.close()
            except Exception:
                pass
        if self._owns_runtime_manager:
            await self._runtime_manager.close_all(mark_closing=False)

    async def close_all(self) -> None:
        """shutdown_all 的别名，满足不同生命周期契约。"""
        await self.shutdown_all()

    def list_active_scopes(self) -> List[str]:
        """返回当前所有处于活跃状态的作用域规范键列表。"""
        return list(self._engines.keys())

