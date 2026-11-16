"""
Memorix Engine Package.
Exposes MemoryEngineFacade, ScopeEngineManager, and Host Shims.
"""

from .host_shims import install_host_shims, uninstall_host_shims

install_host_shims()

from .facade import IngestResult, MemoryEngineFacade, MemoryItem  # noqa: E402
from .scope_manager import ScopeEngineManager  # noqa: E402

__all__ = [
    "MemoryEngineFacade",
    "MemoryItem",
    "IngestResult",
    "ScopeEngineManager",
    "install_host_shims",
    "uninstall_host_shims",
]
