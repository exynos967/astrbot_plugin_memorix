"""Milestone 3 & 4 Verification Suite:
- Declarative Open/Closed AdminContractDispatcher & 14+1 v2.0.0 Admin Contracts
- Knowledge Bundles (.amembundle) export -> inspect -> cross-scope import -> list -> uninstall
- Multimodal Image Memory (PNG ingest, observe, link/unlink, list/get, delete_occurrence, invalid base64)
- Memory Correction Admin (plan -> unconfirmed guard -> confirmed execute -> rollback)
- Identity Canonicalization (observe_identity / resolve_identity)
- Embedded WebUI ASGI endpoints (/api/facts, /api/images/status, /v1/bundles, /v1/images, /v1/admin/*)
- AstrBot Specification & Zero Top-Level astrbot.core.* AST Audit
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_memorix.memorix.core.runtime.admin_contracts import ADMIN_COMPONENT_SPECS
from astrbot_plugin_memorix.memorix.tool_registry import (
    DEFAULT_ADMIN_DISPATCHER,
    AdminContractDispatcher,
    AdminToolRegistration,
    build_all_v2_memorix_tools,
    build_memorix_tools,
)
from astrbot_plugin_memorix.tests.mocks.astrbot_mocks import (
    MockAstrMessageEvent,
    create_test_plugin_instance,
    make_tool_call_context,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _make_png_base64(color: tuple[int, int, int] = (40, 120, 220), size: tuple[int, int] = (16, 16)) -> str:
    width, height = int(size[0]), int(size[1])
    row = b"\x00" + bytes((color[0] & 0xFF, color[1] & 0xFF, color[2] & 0xFF)) * width
    compressed = zlib.compress(row * height)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")
    return base64.b64encode(png_bytes).decode("ascii")


def _parse_tool_json(raw: Any) -> dict[str, Any]:
    assert isinstance(raw, str), f"Expected string tool output, got {type(raw)}"
    data = json.loads(raw)
    assert isinstance(data, dict)
    return data


def _collect_unguarded_astrbot_core_imports(py_path: Path) -> list[str]:
    """Find any top-level/unconditional ``astrbot.core.*`` imports outside TYPE_CHECKING or try/except."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    violations: list[str] = []

    def _is_type_checking_guard(test_node: ast.expr) -> bool:
        if isinstance(test_node, ast.Name) and test_node.id == "TYPE_CHECKING":
            return True
        if isinstance(test_node, ast.Attribute) and test_node.attr == "TYPE_CHECKING":
            return True
        return False

    def _walk(node: ast.AST, *, guarded: bool = False) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking_guard(child.test):
                for body_stmt in child.body:
                    _walk(body_stmt, guarded=True)
                for else_stmt in child.orelse:
                    _walk(else_stmt, guarded=guarded)
                continue
            if isinstance(child, ast.Try):
                for body_stmt in child.body:
                    _walk(body_stmt, guarded=True)
                for handler in child.handlers:
                    _walk(handler, guarded=True)
                for else_stmt in child.orelse:
                    _walk(else_stmt, guarded=True)
                for final_stmt in child.finalbody:
                    _walk(final_stmt, guarded=guarded)
                continue
            if not guarded:
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        if alias.name == "astrbot.core" or alias.name.startswith("astrbot.core."):
                            violations.append(f"{py_path.relative_to(PLUGIN_ROOT)}:{child.lineno} import {alias.name}")
                elif isinstance(child, ast.ImportFrom):
                    mod = child.module or ""
                    if mod == "astrbot.core" or mod.startswith("astrbot.core."):
                        violations.append(f"{py_path.relative_to(PLUGIN_ROOT)}:{child.lineno} from {mod} import ...")
            _walk(child, guarded=guarded)

    _walk(tree, guarded=False)
    return violations


