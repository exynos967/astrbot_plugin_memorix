"""Summary wrapper service."""

from __future__ import annotations

from typing import Any, Dict

from ..amemorix.context import activate_context_shims
from ..amemorix.services.summary_service import SummaryService as BaseSummaryService
from ..app_context import ScopeRuntimeManager


class SummaryService:
    def __init__(self, runtime_manager: ScopeRuntimeManager):
        self.runtime_manager = runtime_manager

    async def maybe_enqueue_auto_summary(
        self,
        *,
        scope_key: str,
        session_id: str,
    ) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await runtime.task_manager.maybe_enqueue_auto_summary(session_id=session_id)

    async def summarize_session(
        self,
        *,
        scope_key: str,
        session_id: str,
        source: str,
        context_length: int = 50,
    ) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            service = BaseSummaryService(runtime.context)
            return await service.import_from_transcript(
                session_id=session_id,
                messages=[],
                source=source,
                context_length=context_length,
            )
