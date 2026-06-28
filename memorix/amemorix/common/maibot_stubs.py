"""Phase 1 依赖剥离桩：宿主 ``src.*`` 中暂无对应插件 shim 的符号。

这些符号在新版 vendored core 的 **尚未接入** 路径（episode/tuning/summary/web_import/
runtime.kernel）中被引用。Phase 1 目标仅为 ``core`` 可 import，这些路径不在运行链上；
Phase 3 移植时会用 ``llm_client.LLMClient`` / ``message_api.MessageAPI`` / 插件配置
真实替换桩。桩采用递归属性 + 可调用语义，确保即便被调用也不会因属性缺失而崩溃，
但返回值无业务意义——任何真实使用都是 Phase 3 重写范围。
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
config_manager = _RecursiveStub()

# LLM 服务桩：llm_api.generate / llm_api.LLMServiceRequest / llm_api.LLMServiceResult
llm_api = _RecursiveStub()
LLMServiceResult = _RecursiveStub()
LLMServiceClient = _RecursiveStub()

# 消息/会话桩：message_api.get_messages_by_time_in_chat /
# chat_manager.get_existing_session_by_session_id
message_api = _RecursiveStub()
chat_manager = _RecursiveStub()
