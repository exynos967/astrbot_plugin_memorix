from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import sys
import types
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from astrbot_plugin_memorix.memorix.app_context import ScopeRuntimeManager
from astrbot_plugin_memorix.memorix.services.admin_service import AdminService
from astrbot_plugin_memorix.memorix.webui.plugin_page_bridge import (
    MAX_ACTIVE_UPLOAD_TICKETS,
    PLUGIN_PAGE_API_ROUTE,
    PLUGIN_PAGE_BUNDLE_DOWNLOAD_ROUTE,
    PLUGIN_PAGE_TICKET_UPLOAD_ROUTE,
    PLUGIN_PAGE_UPLOAD_ROUTE,
    PluginPageWebUIBridge,
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _make_png_bytes(color: tuple[int, int, int] = (80, 140, 220), *, width: int = 24, height: int = 18) -> bytes:
    row = b"\x00" + bytes(color) * width
    raw = row * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._buf = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


class _StubJsonResponse:
    def __init__(self, data: Any, status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code

    def get_json(self) -> Any:
        return self.data


def _install_web_api_stub(
    *,
    files: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    path_params: dict[str, str] | None = None,
    include_file_response: bool = True,
) -> types.ModuleType:
    web_mod = types.ModuleType("astrbot.api.web")

    class _Request:
        def __init__(self) -> None:
            self.query = dict(query or {})
            self.args = dict(query or {})
            self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
            self.path_params = dict(path_params or {})

        async def files(self) -> dict[str, Any]:
            return dict(files or {})

        async def json(self, default: Any = None) -> Any:
            return default

    def json_response(payload: Any, *, status_code: int = 200, headers: Any = None) -> _StubJsonResponse:
        del headers
        return _StubJsonResponse(payload, status_code=status_code)

    def error_response(message: str, status_code: int = 400) -> _StubJsonResponse:
        return _StubJsonResponse({"status": "error", "message": message}, status_code=status_code)

    web_mod.PluginUploadFile = _FakeUploadFile
    web_mod.request = _Request()
    web_mod.json_response = json_response
    web_mod.error_response = error_response

    if include_file_response:
        def file_response(path: str, *, filename: str | None = None, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            p = Path(path)
            return {
                "kind": "file_response",
                "path": str(p),
                "filename": filename or p.name,
                "bytes": p.read_bytes(),
            }

        web_mod.file_response = file_response

    sys.modules["astrbot.api.web"] = web_mod
    if "astrbot.api" in sys.modules:
        sys.modules["astrbot.api"].web = web_mod
    return web_mod


def test_bridge_registers_v2_routes_preserving_legacy_routes() -> None:
    registered: list[tuple[str, Any, list[str], str]] = []
    ctx = types.SimpleNamespace(
        register_web_api=lambda route, handler, methods, desc: registered.append(
            (route, handler, list(methods), desc)
        )
    )
    bridge = PluginPageWebUIBridge(runtime_manager=object(), scope_resolver=lambda: "default")
    bridge.register(ctx, plugin_name="astrbot_plugin_memorix")

    by_route = {route: (handler, methods) for route, handler, methods, _ in registered}
    assert f"/astrbot_plugin_memorix/{PLUGIN_PAGE_API_ROUTE}" in by_route
    assert f"/astrbot_plugin_memorix/{PLUGIN_PAGE_UPLOAD_ROUTE}" in by_route
    assert f"/astrbot_plugin_memorix/{PLUGIN_PAGE_TICKET_UPLOAD_ROUTE}" in by_route
    assert f"/astrbot_plugin_memorix/{PLUGIN_PAGE_BUNDLE_DOWNLOAD_ROUTE}" in by_route
    assert by_route[f"/astrbot_plugin_memorix/{PLUGIN_PAGE_TICKET_UPLOAD_ROUTE}"][1] == ["POST"]
    assert by_route[f"/astrbot_plugin_memorix/{PLUGIN_PAGE_BUNDLE_DOWNLOAD_ROUTE}"][1] == ["GET"]


def test_upload_tickets_import_and_bundle_lifecycle(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(
            base_data_dir=tmp_path / "data",
            plugin_config={"scope": {"mode": "group_global"}, "image_memory": {"require_embedding": False}},
        )
        admin = AdminService(rm)
        bridge = PluginPageWebUIBridge(
            runtime_manager=rm,
            scope_resolver=lambda: "default",
            admin_service=admin,
        )
        try:
            scope_a = "aiocqhttp:group:101"
            scope_b = "aiocqhttp:group:202"

            # 1. Create import ticket via POST /v1/upload-tickets?_scope=...
            ticket_resp = await bridge.dispatch(
                method="POST",
                url=f"/v1/upload-tickets?_scope={scope_a}",
                body={
                    "kind": "import",
                    "options": {
                        "source": "ticket_import_src",
                        "input_mode": "text",
                        "knowledge_type": "factual",
                        "name": "renamed_notes.md",
                    },
                },
            )
            assert ticket_resp["success"] is True
            import_ticket_id = ticket_resp["ticket_id"]

            md_bytes = "蓝发双马尾大小姐编写的优雅架构设计指南。\n\n第二段包含检索调谐说明。".encode("utf-8")
            _install_web_api_stub(
                files={"file": _FakeUploadFile("raw_notes.md", md_bytes)},
                headers={"content-length": str(len(md_bytes))},
                path_params={"ticket_id": import_ticket_id},
            )
            upload_resp = await bridge.handle_ticket_upload(import_ticket_id)
            assert upload_resp.status_code == 200
            upload_json = upload_resp.get_json()
            assert upload_json["status"] == "ok"
            task_id = upload_json["data"]["task_id"]
            assert task_id

            # Single-use: reusing consumed ticket must fail with 400
            reuse_resp = await bridge.handle_ticket_upload(import_ticket_id)
            assert reuse_resp.status_code == 400
            assert reuse_resp.get_json()["status"] == "error"

            # Wait for import worker to finish and verify paragraph written in scope_a
            for _ in range(50):
                polled = await bridge.dispatch(
                    method="GET",
                    url=f"/v1/import/tasks/{task_id}?_scope={scope_a}",
                )
                if polled["status"] in {"completed", "completed_with_errors", "failed"}:
                    break
                await asyncio.sleep(0.05)
            assert polled["status"] == "completed"
            assert polled["files"][0]["name"] == "renamed_notes.md"

            # 2. Expired ticket & max active cap (256)
            expired_ticket = await bridge.dispatch(
                method="POST",
                url=f"/v1/upload-tickets?_scope={scope_a}",
                body={"kind": "import", "options": {}},
            )
            bridge._upload_tickets[expired_ticket["ticket_id"]]["expires_at"] -= 1000.0
            expired_resp = await bridge.handle_ticket_upload(expired_ticket["ticket_id"])
            assert expired_resp.status_code == 400

            for _ in range(MAX_ACTIVE_UPLOAD_TICKETS + 10):
                await bridge.dispatch(
                    method="POST",
                    url=f"/v1/upload-tickets?_scope={scope_a}",
                    body={"kind": "import", "options": {}},
                )
            assert len(bridge._upload_tickets) <= MAX_ACTIVE_UPLOAD_TICKETS

            # 3. Export real .amembundle from scope_a and upload into scope_b via bundle ticket
            exported = await admin.bundle_admin(
                scope_key=scope_a,
                action="export",
                content_level="full",
                package={
                    "id": "amx.test.ticket_bundle",
                    "version": "1.0.0",
                    "name": "Ticket Bundle Pack",
                },
            )
            assert exported["success"] is True
            bundle_bytes = Path(exported["path"]).read_bytes()

            bundle_ticket = await bridge.dispatch(
                method="POST",
                url=f"/v1/upload-tickets?_scope={scope_b}",
                body={"kind": "bundle", "options": {}},
            )
            _install_web_api_stub(
                files={"file": _FakeUploadFile("My Special Bundle (v2)!.amembundle", bundle_bytes)},
                headers={"content-length": str(len(bundle_bytes))},
            )
            bundle_resp = await bridge.handle_ticket_upload(bundle_ticket["ticket_id"])
            assert bundle_resp.status_code == 200
            bundle_data = bundle_resp.get_json()["data"]
            assert bundle_data["success"] is True
            saved_name = bundle_data["file_name"]
            assert saved_name.startswith("My_Special_Bundle_v2-")
            assert saved_name.endswith(".amembundle")
            assert bundle_data["inspect"]["success"] is True
            assert bundle_data["inspect"]["package"]["id"] == "amx.test.ticket_bundle"

            rt_b = await rm.get_runtime(scope_b)
            saved_path = Path(rt_b.context.data_dir) / "imports" / "bundles" / saved_name
            assert saved_path.is_file()

            # 4. Corrupt .amembundle upload MUST fail inspect and immediately delete saved file
            bad_ticket = await bridge.dispatch(
                method="POST",
                url=f"/v1/upload-tickets?_scope={scope_b}",
                body={"kind": "bundle", "options": {}},
            )
            before_files = set((Path(rt_b.context.data_dir) / "imports" / "bundles").iterdir())
            _install_web_api_stub(
                files={"file": _FakeUploadFile("corrupted_archive.amembundle", b"not-a-valid-zip-bundle")},
                headers={"content-length": "22"},
            )
            bad_resp = await bridge.handle_ticket_upload(bad_ticket["ticket_id"])
            assert bad_resp.status_code == 400
            assert bad_resp.get_json()["status"] == "error"
            after_files = set((Path(rt_b.context.data_dir) / "imports" / "bundles").iterdir())
            assert after_files == before_files
        finally:
            await bridge.close()
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())


def test_bundle_download_and_read_chunk_and_path_traversal_guard(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(base_data_dir=tmp_path / "data")
        admin = AdminService(rm)
        bridge = PluginPageWebUIBridge(
            runtime_manager=rm,
            scope_resolver=lambda: "default",
            admin_service=admin,
        )
        try:
            scope = "aiocqhttp:group:303"
            rt = await rm.get_runtime(scope)
            p_hash = rt.context.metadata_store.add_paragraph(
                content="分块读取与下载回退机制验证段落。" * 20,
                source="bundle_dl_src",
            )
            assert p_hash

            exported = await bridge.dispatch(
                method="POST",
                url=f"/v1/bundles?_scope={scope}",
                body={
                    "action": "export",
                    "payload": {
                        "content_level": "knowledge",
                        "package": {"id": "amx.test.dl", "version": "1.0.0", "name": "DL Test"},
                    },
                },
            )
            assert exported["success"] is True
            file_name = Path(exported["path"]).name
            expected_bytes = Path(exported["path"]).read_bytes()

            # 1. Bundle download with astrbot.api.web.file_response
            _install_web_api_stub(
                query={"_scope": scope, "file_name": file_name},
                include_file_response=True,
            )
            dl_res = await bridge.handle_bundle_download()
            assert dl_res["kind"] == "file_response"
            assert dl_res["filename"] == file_name
            assert dl_res["bytes"] == expected_bytes

            # 2. Bundle download fallback when file_response is absent on stubbed astrbot.api.web
            _install_web_api_stub(
                query={"_scope": scope, "file_name": file_name},
                include_file_response=False,
            )
            fallback_res = await bridge.handle_bundle_download()
            assert Path(fallback_res.path).read_bytes() == expected_bytes

            # 3. Path traversal / non-.amembundle / missing file rejections on bundle-download
            for bad_name, expected_code in (
                ("../outside.amembundle", 400),
                ("nested/evil.amembundle", 400),
                ("..\\outside.amembundle", 400),
                ("notes.txt", 400),
                ("nonexistent_pack.amembundle", 404),
            ):
                _install_web_api_stub(query={"_scope": scope, "file_name": bad_name})
                err_res = await bridge.handle_bundle_download()
                assert err_res.status_code == expected_code

            # 4. Chunked read via /v1/bundles and /v1/admin/memory_bundle_admin
            assembled = bytearray()
            offset = 0
            while True:
                chunk_res = await bridge.dispatch(
                    method="POST",
                    url=f"/v1/admin/memory_bundle_admin?_scope={scope}",
                    body={
                        "action": "read_chunk",
                        "payload": {"file_name": file_name, "offset": offset, "chunk_size": 1024},
                    },
                )
                assert chunk_res["success"] is True
                assert chunk_res["file_name"] == file_name
                assert chunk_res["total_size"] == len(expected_bytes)
                assert chunk_res["offset"] == offset
                raw_part = base64.b64decode(chunk_res["chunk_base64"])
                assembled.extend(raw_part)
                offset = chunk_res["next_offset"]
                if chunk_res["eof"]:
                    break
            assert bytes(assembled) == expected_bytes

            # 5. Path traversal & invalid suffix rejection in bundle_admin(action="read_chunk")
            outside_file = tmp_path / "outside.amembundle"
            outside_file.write_bytes(b"secret")
            traversal_chunk = await admin.bundle_admin(
                scope_key=scope,
                action="read_chunk",
                file_name=str(outside_file),
            )
            assert traversal_chunk["success"] is False
            rel_traversal = await admin.bundle_admin(
                scope_key=scope,
                action="read_chunk",
                file_name="../../outside.amembundle",
            )
            assert rel_traversal["success"] is False
        finally:
            await bridge.close()
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())


def test_image_binary_read_and_chat_scope_isolation(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(
            base_data_dir=tmp_path / "data",
            plugin_config={"scope": {"mode": "group_global"}, "image_memory": {"require_embedding": False}},
        )
        admin = AdminService(rm)
        bridge = PluginPageWebUIBridge(
            runtime_manager=rm,
            scope_resolver=lambda: "default",
            admin_service=admin,
        )
        try:
            scope = "aiocqhttp:group:404"
            png_bytes = _make_png_bytes((220, 90, 140))
            png_b64 = base64.b64encode(png_bytes).decode("ascii")

            ingested = await bridge.dispatch(
                method="POST",
                url=f"/v1/images?_scope={scope}",
                body={
                    "action": "ingest",
                    "payload": {
                        "image_base64": png_b64,
                        "source_kind": "chat",
                        "scope_type": "chat",
                        "chat_id": "chat_alpha",
                        "message_id": "m_alpha_1",
                        "user_statement": "测试图片二进制读取",
                    },
                },
            )
            assert ingested["success"] is True
            asset_id = ingested["asset_id"]

            # Allowed in same chat via /v1/images and /v1/admin/memory_image_admin
            read_ok = await bridge.dispatch(
                method="POST",
                url=f"/v1/admin/memory_image_admin?_scope={scope}",
                body={
                    "action": "read",
                    "payload": {"asset_id": asset_id, "chat_id": "chat_alpha"},
                },
            )
            assert read_ok["success"] is True
            assert read_ok["asset_id"] == asset_id
            assert read_ok["mime_type"] == "image/png"
            assert read_ok["byte_size"] == len(png_bytes)
            assert read_ok["width"] == 24
            assert read_ok["height"] == 18
            assert base64.b64decode(read_ok["content_base64"]) == png_bytes
            assert read_ok["data_url"] == f"data:image/png;base64,{read_ok['content_base64']}"

            # Blocked in another chat_id when chat-scoped (matches action="get" isolation)
            read_denied = await bridge.dispatch(
                method="POST",
                url=f"/v1/images?_scope={scope}",
                body={
                    "action": "read",
                    "payload": {"asset_id": asset_id, "chat_id": "chat_beta"},
                },
            )
            assert read_denied["success"] is False
            assert "不可见" in read_denied["error"]
        finally:
            await bridge.close()
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())


def test_v1_query_auto_json_paste_and_dashboard_import_stats(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(base_data_dir=tmp_path / "data")
        admin = AdminService(rm)
        bridge = PluginPageWebUIBridge(
            runtime_manager=rm,
            scope_resolver=lambda: "default",
            admin_service=admin,
        )
        try:
            scope = "aiocqhttp:group:505"

            # 1. JSON paste with dict/list in mode="text" must serialize via json.dumps (not Python repr)
            chat_log_payload = [
                {"sender": "哈雷酱", "content": "本小姐昨天下午品尝了皇家伯爵红茶。", "timestamp": 1730000000},
                {"sender": "助手", "content": "已经为您记录下午茶偏好。", "timestamp": 1730000060},
            ]
            paste_created = await bridge.dispatch(
                method="POST",
                url=f"/v1/import/tasks?_scope={scope}",
                body={
                    "mode": "text",
                    "payload": chat_log_payload,
                    "options": {"chat_log": True, "source": "json_chat_paste"},
                },
            )
            paste_task_id = paste_created["task_id"]
            app = await bridge._get_app(scope)
            raw_inline = app.import_task_manager._tasks[paste_task_id].files[0].inline_content
            assert json.loads(raw_inline) == chat_log_payload
            assert "{'sender'" not in raw_inline

            for _ in range(50):
                detail = await bridge.dispatch(
                    method="GET",
                    url=f"/v1/import/tasks/{paste_task_id}?_scope={scope}",
                )
                if detail["status"] in {"completed", "completed_with_errors", "failed"}:
                    break
                await asyncio.sleep(0.05)
            assert detail["status"] == "completed"

            # 2. Task created directly via AdminService.import_admin is queryable via /v1/import/tasks/{id}
            admin_task_res = await bridge.dispatch(
                method="POST",
                url=f"/v1/admin/memory_import_admin?_scope={scope}",
                body={
                    "action": "create_paste",
                    "payload": {
                        "content": "今天讨论了双向量池持久化与检索路由机制。",
                        "source": "admin_paste_src",
                    },
                },
            )
            assert admin_task_res["success"] is True
            admin_task_id = admin_task_res["task"]["task_id"]

            queried_admin_task = await bridge.dispatch(
                method="GET",
                url=f"/v1/import/tasks/{admin_task_id}?_scope={scope}",
            )
            assert queried_admin_task["task_id"] == admin_task_id

            for _ in range(50):
                queried_admin_task = await bridge.dispatch(
                    method="GET",
                    url=f"/v1/import/tasks/{admin_task_id}?_scope={scope}",
                )
                if queried_admin_task["status"] in {"completed", "completed_with_errors", "failed"}:
                    break
                await asyncio.sleep(0.05)

            # 3. Dashboard status includes counts from import_task_manager
            dash = await bridge.dispatch(method="GET", url=f"/v1/dashboard/status?_scope={scope}")
            import_svc = dash["services"]["import"]
            assert int(import_svc["counts"]["total"]) >= 2
            assert int(import_svc["counts"]["succeeded"]) >= 2
            assert import_svc["latest_task"] is not None

            # 4. POST /v1/query/auto (semantic + time intent routing)
            semantic_res = await bridge.dispatch(
                method="POST",
                url=f"/v1/query/auto?_scope={scope}",
                body={"query": "皇家伯爵红茶", "top_k": 5},
            )
            assert semantic_res["query_type"] in {"search", "hybrid", "time"}
            assert isinstance(semantic_res["results"], list)

            time_routed_res = await bridge.dispatch(
                method="POST",
                url=f"/v1/query/auto?_scope={scope}",
                body={"query": "今天讨论了什么检索路由机制", "top_k": 5},
            )
            assert time_routed_res["route_reason"] == "time_intent"
            assert time_routed_res["query_type"] in {"time", "hybrid"}
        finally:
            await bridge.close()
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())


def test_routes_compat_fixes_and_manual_save_dual_pool_persistence(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(base_data_dir=tmp_path / "data")
        admin = AdminService(rm)
        bridge = PluginPageWebUIBridge(
            runtime_manager=rm,
            scope_resolver=lambda: "default",
            admin_service=admin,
        )
        try:
            scope = "aiocqhttp:group:606"
            rt = await rm.get_runtime(scope)
            ctx = rt.context

            # 1. POST /api/node/create stores label in entity metadata
            node_created = await bridge.dispatch(
                method="POST",
                url=f"/api/node/create?_scope={scope}",
                body={"node_id": "service_node_alpha", "label": "核心服务Alpha"},
            )
            assert node_created["success"] is True
            ent_rows = ctx.metadata_store.query(
                "SELECT metadata FROM entities WHERE name = ?",
                ("service_node_alpha",),
            )
            assert ent_rows
            ent_meta = json.loads(ent_rows[0]["metadata"]) if isinstance(ent_rows[0]["metadata"], str) else ent_rows[0]["metadata"]
            assert ent_meta.get("label") == "核心服务Alpha"

            # 2. Edge actions with explicit source/target containing underscores
            await bridge.dispatch(
                method="POST",
                url=f"/api/node/create?_scope={scope}",
                body={"node_id": "service_node_beta"},
            )
            edge_created = await bridge.dispatch(
                method="POST",
                url=f"/api/edge?_scope={scope}",
                body={
                    "source": "service_node_alpha",
                    "target": "service_node_beta",
                    "predicate": "调用接口",
                    "weight": 0.8,
                },
            )
            assert edge_created["success"] is True

            reinforced = await bridge.dispatch(
                method="POST",
                url=f"/api/memory/edge/reinforce?_scope={scope}",
                body={"source": "service_node_alpha", "target": "service_node_beta"},
            )
            assert reinforced["success"] is True
            assert reinforced["count"] == 1

            protected = await bridge.dispatch(
                method="POST",
                url=f"/api/memory/edge/protect?_scope={scope}",
                body={"source": "service_node_alpha", "target": "service_node_beta", "hours": 12},
            )
            assert protected["success"] is True
            assert protected["count"] == 1

            frozen = await bridge.dispatch(
                method="POST",
                url=f"/api/memory/edge/freeze?_scope={scope}",
                body={"source": "service_node_alpha", "target": "service_node_beta"},
            )
            assert frozen["success"] is True
            assert frozen["frozen_edges"] == 1

            # 3. POST /api/source/list with source filter returns paragraphs for that source
            ctx.metadata_store.add_paragraph(content="来源A段落一", source="expand_source_a")
            ctx.metadata_store.add_paragraph(content="来源B段落一", source="expand_source_b")

            summary_list = await bridge.dispatch(
                method="POST",
                url=f"/api/source/list?_scope={scope}",
                body={},
            )
            assert summary_list["mode"] == "summary"

            expanded = await bridge.dispatch(
                method="POST",
                url=f"/api/source/list?_scope={scope}",
                body={"source": "expand_source_a"},
            )
            assert len(expanded["sources"]) == 1
            assert expanded["sources"][0]["content"] == "来源A段落一"
            assert expanded["sources"][0]["source"] == "expand_source_a"
            assert "hash" in expanded["sources"][0]

            # 4. PATCH /api/config/runtime syncs _runtime_auto_save, rebuilds retriever/threshold, returns persist_supported: False
            assert await admin._image_runtime(scope, ctx) is None
            patched = await bridge.dispatch(
                method="PATCH",
                url=f"/api/config/runtime?_scope={scope}",
                body={
                    "updates": {
                        "advanced.enable_auto_save": False,
                        "retrieval.top_k_final": 19,
                        "threshold.min_threshold": 0.44,
                        "image_memory.require_embedding": False,
                    },
                    "persist": True,
                },
            )
            assert patched["success"] is True
            assert patched["persisted"] is False
            assert patched["persist_supported"] is False
            assert ctx._runtime_auto_save is False
            assert ctx.retriever.config.top_k_final == 19
            assert abs(ctx.threshold_filter.config.min_threshold - 0.44) < 1e-6

            # 5. POST /api/save persists dual-pool stores + image_memory_runtime vector store
            dim = ctx.paragraph_vector_store.dimension
            vec = np.ones((1, dim), dtype=np.float32)
            ctx.paragraph_vector_store.add(vec, ["para_dual_1"])
            ctx.graph_vector_store.add(vec, ["entity:ent_dual_1"])
            ctx._dual_vector_pools_ready = True

            img_rt = await admin._image_runtime(scope, ctx)
            img_vec_dir = Path(ctx.data_dir) / "images" / "vectors" / "test_gen"
            img_vec_dir.mkdir(parents=True, exist_ok=True)
            from astrbot_plugin_memorix.memorix.core.storage import QuantizationType, VectorStore

            img_store = VectorStore(dimension=8, quantization_type=QuantizationType.INT8, data_dir=img_vec_dir)
            img_store.add(np.ones((1, 8), dtype=np.float32), ["img_asset_1"])
            img_rt.vector_store = img_store
            img_rt.fingerprint = {"provider": "test", "model": "mock-clip", "dimension": 8}

            save_res = await bridge.dispatch(method="POST", url=f"/api/save?_scope={scope}", body={})
            assert save_res["success"] is True
            assert "paragraph_vector_store" in save_res["saved"]
            assert "graph_vector_store" in save_res["saved"]
            assert "image_vector_store" in save_res["saved"]
            assert ctx.paragraph_vector_store.has_data()
            assert ctx.graph_vector_store.has_data()
            assert img_store.has_data()
        finally:
            await bridge.close()
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())


def test_canonical_person_id_resolution_guard_in_profile_and_fact_admin(tmp_path: Path) -> None:
    async def _run() -> None:
        rm = ScopeRuntimeManager(base_data_dir=tmp_path / "data")
        admin = AdminService(rm)
        try:
            scope = "aiocqhttp:group:707"
            rt = await rm.get_runtime(scope)
            canonical_pid = "aiocqhttp:10086"

            observed = await admin.profile_admin(
                scope_key=scope,
                action="observe_identity",
                platform="aiocqhttp",
                user_id="10086",
                aliases=["哈雷大小姐"],
            )
            assert observed["success"] is True
            assert observed["person_id"] == canonical_pid
            rt.context.metadata_store.upsert_person_registry(
                person_id=canonical_pid,
                person_name="哈雷大小姐",
                nickname="蓝发双马尾",
                user_id="10086",
                platform="aiocqhttp",
            )

            # Passing display name to set_aliases / get_aliases / delete_aliases resolves to canonical_pid
            alias_set = await admin.profile_admin(
                scope_key=scope,
                action="set_aliases",
                person_id="哈雷大小姐",
                aliases=["天才工程师哈雷"],
            )
            assert alias_set["success"] is True
            assert alias_set["person_id"] == canonical_pid

            alias_get = await admin.profile_admin(
                scope_key=scope,
                action="get_aliases",
                person_id="哈雷大小姐",
            )
            assert alias_get["success"] is True
            assert alias_get["person_id"] == canonical_pid
            assert "天才工程师哈雷" in alias_get["manual_aliases"]

            alias_del = await admin.profile_admin(
                scope_key=scope,
                action="delete_aliases",
                person_id="哈雷大小姐",
            )
            assert alias_del["success"] is True
            assert alias_del["person_id"] == canonical_pid

            # Passing display name to FactAdminService create & list resolves to canonical_pid
            fact_created = await admin.fact_admin(
                scope_key=scope,
                action="create",
                scope_type="person",
                scope_id="哈雷大小姐",
                fact_key="hair_style",
                value_text="蓝发双马尾",
            )
            assert fact_created["success"] is True
            assert fact_created["claim"]["scope_id"] == canonical_pid

            fact_listed = await admin.fact_admin(
                scope_key=scope,
                action="list",
                scope_type="person",
                scope_id="哈雷大小姐",
            )
            assert fact_listed["success"] is True
            assert fact_listed["count"] == 1
            assert fact_listed["items"][0]["scope_id"] == canonical_pid
            assert fact_listed["items"][0]["value_text"] == "蓝发双马尾"
        finally:
            await admin.close()
            await rm.close_all()

    asyncio.run(_run())
