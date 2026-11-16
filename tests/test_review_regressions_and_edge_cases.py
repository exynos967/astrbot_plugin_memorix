"""Regression and adversarial edge-case tests for A_memorix v2.0.0 alignment review."""

from __future__ import annotations

import asyncio
import importlib
import json
import struct
import sys
import types
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from astrbot_plugin_memorix.main import PLUGIN_VERSION
from astrbot_plugin_memorix.memorix.amemorix import __version__ as AMEMORIX_VERSION
from astrbot_plugin_memorix.memorix.core.image.asset_store import ImageAssetStore, _inspect_without_pil
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.engine import ScopeEngineManager
from astrbot_plugin_memorix.memorix.engine.host_shims import install_host_shims, llm_shim
from astrbot_plugin_memorix.memorix.tool_registry import (
    AdminToolRegistration,
    build_default_admin_dispatcher,
    build_memorix_tools,
)
from astrbot_plugin_memorix.tests.mocks.astrbot_mocks import create_test_plugin_instance


def test_version_alignment_v2_0_0():
    assert PLUGIN_VERSION == "2.0.0"
    assert AMEMORIX_VERSION == "2.0.0"


def test_clear_and_manual_retract_under_frozen_windows_clock(tmp_path: Path):
    """Reproduces coarse Windows timer where clear + manual retract happen in the exact same float timestamp."""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        fixed_ts = 1_760_000_000.25
        with patch("astrbot_plugin_memorix.memorix.core.storage.metadata_fact.datetime") as mock_dt:
            mock_dt.now.return_value.timestamp.return_value = fixed_ts
            claim = store.upsert_fact_claim(
                scope_type="person",
                scope_id="p_frozen",
                fact_key="city",
                value_text="上海",
                authority="manual",
            )
            deleted = store.delete_memories("clear", [])
            assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"

            # Manual retraction in the exact same clock tick (and even with explicit identical timestamp)
            store.retract_fact_claim(claim["claim_id"], reason="later_manual_retraction")
            store.restore_memory_operation(deleted["operation_id"])
            assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
    finally:
        store.close()


def test_paragraph_soft_delete_then_manual_retract_survives_restore_and_summary_reingest(tmp_path: Path):
    """Manual retraction after paragraph soft-delete must survive paragraph restore AND summary re-import."""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        para_hash = store.add_paragraph("小红住在深圳", source="chat:1")
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="p_shenzhen",
            fact_key="city",
            value_text="深圳",
            authority="summary_derived",
            evidence_type="paragraph",
            evidence_id=para_hash,
        )
        op = store.delete_memories("paragraph", [para_hash])
        assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"

        # Explicit manual retraction while claim is already retracted via detach_evidence
        store.retract_fact_claim(claim["claim_id"], reason="user_denied_city")

        # Restoring the deleted paragraph must NOT resurrect the manually retracted claim
        store.restore_memory_operation(op["operation_id"])
        assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"

        # Subsequent non-manual summary re-assertion must ALSO respect the manual retraction
        reingested = store.upsert_fact_claim(
            scope_type="person",
            scope_id="p_shenzhen",
            fact_key="city",
            value_text="深圳",
            authority="summary_derived",
            evidence_type="paragraph",
            evidence_id=para_hash,
        )
        assert reingested["status"] == "retracted"
        assert reingested["restored"] is False
    finally:
        store.close()


def test_post_clear_summary_can_assert_new_facts(tmp_path: Path):
    """Facts retracted solely by memory_scope_cleared (not manual retract) can be re-learned after clear."""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        claim = store.upsert_fact_claim(
            scope_type="person",
            scope_id="p_clear",
            fact_key="drink",
            value_text="红茶",
            authority="summary_derived",
        )
        store.delete_memories("clear", [])
        assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"

        relearned = store.upsert_fact_claim(
            scope_type="person",
            scope_id="p_clear",
            fact_key="drink",
            value_text="红茶",
            authority="summary_derived",
        )
        assert relearned["status"] == "active"
    finally:
        store.close()


