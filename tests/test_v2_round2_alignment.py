"""Round 2 alignment & architectural unification tests for A_memorix v2.0.0 / Schema 29."""

from __future__ import annotations

import ast
import asyncio
import json
import struct
import zlib
from enum import Enum
from pathlib import Path

import pytest
from astrbot_plugin_memorix.memorix import app_context as app_context_mod
from astrbot_plugin_memorix.memorix.engine.host_shims import config_shim, embedding_shim, llm_shim
from astrbot_plugin_memorix.memorix.services.person_fact_writeback_service import (
    PersonFactWritebackItem,
)
from astrbot_plugin_memorix.memorix.utils import message_formatting
from astrbot_plugin_memorix.tests.mocks.astrbot_mocks import (
    MockAstrMessageEvent,
    MockImage,
    MockLLMResponse,
    MockPlain,
    MockProviderRequest,
    create_test_plugin_instance,
)


def _make_valid_png_bytes(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (240, 140, 40)) -> bytes:
    """Construct a valid minimal 8-bit RGB PNG using pure stdlib."""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _make_animated_gif_bytes() -> bytes:
    """Construct a minimal 2-frame GIF89a image that ImageAssetStore rejects as animated."""
    header = b"GIF89a" + struct.pack("<HHBBB", 2, 2, 0x80, 0, 0) + (b"\x00\x00\x00\xff\xff\xff")
    gce = b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
    img_desc = b"\x2c" + struct.pack("<HHHHB", 0, 0, 2, 2, 0)
    lzw_data = b"\x02\x02\x4c\x01\x00"
    frame = gce + img_desc + lzw_data
    return header + frame + frame + b"\x3b"


async def _drain_plugin_background_tasks(plugin) -> None:
    for _ in range(5):
        pending = [t for t in list(getattr(plugin, "_background_tasks", set())) if not t.done()]
        if not pending:
            await asyncio.sleep(0.02)
            pending = [t for t in list(getattr(plugin, "_background_tasks", set())) if not t.done()]
            if not pending:
                break
        await asyncio.gather(*pending, return_exceptions=True)


def test_app_context_has_zero_private_astrbot_core_imports() -> None:
    """memorix/app_context.py must only rely on public astrbot.api.* interfaces."""
    source_path = Path(app_context_mod.__file__).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("astrbot.core"):
                    forbidden.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = str(node.module or "")
            if mod.startswith("astrbot.core"):
                forbidden.append(mod)
    assert forbidden == [], f"Private astrbot.core imports found in app_context.py: {forbidden}"


def test_base_data_dir_resolution_parity_across_plugin_and_runtime_manager(tmp_path: Path, monkeypatch) -> None:
    """MemorixPlugin, ScopeRuntimeManager, and ScopeEngineManager must resolve identical paths dynamically."""
    star_dir = tmp_path / "star_tools_dir" / "plugin_data" / "astrbot_plugin_memorix"
    ctx_dir = tmp_path / "context_private_dir"

    import astrbot.api.star as star_mod

    class _FakeStarTools:
        @staticmethod
        def get_data_dir(_plugin_name: str):
            return star_dir

    monkeypatch.setattr(star_mod, "StarTools", _FakeStarTools, raising=False)

    # Case 1: Both StarTools.get_data_dir and context._data_dir exist (no explicit storage.data_dir)
    plugin, mock_ctx = create_test_plugin_instance(tmp_path, custom_config={"storage": {"data_dir": ""}})
    mock_ctx._data_dir = ctx_dir
    try:
        resolved_plugin = plugin._resolve_base_data_dir()
        resolved_runtime = plugin.runtime_manager._resolve_plugin_data_dir()
        assert resolved_plugin == resolved_runtime == plugin.base_data_dir == star_dir.resolve()
        assert plugin.scope_manager.get_scope_dir("aiocqhttp:group:1") == plugin.runtime_manager._scope_dir(
            "aiocqhttp:group:1"
        )
    finally:
        asyncio.run(plugin.terminate())

    # Case 2: StarTools unavailable -> both dynamically resolve to (context._data_dir / "memorix_data") without manual reset
    monkeypatch.delattr(star_mod, "StarTools", raising=False)
    plugin2, mock_ctx2 = create_test_plugin_instance(tmp_path / "case2", custom_config={"storage": {"data_dir": ""}})
    mock_ctx2._data_dir = ctx_dir
    try:
        expected_ctx_dir = (ctx_dir / "memorix_data").resolve()
        assert (
            plugin2._resolve_base_data_dir()
            == plugin2.runtime_manager._resolve_plugin_data_dir()
            == plugin2.base_data_dir
            == expected_ctx_dir
        )
    finally:
        asyncio.run(plugin2.terminate())

    # Case 3: Explicit storage.data_dir -> ScopeEngineManager.get_scope_dir matches ScopeRuntimeManager._scope_dir
    explicit_dir = tmp_path / "explicit_storage_root"
    plugin3, _ = create_test_plugin_instance(
        tmp_path / "case3",
        custom_config={"storage": {"data_dir": str(explicit_dir)}},
    )
    try:
        assert plugin3.base_data_dir == explicit_dir.resolve()
        assert plugin3.scope_manager.get_scope_dir("aiocqhttp:group:99") == plugin3.runtime_manager._scope_dir(
            "aiocqhttp:group:99"
        )
    finally:
        asyncio.run(plugin3.terminate())