def test_astrbot_metadata_and_conf_schema_v2_alignment() -> None:
    schema_path = PLUGIN_ROOT / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "image_memory" in schema
    assert schema["image_memory"]["items"]["enabled"]["default"] is True
    assert schema["image_memory"]["items"]["require_embedding"]["default"] is True
    assert schema["retrieval"]["items"]["vector_pools"]["items"]["mode"]["default"] == "dual"
    assert schema["integration"]["items"]["enable_v2_extended_tools"]["default"] is False

    meta_path = PLUGIN_ROOT / "metadata.yaml"
    meta: dict[str, str] = {}
    for raw_line in meta_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or raw_line.startswith(" ") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip("\"'")
    assert meta["name"] == "astrbot_plugin_memorix"
    assert meta["version"] == "v2.0.0"
    assert "A_Memorix v2.0" in meta["desc"]


def test_zero_unguarded_astrbot_core_imports_across_plugin() -> None:
    targets = [PLUGIN_ROOT / "main.py", *sorted((PLUGIN_ROOT / "memorix").rglob("*.py"))]
    all_violations: list[str] = []
    for py_file in targets:
        all_violations.extend(_collect_unguarded_astrbot_core_imports(py_file))
    assert not all_violations, f"Found unguarded astrbot.core.* imports: {all_violations}"


def test_admin_contract_dispatcher_covers_all_14_upstream_specs_and_ocp() -> None:
    registered_names = set(DEFAULT_ADMIN_DISPATCHER.component_names(include_extended_v2=True))
    expected_upstream_names = set(ADMIN_COMPONENT_SPECS.keys())
    assert expected_upstream_names.issubset(registered_names)
    assert "memory_image_admin" in registered_names

    for comp_name, upstream_spec in ADMIN_COMPONENT_SPECS.items():
        reg = DEFAULT_ADMIN_DISPATCHER.get(comp_name)
        assert reg is not None, f"Missing registration for {comp_name}"
        missing = set(upstream_spec.actions) - set(reg.allowed_actions)
        assert not missing, f"{comp_name} missing upstream actions: {missing}"

    # Verify Open/Closed Principle (OCP) custom contract extension without modifying AdminService
    custom_dispatcher = AdminContractDispatcher()

    async def _echo_handler(_plugin: Any, *, scope_key: str, action: str, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "custom_echo": True, "scope": scope_key, "action": action, "payload": kwargs}

    custom_dispatcher.register(
        AdminToolRegistration(
            name="memory_custom_extension_admin",
            admin_method="unused",
            allowed_actions=frozenset({"ping"}),
            custom_handler=_echo_handler,
        )
    )
    res = asyncio.run(
        custom_dispatcher.dispatch(
            object(),
            scope_key="aiocqhttp:group:777",
            component_name="memory_custom_extension_admin",
            action="ping",
            payload={"scope_key": "spoofed:scope", "hello": "world"},
        )
    )
    assert res["success"] is True
    assert res["custom_echo"] is True
    assert res["scope"] == "aiocqhttp:group:777"
    assert res["payload"] == {"hello": "world"}

    with pytest.raises(ValueError, match="Unsupported Memorix admin component"):
        asyncio.run(
            custom_dispatcher.dispatch(
                object(),
                scope_key="aiocqhttp:group:777",
                component_name="nonexistent_admin",
                action="ping",
            )
        )