def test_host_shims_astrbot_data_plugins_namespace_and_hot_reload(tmp_path: Path):
    """Simulates AstrBot StarManager loading & hot-reloading under data.plugins.astrbot_plugin_memorix."""
    plugin_root = Path(__file__).resolve().parents[1]
    saved_modules = {
        k: v
        for k, v in sys.modules.items()
        if k == "data" or k.startswith("data.") or k == "src.A_memorix" or k.startswith("src.A_memorix.")
    }
    try:
        # Build synthetic data.plugins.astrbot_plugin_memorix package hierarchy like AstrBot StarManager
        data_pkg = types.ModuleType("data")
        data_pkg.__path__ = []
        plugins_pkg = types.ModuleType("data.plugins")
        plugins_pkg.__path__ = [str(plugin_root.parent)]
        sys.modules["data"] = data_pkg
        sys.modules["data.plugins"] = plugins_pkg

        shims_g1 = importlib.import_module("data.plugins.astrbot_plugin_memorix.memorix.engine.host_shims")
        core_g1 = importlib.import_module("data.plugins.astrbot_plugin_memorix.memorix.core")
        shims_g1.install_host_shims()
        finder_count_g1 = sum(1 for f in sys.meta_path if getattr(f, "_memorix_host_shim_finder", False))
        assert finder_count_g1 == 1
        assert sys.modules["src.A_memorix.core"] is core_g1
        schema_mod_g1 = importlib.import_module("src.A_memorix.core.storage.metadata_schema")
        assert schema_mod_g1.SCHEMA_VERSION == 31

        # Simulate AstrBot PluginManager.reload(): purge data.plugins.astrbot_plugin_memorix.* and re-import
        for key in [k for k in list(sys.modules) if k.startswith("data.plugins.astrbot_plugin_memorix")]:
            sys.modules.pop(key, None)

        shims_g2 = importlib.import_module("data.plugins.astrbot_plugin_memorix.memorix.engine.host_shims")
        core_g2 = importlib.import_module("data.plugins.astrbot_plugin_memorix.memorix.core")
        assert core_g2 is not core_g1
        shims_g2.install_host_shims()

        finder_count_g2 = sum(1 for f in sys.meta_path if getattr(f, "_memorix_host_shim_finder", False))
        assert finder_count_g2 == 1, f"Hot-reload leaked _SrcMetaPathFinder instances: {finder_count_g2}"
        assert sys.modules["src.A_memorix.core"] is core_g2
        schema_mod_g2 = importlib.import_module("src.A_memorix.core.storage.metadata_schema")
        assert schema_mod_g2 is not schema_mod_g1

        # Also verify terminate() detaches active provider_bridge
        async def _check_terminate_bridge_cleanup() -> None:
            plugin, _ = create_test_plugin_instance(tmp_path)
            await plugin.initialize()
            assert llm_shim.get_active_provider_bridge() is plugin.provider_bridge
            await plugin.terminate()
            assert llm_shim.get_active_provider_bridge() is None

        asyncio.run(_check_terminate_bridge_cleanup())
    finally:
        for key in [
            k
            for k in list(sys.modules)
            if k == "data" or k.startswith("data.") or k == "src.A_memorix" or k.startswith("src.A_memorix.")
        ]:
            sys.modules.pop(key, None)
        sys.modules.update(saved_modules)
        install_host_shims()


def test_runtime_admin_rebuild_all_vectors_above_100_and_backfill_non_starvation(tmp_path: Path):
    """Verifies rebuild_all_vectors does not truncate at 100 or drop entities, and paragraph_backfill_once finds #101+."""
    async def _run() -> None:
        plugin, _ = create_test_plugin_instance(tmp_path)
        await plugin.initialize()
        try:
            scope_key = "aiocqhttp:group:vec_test"
            runtime = await plugin.runtime_manager.get_runtime(scope_key)
            store = runtime.context.metadata_store

            p_hashes = [store.add_paragraph(f"段落向量重建测试内容编号 {idx}", source="vec_src") for idx in range(135)]
            e_hashes = [store.add_entity(f"实体_{idx}", source_paragraph=p_hashes[idx]) for idx in range(3)]

            res = await plugin.admin_service.runtime_admin(scope_key=scope_key, action="rebuild_all_vectors")
            assert res["success"] is True
            assert res["paragraph_processed"] == 135
            assert res["paragraph_generated"] == 135
            assert res["entity_processed"] == 3
            assert res["entity_generated"] == 3

            adapter = await plugin.admin_service._bundle_kernel_adapter(
                scope_key, runtime.context, include_image_runtime=False
            )
            p_store = adapter._paragraph_store()
            g_store = adapter._graph_vector_store()
            for h in p_hashes:
                assert h in p_store
            for eh in e_hashes:
                assert adapter._graph_vector_id("entity", eh) in g_store

            # Add 20 more paragraphs directly to SQLite (unindexed in vector_store) after the first 135
            late_hashes = [store.add_paragraph(f"后续缺失向量段落 {idx}", source="vec_src") for idx in range(20)]
            backfill = await plugin.admin_service.runtime_admin(
                scope_key=scope_key,
                action="paragraph_backfill_once",
                limit=25,
            )
            assert backfill["success"] is True
            assert backfill["processed"] == 20
            assert backfill["generated"] == 20
            for lh in late_hashes:
                assert lh in p_store
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_scope_engine_manager_collision_isolation_and_dashboard_parity(tmp_path: Path):
    """Distinct scopes with identical sanitized stems must never share engines or directories."""
    async def _run() -> None:
        manager = ScopeEngineManager(
            base_data_dir=tmp_path,
            config={"embedding": {"provider": "builtin_hash"}},
        )
        try:
            eng_colon = await manager.get_engine("aiocqhttp:group:777")
            eng_under = await manager.get_engine("aiocqhttp_group_777")
            assert eng_colon is not eng_under
            assert eng_colon.data_dir != eng_under.data_dir
            assert manager.list_active_scopes() == ["aiocqhttp:group:777", "aiocqhttp_group_777"]
        finally:
            await manager.shutdown_all()

        # Verify MemorixPlugin._resolve_dashboard_webui_scope() aligns with ScopeRuntimeManager
        plugin_dir = tmp_path / "plugin_parity"
        plugin, _ = create_test_plugin_instance(plugin_dir)
        await plugin.initialize()
        try:
            eng = await plugin.get_scope_engine("aiocqhttp:group:999")
            resolved_scope = plugin._resolve_dashboard_webui_scope()
            assert resolved_scope == "aiocqhttp:group:999"
            rt = await plugin.runtime_manager.get_runtime(resolved_scope)
            assert Path(rt.context.data_dir).resolve() == Path(eng.data_dir).resolve()
        finally:
            await plugin.terminate()

    asyncio.run(_run())