def test_unified_scope_engine_and_runtime_no_split_brain_and_survives_restart(tmp_path: Path) -> None:
    """ScopeEngineManager and ScopeRuntimeManager share exact stores in-memory and do not overwrite .npz on close."""

    async def _scenario() -> None:
        data_root = tmp_path / "unified_memorix_data"
        scope_key = "aiocqhttp:group:split_brain_check"
        cfg = {
            "storage": {"data_dir": str(data_root)},
            "ingest": {"memory_write_mode": "direct"},
        }

        plugin, _ = create_test_plugin_instance(tmp_path, custom_config=cfg)
        try:
            engine = await plugin.get_scope_engine(scope_key)
            runtime = await plugin.runtime_manager.get_runtime(scope_key)

            # Exact in-memory object identity (zero split-brain stores)
            assert engine.runtime is runtime
            assert engine.metadata_store is runtime.context.metadata_store
            assert engine.vector_store is runtime.context.vector_store
            assert engine.paragraph_vector_store is runtime.context.paragraph_vector_store
            assert engine.graph_store is runtime.context.graph_store

            # 1) Ingest via MemoryEngineFacade -> immediately searchable via plugin.query_service
            facade_res = await engine.ingest_message(
                sender_id="u_alpha",
                sender_name="林舰长",
                content="阿尔法星际空间站位于木卫二轨道，核心代号为欧罗巴七号。",
                role="user",
                chat_id="s_unified",
            )
            assert facade_res.success is True
            assert facade_res.hash != ""

            q_from_service = await plugin.query_service.search(
                scope_key=scope_key,
                query="欧罗巴七号空间站",
                top_k=5,
            )
            assert any("欧罗巴七号" in str(item.get("content", "")) for item in q_from_service.get("results", []))

            # 2) Ingest via plugin.ingest_service -> immediately searchable via MemoryEngineFacade.search
            svc_res = await plugin.ingest_service.ingest_text(
                scope_key=scope_key,
                external_id="ext_beta_1",
                source_type="manual_note",
                text="贝塔深海研究所位于马里亚纳海沟，主反应堆型号是海神九型。",
                chat_id="s_unified",
            )
            assert svc_res.get("success") is True

            q_from_facade = await engine.search(query="海神九型反应堆", limit=5)
            assert any("海神九型" in item.content for item in q_from_facade)
        finally:
            await plugin.terminate()

        # 3) Restart a fresh plugin over the exact same data_root: NEITHER memory may be lost or overwritten!
        plugin_restarted, _ = create_test_plugin_instance(tmp_path / "restart_ctx", custom_config=cfg)
        try:
            hit_alpha = await plugin_restarted.query_service.search(
                scope_key=scope_key,
                query="欧罗巴七号空间站",
                top_k=5,
            )
            hit_beta = await plugin_restarted.query_service.search(
                scope_key=scope_key,
                query="海神九型反应堆",
                top_k=5,
            )
            assert any("欧罗巴七号" in str(item.get("content", "")) for item in hit_alpha.get("results", []))
            assert any("海神九型" in str(item.get("content", "")) for item in hit_beta.get("results", []))
        finally:
            await plugin_restarted.terminate()

    asyncio.run(_scenario())


def test_host_shims_multi_scope_isolation_and_config_reload_callbacks(tmp_path: Path) -> None:
    """Concurrent multi-scope initialization must not clobber global storage.data_dir or provider bridge."""

    async def _scenario() -> None:
        data_root = tmp_path / "multi_scope_root"
        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(data_root)},
                "integration": {"enable_v2_extended_tools": True},
            },
        )
        reload_events: list[int] = []

        def _on_reload() -> None:
            reload_events.append(1)

        config_shim.config_manager.register_reload_callback(_on_reload)
        try:
            # global_config.a_memorix proxy compatibility
            assert bool(config_shim.global_config.a_memorix.integration.enable_v2_extended_tools) is True
            global_data_dir_before = str(config_shim.global_config.storage.data_dir)
            bridge_before = llm_shim.get_active_provider_bridge()
            assert bridge_before is plugin.provider_bridge

            # Initialize 4 distinct scopes concurrently
            scopes = [f"aiocqhttp:group:concurrent_{idx}" for idx in range(4)]
            runtimes = await asyncio.gather(*(plugin.runtime_manager.get_runtime(s) for s in scopes))
            assert len({rt.settings.data_dir for rt in runtimes}) == 4

            # Global plugin data_dir and active bridge must NOT be clobbered by per-scope dirs
            assert str(config_shim.global_config.storage.data_dir) == global_data_dir_before
            assert llm_shim.get_active_provider_bridge() is bridge_before

            # Coroutine-local ContextVar isolation check
            async def _read_scoped(val: str) -> str:
                with config_shim.use_scope_config({"storage": {"data_dir": val}}):
                    await asyncio.sleep(0.01)
                    return str(config_shim.global_config.storage.data_dir)

            r1, r2 = await asyncio.gather(_read_scoped("/scope/one"), _read_scoped("/scope/two"))
            assert (r1, r2) == ("/scope/one", "/scope/two")
            assert str(config_shim.global_config.storage.data_dir) == global_data_dir_before

            # Reload callback firing & unregistration
            config_shim.update_global_config({"scope": {"mode": "umo"}}, preserve_storage_data_dir=True)
            assert len(reload_events) == 1
            assert str(config_shim.global_config.storage.data_dir) == global_data_dir_before
            config_shim.config_manager.unregister_reload_callback(_on_reload)
            config_shim.update_global_config({"scope": {"mode": "group_global"}}, preserve_storage_data_dir=True)
            assert len(reload_events) == 1
        finally:
            config_shim.config_manager.unregister_reload_callback(_on_reload)
            await plugin.terminate()

        assert llm_shim.get_active_provider_bridge() is None

    asyncio.run(_scenario())


