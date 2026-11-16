"""Tier 1 E2E Tests: Feature Coverage and Core Public Contracts.

Tests coverage of individual features via public plugin and tool interfaces:
- Plugin lifecycle (initialize and terminate)
- Memory ingestion and search FunctionTools
- Memory statistics FunctionTool
- Person profile extraction FunctionTool
- Structured Fact Ledger administration (create, get, retract, restore, list)
- Knowledge sources administration
- Memory feedback administration
- Multimodal message ingestion handling
"""

from __future__ import annotations

import asyncio
import json

from astrbot_plugin_memorix.tests.mocks import (
    MockAstrMessageEvent,
    MockImage,
    MockPlain,
    create_test_plugin_instance,
    make_tool_call_context,
)


async def _drain_plugin_tasks(plugin, timeout: float = 2.0) -> None:
    """Helper to cleanly wait for any spawned background tasks."""
    pending = [t for t in plugin._background_tasks if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)


def test_tier1_plugin_lifecycle_initialize_and_terminate(tmp_path):
    """Tier 1.1: Plugin lifecycle contract: initialize registers tools & APIs; terminate cleanly unregisters."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)

        # 1. Initialize
        await plugin.initialize()

        # Verify LLM tools registered in AstrBot context
        tool_manager = mock_ctx.get_llm_tool_manager()
        registered_tool_names = [getattr(t, "name", "") for t in tool_manager.func_list]
        assert "search_memory" in registered_tool_names
        assert "ingest_text" in registered_tool_names
        assert "memory_stats" in registered_tool_names
        assert "memory_fact_admin" in registered_tool_names

        # Verify WebUI bridge registered web APIs
        registered_routes = [item["route"] for item in mock_ctx.registered_web_apis]
        assert any("/astrbot_plugin_memorix/webui" in r for r in registered_routes)

        # 2. Terminate
        await plugin.terminate()

        # Verify LLM tools removed
        post_terminate_tools = [getattr(t, "name", "") for t in tool_manager.func_list]
        assert "search_memory" not in post_terminate_tools
        assert "memory_fact_admin" not in post_terminate_tools

    asyncio.run(_run())


def test_tier1_ingest_and_search_tools(tmp_path):
    """Tier 1.2: CRUD operations via public FunctionTools: ingest_text -> search_memory."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(
                message_str="Recording memory",
                sender_id="user_alice",
                sender_name="Alice",
                group_id="group_music",
            )
            tool_ctx = make_tool_call_context(event, mock_ctx)
            tools = {getattr(t, "name", ""): t for t in plugin._llm_tools}

            # 1. Ingest text
            ingest_tool = tools["ingest_text"]
            fact_text = "爱丽丝是巴黎著名的首席长笛演奏家，热爱巴赫的乐曲。"
            ingest_res = await ingest_tool.call(tool_ctx, text=fact_text, category="music_career")
            assert "success" in str(ingest_res).lower() or "true" in str(ingest_res).lower()

            # 2. Search memory
            search_tool = tools["search_memory"]
            search_res = await search_tool.call(tool_ctx, query="长笛演奏家 巴赫", mode="search", limit=3)
            search_text = str(search_res)
            assert "爱丽丝" in search_text or "长笛" in search_text
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_memory_stats_tool(tmp_path):
    """Tier 1.3: memory_stats tool returns valid scope stats."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(group_id="group_stats_test")
            tool_ctx = make_tool_call_context(event, mock_ctx)
            stats_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_stats")

            res = await stats_tool.call(tool_ctx)
            res_str = str(res)
            # Stats payload should report scope or paragraphs count
            assert "scope" in res_str or "paragraph" in res_str or "success" in res_str
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_person_profile_tool(tmp_path):
    """Tier 1.4: get_person_profile returns candidate profile structure."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(
                message_str="Hello",
                sender_id="u_bob",
                sender_name="Bob",
                group_id="group_bob",
            )
            tool_ctx = make_tool_call_context(event, mock_ctx)
            profile_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "get_person_profile")

            res = await profile_tool.call(tool_ctx, person_name="Bob")
            assert res is not None
            assert "Bob" in str(res) or "success" in str(res) or "chat_id" in str(res)
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_fact_ledger_admin_lifecycle(tmp_path):
    """Tier 1.5: memory_fact_admin: create -> get -> retract -> restore -> list."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            admin_event = MockAstrMessageEvent(
                sender_id="admin_01",
                sender_name="Admin",
                group_id="group_facts",
                is_admin=True,
                role="admin",
            )
            tool_ctx = make_tool_call_context(admin_event, mock_ctx)
            fact_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_fact_admin")

            # 1. Create Fact Claim
            create_res = await fact_tool.call(
                tool_ctx,
                action="create",
                scope_type="person",
                scope_id="p_david",
                fact_key="specialty",
                value_text="量子物理学家",
            )
            create_data = json.loads(str(create_res))
            assert create_data.get("success") is True
            claim_id = create_data.get("claim", {}).get("claim_id")
            assert claim_id, "Expected valid claim_id in creation response"

            # 2. Get Fact Claim
            get_res = await fact_tool.call(tool_ctx, action="get", claim_id=claim_id)
            get_data = json.loads(str(get_res))
            assert get_data.get("success") is True
            claim = get_data.get("claim", {})
            assert claim.get("fact_key") == "specialty"
            assert claim.get("value_text") == "量子物理学家"
            assert claim.get("status") == "active"

            # 3. Retract Fact Claim
            retract_res = await fact_tool.call(
                tool_ctx,
                action="retract",
                claim_id=claim_id,
                reason="用户更正事实",
            )
            retract_data = json.loads(str(retract_res))
            assert retract_data.get("success") is True
            assert retract_data.get("claim", {}).get("status") == "retracted"

            # 4. Restore Fact Claim
            restore_res = await fact_tool.call(
                tool_ctx,
                action="restore",
                claim_id=claim_id,
                reason="撤回误操作恢复",
            )
            restore_data = json.loads(str(restore_res))
            assert restore_data.get("success") is True
            assert restore_data.get("claim", {}).get("status") == "active"

            # 5. List Claims
            list_res = await fact_tool.call(
                tool_ctx,
                action="list",
                scope_type="person",
                scope_id="p_david",
            )
            list_data = json.loads(str(list_res))
            assert list_data.get("success") is True
            claims = list_data.get("items", [])
            assert any(c.get("claim_id") == claim_id for c in claims)
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_knowledge_source_admin(tmp_path):
    """Tier 1.6: memory_source_admin list operation succeeds."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            admin_event = MockAstrMessageEvent(is_admin=True, role="admin", group_id="g_sources")
            tool_ctx = make_tool_call_context(admin_event, mock_ctx)
            source_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_source_admin")

            res = await source_tool.call(tool_ctx, action="list")
            res_data = json.loads(str(res))
            assert res_data.get("success") is True
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_feedback_admin(tmp_path):
    """Tier 1.7: memory_feedback_admin list/queue inspection."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            admin_event = MockAstrMessageEvent(is_admin=True, role="admin", group_id="g_feedback")
            tool_ctx = make_tool_call_context(admin_event, mock_ctx)
            feedback_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_feedback_admin")

            res = await feedback_tool.call(tool_ctx, action="list")
            res_data = json.loads(str(res))
            assert "success" in res_data
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier1_multimodal_image_message_ingestion(tmp_path):
    """Tier 1.8: Ingest message with image component and text without crash."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(
                message_str="看这只小猫 [图片]",
                sender_id="u_cat_lover",
                sender_name="CatLover",
                group_id="g_cats",
                components=[
                    MockPlain("看这只小猫 "),
                    MockImage(url="http://example.org/kitten.jpg"),
                ],
            )
            await plugin.on_all_messages(event)
            await _drain_plugin_tasks(plugin)

            # Ingestion completes smoothly
            assert True
        finally:
            await plugin.terminate()

    asyncio.run(_run())