def test_admin_dispatcher_enforces_allowed_actions_and_config_alias(tmp_path: Path):
    """AdminContractDispatcher must reject actions outside allowed_actions before calling handlers."""
    async def _run() -> None:
        dispatcher = build_default_admin_dispatcher()
        called = False

        async def _custom_handler(plugin, *, scope_key: str, action: str, **kwargs):
            nonlocal called
            called = True
            return {"success": True}

        dispatcher.register(
            AdminToolRegistration(
                name="memory_custom_ext",
                admin_method="",
                allowed_actions=frozenset({"ping"}),
                custom_handler=_custom_handler,
            )
        )
        blocked = await dispatcher.dispatch(
            object(),
            scope_key="s1",
            component_name="memory_custom_ext",
            action="drop_all",
        )
        assert blocked["success"] is False
        assert "drop_all" in blocked["error"]
        assert called is False

        ok = await dispatcher.dispatch(
            object(),
            scope_key="s1",
            component_name="memory_custom_ext",
            action="ping",
        )
        assert ok["success"] is True
        assert called is True

        # Verify config alias enable_extended_v2_admin_tools works alongside enable_v2_extended_tools
        dummy_plugin = types.SimpleNamespace(
            config={"integration": {"enable_extended_v2_admin_tools": True}}
        )
        tool_names = [t.name for t in build_memorix_tools(dummy_plugin)]
        assert len(tool_names) == 21
        assert "memory_bundle_admin" in tool_names
        assert "memory_image_admin" in tool_names

    asyncio.run(_run())