def test_extended_v2_tool_builder_toggle_and_rbac_spoofing_guard(tmp_path: Path) -> None:
    async def _run() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"integration": {"enable_v2_extended_tools": True}},
        )
        await plugin.initialize()
        try:
            tool_names = [t.name for t in plugin._llm_tools]
            assert len(tool_names) == 21
            assert tool_names[-3:] == [
                "memory_bundle_admin",
                "memory_correction_admin",
                "memory_image_admin",
            ]
            assert len(build_memorix_tools(plugin, include_extended_v2_tools=False)) == 18
            assert len(build_all_v2_memorix_tools(plugin)) == 21

            bundle_tool = next(t for t in plugin._llm_tools if t.name == "memory_bundle_admin")
            image_tool = next(t for t in plugin._llm_tools if t.name == "memory_image_admin")

            # 1. Non-admin member MUST be rejected
            member_event = MockAstrMessageEvent(group_id="grp_rbac", sender_id="member_1", role="member")
            member_ctx = make_tool_call_context(member_event, mock_ctx)
            denied = _parse_tool_json(await bundle_tool.call(member_ctx, action="list"))
            assert denied["success"] is False
            assert "admin permission" in denied["error"]

            # 2. Admin passing spoofed scope_key MUST have scope_key discarded in favor of event scope
            admin_event = MockAstrMessageEvent(group_id="grp_admin_real", sender_id="admin_1", role="admin")
            admin_ctx = make_tool_call_context(admin_event, mock_ctx)
            expected_scope = plugin._resolve_scope(admin_event)
            allowed = _parse_tool_json(
                await image_tool.call(admin_ctx, action="status", scope_key="aiocqhttp:group:victim_999")
            )
            assert allowed["success"] is True
            assert allowed["scope"] == expected_scope
            assert "victim_999" not in allowed["scope"]
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_knowledge_bundle_export_inspect_import_uninstall_roundtrip(tmp_path: Path) -> None:
    async def _run() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"integration": {"enable_v2_extended_tools": True}},
        )
        await plugin.initialize()
        try:
            # Scope A: populate paragraphs, entities, relations, and structured Fact Ledger claims
            ev_a = MockAstrMessageEvent(group_id="bundle_src_grp", sender_id="admin_a", role="admin")
            ctx_a = make_tool_call_context(ev_a, mock_ctx)
            scope_a = plugin._resolve_scope(ev_a)

            ingest_tool = next(t for t in plugin._llm_tools if t.name == "ingest_text")
            fact_tool = next(t for t in plugin._llm_tools if t.name == "memory_fact_admin")
            bundle_tool = next(t for t in plugin._llm_tools if t.name == "memory_bundle_admin")

            ingested = _parse_tool_json(
                await ingest_tool.call(
                    ctx_a,
                    text="哈雷酱最喜欢在下午三点品尝大吉岭红茶配司康饼。",
                    person_ids=["aiocqhttp:admin_a"],
                    entities=["哈雷酱", "大吉岭红茶", "司康饼"],
                    relations=[
                        {
                            "subject": "哈雷酱",
                            "predicate": "喜爱饮品",
                            "object": "大吉岭红茶",
                            "confidence": 0.95,
                        }
                    ],
                )
            )
            assert ingested["success"] is True

            fact_created = _parse_tool_json(
                await fact_tool.call(
                    ctx_a,
                    action="create",
                    scope_type="person",
                    scope_id="aiocqhttp:admin_a",
                    fact_key="favorite_tea",
                    value_text="大吉岭红茶",
                    authority="manual",
                    profile_section="interaction_preferences",
                )
            )
            assert fact_created["success"] is True

            # Export full .amembundle from Scope A
            exported = _parse_tool_json(
                await bundle_tool.call(
                    ctx_a,
                    action="export",
                    content_level="full",
                    package={
                        "id": "amx.test.tea_pack",
                        "version": "1.0.0",
                        "name": "皇家红茶记忆包",
                        "description": "跨作用域完整记忆包端到端测试",
                    },
                )
            )
            assert exported["success"] is True
            bundle_src_path = Path(exported["path"])
            assert bundle_src_path.is_file()
            assert bundle_src_path.suffix == ".amembundle"
            assert exported["counts"]["paragraphs"] >= 1
            assert exported["counts"]["fact_claims"] >= 1

            # Inspect in Scope A
            inspected = _parse_tool_json(
                await bundle_tool.call(ctx_a, action="inspect", path=bundle_src_path.name)
            )
            assert inspected["success"] is True
            assert inspected["package"]["id"] == "amx.test.tea_pack"
            assert inspected["content_level"] == "full"

            # Copy .amembundle into Scope B's controlled imports/bundles directory and import!
            ev_b = MockAstrMessageEvent(group_id="bundle_dst_grp", sender_id="admin_b", role="admin")
            ctx_b = make_tool_call_context(ev_b, mock_ctx)
            scope_b = plugin._resolve_scope(ev_b)
            assert scope_b != scope_a

            rt_b = await plugin.runtime_manager.get_runtime(scope_b)
            dst_bundles_dir = Path(rt_b.context.data_dir) / "imports" / "bundles"
            dst_bundles_dir.mkdir(parents=True, exist_ok=True)
            dst_bundle_path = dst_bundles_dir / bundle_src_path.name
            shutil.copy2(bundle_src_path, dst_bundle_path)

            imported = _parse_tool_json(
                await bundle_tool.call(
                    ctx_b,
                    action="import",
                    path=dst_bundle_path.name,
                    mode="restore",
                    scope_type="global",
                )
            )
            assert imported["success"] is True, f"Bundle import failed: {imported}"
            installation_id = imported["installation_id"]
            assert installation_id
            assert imported["inserted"]["paragraphs"] >= 1
            assert imported["inserted"]["fact_claims"] >= 1

            # Verify installed list & migrated Fact Ledger claim in Scope B
            listed = _parse_tool_json(await bundle_tool.call(ctx_b, action="list"))
            assert listed["success"] is True
            assert listed["count"] == 1
            assert listed["items"][0]["installation_id"] == installation_id

            facts_in_b = _parse_tool_json(
                await fact_tool.call(
                    ctx_b,
                    action="list",
                    scope_type="person",
                    scope_id="aiocqhttp:admin_a",
                )
            )
            assert facts_in_b["success"] is True
            assert any(item["value_text"] == "大吉岭红茶" for item in facts_in_b["items"])

            # Uninstall from Scope B and verify exclusive memories are removed cleanly
            uninstalled = _parse_tool_json(
                await bundle_tool.call(ctx_b, action="uninstall", installation_id=installation_id)
            )
            assert uninstalled["success"] is True, f"Bundle uninstall failed: {uninstalled}"
            assert uninstalled["removed_memories"] is True
            assert uninstalled["removed"]["paragraphs"] >= 1

            listed_after = _parse_tool_json(await bundle_tool.call(ctx_b, action="list"))
            assert listed_after["count"] == 0
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_multimodal_image_memory_lifecycle_and_edge_cases(tmp_path: Path) -> None:
    async def _run() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "integration": {"enable_v2_extended_tools": True},
                "image_memory": {"enabled": True, "require_embedding": False},
            },
        )
        await plugin.initialize()
        try:
            ev = MockAstrMessageEvent(group_id="img_grp_1", sender_id="admin_img", role="admin")
            tool_ctx = make_tool_call_context(ev, mock_ctx)
            scope = plugin._resolve_scope(ev)
            image_tool = next(t for t in plugin._llm_tools if t.name == "memory_image_admin")
            ingest_tool = next(t for t in plugin._llm_tools if t.name == "ingest_text")

            # Edge case 1: invalid base64
            bad_b64 = _parse_tool_json(
                await image_tool.call(tool_ctx, action="ingest", image_base64="not-valid-base64!!!")
            )
            assert bad_b64["success"] is False
            assert "Base64" in bad_b64["error"]

            # Edge case 2: missing image payload
            missing_img = _parse_tool_json(await image_tool.call(tool_ctx, action="ingest"))
            assert missing_img["success"] is False

            # Valid PNG ingest
            png_b64 = _make_png_base64((120, 60, 200))
            ingested = _parse_tool_json(
                await image_tool.call(
                    tool_ctx,
                    action="ingest",
                    image_base64=png_b64,
                    source_kind="chat",
                    scope_type="chat",
                    chat_id="img_grp_1",
                    message_id="msg_img_001",
                    user_statement="这是本小姐的设计架构草图",
                )
            )
            assert ingested["success"] is True, f"Image ingest failed: {ingested}"
            asset_id = ingested["asset_id"]
            occurrence_id = ingested["occurrence_id"]
            assert asset_id and occurrence_id

            # Add manual observation
            observed = _parse_tool_json(
                await image_tool.call(
                    tool_ctx,
                    action="observe",
                    occurrence_id=occurrence_id,
                    text="蓝发双马尾大小姐绘制的 Clean Architecture 分层图",
                    source_kind="manual",
                    confirm_status="confirmed",
                )
            )
            assert observed["success"] is True, f"Image observe failed: {observed}"

            # Link image occurrence to a stored paragraph, then verify get/list & unlink
            para_res = _parse_tool_json(
                await ingest_tool.call(tool_ctx, text="架构设计讨论：插件通过门面层隔离内核。")
            )
            rt = await plugin.runtime_manager.get_runtime(scope)
            para_rows = rt.context.metadata_store.query("SELECT hash FROM paragraphs LIMIT 1")
            assert para_rows
            para_hash = str(para_rows[0]["hash"])

            linked = _parse_tool_json(
                await image_tool.call(
                    tool_ctx,
                    action="link",
                    occurrence_id=occurrence_id,
                    target_type="paragraph",
                    target_id=para_hash,
                    link_kind="evidence",
                )
            )
            assert linked["success"] is True, f"Image link failed: {linked}"
            link_id = linked["link"]["link_id"]

            listed = _parse_tool_json(await image_tool.call(tool_ctx, action="list", chat_id="img_grp_1"))
            assert listed["success"] is True, f"Image list failed: {listed}"
            assert len(listed["items"]) == 1

            detail = _parse_tool_json(await image_tool.call(tool_ctx, action="get", asset_id=asset_id))
            assert detail["success"] is True, f"Image get failed: {detail}"
            assert detail["asset"]["asset_id"] == asset_id
            assert len(detail["observations"]) >= 2
            assert len(detail["links"]) == 1

            unlinked = _parse_tool_json(await image_tool.call(tool_ctx, action="unlink", link_id=link_id))
            assert unlinked["success"] is True, f"Image unlink failed: {unlinked}"

            deleted = _parse_tool_json(
                await image_tool.call(tool_ctx, action="delete_occurrence", occurrence_id=occurrence_id)
            )
            assert deleted["success"] is True, f"Image delete_occurrence failed: {deleted}"

            listed_after = _parse_tool_json(await image_tool.call(tool_ctx, action="list", chat_id="img_grp_1"))
            assert len(listed_after["items"]) == 0
            assert para_res["success"] is True
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_identity_canonicalization_and_correction_contract_dispatch(tmp_path: Path) -> None:
    async def _run() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "integration": {
                    "enable_v2_extended_tools": True,
                    "fuzzy_modify": {
                        "enabled": True,
                        "auto_execute_enabled": False,
                    },
                }
            },
        )
        await plugin.initialize()
        try:
            ev = MockAstrMessageEvent(group_id="id_corr_grp", sender_id="admin_id", role="admin")
            tool_ctx = make_tool_call_context(ev, mock_ctx)
            scope = plugin._resolve_scope(ev)

            # 1. Test observe_identity + resolve_identity via AdminService.dispatch_contract
            observed = await plugin.admin_service.dispatch_contract(
                scope_key=scope,
                component_name="memory_profile_admin",
                action="observe_identity",
                payload={
                    "platform": "aiocqhttp",
                    "user_id": "10086",
                    "aliases": ["哈雷大小姐", "蓝发天才工程师"],
                    "group_id": "id_corr_grp",
                },
            )
            assert observed["success"] is True, f"observe_identity failed: {observed}"
            canonical_pid = observed["person_id"]
            assert canonical_pid == "aiocqhttp:10086"

            resolved = await plugin.admin_service.dispatch_contract(
                scope_key=scope,
                component_name="memory_profile_admin",
                action="resolve_identity",
                payload={"identifier": "蓝发天才工程师"},
            )
            assert resolved["success"] is True, f"resolve_identity failed: {resolved}"
            assert resolved["person_id"] == canonical_pid

            # 2. Test memory_correction_admin contract (plan -> unconfirmed guard -> confirmed execute -> rollback)
            ingest_tool = next(t for t in plugin._llm_tools if t.name == "ingest_text")
            correction_tool = next(t for t in plugin._llm_tools if t.name == "memory_correction_admin")

            await ingest_tool.call(
                tool_ctx,
                text="用户目前的居住城市是旧金山。",
                person_ids=[canonical_pid],
                chat_id="id_corr_grp",
            )
            rt = await plugin.runtime_manager.get_runtime(scope)
            para_rows = rt.context.metadata_store.query("SELECT hash FROM paragraphs ORDER BY created_at DESC LIMIT 1")
            assert para_rows
            para_hash = str(para_rows[0]["hash"])

            # Configure mock provider LLM JSON plan response for FuzzyModifyService
            mock_ctx._default_provider.default_response = json.dumps(
                {
                    "confidence": 0.92,
                    "risk_level": "low",
                    "reason": "用户搬迁更新居住地",
                    "operations": [
                        {
                            "action": "mark_superseded",
                            "candidate_id": f"paragraph:{para_hash}",
                            "reason": "旧金山居住信息已过期",
                        },
                        {
                            "action": "ingest_text",
                            "text": "用户目前的居住城市是东京。",
                            "reason": "修正居住城市为东京",
                        },
                    ],
                },
                ensure_ascii=False,
            )

            planned = _parse_tool_json(
                await correction_tool.call(
                    tool_ctx,
                    action="plan",
                    request_text="把居住城市从旧金山改成东京",
                    person_id=canonical_pid,
                    chat_id="id_corr_grp",
                )
            )
            assert planned["success"] is True, f"Plan failed: {planned}"
            plan_id = planned["plan_id"]
            assert plan_id

            # Unconfirmed execute MUST be rejected when auto_execute_enabled is False
            unconfirmed = _parse_tool_json(
                await correction_tool.call(tool_ctx, action="execute", plan_id=plan_id, confirmed=False)
            )
            assert unconfirmed["success"] is False

            # Confirmed execute succeeds
            executed = _parse_tool_json(
                await correction_tool.call(tool_ctx, action="execute", plan_id=plan_id, confirmed=True)
            )
            assert executed["success"] is True, f"Execute failed: {executed}"

            # Rollback restores original content
            rolled_back = _parse_tool_json(
                await correction_tool.call(tool_ctx, action="rollback", plan_id=plan_id, reason="测试回滚")
            )
            assert rolled_back["success"] is True, f"Rollback failed: {rolled_back}"
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_webui_v2_endpoints_via_asgi_bridge(tmp_path: Path) -> None:
    async def _run() -> None:
        plugin, _ = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            scope = "aiocqhttp:group:webui_v2"
            await plugin.runtime_manager.get_runtime(scope)
            bridge = plugin.webui_page_bridge
            assert bridge is not None

            # 1. Create a structured fact via unified /v1/admin/memory_fact_admin
            created = await bridge.dispatch(
                method="POST",
                url=f"/v1/admin/memory_fact_admin?_scope={scope}",
                body={
                    "action": "create",
                    "payload": {
                        "scope_type": "person",
                        "scope_id": "aiocqhttp:42",
                        "fact_key": "editor",
                        "value_text": "Neovim",
                        "authority": "manual",
                    },
                },
            )
            assert created["success"] is True

            # 2. Read Fact Ledger via /api/facts
            facts = await bridge.dispatch(
                method="GET",
                url=f"/api/facts?scope_type=person&scope_id=aiocqhttp:42&_scope={scope}",
                body=None,
            )
            assert facts["success"] is True
            assert facts["count"] == 1
            assert facts["items"][0]["value_text"] == "Neovim"

            # 3. Check Image Memory status via /api/images/status and /v1/images (default require_embedding=True pauses unconfigured runtime)
            img_api = await bridge.dispatch(
                method="GET",
                url=f"/api/images/status?_scope={scope}",
                body=None,
            )
            assert img_api["success"] is True
            assert img_api["enabled"] is False
            assert img_api["configured_enabled"] is True
            assert img_api["require_embedding"] is True
            assert img_api["embedding_configured"] is False
            assert img_api["paused_by_missing_embedding"] is True
            assert img_api["asset_count"] == 0

            img_v1 = await bridge.dispatch(
                method="POST",
                url=f"/v1/images?_scope={scope}",
                body={"action": "status", "payload": {}},
            )
            assert img_v1["success"] is True
            assert img_v1["enabled"] is False
            assert img_v1["reason"] == "missing_image_embedding"

            # 4. List installed Knowledge Bundles via /v1/bundles
            bundles_v1 = await bridge.dispatch(
                method="POST",
                url=f"/v1/bundles?_scope={scope}",
                body={"action": "list", "payload": {"limit": 10}},
            )
            assert bundles_v1["success"] is True
            assert bundles_v1["count"] == 0
        finally:
            await plugin.terminate()

    asyncio.run(_run())
