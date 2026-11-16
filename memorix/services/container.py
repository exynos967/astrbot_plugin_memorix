"""DomainServiceContainer: Encapsulates lifecycle and dependencies of Memorix domain services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..amemorix.services.feedback_service import FeedbackService
from ..amemorix.services.fuzzy_modify_service import FuzzyModifyService
from ..app_context import ScopeRuntimeManager
from .admin_service import AdminService
from .ingest_service import IngestService
from .memory_service import MemoryService
from .person_fact_writeback_service import PersonFactWritebackService
from .profile_service import ProfileService
from .query_service import QueryService
from .summary_service import SummaryService


class DomainServiceContainer:
    """集中管理与协调 Memorix 领域服务，对宿主入口屏蔽装配细节 (SRP & OCP)。"""

    def __init__(
        self,
        *,
        config: Dict[str, Any],
        context: Any,
        engine_manager: Optional[Any] = None,
        runtime_manager: Optional[ScopeRuntimeManager] = None,
        base_data_dir: Optional[Path | str] = None,
        provider_bridge: Optional[Any] = None,
    ) -> None:
        self.config = dict(config or {})
        self.context = context
        self.engine_manager = engine_manager

        if runtime_manager is not None:
            self.runtime_manager = runtime_manager
        else:
            self.runtime_manager = ScopeRuntimeManager(
                plugin_name="astrbot_plugin_memorix",
                plugin_config=self.config,
                astrbot_context=context,
                base_data_dir=base_data_dir,
                provider_bridge=provider_bridge,
            )

        # 组装领域服务
        self.ingest_service = IngestService(self.runtime_manager, self.config)
        self.query_service = QueryService(self.runtime_manager)
        self.memory_service = MemoryService(self.runtime_manager)
        self.profile_service = ProfileService(self.runtime_manager)
        self.summary_service = SummaryService(self.runtime_manager)
        self.person_fact_writeback_service = PersonFactWritebackService(self.runtime_manager, self.config)
        self.feedback_service = FeedbackService(
            self.runtime_manager,
            self.config,
            ingest_service=self.ingest_service,
        )
        self.fuzzy_modify_service = FuzzyModifyService(self.runtime_manager, self.config)
        self.admin_service = AdminService(
            self.runtime_manager,
            feedback_service=self.feedback_service,
            fuzzy_modify_service=self.fuzzy_modify_service,
        )

    async def start(self) -> None:
        """启动需要后台轮询或自愈任务的领域服务。"""
        await self.person_fact_writeback_service.start()
        await self.feedback_service.start_background_loops()

    async def close(self) -> None:
        """按序优雅关闭所有领域服务。"""
        try:
            await self.person_fact_writeback_service.close()
        except Exception:
            pass
        try:
            await self.feedback_service.stop_background_loops()
        except Exception:
            pass
        try:
            await self.admin_service.close()
        except Exception:
            pass
        try:
            await self.runtime_manager.close_all()
        except Exception:
            pass