def test_bundle_import_corrupted_checksum_malformed_jsonl_and_zip_slip_rollback(tmp_path: Path):
    """Attacks .amembundle import with checksum tampering, zip-slip path traversal, and malformed JSONL."""
    async def _run() -> None:
        src_dir = tmp_path / "src_scope"
        dst_dir = tmp_path / "dst_scope"
        src_plugin, _ = create_test_plugin_instance(src_dir)
        dst_plugin, _ = create_test_plugin_instance(dst_dir)
        await src_plugin.initialize()
        await dst_plugin.initialize()
        try:
            scope = "aiocqhttp:group:bundle_sec"
            await src_plugin.ingest_service.ingest_text(
                scope_key=scope,
                external_id="gold_1",
                source_type="manual",
                text="黄金知识包核心机密：星辰大海计划启动于2026年。",
                respect_filter=False,
            )
            exported = await src_plugin.admin_service.bundle_admin(
                scope_key=scope,
                action="export",
                content_level="knowledge",
            )
            assert exported["success"] is True
            valid_bundle_path = Path(exported["path"])
            assert valid_bundle_path.is_file()

            # Seed destination scope with existing memory that must survive failed imports
            await dst_plugin.ingest_service.ingest_text(
                scope_key=scope,
                external_id="dst_orig_1",
                source_type="manual",
                text="目标作用域原有记忆：绝对不能被损坏包破坏。",
                respect_filter=False,
            )
            dst_rt = await dst_plugin.runtime_manager.get_runtime(scope)
            count_before = dst_rt.context.metadata_store.count_paragraphs()
            assert count_before == 1
            bundles_dir = Path(dst_rt.context.data_dir) / "imports" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)

            # Attack 1: Tampered knowledge.json content (SHA-256 mismatch against checksums.json)
            tampered_checksum_bundle = bundles_dir / "tampered_checksum.amembundle"
            with zipfile.ZipFile(valid_bundle_path, "r") as zin, zipfile.ZipFile(
                tampered_checksum_bundle, "w"
            ) as zout:
                for item in zin.infolist():
                    raw = zin.read(item.filename)
                    if item.filename == "knowledge.json":
                        raw = raw + b" "
                    zout.writestr(item, raw)

            res_tampered = await dst_plugin.admin_service.bundle_admin(
                scope_key=scope,
                action="import",
                path=tampered_checksum_bundle.name,
                mode="merge",
            )
            assert res_tampered["success"] is False
            assert "不存在" not in res_tampered["error"]
            assert dst_rt.context.metadata_store.count_paragraphs() == count_before

            # Attack 2: Zip-slip entry (`../escaped.txt`) inside archive
            zip_slip_bundle = bundles_dir / "zip_slip.amembundle"
            with zipfile.ZipFile(valid_bundle_path, "r") as zin, zipfile.ZipFile(zip_slip_bundle, "w") as zout:
                for item in zin.infolist():
                    zout.writestr(item, zin.read(item.filename))
                zout.writestr("../escaped.txt", b"pwned")

            res_slip = await dst_plugin.admin_service.bundle_admin(
                scope_key=scope,
                action="import",
                path=zip_slip_bundle.name,
                mode="merge",
            )
            assert res_slip["success"] is False
            assert "不存在" not in res_slip["error"]
            assert not (bundles_dir.parent / "escaped.txt").exists()
            assert dst_rt.context.metadata_store.count_paragraphs() == count_before

            # Attack 3: Valid SHA-256 in checksums.json, but malformed JSON in knowledge.json
            import hashlib as _hl

            malformed_json_bundle = bundles_dir / "malformed_knowledge.amembundle"
            with zipfile.ZipFile(valid_bundle_path, "r") as zin:
                entries = {item.filename: zin.read(item.filename) for item in zin.infolist()}
            entries["knowledge.json"] = b"{not_valid_json_at_all"
            non_checksum_files = {
                name: _hl.sha256(data).hexdigest()
                for name, data in sorted(entries.items())
                if name != "checksums.json"
            }
            entries["checksums.json"] = json.dumps(
                {"algorithm": "sha256", "files": non_checksum_files},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with zipfile.ZipFile(malformed_json_bundle, "w") as zout:
                for name, data in entries.items():
                    zout.writestr(name, data)

            res_malformed = await dst_plugin.admin_service.bundle_admin(
                scope_key=scope,
                action="import",
                path=malformed_json_bundle.name,
                mode="merge",
            )
            assert res_malformed["success"] is False
            assert "不存在" not in res_malformed["error"]
            assert dst_rt.context.metadata_store.count_paragraphs() == count_before

            # Sanity control: clean bundle copied into bundles_dir imports cleanly!
            clean_copy = bundles_dir / "clean_gold.amembundle"
            clean_copy.write_bytes(valid_bundle_path.read_bytes())
            res_clean = await dst_plugin.admin_service.bundle_admin(
                scope_key=scope,
                action="import",
                path=clean_copy.name,
                mode="merge",
            )
            assert res_clean["success"] is True, f"Clean bundle import failed: {res_clean}"
            assert dst_rt.context.metadata_store.count_paragraphs() == count_before + 1
        finally:
            await src_plugin.terminate()
            await dst_plugin.terminate()

    asyncio.run(_run())


def _make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _build_stdlib_png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + (b"\x28\x50\x78" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _make_png_chunk(b"IHDR", ihdr)
        + _make_png_chunk(b"IDAT", zlib.compress(raw_scanlines))
        + _make_png_chunk(b"IEND", b"")
    )


def _build_stdlib_jpeg_with_exif(width: int, height: int) -> bytes:
    app1_body = b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00"
    app1 = b"\xff\xe1" + struct.pack(">H", len(app1_body) + 2) + app1_body
    sof0_body = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_body) + 2) + sof0_body
    sos_body = b"\x01\x01\x00\x00\x3f\x00"
    sos = b"\xff\xda" + struct.pack(">H", len(sos_body) + 2) + sos_body
    return b"\xff\xd8" + app1 + sof0 + sos + b"\x7f\x00" + b"\xff\xd9"


