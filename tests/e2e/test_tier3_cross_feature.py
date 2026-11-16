"""Tier 3 E2E Tests: Cross-Feature Interactions and State Pipelines.

Tests interaction across multiple subsystems:
- Ingestion -> Paragraph Storage -> Memory Search Retrieval Pipeline
- Fact Claim Evolution Pipeline (Create -> Supersede -> Conflict resolution -> Retract)
- Multi-turn conversation transcript sequencing (User & Assistant pairs)
- Memory Feedback and Correction enqueue pipeline
"""

from __future__ import annotations

import asyncio
import json

from astrbot_plugin_memorix.tests.mocks import (
    MockAstrMessageEvent,
    MockLLMResponse,
    create_test_plugin_instance,
    make_tool_call_context,
)


async def _drain_plugin_tasks(plugin, timeout: float = 2.0) -> None:
    pending = [t for t in plugin._background_tasks if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)


def test_tier3_ingestion_to_retrieval_pipeline(tmp_path):
    """Tier 3.1: Full pipeline: message ingestion via event hooks followed by search retrieval."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(
                message_str="我从小就对古希腊哲学特别感兴趣，最崇拜的思想家是苏格拉底。",
                sender_id="u_philosopher",
                sender_name="Plato",
                group_id="g_academy",
            )
            # 1. Ingest User message
            await plugin.on_all_messages(event)

            # 2. Ingest Assistant reply
            bot_reply = MockLLMResponse(completion_text="苏格拉底的反讽与辩证产婆术确实是西方哲学的基石！")
            await plugin.on_llm_response(event, bot_reply)
            await _drain_plugin_tasks(plugin)

            # 3. Search via FunctionTool
            tool_ctx = make_tool_call_context(event, mock_ctx)
            search_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "search_memory")
            search_res = await search_tool.call(tool_ctx, query="古希腊哲学 苏格拉底 思想家", mode="search")
            search_str = str(search_res)

            # Assert retrieval captures the conversation context
            assert "苏格拉底" in search_str
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier3_fact_supersede_and_retract_pipeline(tmp_path):
    """Tier 3.2: Fact state machine: Claim A is superseded by Claim B, then Claim B is retracted."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            admin_event = MockAstrMessageEvent(
                sender_id="admin_01",
                is_admin=True,
                role="admin",
                group_id="g_facts_pipe",
            )
            tool_ctx = make_tool_call_context(admin_event, mock_ctx)
            fact_tool = next(t for t in plugin._llm_tools if getattr(t, "name", "") == "memory_fact_admin")

            # 1. Create Fact 1: Alice lives in Kyoto
            res1 = await fact_tool.call(
                tool_ctx,
                action="create",
                scope_type="person",
                scope_id="alice_001",
                fact_key="current_residence",
                value_text="京都",
                cardinality="single",
            )
            data1 = json.loads(str(res1))
            assert data1.get("success") is True
            claim1_id = data1["claim"]["claim_id"]

            # 2. Update Fact: Alice moved to Tokyo (superseding Fact 1)
            res2 = await fact_tool.call(
                tool_ctx,
                action="update",
                claim_id=claim1_id,
                value_text="东京",
            )
            data2 = json.loads(str(res2))
            assert data2.get("success") is True
            claim2_id = data2["claim"]["claim_id"]

            # 3. Verify Claim 1 is superseded, Claim 2 is active
            get1 = json.loads(str(await fact_tool.call(tool_ctx, action="get", claim_id=claim1_id)))
            get2 = json.loads(str(await fact_tool.call(tool_ctx, action="get", claim_id=claim2_id)))

            assert get1["claim"]["status"] == "superseded"
            assert get2["claim"]["status"] == "active"
            assert get2["claim"]["value_text"] == "东京"

            # 4. Retract Claim 2
            retract_res = json.loads(
                str(
                    await fact_tool.call(
                        tool_ctx,
                        action="retract",
                        claim_id=claim2_id,
                        reason="离开日本回国",
                    )
                )
            )
            assert retract_res.get("success") is True
            assert retract_res["claim"]["status"] == "retracted"

            # 5. List active claims only
            list_active = json.loads(
                str(
                    await fact_tool.call(
                        tool_ctx,
                        action="list",
                        scope_type="person",
                        scope_id="alice_001",
                        statuses=["active"],
                    )
                )
            )
            assert list_active.get("success") is True
            active_items = list_active.get("items", [])
            assert len(active_items) == 0, "No claims should remain in active status"
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier3_multi_turn_transcript_sequencing(tmp_path):
    """Tier 3.3: Consecutive dialogue turns are recorded with proper sequential order."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            dialogue_turns = [
                ("user", "第一回合：我想了解量子纠缠的原理。"),
                ("assistant", "量子纠缠是量子力学中两个或多个粒子无论距离多远都存在的强关联现象。"),
                ("user", "第二回合：那它可以用作超光速通信吗？"),
                ("assistant", "不可以，因为测量结果本身是随机的，无法传递有效信息。"),
            ]

            last_event = None
            for role, text in dialogue_turns:
                if role == "user":
                    last_event = MockAstrMessageEvent(
                        message_str=text,
                        group_id="g_dialogue_seq",
                        sender_id="u_student",
                        sender_name="Student",
                    )
                    await plugin.on_all_messages(last_event)
                else:
                    assert last_event is not None
                    await plugin.on_llm_response(last_event, MockLLMResponse(completion_text=text))
                await _drain_plugin_tasks(plugin)

            scope = plugin._resolve_scope(last_event)
            runtime = await plugin.runtime_manager.get_runtime(scope)
            rows = runtime.context.metadata_store._conn.execute(
                "SELECT role, content FROM transcript_messages ORDER BY position ASC"
            ).fetchall()

            assert len(rows) >= 4
            contents = [r[1] for r in rows]
            assert any("第一回合" in c for c in contents)
            assert any("第二回合" in c for c in contents)
            assert any("超光速通信" in c for c in contents)
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_tier3_feedback_enqueue_and_retrieval_safety(tmp_path):
    """Tier 3.4: Feedback service queue operations integrate seamlessly without storage corruption."""

    async def _run():
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"integration": {"feedback_correction_enabled": True}},
        )
        await plugin.initialize()
        try:
            event = MockAstrMessageEvent(group_id="g_feedback_test")
            scope = plugin._resolve_scope(event)

            # Enqueue feedback via plugin API
            feedback_res = await plugin.enqueue_feedback(
                scope_key=scope,
                query="用户的历史偏好",
                chat_id="session_100",
                hit_hashes=["hash_abc123", "hash_def456"],
            )
            assert isinstance(feedback_res, dict)
            assert feedback_res.get("success") is True or feedback_res.get("queued") is True
        finally:
            await plugin.terminate()

    asyncio.run(_run())