def test_person_fact_writeback_v2_deduplicator_and_dual_pool_persistence(tmp_path: Path) -> None:
    """PersonFactWritebackService deduplicates repeated facts across turns, promotes to stable, and persists dual pools."""

    async def _scenario() -> None:
        data_root = tmp_path / "pfw_dual_data"
        scope_key = "aiocqhttp:group:pfw_g1"
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(data_root)},
                "retrieval": {"vector_pools": {"mode": "dual"}},
            },
        )
        try:
            # Pre-create dual_ready.json in the resolved scope directory before first get_runtime
            scope_dir = plugin.runtime_manager._scope_dir(scope_key)
            vectors_dir = scope_dir / "vectors"
            (vectors_dir / "paragraph").mkdir(parents=True, exist_ok=True)
            (vectors_dir / "graph").mkdir(parents=True, exist_ok=True)
            (vectors_dir / "dual_ready.json").write_text(
                json.dumps({"status": "ready", "dimension": 1024}),
                encoding="utf-8",
            )

            runtime = await plugin.runtime_manager.get_runtime(scope_key)
            assert runtime.context._dual_vector_pools_enabled() is True

            def _responder(prompt: str | None, _contexts):
                text = str(prompt or "")
                if "严格输出 JSON 数组" in text:
                    return json.dumps(["该用户目前在杭州做后端工程师"], ensure_ascii=False)
                # Offline provider intentionally returns non-JSON for FactDeduplicator schema prompt
                # so deterministic fallback + multi-observation confirmation are both exercised!
                return "not_a_json_object"

            mock_ctx._default_provider.response_generator = _responder

            svc = plugin.person_fact_writeback_service
            item1 = PersonFactWritebackItem(
                scope_key=scope_key,
                session_id="pfw_g1",
                user_text="我现在在杭州做后端工程师",
                assistant_text="杭州的互联网氛围很不错！",
                user_id="u_hz_1",
                group_id="pfw_g1",
                platform="aiocqhttp",
                sender_name="阿泽",
                message_id="msg_turn_1",
                timestamp=1_770_000_100.0,
            )

            # Turn 1: creates 1 paragraph + 1 uncertain claim
            await svc._handle_item(item1)
            person_id = "aiocqhttp:u_hz_1"
            claims_after_t1 = runtime.context.metadata_store.list_fact_claims(
                scope_type="person",
                scope_id=person_id,
                statuses=["active"],
            )
            assert len(claims_after_t1) == 1
            assert claims_after_t1[0]["stability"] == "uncertain"
            assert claims_after_t1[0]["profile_section"] == "uncertain_notes"
            paras_after_t1 = runtime.context.metadata_store.get_paragraphs_by_source(f"person_fact:pfw_g1:{person_id}")
            assert len(paras_after_t1) == 1
            assert runtime.context.paragraph_vector_store.num_vectors == 1

            # Replay exact same message_id: idempotent via external_memory_ref receipt
            await svc._handle_item(item1)
            assert len(runtime.context.metadata_store.get_paragraphs_by_source(f"person_fact:pfw_g1:{person_id}")) == 1

            # Turn 2 (new independent message confirming same fact): reuses paragraph & promotes claim to stable!
            item2 = PersonFactWritebackItem(
                scope_key=scope_key,
                session_id="pfw_g1",
                user_text="对的，我一直在杭州做后端开发工作",
                assistant_text="记住了！",
                user_id="u_hz_1",
                group_id="pfw_g1",
                platform="aiocqhttp",
                sender_name="阿泽",
                message_id="msg_turn_2",
                timestamp=1_770_000_200.0,
            )
            await svc._handle_item(item2)

            claims_after_t2 = runtime.context.metadata_store.list_fact_claims(
                scope_type="person",
                scope_id=person_id,
                statuses=["active"],
            )
            assert len(claims_after_t2) == 1
            assert claims_after_t2[0]["claim_id"] == claims_after_t1[0]["claim_id"]
            assert claims_after_t2[0]["stability"] == "stable"
            assert claims_after_t2[0]["profile_section"] == "stable_facts"
            claim_evidences = runtime.context.metadata_store.get_fact_evidence(claims_after_t2[0]["claim_id"])
            source_evidences = [
                ev for ev in claim_evidences if ev.get("evidence_type") == "source"
            ]
            assert len(source_evidences) == 2

            # Zero duplicate paragraphs created!
            paras_after_t2 = runtime.context.metadata_store.get_paragraphs_by_source(f"person_fact:pfw_g1:{person_id}")
            assert len(paras_after_t2) == 1
            assert runtime.context.paragraph_vector_store.num_vectors == 1
        finally:
            await plugin.terminate()

        # Verify dual-pool paragraph_vector_store was saved to disk AND reloads on fresh startup!
        plugin2, _ = create_test_plugin_instance(
            tmp_path / "pfw_restart",
            custom_config={
                "storage": {"data_dir": str(data_root)},
                "retrieval": {"vector_pools": {"mode": "dual"}},
            },
        )
        try:
            runtime2 = await plugin2.runtime_manager.get_runtime(scope_key)
            assert runtime2.context._dual_vector_pools_enabled() is True
            assert runtime2.context.paragraph_vector_store.num_vectors == 1
        finally:
            await plugin2.terminate()

    asyncio.run(_scenario())


