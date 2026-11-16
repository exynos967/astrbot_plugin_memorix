"""聊天数据迁移的并发边界与取消安全线程执行辅助。

为运行时服务（feedback_correction / ingest episode rebuild）与宿主 WebUI 迁移流程提供统一的
会话级暂停/排空门控，兼容多事件循环测试场景与 host_shims 动态桥接。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Optional, Set, Tuple, TypeVar

from .async_work import cancellation_safe_to_thread

T = TypeVar("T")


async def settled_thread(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """取消协程时仍等事务线程结束，避免补偿与尚未提交的原操作竞跑。"""
    return await cancellation_safe_to_thread(operation, *args, **kwargs)


class ChatMigrationGate:
    """聊天流级别的在线迁移活动门控。"""

    def __init__(self) -> None:
        self.blocked: Set[str] = set()
        self.active: Dict[str, int] = {}
        self.changed = asyncio.Event()
        self._changed_loop: Optional[asyncio.AbstractEventLoop] = None
        self.owners: Dict[Tuple[Optional[asyncio.Task[Any]], str], int] = {}

    def _event_for_current_loop(self) -> asyncio.Event:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self.changed
        if self._changed_loop is not loop:
            self.changed = asyncio.Event()
            self._changed_loop = loop
        return self.changed

    def _notify(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and self._changed_loop in {None, loop}:
            self.changed.set()
        self.changed = asyncio.Event()
        self._changed_loop = loop

    @asynccontextmanager
    async def activity(self, session_id: str) -> AsyncIterator[None]:
        if not session_id and "" not in self.blocked:
            yield
            return
        owner = (asyncio.current_task(), session_id)
        if owner in self.owners:
            self.owners[owner] += 1
            try:
                yield
            finally:
                self.owners[owner] -= 1
            return
        while session_id in self.blocked:
            await self._event_for_current_loop().wait()
        self.owners[owner] = 1
        self.active[session_id] = self.active.get(session_id, 0) + 1
        try:
            yield
        finally:
            self.owners.pop(owner, None)
            remaining = self.active.get(session_id, 0) - 1
            if remaining > 0:
                self.active[session_id] = remaining
            else:
                self.active.pop(session_id, None)
            self._notify()

    async def pause(self, session_ids: Set[str]) -> None:
        if self.blocked.intersection(session_ids):
            raise ValueError("聊天流已有迁移任务，请先完成或恢复该任务")
        self.blocked.update(session_ids)
        try:
            async with asyncio.timeout(60):
                while any(self.active.get(key, 0) for key in session_ids):
                    await self._event_for_current_loop().wait()
        except BaseException:
            self.resume(session_ids)
            raise

    def resume(self, session_ids: Set[str]) -> None:
        self.blocked.difference_update(session_ids)
        self._notify()


chat_migration_gate = ChatMigrationGate()


def get_chat_migration_gate() -> Any:
    """优先使用宿主显式注入的真实门控（排除 _RecursiveStub），否则回退内置单例。"""
    try:
        from src.services import chat_migration_gate as host_mod

        candidate = getattr(host_mod, "chat_migration_gate", None)
        if candidate is not None and isinstance(getattr(candidate, "blocked", None), set):
            return candidate
    except Exception:
        pass
    return chat_migration_gate
