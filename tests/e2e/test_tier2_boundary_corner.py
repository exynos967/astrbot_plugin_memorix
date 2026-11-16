"""Tier 2 E2E Tests: Boundary, Corner Cases, and Adversarial Security.

Tests system robustness against:
- Empty, whitespace-only, and extreme length queries
- Special characters, SQL injection strings, XSS, control characters, and Unicode emojis
- Confused Deputy attack prevention (LLM scope spoofing)
- Unauthorized access rejection for administrative FunctionTools
- Command message filtering (prefix-based and At-mention stripped)
- Nonexistent IDs and malformed arguments
- Concurrent multi-scope data isolation
"""

from __future__ import annotations

import asyncio
import json

from astrbot_plugin_memorix.tests.mocks import (
    MockAstrMessageEvent,
    MockAt,
    MockPlain,
    create_test_plugin_instance,
    make_tool_call_context,
)


async def _drain_plugin_tasks(plugin, timeout: float = 2.0) -> None:
    pending = [t for t in plugin._background_tasks if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)


def test_tier2_empty_and_whitespace_queries(tmp_path):
    """Tier 2.1: Empty and whitespace queries to search_memory are handled gracefully."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(group_id="g_empty_test")
            tool_ctx = make_tool_call_context(event, mock_ctx)
            search_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "search_memory")

            for empty_query in ["", "   ", "\t\n\r", "   \n  "]:
                res = await search_tool.call(tool_ctx, query=empty_query, mode="search")
                # Must return without raising exception; output should not claim matching real hits
                res_str = str(res)
                assert isinstance(res_str, str)
                assert "500" not in res_str
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_extreme_length_and_special_characters(tmp_path):
    """Tier 2.2: Extreme string size and adversarial SQL/XSS/control characters."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(group_id="g_adversarial")
            tool_ctx = make_tool_call_context(event, mock_ctx)
            tools = {getattr(t, "name", ""): t for t in plugin._llm_tools}

            ingest_tool = tools["ingest_text"]
            search_tool = tools["search_memory"]

            # 1. Extreme length text (12,000 characters)
            huge_text = "重要安全警报：" + ("A" * 12000)
            res_huge = await ingest_tool.call(tool_ctx, text=huge_text, category="stress")
            assert "success" in str(res_huge).lower() or "true" in str(res_huge).lower()

            # 2. SQL injection and XSS characters
            sql_payload = "'; DROP TABLE paragraphs; DROP TABLE fact_claims; SELECT * FROM '1"
            xss_payload = "<script>alert('pwned')</script>\"'--&"
            emoji_payload = "👨‍👩‍👧‍👦 🚀 🌟 💥 \x01\x02\t\n测试控制字符"

            combined_payload = f"测试特殊符号：{sql_payload} | {xss_payload} | {emoji_payload}"
            res_inj = await ingest_tool.call(tool_ctx, text=combined_payload, category="security")
            assert "success" in str(res_inj).lower() or "true" in str(res_inj).lower()

            # 3. Search using SQL injection payload
            search_res = await search_tool.call(tool_ctx, query=sql_payload, mode="search")
            search_str = str(search_res)
            assert isinstance(search_str, str)

            # 4. Verify SQLite database tables are completely intact
            runtime = await plugin.runtime_manager.get_runtime(plugin._resolve_scope(event))
            tables = {
                row[0]
                for row in runtime.context.metadata_store._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "paragraphs" in tables
            assert "fact_claims" in tables
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_confused_deputy_scope_tampering(tmp_path):
    """Tier 2.3: Confused Deputy Defense: Tool must reject LLM-spoofed scope_key."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            # Legit event belongs to group_legit
            legit_event = MockAstrMessageEvent(group_id="group_legit")
            tool_ctx = make_tool_call_context(legit_event, mock_ctx)
            ingest_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "ingest_text")

            # Adversary passes spoofed scope_key
            res = await ingest_tool.call(
                tool_ctx,
                text="机密财务报表内容",
                category="finance",
                scope_key="aiocqhttp:group:malicious_spoofed_victim",
            )
            assert str(res)
            # The tool MUST ignore the spoofed scope_key and write to legit_event's scope
            expected_scope = plugin._resolve_scope(legit_event)
            assert "group_legit" in expected_scope

            # Verify target runtime contains the ingested data
            runtime_legit = await plugin.runtime_manager.get_runtime(expected_scope)
            rows = runtime_legit.context.metadata_store._conn.execute(
                "SELECT content FROM paragraphs"
            ).fetchall()
            assert any("机密财务报表内容" in r[0] for r in rows)

            # Verify spoofed scope was never accessed or created
            all_scopes = plugin.runtime_manager.list_scope_keys()
            assert "aiocqhttp:group:malicious_spoofed_victim" not in all_scopes
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_unauthorized_admin_tool_rejection(tmp_path):
    """Tier 2.4: Non-admin users are strictly rejected when invoking admin tools."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            # Regular user event
            member_event = MockAstrMessageEvent(
                sender_id="regular_user",
                group_id="g_chat",
                is_admin=False,
                role="member",
            )
            tool_ctx = make_tool_call_context(member_event, mock_ctx)
            admin_tools = [
                t for t in plugin._llm_tools if getattr(t, "name", "") in {"memory_fact_admin", "memory_source_admin"}
            ]

            for tool in admin_tools:
                res = await tool.call(tool_ctx, action="list")
                res_str = str(res)
                # Verify rejection payload returned
                assert "require AstrBot admin permission" in res_str or "success': False" in res_str or '"success": false' in res_str
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_command_message_skipping(tmp_path):
    """Tier 2.5: Command messages (/help, !reset, ！管理) are identified and skipped from ingestion."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            # 1. Direct slash command
            slash_cmd = MockAstrMessageEvent(message_str="/help", group_id="g_commands")
            assert plugin._is_command_message(slash_cmd.message_str) is True

            # 2. Exclamation mark command
            excl_cmd = MockAstrMessageEvent(message_str="!status", group_id="g_commands")
            assert plugin._is_command_message(excl_cmd.message_str) is True

            # 3. Chinese exclamation mark
            cn_excl_cmd = MockAstrMessageEvent(message_str="！记忆统计", group_id="g_commands")
            assert plugin._is_command_message(cn_excl_cmd.message_str) is True

            # 4. Message with leading @Bot mention and command
            at_cmd = MockAstrMessageEvent(
                message_str="@bot_999 /query_secret",
                group_id="g_commands",
                components=[
                    MockAt(qq="bot_999", name="Bot"),
                    MockPlain(" /query_secret"),
                ],
            )
            # Process through on_all_messages
            await plugin.on_all_messages(slash_cmd)
            await plugin.on_all_messages(excl_cmd)
            await plugin.on_all_messages(cn_excl_cmd)
            await plugin.on_all_messages(at_cmd)
            await _drain_plugin_tasks(plugin)

            # Assert no command text entered paragraphs store
            scope = plugin._resolve_scope(slash_cmd)
            runtime = await plugin.runtime_manager.get_runtime(scope)
            rows = runtime.context.metadata_store._conn.execute(
                "SELECT content FROM paragraphs"
            ).fetchall()
            assert len(rows) == 0, f"Expected 0 paragraphs, got {len(rows)}"
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_nonexistent_ids_and_malformed_arguments(tmp_path):
    """Tier 2.6: Operations on nonexistent IDs or invalid actions return clean error dicts."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            admin_event = MockAstrMessageEvent(is_admin=True, role="admin", group_id="g_errors")
            tool_ctx = make_tool_call_context(admin_event, mock_ctx)
            fact_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_fact_admin")

            # 1. Nonexistent claim_id
            get_res = await fact_tool.call(tool_ctx, action="get", claim_id="nonexistent-uuid-000")
            get_data = json.loads(str(get_res))
            assert get_data.get("success") is False
            assert "不存在" in get_data.get("error", "")

            # 2. Invalid action
            inv_res = await fact_tool.call(tool_ctx, action="destroy_all_universe")
            inv_data = json.loads(str(inv_res))
            assert inv_data.get("success") is False
            assert "不支持" in inv_data.get("error", "")
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier2_concurrent_scope_isolation(tmp_path):
    """Tier 2.7: Multi-scope isolation: Data in Group Alpha never leaks to Group Beta."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            # Setup Group Alpha and Group Beta
            event_a = MockAstrMessageEvent(group_id="group_alpha")
            event_b = MockAstrMessageEvent(group_id="group_beta")

            tool_ctx_a = make_tool_call_context(event_a, mock_ctx)
            tool_ctx_b = make_tool_call_context(event_b, mock_ctx)

            tools = {getattr(t, "name", ""): t for t in plugin._llm_tools}
            ingest_tool = tools["ingest_text"]
            search_tool = tools["search_memory"]

            # Ingest exclusive knowledge
            await ingest_tool.call(tool_ctx_a, text="阿尔法基地的最高通关密码是 ALPHA-SECRET-999")
            await ingest_tool.call(tool_ctx_b, text="贝塔前哨站的备用核电密钥是 BETA-CIPHER-888")

            # Search in Scope Alpha
            res_a = await search_tool.call(tool_ctx_a, query="通关密码 核电密钥", mode="search")
            res_a_str = str(res_a)
            assert "ALPHA-SECRET-999" in res_a_str
            assert "BETA-CIPHER-888" not in res_a_str

            # Search in Scope Beta
            res_b = await search_tool.call(tool_ctx_b, query="通关密码 核电密钥", mode="search")
            res_b_str = str(res_b)
            assert "BETA-CIPHER-888" in res_b_str
            assert "ALPHA-SECRET-999" not in res_b_str
        finally:
            await plugin.terminate()

    asyncio.run(_run())