def test_chat_image_ingestion_single_pass_dedup_and_v2_image_memory(tmp_path: Path, monkeypatch) -> None:
    """Chat images are copied & captioned ONCE across on_all_messages + on_llm_response and stored in ImageMemoryRuntime."""

    class _ComponentType(str, Enum):
        Image = "Image"

    class _EnumTypedImage:
        """Mimics real AstrBot Image component where .type is ComponentType(str, Enum)."""

        def __init__(self, path: Path) -> None:
            self.type = _ComponentType.Image
            self.path = str(path)
            self.file = path.resolve().as_uri()
            self.url = ""

    async def _passthrough_compress(path: str) -> str:
        return path

    monkeypatch.setattr(message_formatting, "compress_image", _passthrough_compress)

    async def _scenario() -> None:
        png_file = tmp_path / "orange_cat.png"
        png_file.write_bytes(_make_valid_png_bytes())

        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "img_mem_data")},
                "image_memory": {"enabled": True, "require_embedding": False},
                "person_fact_writeback": {"enabled": False},
                "summarization": {"auto_import": {"enabled": False}},
                "ingest": {
                    "memory_write_mode": "transcript_only",
                    "image_caption": {
                        "enabled": True,
                        "prompt": "请描述这张聊天图片",
                        "max_count": 2,
                    },
                },
            },
        )
        vision_calls: list[dict] = []

        orig_text_chat = mock_ctx._default_provider.text_chat

        async def _tracked_text_chat(**kwargs):
            if kwargs.get("image_urls"):
                vision_calls.append(kwargs)
                return MockLLMResponse(completion_text="一只趴在窗台的橘猫")
            return await orig_text_chat(**kwargs)

        mock_ctx._default_provider.text_chat = _tracked_text_chat

        copy_calls = 0
        orig_capture = message_formatting.capture_event_images_to_safe_dir

        async def _counted_capture(ev):
            nonlocal copy_calls
            copy_calls += 1
            res = await orig_capture(ev)
            return res

        monkeypatch.setattr(
            "astrbot_plugin_memorix.memorix.message_ingestion.capture_event_images_to_safe_dir",
            _counted_capture,
        )

        try:
            ev = MockAstrMessageEvent(
                message_str="这是我家的小橘猫",
                sender_id="u_cat",
                sender_name="小橘主人",
                group_id="g_img",
                components=[MockPlain("这是我家的小橘猫"), _EnumTypedImage(png_file)],
            )
            ev.message_obj.message_id = "msg_cat_1"

            # Trigger BOTH hooks on the exact same event (real AstrBot pipeline order)
            await plugin.on_all_messages(ev)
            await plugin.on_llm_response(ev, MockLLMResponse(completion_text="好可爱的橘猫呀！"))
            await _drain_plugin_background_tasks(plugin)

            # 1) Single-pass guarantee: copied ONCE, VLM captioned ONCE, temp cache cleaned up
            assert copy_calls == 1
            assert len(vision_calls) == 1
            capture_state = getattr(ev, "_memorix_event_capture", None)
            assert capture_state is not None
            for cap_img in capture_state.captured_images:
                assert not Path(cap_img.safe_path).exists()

            # 2) ImageMemoryRuntime asset + occurrence + observations check
            scope_key = plugin._resolve_scope(ev)
            listed = await plugin.admin_service.image_admin(scope_key=scope_key, action="list", chat_id="g_img")
            assert listed["success"] is True
            items = listed.get("items", [])
            assert len(items) == 1
            asset_id = items[0]["asset_id"]

            detail = await plugin.admin_service.image_admin(scope_key=scope_key, action="get", asset_id=asset_id)
            assert detail["success"] is True
            assert len(detail["occurrences"]) == 1
            occ = detail["occurrences"][0]
            assert occ["external_ref"] == "chat:g_img:msg_cat_1:1"
            assert occ["chat_id"] == "g_img"
            assert occ["message_id"] == "msg_cat_1"
            assert occ["component_path"] == "1"

            obs_by_kind = {item["source_kind"]: item["text"] for item in detail["observations"]}
            assert obs_by_kind.get("user_statement") == "这是我家的小橘猫"
            assert obs_by_kind.get("model_description") == "[图片：一只趴在窗台的橘猫]"

            # 3) User transcript received the enriched caption
            runtime = await plugin.runtime_manager.get_runtime(scope_key)
            transcript = runtime.context.metadata_store.get_transcript_messages("g_img", limit=10)
            user_msgs = [m for m in transcript if m.get("role") == "user"]
            assert len(user_msgs) == 1
            assert "[图片：一只趴在窗台的橘猫]" in user_msgs[0]["content"]
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_chat_image_edge_cases_pure_image_animated_gif_command_and_filtered(tmp_path: Path) -> None:
    """Verify pure-image messages, animated GIF rejection safety, command skip, and chat filter skip."""

    async def _scenario() -> None:
        pure_png = tmp_path / "pure_static.png"
        pure_png.write_bytes(_make_valid_png_bytes(width=4, height=4, rgb=(20, 120, 220)))
        anim_gif = tmp_path / "animated.gif"
        anim_gif.write_bytes(_make_animated_gif_bytes())

        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "img_edge_data")},
                "image_memory": {"enabled": True, "require_embedding": False},
                "person_fact_writeback": {"enabled": False},
                "summarization": {"auto_import": {"enabled": False}},
                "filter": {
                    "enabled": True,
                    "mode": "blacklist",
                    "chats": ["group:g_blocked"],
                },
                "ingest": {
                    "memory_write_mode": "transcript_only",
                    "skip_placeholder_only": True,
                    "skip_empty_text": True,
                    "skip_command_messages": True,
                    "image_caption": {"enabled": False},
                },
            },
        )
        try:
            # Edge Case A: Pure image message (no text, caption disabled) -> stored in ImageMemoryRuntime, 0 transcript rows
            ev_pure = MockAstrMessageEvent(
                message_str="",
                sender_id="u_edge",
                group_id="g_allowed",
                components=[MockImage(path=str(pure_png))],
            )
            ev_pure.message_obj.message_id = "msg_pure_img"
            await plugin.on_all_messages(ev_pure)
            await _drain_plugin_background_tasks(plugin)

            scope_allowed = plugin._resolve_scope(ev_pure)
            listed = await plugin.admin_service.image_admin(scope_key=scope_allowed, action="list", chat_id="g_allowed")
            assert len(listed.get("items", [])) == 1
            rt_allowed = await plugin.runtime_manager.get_runtime(scope_allowed)
            assert rt_allowed.context.metadata_store.get_transcript_messages("g_allowed", limit=10) == []

            # Edge Case B: Animated GIF + text -> GIF rejected gracefully, text message STILL recorded in transcript!
            ev_gif = MockAstrMessageEvent(
                message_str="看这个动图",
                sender_id="u_edge",
                group_id="g_allowed",
                components=[MockPlain("看这个动图"), MockImage(path=str(anim_gif))],
            )
            ev_gif.message_obj.message_id = "msg_anim_gif"
            await plugin.on_all_messages(ev_gif)
            await _drain_plugin_background_tasks(plugin)

            listed_after_gif = await plugin.admin_service.image_admin(
                scope_key=scope_allowed, action="list", chat_id="g_allowed"
            )
            assert len(listed_after_gif.get("items", [])) == 1  # Still only pure_png
            msgs_after_gif = rt_allowed.context.metadata_store.get_transcript_messages("g_allowed", limit=10)
            assert len(msgs_after_gif) == 1
            assert "看这个动图" in msgs_after_gif[0]["content"]

            # Edge Case C: Command message with image -> skipped completely
            cmd_png = tmp_path / "cmd_static.png"
            cmd_png.write_bytes(_make_valid_png_bytes(width=6, height=6, rgb=(10, 200, 50)))
            ev_cmd = MockAstrMessageEvent(
                message_str="/help",
                sender_id="u_edge",
                group_id="g_allowed",
                components=[MockPlain("/help"), MockImage(path=str(cmd_png))],
            )
            ev_cmd.message_obj.message_id = "msg_cmd_img"
            await plugin.on_all_messages(ev_cmd)
            await _drain_plugin_background_tasks(plugin)

            listed_after_cmd = await plugin.admin_service.image_admin(
                scope_key=scope_allowed, action="list", chat_id="g_allowed"
            )
            assert len(listed_after_cmd.get("items", [])) == 1

            # Edge Case D: Blacklisted group with image -> skipped completely (zero image assets, zero transcript rows)
            ev_blocked = MockAstrMessageEvent(
                message_str="黑名单群图片",
                sender_id="u_edge",
                group_id="g_blocked",
                components=[MockPlain("黑名单群图片"), MockImage(path=str(cmd_png))],
            )
            ev_blocked.message_obj.message_id = "msg_blocked_img"
            await plugin.on_all_messages(ev_blocked)
            await _drain_plugin_background_tasks(plugin)

            scope_blocked = plugin._resolve_scope(ev_blocked)
            listed_blocked = await plugin.admin_service.image_admin(
                scope_key=scope_blocked, action="list", chat_id="g_blocked"
            )
            assert listed_blocked.get("items", []) == []
            rt_blocked = await plugin.runtime_manager.get_runtime(scope_blocked)
            assert rt_blocked.context.metadata_store.get_transcript_messages("g_blocked", limit=10) == []
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_person_fact_writeback_llm_paraphrase_reuse_and_temporal_separation(tmp_path: Path) -> None:
    """Verify LLM-backed FactDeduplicator reuses paraphrased stable facts while keeping distinct temporal observations separate."""

    async def _scenario() -> None:
        scope_key = "aiocqhttp:group:pfw_llm"
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "pfw_llm_data")}},
        )
        dedup_llm_calls = 0
        next_extracted_facts: list[str] = []

        def _responder(prompt: str | None, _contexts):
            nonlocal dedup_llm_calls
            text = str(prompt or "")
            if "严格输出 JSON 数组" in text:
                return json.dumps(next_extracted_facts, ensure_ascii=False)
            # FactDeduplicator LLM schema prompt
            dedup_llm_calls += 1
            return json.dumps(
                {
                    "decisions": [
                        {
                            "fact_id": "f1",
                            "action": "reuse",
                            "target_id": "c1",
                            "candidate_ids": [],
                        }
                    ],
                    "relation_support": {},
                },
                ensure_ascii=False,
            )

        mock_ctx._default_provider.response_generator = _responder

        try:
            svc = plugin.person_fact_writeback_service
            person_id = "aiocqhttp:u_coffee"

            # Turn 1 & Turn 2 establish a confirmed stable claim
            next_extracted_facts = ["喜欢手冲耶加雪菲咖啡"]
            await svc._handle_item(
                PersonFactWritebackItem(
                    scope_key=scope_key,
                    session_id="pfw_llm",
                    user_text="我平时最喜欢手冲耶加雪菲咖啡",
                    assistant_text="耶加雪菲花果香很棒",
                    user_id="u_coffee",
                    group_id="pfw_llm",
                    platform="aiocqhttp",
                    sender_name="林栖",
                    message_id="m_c1",
                    timestamp=1_770_010_100.0,
                )
            )
            await svc._handle_item(
                PersonFactWritebackItem(
                    scope_key=scope_key,
                    session_id="pfw_llm",
                    user_text="嗯，手冲耶加雪菲咖啡是我每天的爱好",
                    assistant_text="好品味",
                    user_id="u_coffee",
                    group_id="pfw_llm",
                    platform="aiocqhttp",
                    sender_name="林栖",
                    message_id="m_c2",
                    timestamp=1_770_010_200.0,
                )
            )

            # Turn 3: Paraphrased wording -> exercises real LLM FactDeduplicator.plan -> reuses c1 without creating new claim!
            next_extracted_facts = ["偏爱自己冲泡耶加雪菲咖啡豆"]
            await svc._handle_item(
                PersonFactWritebackItem(
                    scope_key=scope_key,
                    session_id="pfw_llm",
                    user_text="周末也在家自己冲耶加雪菲豆子喝",
                    assistant_text="很惬意",
                    user_id="u_coffee",
                    group_id="pfw_llm",
                    platform="aiocqhttp",
                    sender_name="林栖",
                    message_id="m_c3",
                    timestamp=1_770_010_300.0,
                )
            )
            assert dedup_llm_calls == 1
            rt = await plugin.runtime_manager.get_runtime(scope_key)
            claims = rt.context.metadata_store.list_fact_claims(
                scope_type="person",
                scope_id=person_id,
                statuses=["active"],
            )
            assert len(claims) == 1
            assert claims[0]["stability"] == "stable"
            evidences = [
                ev
                for ev in rt.context.metadata_store.get_fact_evidence(claims[0]["claim_id"])
                if ev.get("evidence_type") == "source"
            ]
            assert len(evidences) == 3

            # Temporal event check: offline fallback must NOT auto-promote / merge repeated temporal text across days
            mock_ctx._default_provider.response_generator = lambda p, _c: (
                json.dumps(["今天跑了五公里"], ensure_ascii=False)
                if "严格输出 JSON 数组" in str(p or "")
                else "offline_non_json"
            )
            for idx, ts in enumerate((1_770_020_100.0, 1_770_106_500.0), start=1):
                await svc._handle_item(
                    PersonFactWritebackItem(
                        scope_key=scope_key,
                        session_id="pfw_llm",
                        user_text="今天跑了五公里",
                        assistant_text="坚持运动真棒",
                        user_id="u_runner",
                        group_id="pfw_llm",
                        platform="aiocqhttp",
                        sender_name="跑者",
                        message_id=f"m_run_{idx}",
                        timestamp=ts,
                    )
                )
            runner_claims = rt.context.metadata_store.list_fact_claims(
                scope_type="person",
                scope_id="aiocqhttp:u_runner",
                statuses=["active"],
            )
            assert len(runner_claims) == 2
            assert all(c["stability"] == "uncertain" for c in runner_claims)
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_chat_image_memory_recall_injection_exact_hash_and_chat_isolation(tmp_path: Path) -> None:
    """Turn-1 chat image observation is recalled into 【图片记忆-内部参考】 on Turn-2 and isolated per chat_id when non-global."""

    async def _scenario() -> None:
        cat_png = tmp_path / "ahuang.png"
        cat_png.write_bytes(_make_valid_png_bytes(width=8, height=8, rgb=(245, 150, 50)))

        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "img_recall_data")},
                "scope": {"mode": "user_global"},
                "image_memory": {"enabled": True, "require_embedding": False, "auto_inject_enabled": True},
                "person_fact_writeback": {"enabled": False},
                "summarization": {"auto_import": {"enabled": False}},
            },
        )
        try:
            # Turn 1 in group_a: user introduces the cat photo
            ev1 = MockAstrMessageEvent(
                message_str="这是我家的小橘猫阿黄",
                sender_id="u_shared",
                sender_name="阿黄铲屎官",
                group_id="group_a",
                components=[MockPlain("这是我家的小橘猫阿黄"), MockImage(path=str(cat_png))],
            )
            ev1.message_obj.message_id = "m_img_recall_1"
            await plugin.on_all_messages(ev1)
            await _drain_plugin_background_tasks(plugin)

            # Turn 1 LLM request on the SAME message must NOT self-hit its own occurrence
            req1 = MockProviderRequest(prompt="这是我家的小橘猫阿黄")
            await plugin.on_llm_request(ev1, req1)
            injected_t1 = "\n".join(getattr(p, "text", "") for p in req1.extra_user_content_parts)
            assert "【图片记忆-内部参考】" not in injected_t1

            # Turn 2 in group_a: user resends the exact same photo with short question ("它叫什么", < min_query_chars=4)
            ev2 = MockAstrMessageEvent(
                message_str="它叫什么",
                sender_id="u_shared",
                sender_name="阿黄铲屎官",
                group_id="group_a",
                components=[MockPlain("它叫什么"), MockImage(path=str(cat_png))],
            )
            ev2.message_obj.message_id = "m_img_recall_2"
            req2 = MockProviderRequest(prompt="它叫什么")
            await plugin.on_llm_request(ev2, req2)
            injected_t2 = "\n".join(getattr(p, "text", "") for p in req2.extra_user_content_parts)
            assert "【图片记忆-内部参考】" in injected_t2
            assert "[同一图片]" in injected_t2
            assert "这是我家的小橘猫阿黄" in injected_t2

            # Cross-chat isolation check under user_global (same scope_key for u_shared, different chat_id="group_b")
            ev_other_chat = MockAstrMessageEvent(
                message_str="它叫什么",
                sender_id="u_shared",
                sender_name="阿黄铲屎官",
                group_id="group_b",
                components=[MockPlain("它叫什么"), MockImage(path=str(cat_png))],
            )
            ev_other_chat.message_obj.message_id = "m_img_recall_other"
            req_other = MockProviderRequest(prompt="它叫什么")
            await plugin.on_llm_request(ev_other_chat, req_other)
            injected_other = "\n".join(getattr(p, "text", "") for p in req_other.extra_user_content_parts)
            assert "【图片记忆-内部参考】" not in injected_other

            # Switching scope.mode to platform_global explicitly enables cross-chat image recall
            plugin.config["scope"]["mode"] = "platform_global"
            scope_u = "aiocqhttp:user:u_shared"
            search_global = await plugin.admin_service.image_admin(
                scope_key=scope_u,
                action="search",
                image_bytes=cat_png.read_bytes(),
                chat_id="group_b",
            )
            assert len(search_global.get("hits", [])) == 1
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_visual_similar_image_recall_via_embedding_shim_and_host_image_embedder(tmp_path: Path) -> None:
    """HostImageEmbedder cleanly handles unconfigured mode and performs end-to-end visual_similar recall when configured."""
    from astrbot_plugin_memorix.memorix.core.image.embedder import HostImageEmbedder

    def _extract_png_first_pixel_rgb(png_bytes: bytes) -> tuple[float, float, float]:
        offset = 8
        idat_parts: list[bytes] = []
        while offset + 8 <= len(png_bytes):
            length = struct.unpack(">I", png_bytes[offset : offset + 4])[0]
            tag = png_bytes[offset + 4 : offset + 8]
            chunk_data = png_bytes[offset + 8 : offset + 8 + length]
            if tag == b"IDAT":
                idat_parts.append(chunk_data)
            offset += 12 + length
        raw = zlib.decompress(b"".join(idat_parts))
        return float(raw[1]), float(raw[2]), float(raw[3])

    async def _scenario() -> None:
        # 1) Unconfigured HostImageEmbedder must degrade gracefully, never raise _RecursiveStub TypeError
        unconfigured = HostImageEmbedder()
        probe_status = await unconfigured.probe()
        assert probe_status["available"] is False
        assert probe_status["status"] == "unavailable"

        img_a = tmp_path / "sunset_cat_a.png"
        img_b = tmp_path / "sunset_cat_b.png"
        img_a.write_bytes(_make_valid_png_bytes(width=8, height=8, rgb=(240, 120, 40)))
        img_b.write_bytes(_make_valid_png_bytes(width=8, height=8, rgb=(236, 118, 44)))
        assert img_a.read_bytes() != img_b.read_bytes()

        def _fake_multimodal_embedder(image_bytes: bytes, mime_type: str = "image/png"):
            _ = mime_type
            r, g, b = _extract_png_first_pixel_rgb(image_bytes)
            return embedding_shim.ImageEmbeddingResult(
                embedding=[r, g, b, 16.0],
                model_name="mock-clip-v1",
                model_identifier="mock-clip-v1",
                api_provider="mock_vision",
            )

        with embedding_shim.use_image_embedder(_fake_multimodal_embedder):
            plugin, _ = create_test_plugin_instance(
                tmp_path / "vis_sim",
                custom_config={
                    "storage": {"data_dir": str(tmp_path / "vis_sim_data")},
                    "image_memory": {
                        "enabled": True,
                        "auto_inject_enabled": True,
                        "similarity_threshold": 0.80,
                    },
                    "person_fact_writeback": {"enabled": False},
                    "summarization": {"auto_import": {"enabled": False}},
                },
            )
            try:
                # Turn 1: ingest img_a; background job drain automatically indexes its vector!
                ev1 = MockAstrMessageEvent(
                    message_str="这是趴在沙发上的奶酪猫",
                    sender_id="u_vis",
                    sender_name="摄影师",
                    group_id="g_vis",
                    components=[MockPlain("这是趴在沙发上的奶酪猫"), MockImage(path=str(img_a))],
                )
                ev1.message_obj.message_id = "m_vis_1"
                await plugin.on_all_messages(ev1)
                await _drain_plugin_background_tasks(plugin)

                # Turn 2: send a DIFFERENT PNG file (img_b) -> recalls img_a via visual_similar!
                ev2 = MockAstrMessageEvent(
                    message_str="认得这只猫吗",
                    sender_id="u_vis",
                    sender_name="摄影师",
                    group_id="g_vis",
                    components=[MockPlain("认得这只猫吗"), MockImage(path=str(img_b))],
                )
                ev2.message_obj.message_id = "m_vis_2"
                req2 = MockProviderRequest(prompt="认得这只猫吗")
                await plugin.on_llm_request(ev2, req2)
                injected = "\n".join(getattr(p, "text", "") for p in req2.extra_user_content_parts)
                assert "【图片记忆-内部参考】" in injected
                assert "相似图片(相似度=" in injected
                assert "这是趴在沙发上的奶酪猫" in injected
            finally:
                await plugin.terminate()

    asyncio.run(_scenario())