def _build_stdlib_bmp(width: int, height: int) -> bytes:
    row_stride = ((width * 3 + 3) // 4) * 4
    pixel_bytes = b"\x40" * (row_stride * height)
    pixel_offset = 14 + 40
    file_size = pixel_offset + len(pixel_bytes)
    bmp_header = b"BM" + struct.pack("<III", file_size, 0, pixel_offset)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        len(pixel_bytes),
        2835,
        2835,
        0,
        0,
    )
    return bmp_header + dib_header + pixel_bytes


def _build_stdlib_webp_vp8l(width: int, height: int) -> bytes:
    bits = ((width - 1) & 0x3FFF) | (((height - 1) & 0x3FFF) << 14)
    vp8l_payload = bytes([0x2F]) + bits.to_bytes(4, "little") + b"\x00\x00\x00"
    riff_body = b"WEBP" + b"VP8L" + struct.pack("<I", len(vp8l_payload)) + vp8l_payload
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


def _build_stdlib_animated_webp(width: int, height: int, frame_count: int) -> bytes:
    flags = 0x02  # animation bit
    vp8x_payload = (
        bytes([flags, 0, 0, 0])
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    chunks = [b"VP8X" + struct.pack("<I", len(vp8x_payload)) + vp8x_payload]
    for _ in range(frame_count):
        anmf_payload = b"\x00" * 16
        chunks.append(b"ANMF" + struct.pack("<I", len(anmf_payload)) + anmf_payload)
    riff_body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


def test_image_inspector_pillow_parity_and_truncation_bomb_guards(tmp_path: Path):
    """Verifies ImageAssetStore.inspect and _inspect_without_pil across PNG/JPEG/BMP/WebP and malformed/bomb inputs."""
    store = ImageAssetStore(tmp_path / "assets", max_bytes=5 * 1024 * 1024, max_pixels=10_000)

    png_bytes = _build_stdlib_png(17, 13)
    jpeg_bytes = _build_stdlib_jpeg_with_exif(23, 19)
    bmp_bytes = _build_stdlib_bmp(15, 11)
    webp_bytes = _build_stdlib_webp_vp8l(21, 14)
    anim_webp_bytes = _build_stdlib_animated_webp(12, 10, 3)

    expected = [
        ("PNG", png_bytes, ("PNG", 17, 13, 1)),
        ("JPEG", jpeg_bytes, ("JPEG", 23, 19, 1)),
        ("BMP", bmp_bytes, ("BMP", 15, 11, 1)),
        ("WEBP", webp_bytes, ("WEBP", 21, 14, 1)),
        ("WEBP_ANIM", anim_webp_bytes, ("WEBP", 12, 10, 3)),
    ]
    for label, payload, exp_tuple in expected:
        assert _inspect_without_pil(payload, max_pixels=10_000) == exp_tuple, f"Mismatch on {label}"
        inspected = store.inspect(payload)
        assert (inspected.width, inspected.height, inspected.frame_count) == exp_tuple[1:]

    # 2. Truncated JPEG (cut before SOS/EOI), truncated BMP, truncated WebP must be rejected
    sos_pos = jpeg_bytes.find(b"\xff\xda")
    assert sos_pos > 0
    with pytest.raises(ValueError):
        _inspect_without_pil(jpeg_bytes[:sos_pos], max_pixels=10_000)

    with pytest.raises(ValueError):
        _inspect_without_pil(bmp_bytes[:30], max_pixels=10_000)

    with pytest.raises(ValueError):
        _inspect_without_pil(webp_bytes[:22], max_pixels=10_000)

    # 3. PNG IHDR pixel limit pre-decompression guard & over-decompression bomb guard
    ihdr_huge = struct.pack(">IIBBBBB", 500, 500, 8, 2, 0, 0, 0)  # 250,000 > max_pixels=10,000
    huge_png = (
        b"\x89PNG\r\n\x1a\n"
        + _make_png_chunk(b"IHDR", ihdr_huge)
        + _make_png_chunk(b"IDAT", zlib.compress(b"\x00" * 32))
        + _make_png_chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="像素超过限制"):
        _inspect_without_pil(huge_png, max_pixels=10_000)

    # Small 2x2 IHDR claiming tiny image, but IDAT expands to 200KB of zeros (zlib bomb spoof)
    ihdr_tiny = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    spoof_bomb_png = (
        b"\x89PNG\r\n\x1a\n"
        + _make_png_chunk(b"IHDR", ihdr_tiny)
        + _make_png_chunk(b"IDAT", zlib.compress(b"\x00" * 200_000))
        + _make_png_chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError):
        _inspect_without_pil(spoof_bomb_png, max_pixels=10_000)
