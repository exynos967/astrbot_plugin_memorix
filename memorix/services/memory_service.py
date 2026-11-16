"""Memory service wrapper."""

from __future__ import annotations

from typing import Any, Dict

from ..amemorix.context import activate_context_shims
from ..amemorix.services.memory_service import MemoryService as BaseMemoryService
from ..app_context import ScopeRuntimeManager


class MemoryService:
    def __init__(self, runtime_manager: ScopeRuntimeManager):
        self.runtime_manager = runtime_manager

    async def status(self, *, scope_key: str) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await BaseMemoryService(runtime.context).status()

    async def protect(self, *, scope_key: str, query_or_hash: str, hours: float = 24.0) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await BaseMemoryService(runtime.context).protect(query_or_hash=query_or_hash, hours=hours)

    async def reinforce(self, *, scope_key: str, query_or_hash: str) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await BaseMemoryService(runtime.context).reinforce(query_or_hash=query_or_hash)

    async def restore(self, *, scope_key: str, hash_value: str, restore_type: str = "relation") -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await BaseMemoryService(runtime.context).restore(hash_value=hash_value, restore_type=restore_type)

    async def freeze(self, *, scope_key: str, query_or_hash: str) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        with activate_context_shims(runtime.context):
            return await BaseMemoryService(runtime.context).freeze(query_or_hash=query_or_hash)
