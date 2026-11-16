"""
Async Work Host Shim.
Bridges src.common.async_work to cancellation-safe background thread & task helpers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")

TASK_STOP_TIMEOUT_SECONDS = 30.0


async def run_sync_work(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在后台线程池中调度执行耗时同步工作，严格避免阻塞主事件循环。"""
    return await asyncio.to_thread(func, *args, **kwargs)


async def cancellation_safe_to_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """跨重复取消信号护航后台线程完成写入。"""
    from ...core.utils.async_work import cancellation_safe_to_thread as _safe_to_thread

    return await _safe_to_thread(func, *args, **kwargs)


async def cancel_and_wait_for_tasks(tasks: Iterable[asyncio.Task[Any] | None], *, operation: str) -> None:
    """有界取消并等待后台任务退出。"""
    from ...core.utils.async_work import cancel_and_wait_for_tasks as _cancel_and_wait

    await _cancel_and_wait(tasks, operation=operation)