def test_single_flight_event_capture_and_zero_temp_file_leak(tmp_path: Path, monkeypatch) -> None:
    """Default caption=False writes zero temp files; caption=True deduplicates concurrent captures and cleans up on cancel."""

    async def _scenario() -> None:
        png_path = tmp_path / "zero_leak.png"
        png_path.write_bytes(_make_valid_png_bytes(width=6, height=6, rgb=(80, 160, 240)))

        # Part A: Default include_image_caption=False -> zero temp files written to disk
        plugin_nocap, _ = create_test_plugin_instance(
            tmp_path / "nocap",
            custom_config={
                "storage": {"data_dir": str(tmp_path / "nocap_data")},
                "image_memory": {"enabled": True, "require_embedding": False},
                "ingest": {"image_caption": {"enabled": False}},
            },
        )
        try:
            ev_nocap = MockAstrMessageEvent(
                message_str="无转述图片",
                sender_id="u1",
                group_id="g1",
                components=[MockPlain("无转述图片"), MockImage(path=str(png_path))],
            )
            state_nocap = await plugin_nocap.message_ingestion.capture_event_once(ev_nocap)
            assert len(state_nocap.captured_images) == 1
            assert state_nocap.captured_images[0].safe_path == ""
            assert state_nocap.captured_images[0].image_bytes == png_path.read_bytes()
        finally:
            await plugin_nocap.terminate()

        # Part B: Concurrent capture_event_once calls with include_image_caption=True share single flight & clean up on cancel
        plugin_cap, _ = create_test_plugin_instance(
            tmp_path / "cap",
            custom_config={
                "storage": {"data_dir": str(tmp_path / "cap_data")},
                "image_memory": {"enabled": True, "require_embedding": False},
                "ingest": {"image_caption": {"enabled": True}},
            },
        )
        capture_invocations = 0
        orig_capture_fn = message_formatting.capture_event_images_to_safe_dir

        async def _slow_counted_capture(event, **kwargs):
            nonlocal capture_invocations
            capture_invocations += 1
            await asyncio.sleep(0.02)
            return await orig_capture_fn(event, **kwargs)

        monkeypatch.setattr(
            "astrbot_plugin_memorix.memorix.message_ingestion.capture_event_images_to_safe_dir",
            _slow_counted_capture,
        )
        try:
            ev_cap = MockAstrMessageEvent(
                message_str="并发图片采集",
                sender_id="u2",
                group_id="g2",
                components=[MockPlain("并发图片采集"), MockImage(path=str(png_path))],
            )
            s1, s2, s3 = await asyncio.gather(
                plugin_cap.message_ingestion.capture_event_once(ev_cap),
                plugin_cap.message_ingestion.capture_event_once(ev_cap),
                plugin_cap.message_ingestion.capture_event_once(ev_cap),
            )
            assert s1 is s2 is s3
            assert capture_invocations == 1
            safe_file = Path(s1.captured_images[0].safe_path)
            assert safe_file.is_file()

            # Simulate background task cancelled BEFORE _ensure_event_processed starts -> temp file must be removed!
            async def _blocked_process(_ev, _cap):
                await asyncio.sleep(10)
                return ""

            monkeypatch.setattr(plugin_cap.message_ingestion, "_ensure_event_processed", _blocked_process)
            bg_task = asyncio.create_task(plugin_cap.message_ingestion._record_message_background(ev_cap, s1))
            await asyncio.sleep(0.01)
            bg_task.cancel()
            await asyncio.gather(bg_task, return_exceptions=True)
            assert not safe_file.exists()
        finally:
            await plugin_cap.terminate()

    asyncio.run(_scenario())


