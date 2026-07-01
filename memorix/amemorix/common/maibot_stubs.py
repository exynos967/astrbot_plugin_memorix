"""Phase 1 依赖剥离桩：宿主 ``src.*`` 中暂无对应插件 shim 的符号。

这些符号仅保留给 ``core/runtime/sdk_memory_kernel.py`` 这个未接入插件主线的
上游 SDK 运行时。插件实际运行路径已用 ``generate_text``、``MessageAPI`` 和
``ctx.get_config`` 替换宿主依赖。桩采用递归属性 + 可调用语义，确保即便被调用也
不会因属性缺失而崩溃，但返回值无业务意义——任何真实使用都应改成本地适配。
"""

from __future__ import annotations

from typing import Any


class _RecursiveStub:
    """递归桩：任意属性访问/调用返回自身，可迭代为空。"""

    __slots__ = ()

    def __getattr__(self, _name: str) -> "_RecursiveStub":
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> "_RecursiveStub":
        return self

    def __iter__(self):
        return iter(())


# 配置树桩：global_config.a_memorix.integration / global_config.bot.nickname 等
global_config = _RecursiveStub()

# LLM 服务桩：SDKMemoryKernel 反馈纠错旧路径仍引用 LLMServiceClient。
LLMServiceClient = _RecursiveStub()

# 消息/会话桩：message_api.get_messages_by_time_in_chat /
# chat_manager.get_existing_session_by_session_id
message_api = _RecursiveStub()
chat_manager = _RecursiveStub()
