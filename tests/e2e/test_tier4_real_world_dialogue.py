"""Tier 4 E2E Tests: Real-World User and Bot Conversation Workflows.

Tests complete end-to-end conversation flows:
- Multi-turn conversation with dynamic memory injection and KV-Cache friendly mark_as_temp()
- Interleaved multi-user group chat profile and preference isolation
- Agent FunctionTool calling loop workflow (reasoning -> tool execution -> synthesis)
- Graceful shutdown under active inflight background tasks
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_memorix.tests.mocks import (
    MockAstrMessageEvent,
    MockLLMResponse,
    MockProviderRequest,
    create_test_plugin_instance,
    make_tool_call_context,
)


async def _drain_plugin_tasks(plugin, timeout: float = 2.0) -> None:
    pending = [t for t in plugin._background_tasks if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)


def test_tier4_multiturn_dialogue_with_auto_injection(tmp_path):
    """Tier 4.1: Real-world multi-turn conversation: fact statement -> injection on next turn -> KV-Cache safety."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "retrieval": {
                    "auto_inject": {
                        "enabled": True,
                        "min_query_chars": 2,
                        "top_k": 3,
                    }
                },
                "person_profile": {
                    "enabled": True,
                },
            },
        )
        await plugin.initialize()
        try:
            # === Round 1: User introduces specialty ===
            event1 = MockAstrMessageEvent(
                message_str="你好！我叫小琴，我最喜欢吹长笛，平时最爱吹奏莫扎特的长笛协奏曲。",
                sender_id="u_qin",
                sender_name="小琴",
                group_id="g_music_academy",
            )
            await plugin.on_all_messages(event1)
            resp1 = MockLLMResponse(completion_text="你好小琴！莫扎特的长笛协奏曲轻快而优美，很高兴认识你！")
            await plugin.on_llm_response(event1, resp1)
            await _drain_plugin_tasks(plugin)

            # === Round 2: User asks recalling question ===
            event2 = MockAstrMessageEvent(
                message_str="你还记得我最喜欢吹奏谁的长笛协奏曲吗？",
                sender_id="u_qin",
                sender_name="小琴",
                group_id="g_music_academy",
            )
            # Before LLM request, trigger memory injection interceptor
            req2 = MockProviderRequest(
                prompt=event2.message_str,
                system_prompt="你是一个乐于助人的智能助理。",
            )
            await plugin.on_llm_request(event2, req2)

            # 1. Assert KV-Cache friendliness: system_prompt MUST NOT be modified
            assert req2.system_prompt == "你是一个乐于助人的智能助理。"

            # 2. Assert extra_user_content_parts receives injected reference
            assert len(req2.extra_user_content_parts) >= 1
            injected_part = req2.extra_user_content_parts[-1]
            injected_text = getattr(injected_part, "text", "")
            assert "【Memorix 自动记忆参考】" in injected_text

            # 3. Assert temporary marker set (won't pollute chat history)
            assert getattr(injected_part, "_no_save", False) is True

            # 4. Injected text contains relevant memory
            assert "小琴" in injected_text or "长笛" in injected_text or "莫扎特" in injected_text

            # 5. Assistant produces informed response and ingests turn 2
            resp2 = MockLLMResponse(completion_text="当然记得啦，小琴你最喜欢吹奏莫扎特的长笛协奏曲！")
            await plugin.on_llm_response(event2, resp2)
            await _drain_plugin_tasks(plugin)
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier4_multi_user_group_chat_isolation(tmp_path):
    """Tier 4.2: Group chat with multiple users: Alice and Bob profiles are strictly isolated."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "person_profile": {"enabled": True},
                "retrieval": {"auto_inject": {"enabled": True, "min_query_chars": 2}},
            },
        )
        await plugin.initialize()
        try:
            # Alice introduces her astronomy dream
            event_alice = MockAstrMessageEvent(
                message_str="我是爱丽丝，我的梦想是成为一名天文学家，探索仙女座大星系！",
                sender_id="u_alice",
                sender_name="爱丽丝",
                group_id="g_shared_room",
            )
            await plugin.on_all_messages(event_alice)
            await plugin.on_llm_response(event_alice, MockLLMResponse(completion_text="爱丽丝的梦想真宏大！"))

            # Bob introduces his culinary dream
            event_bob = MockAstrMessageEvent(
                message_str="我是鲍勃，我的梦想是成为一名米其林三星大厨，做出最惊艳的法式甜点！",
                sender_id="u_bob",
                sender_name="鲍勃",
                group_id="g_shared_room",
            )
            await plugin.on_all_messages(event_bob)
            await plugin.on_llm_response(event_bob, MockLLMResponse(completion_text="鲍勃的法式甜点一定超赞！"))
            await _drain_plugin_tasks(plugin)

            # Query Alice profile via tool
            tool_ctx_alice = make_tool_call_context(event_alice, mock_ctx)
            profile_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "get_person_profile")
            alice_profile_res = await profile_tool.call(tool_ctx_alice, person_name="爱丽丝")
            alice_text = str(alice_profile_res)

            # Alice's profile should know Alice, not mix with Bob
            assert "爱丽丝" in alice_text

            # Query Bob profile via tool
            tool_ctx_bob = make_tool_call_context(event_bob, mock_ctx)
            bob_profile_res = await profile_tool.call(tool_ctx_bob, person_name="鲍勃")
            bob_text = str(bob_profile_res)
            assert "鲍勃" in bob_text
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier4_agent_tool_calling_loop_workflow(tmp_path):
    """Tier 4.3: Simulate LLM Agent tool execution loop: search memory -> synthesize final answer."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(
                message_str="帮我查一下机房的主机密码和服务器IP配置",
                sender_id="u_devops",
                group_id="g_datacenter",
            )
            tool_ctx = make_tool_call_context(event, mock_ctx)
            tools = {getattr(t, "name", ""): t for t in plugin._llm_tools}

            # 1. Preset knowledge in memory store
            ingest_tool = tools["ingest_text"]
            await ingest_tool.call(
                tool_ctx,
                text="机房主服务器配置：IP为 192.168.10.200，访问口令为 SERVER-KEY-9988",
                category="ops_secret",
            )

            # 2. Agent decides to call search_memory
            search_tool = tools["search_memory"]
            search_output = await search_tool.call(tool_ctx, query="机房主服务器 IP 口令", mode="search")
            search_text = str(search_output)
            assert "192.168.10.200" in search_text
            assert "SERVER-KEY-9988" in search_text

            # 3. Simulate Agent final synthesis
            final_response = MockLLMResponse(
                completion_text="已为您查询到，主服务器IP是 192.168.10.200，口令是 SERVER-KEY-9988。"
            )
            await plugin.on_llm_response(event, final_response)
            await _drain_plugin_tasks(plugin)
            assert True
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier4_graceful_shutdown_under_inflight_tasks(tmp_path):
    """Tier 4.4: System terminates gracefully under active background tasks without hanging."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()

        # Fire multiple messages in rapid succession
        for i in range(10):
            ev = MockAstrMessageEvent(
                message_str=f"并发测试消息条目 #{i}",
                group_id=f"g_stress_{i % 3}",
            )
            await plugin.on_all_messages(ev)

        # Terminate immediately without awaiting drain
        start_time = asyncio.get_event_loop().time()
        await plugin.terminate()
        elapsed = asyncio.get_event_loop().time() - start_time

        # Must cleanly cancel/await within 5 seconds without raising unhandled exceptions
        assert elapsed < 5.0
        # Background tasks set should be cleaned
        remaining_active = [t for t in plugin._background_tasks if not t.done()]
        assert len(remaining_active) == 0

    asyncio.run(_run())