def test_multi_scope_service_and_task_worker_shim_isolation(tmp_path: Path) -> None:
    """Concurrent multi-scope TaskManager/AdminService operations bind per-scope provider_bridge & config via ContextVar."""

    class _TaggedBridge:
        enabled = True
        chat_provider_id = "tagged_mock"

        def __init__(self, tag: str) -> None:
            self.tag = tag
            self.seen_data_dirs: list[str] = []

        async def generate_text(self, prompt: str, **_kwargs) -> str:
            await asyncio.sleep(0.01)
            self.seen_data_dirs.append(str(config_shim.global_config.storage.data_dir))
            if "json" in prompt.lower() or "{" in prompt:
                return json.dumps(
                    {
                        "summary": f"summary-from-{self.tag}",
                        "entities": [],
                        "relations": [],
                        "facts": [{"fact_id": "f1", "text": f"summary-from-{self.tag}"}],
                    },
                    ensure_ascii=False,
                )
            return f"summary-from-{self.tag}"

    async def _scenario() -> None:
        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "iso_root")}},
        )
        try:
            rt_a = await plugin.runtime_manager.get_runtime("aiocqhttp:group:iso_a")
            rt_b = await plugin.runtime_manager.get_runtime("aiocqhttp:group:iso_b")

            # Verify WebUI bridge shares AdminService's per-scope ImportTaskManager instead of spawning a duplicate manager
            webui_app_a = await plugin.webui_page_bridge._get_app("aiocqhttp:group:iso_a")
            admin_import_mgr_a = await plugin.admin_service._import_manager("aiocqhttp:group:iso_a", rt_a.context)
            assert webui_app_a.import_task_manager is admin_import_mgr_a
            assert webui_app_a.owns_import_task_manager is False

            bridge_a = _TaggedBridge("scope_A")
            bridge_b = _TaggedBridge("scope_B")
            rt_a.context.provider_bridge = bridge_a
            rt_b.context.provider_bridge = bridge_b

            # Clear process-global provider bridge to prove workers/services rely on ContextVar scope_shims, NOT global state!
            llm_shim.set_active_provider_bridge(None)

            await plugin.ingest_service.ingest_message(
                scope_key="aiocqhttp:group:iso_a",
                session_id="iso_a",
                role="user",
                content="我们在讨论量子计算架构设计",
                source="chat:aiocqhttp:iso_a",
                user_id="u_a",
                group_id="iso_a",
                platform="aiocqhttp",
                sender_name="Alice",
            )
            await plugin.ingest_service.ingest_message(
                scope_key="aiocqhttp:group:iso_b",
                session_id="iso_b",
                role="user",
                content="我们在讨论分布式存储共识协议",
                source="chat:aiocqhttp:iso_b",
                user_id="u_b",
                group_id="iso_b",
                platform="aiocqhttp",
                sender_name="Bob",
            )

            cfg_a, cfg_b, sum_a, sum_b = await asyncio.gather(
                plugin.admin_service.runtime_admin(scope_key="aiocqhttp:group:iso_a", action="get_config"),
                plugin.admin_service.runtime_admin(scope_key="aiocqhttp:group:iso_b", action="get_config"),
                plugin.summary_service.summarize_session(
                    scope_key="aiocqhttp:group:iso_a",
                    session_id="iso_a",
                    source="summary:iso_a",
                ),
                plugin.summary_service.summarize_session(
                    scope_key="aiocqhttp:group:iso_b",
                    session_id="iso_b",
                    source="summary:iso_b",
                ),
            )
            assert cfg_a["data_dir"] == str(rt_a.context.data_dir)
            assert cfg_b["data_dir"] == str(rt_b.context.data_dir)
            assert cfg_a["data_dir"] != cfg_b["data_dir"]
            assert sum_a.get("success") is True and sum_b.get("success") is True
            assert bridge_a.seen_data_dirs and all(d == str(rt_a.context.data_dir) for d in bridge_a.seen_data_dirs)
            assert bridge_b.seen_data_dirs and all(d == str(rt_b.context.data_dir) for d in bridge_b.seen_data_dirs)
            assert llm_shim.get_active_provider_bridge() is None
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_person_fact_writeback_non_durable_confirmation_rollback_safety_and_episode_bump(
    tmp_path: Path, monkeypatch
) -> None:
    """Non-durable-regex facts confirm cleanly without duplicate paragraphs, roll back atomically on commit error, and bump episode rebuild."""
    from astrbot_plugin_memorix.memorix.core.utils.fact_deduplication import FactDeduplicator

    async def _scenario() -> None:
        scope_key = "aiocqhttp:group:pfw_edge"
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "pfw_edge_data")}},
        )
        try:
            # Fact text intentionally lacks _DURABLE_FACT_RE keywords ("住在/来自/喜欢/擅长/在...工作")
            mock_ctx._default_provider.response_generator = lambda p, _c: (
                json.dumps(["小明是计算机系研究生"], ensure_ascii=False)
                if "严格输出 JSON 数组" in str(p or "")
                else "offline_non_json"
            )
            svc = plugin.person_fact_writeback_service
            rt = await plugin.runtime_manager.get_runtime(scope_key)
            person_id = "aiocqhttp:u_xm"
            source = f"person_fact:pfw_edge:{person_id}"

            # Turn 1 -> uncertain claim
            await svc._handle_item(
                PersonFactWritebackItem(
                    scope_key=scope_key,
                    session_id="pfw_edge",
                    user_text="我是计算机系研究生",
                    assistant_text="研究生科研辛苦啦",
                    user_id="u_xm",
                    group_id="pfw_edge",
                    platform="aiocqhttp",
                    sender_name="小明",
                    message_id="xm_t1",
                    timestamp=1_770_100_001.0,
                )
            )
            claims_t1 = rt.context.metadata_store.list_fact_claims(
                scope_type="person", scope_id=person_id, statuses=["active"]
            )
            assert len(claims_t1) == 1
            assert claims_t1[0]["stability"] == "uncertain"

            # Inject a transient failure inside FactDeduplicator.commit -> atomic SQLite transaction MUST roll back promotion!
            orig_commit = FactDeduplicator.commit

            def _boom_commit(*_a, **_kw):
                raise RuntimeError("transient dedup commit failure")

            monkeypatch.setattr(FactDeduplicator, "commit", _boom_commit)
            with pytest.raises(RuntimeError, match="transient dedup commit failure"):
                await svc._handle_item(
                    PersonFactWritebackItem(
                        scope_key=scope_key,
                        session_id="pfw_edge",
                        user_text="嗯对，我是计算机系研究生",
                        assistant_text="明白",
                        user_id="u_xm",
                        group_id="pfw_edge",
                        platform="aiocqhttp",
                        sender_name="小明",
                        message_id="xm_t2_fail",
                        timestamp=1_770_100_050.0,
                    )
                )
            claims_after_fail = rt.context.metadata_store.list_fact_claims(
                scope_type="person", scope_id=person_id, statuses=["active"]
            )
            assert len(claims_after_fail) == 1
            assert claims_after_fail[0]["stability"] == "uncertain"
            assert claims_after_fail[0]["profile_section"] == "uncertain_notes"

            # Restore commit and replay xm_t2_fail -> promotes to stable, reuses paragraph (count == 1), bumps episode rebuild!
            monkeypatch.setattr(FactDeduplicator, "commit", orig_commit)
            await svc._handle_item(
                PersonFactWritebackItem(
                    scope_key=scope_key,
                    session_id="pfw_edge",
                    user_text="嗯对，我是计算机系研究生",
                    assistant_text="明白",
                    user_id="u_xm",
                    group_id="pfw_edge",
                    platform="aiocqhttp",
                    sender_name="小明",
                    message_id="xm_t2_fail",
                    timestamp=1_770_100_050.0,
                )
            )
            claims_t2 = rt.context.metadata_store.list_fact_claims(
                scope_type="person", scope_id=person_id, statuses=["active"]
            )
            assert len(claims_t2) == 1
            assert claims_t2[0]["stability"] == "stable"
            assert claims_t2[0]["profile_section"] == "stable_facts"
            assert len(rt.context.metadata_store.get_paragraphs_by_source(source)) == 1
            rebuild_rows = rt.context.metadata_store.query(
                "SELECT desired_revision, reason FROM episode_rebuild_sources WHERE source = ?",
                (source,),
            )
            assert rebuild_rows and int(rebuild_rows[0]["desired_revision"] or 0) >= 2
            assert rebuild_rows[0]["reason"] == "dedup_person_fact_complete"
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_ingest_text_external_id_idempotency(tmp_path: Path) -> None:
    """IngestService.ingest_text deduplicates repeated external_id submissions via external_memory_refs."""

    async def _scenario() -> None:
        scope_key = "aiocqhttp:group:ext_idem"
        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "ext_idem_data")}},
        )
        try:
            res1 = await plugin.ingest_service.ingest_text(
                scope_key=scope_key,
                external_id="kb-doc-2026-001",
                source_type="manual_doc",
                chat_id="ext_idem",
                text="Memorix v2.0 支持基于 external_memory_refs 的幂等外部文档写入。",
                respect_filter=False,
            )
            assert res1["success"] is True
            assert len(res1["stored_ids"]) == 1
            assert res1["skipped_ids"] == []

            res2 = await plugin.ingest_service.ingest_text(
                scope_key=scope_key,
                external_id="kb-doc-2026-001",
                source_type="manual_doc",
                chat_id="ext_idem",
                text="Memorix v2.0 支持基于 external_memory_refs 的幂等外部文档写入。",
                respect_filter=False,
            )
            assert res2["success"] is True
            assert res2["stored_ids"] == []
            assert res2["skipped_ids"] == res1["stored_ids"]

            rt = await plugin.runtime_manager.get_runtime(scope_key)
            assert len(rt.context.metadata_store.get_paragraphs_by_source(res1["source"])) == 1
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())

