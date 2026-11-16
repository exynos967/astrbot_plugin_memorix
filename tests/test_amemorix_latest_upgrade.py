"""Comprehensive offline unit & E2E regression tests for MaiBot HEAD (5221c028e / Schema 31) upgrade."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from astrbot_plugin_memorix.memorix.amemorix.bootstrap import build_context
from astrbot_plugin_memorix.memorix.amemorix.services.query_service import QueryService
from astrbot_plugin_memorix.memorix.amemorix.settings import AppSettings
from astrbot_plugin_memorix.memorix.core.embedding.api_adapter import EmbeddingAPIAdapter
from astrbot_plugin_memorix.memorix.core.image.embedder import _encode_solid_rgb_png
from astrbot_plugin_memorix.memorix.core.runtime.services.background_task_service import (
    MemoryBackgroundTaskService,
)
from astrbot_plugin_memorix.memorix.core.runtime.services.base import KernelServiceBase
from astrbot_plugin_memorix.memorix.core.runtime.services.chat_migration_service import (
    ChatMemoryMigration,
)
from astrbot_plugin_memorix.memorix.core.runtime.services.feedback_correction_service import (
    MemoryFeedbackCorrectionService,
)
from astrbot_plugin_memorix.memorix.core.runtime.services.ingest_service import MemoryIngestService
from astrbot_plugin_memorix.memorix.core.runtime.services.runtime_lifecycle_service import (
    MemoryRuntimeLifecycleService,
)
from astrbot_plugin_memorix.memorix.core.runtime.services.search_hit_processing_service import (
    MemorySearchHitProcessingService,
)
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import SCHEMA_VERSION, MetadataStore
from astrbot_plugin_memorix.memorix.core.utils import async_work, web_import_manager
from astrbot_plugin_memorix.memorix.core.utils.fact_deduplication import FactDeduplicator
from astrbot_plugin_memorix.memorix.core.utils.model_routing import (
    LLMResult,
    resolve_fast_text_generation_model,
)
from astrbot_plugin_memorix.memorix.core.utils.person_profile_service import PersonProfileService
from astrbot_plugin_memorix.memorix.core.utils.retrieval_tuning_manager import RetrievalTuningManager
from astrbot_plugin_memorix.memorix.core.utils.runtime_self_check import (
    run_embedding_runtime_self_check,
)
from astrbot_plugin_memorix.memorix.core.utils.search_postprocess import apply_safe_content_dedup
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import (
    SummaryImporter,
    _normalize_summary_facts,
)
from astrbot_plugin_memorix.memorix.core.utils.web_import_manager import ImportTaskManager
from astrbot_plugin_memorix.memorix.engine.host_shims import (
    config_shim,
    embedding_shim,
    llm_shim,
    prompt_shim,
)
from astrbot_plugin_memorix.memorix.providers.astrbot_provider_bridge import AstrBotProviderBridge
from astrbot_plugin_memorix.tests.mocks.astrbot_mocks import (
    MockAstrContext,
    MockAstrMessageEvent,
    MockChatProvider,
    MockImage,
    MockPlain,
    MockProviderRequest,
    create_test_plugin_instance,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MONOREPO_MEMORIX_DIR = PLUGIN_ROOT.parent


def test_amemorix_standalone_and_host_ref_parity() -> None:
    """Verify standalone memorix/amemorix matches MaiBot/src/A_memorix + maibot_host_ref byte-for-byte."""
    maibot_root = MONOREPO_MEMORIX_DIR / "MaiBot"
    amemorix_root = MONOREPO_MEMORIX_DIR / "amemorix"
    if not (maibot_root / "src" / "A_memorix").is_dir() or not amemorix_root.is_dir():
        pytest.skip("Standalone MaiBot / amemorix sibling directories not present in environment")

    def _collect(base: Path, ignore_top: tuple[str, ...] = ()) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        for path in base.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(base).as_posix()
            if "__pycache__" in rel.split("/") or rel == ".git" or rel.startswith(".git/"):
                continue
            if any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in ignore_top):
                continue
            mapping[rel] = path
        return mapping

    upstream_files = _collect(maibot_root / "src" / "A_memorix")
    standalone_files = _collect(amemorix_root, ignore_top=("maibot_host_ref",))
    assert set(upstream_files) == set(standalone_files)
    for rel, src_path in upstream_files.items():
        assert src_path.read_bytes().replace(b"\r\n", b"\n") == standalone_files[rel].read_bytes().replace(
            b"\r\n", b"\n"
        ), f"Content mismatch in amemorix/{rel}"

    host_ref_dir = amemorix_root / "maibot_host_ref"
    ref_files = _collect(host_ref_dir, ignore_top=("EXTRACTION_MANIFEST.md",))
    assert len(ref_files) >= 220
    # Ensure stale flat prompts were removed after modular prompt migration (dc17a7db9 / 159616973)
    assert not any(rel.endswith("/a_memorix_chat_summary.prompt") and "/main/memory/" not in rel for rel in ref_files)
    for rel, ref_path in ref_files.items():
        upstream_ref = maibot_root / rel
        assert upstream_ref.is_file(), f"Stale file in maibot_host_ref missing from MaiBot HEAD: {rel}"
        assert ref_path.read_bytes().replace(b"\r\n", b"\n") == upstream_ref.read_bytes().replace(
            b"\r\n", b"\n"
        ), f"Content mismatch in maibot_host_ref/{rel}"


def test_image_memory_gating_unconfigured_and_require_embedding_407deaf90(tmp_path: Path) -> None:
    """Verify 407deaf90 upstream-aligned image memory gating: require_embedding=True by default."""
    from astrbot_plugin_memorix.memorix.amemorix.settings import DEFAULT_CONFIG

    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["image_memory"]["items"]["enabled"]["default"] is True
    assert schema["image_memory"]["items"]["require_embedding"]["default"] is True
    assert DEFAULT_CONFIG["image_memory"]["enabled"] is True
    assert DEFAULT_CONFIG["image_memory"]["require_embedding"] is True

    img_path = tmp_path / "sample_gate.png"
    img_path.write_bytes(_encode_solid_rgb_png((64, 128, 192)))

    async def _scenario() -> None:
        # Case A: Out-of-the-box default mode (require_embedding omitted -> defaults to True, no image embedder)
        # -> runtime auto-pauses image memory without mutating user's saved plugin.config, zero embedding errors!
        assert embedding_shim.is_image_embedding_configured(config={}) is False
        assert embedding_shim.is_image_memory_enabled(config={}) is False

        plugin_default, _ = create_test_plugin_instance(
            tmp_path / "default_strict_mode",
            custom_config={
                "storage": {"data_dir": str(tmp_path / "default_strict_data")},
                "image_memory": {"enabled": True},
            },
        )
        await plugin_default.initialize()
        try:
            assert plugin_default.config["image_memory"] == {"enabled": True}
            assert embedding_shim.is_image_embedding_configured(config=plugin_default.config) is False
            assert embedding_shim.is_image_memory_enabled(config=plugin_default.config) is False

            ev_strict = MockAstrMessageEvent(
                message_str="未配置图片嵌入时的默认图片消息",
                sender_id="u_strict",
                sender_name="严测试",
                group_id="g_img_strict",
                session_id="g_img_strict",
                components=[MockPlain("未配置图片嵌入时的默认图片消息"), MockImage(path=str(img_path))],
            )
            ev_strict.message_obj.message_id = "m_strict_1"
            scope_strict = plugin_default._resolve_scope(ev_strict)
            rt_strict = await plugin_default.runtime_manager.get_runtime(scope_strict)
            assert rt_strict.settings.get("image_memory.enabled") is False
            assert rt_strict.settings.get("image_memory.require_embedding") is True
            # Saved user config remains completely untouched!
            assert plugin_default.config["image_memory"] == {"enabled": True}

            cap_s = await plugin_default.message_ingestion.capture_event_once(ev_strict)
            assert cap_s.captured_images == []
            await plugin_default.message_ingestion._record_message_background(ev_strict, cap_s)

            req = MockProviderRequest(prompt="未配置图片嵌入时的默认图片消息")
            await plugin_default.memory_injection.inject(ev_strict, req)
            assert rt_strict.context.metadata_store.image_memory_stats()["asset_count"] == 0

            status_res = await plugin_default.admin_service.image_admin(scope_key=scope_strict, action="status")
            assert status_res["success"] is True
            assert status_res["enabled"] is False
            assert status_res["configured_enabled"] is True
            assert status_res["require_embedding"] is True
            assert status_res["embedding_configured"] is False
            assert status_res["paused_by_missing_embedding"] is True
            assert status_res["status"] == "disabled"
            assert status_res["reason"] == "missing_image_embedding"
            assert "未配置" in status_res["message"] and "暂停" in status_res["message"]

            assert plugin_default.webui_page_bridge is not None
            api_status = await plugin_default.webui_page_bridge.dispatch(
                method="GET",
                url=f"/api/images/status?_scope={scope_strict}",
                body=None,
            )
            assert api_status["success"] is True
            assert api_status["enabled"] is False
            assert api_status["configured_enabled"] is True
            assert api_status["require_embedding"] is True
            assert api_status["embedding_configured"] is False
            assert api_status["paused_by_missing_embedding"] is True
            assert api_status["reason"] == "missing_image_embedding"
            assert "未配置" in api_status["message"]

            cfg_resp = await plugin_default.webui_page_bridge.dispatch(
                method="GET",
                url=f"/api/config?_scope={scope_strict}",
                body=None,
            )
            assert cfg_resp["config"]["image_memory"]["enabled"] is True
            assert cfg_resp["config"]["image_memory"]["effective_enabled"] is False
        finally:
            await plugin_default.terminate()

        # Case B: Manually disabled mode (image_memory.enabled=False) -> distinct reason & message
        manual_off = embedding_shim.resolve_image_memory_status(
            config={"image_memory": {"enabled": False, "require_embedding": True}}
        )
        assert manual_off["enabled"] is False
        assert manual_off["configured_enabled"] is False
        assert manual_off["paused_by_missing_embedding"] is False
        assert manual_off["reason"] == "manually_disabled"
        assert "关闭" in manual_off["message"]

        # Case C: Explicit opt-in exact-hash mode (enabled=True, require_embedding=False, no image embedder)
        # -> exact-hash image memory works without triggering background embedding errors.
        plugin_exact, _ = create_test_plugin_instance(
            tmp_path / "exact_hash_mode",
            custom_config={
                "storage": {"data_dir": str(tmp_path / "exact_hash_data")},
                "image_memory": {"enabled": True, "require_embedding": False, "openapi": {"enabled": False}},
            },
        )
        try:
            assert embedding_shim.is_image_embedding_configured(config=plugin_exact.config) is False
            assert embedding_shim.is_image_memory_enabled(config=plugin_exact.config) is True

            ev = MockAstrMessageEvent(
                message_str="看看这张风景图",
                sender_id="u_img",
                sender_name="林测试",
                group_id="g_img_exact",
                session_id="g_img_exact",
                components=[MockPlain("看看这张风景图"), MockImage(path=str(img_path))],
            )
            ev.message_obj.message_id = "m_exact_1"
            scope_key = plugin_exact._resolve_scope(ev)
            cap = await plugin_exact.message_ingestion.capture_event_once(ev)
            await plugin_exact.message_ingestion._record_message_background(ev, cap)

            jobs_res = await plugin_exact.admin_service.image_admin(scope_key=scope_key, action="process_jobs")
            assert jobs_res["success"] is True
            assert jobs_res["status"] == "unconfigured"
            assert jobs_res["embedding_configured"] is False
            assert jobs_res["require_embedding"] is False

            # 0-hit search with a different PNG in exact_hash fallback must return success=True (not error)
            other_png = _encode_solid_rgb_png((200, 10, 10))
            search_miss = await plugin_exact.admin_service.image_admin(
                scope_key=scope_key,
                action="search",
                query_bytes=other_png,
                chat_id="g_img_exact",
            )
            assert search_miss["success"] is True
            assert search_miss["hits"] == []
        finally:
            await plugin_exact.terminate()

        # Case D: Custom image embedder or OpenAPI configured satisfies default require_embedding=True
        configured_cfg = {
            "image_memory": {
                "enabled": True,
                "openapi": {"enabled": True, "base_url": "https://img-emb.example/v1", "model": "clip-v1"},
            }
        }
        assert embedding_shim.is_image_embedding_configured(config=configured_cfg) is True
        assert embedding_shim.is_image_memory_enabled(config=configured_cfg) is True
        assert embedding_shim.resolve_image_memory_status(config=configured_cfg)["reason"] == "ready"

        with embedding_shim.use_image_embedder(lambda raw: [1.0, 0.0, 0.0, 0.0]):
            assert embedding_shim.is_image_embedding_configured(
                config={"image_memory": {"enabled": True}}
            ) is True
            assert embedding_shim.is_image_memory_enabled(
                config={"image_memory": {"enabled": True}}
            ) is True

    asyncio.run(_scenario())


def test_modular_prompts_meta_toml_and_prompt_shim_dc17a7db9_159616973(tmp_path: Path) -> None:
    """Verify recursive prompt discovery, .meta.toml metadata, duplicate stem detection, and transform_template."""
    locales = prompt_shim.discover_prompt_locales()
    assert {"zh-CN", "en-US", "ja-JP"} <= set(locales)

    for loc in ("zh-CN", "en-US", "ja-JP"):
        templates = prompt_shim.list_prompt_templates(locale=loc)
        assert {
            "a_memorix_chat_summary",
            "a_memorix_fact_deduplication",
            "memory_fuzzy_modify_plan",
            "heuristic_memory_impression",
            "planner_memory_group",
            "planner_memory_private",
        } <= set(templates)

    summary_info = prompt_shim.get_prompt_template_info("a_memorix_chat_summary", locale="zh-CN")
    assert summary_info is not None
    assert "main/memory/a_memorix_chat_summary.prompt" in summary_info.path.as_posix()
    assert summary_info.metadata.display_name != ""
    assert isinstance(summary_info.metadata.description, str) and summary_info.metadata.description != ""

    impression_info = prompt_shim.get_prompt_template_info("heuristic_memory_impression", locale="zh-CN")
    assert impression_info is not None
    assert "instructions/memory/heuristic_memory_impression.prompt" in impression_info.path.as_posix()
    assert impression_info.metadata.display_name != ""

    # Verify 00437ef2d update to zh-CN a_memorix_fact_deduplication.prompt
    dedup_tpl = prompt_shim.load_prompt_template("a_memorix_fact_deduplication", locale="zh-CN")
    assert dedup_tpl.template.startswith("判断同一归属范围内的事实是否重复。")

    # Renamed prompt helpers (9233e27f9)
    assert prompt_shim.normalize_prompt_name("retro_replyer") == "reply_group"
    assert prompt_shim.get_custom_prompt_storage_name("reply_group") == "retro_replyer"

    # transform_template hook on built-in & outdated custom fallback templates (db65ab0f0)
    transformed = prompt_shim.load_prompt(
        "a_memorix_fact_deduplication",
        locale="zh-CN",
        transform_template=lambda raw: raw.replace("只输出一个 JSON 对象", "【转换标记】只输出一个 JSON 对象"),
        payload='{"facts":[]}',
    )
    assert "【转换标记】只输出一个 JSON 对象" in transformed
    assert '{"facts":[]}' in transformed

    custom_root = tmp_path / "custom_prompts"
    (custom_root / "zh-CN").mkdir(parents=True, exist_ok=True)
    (custom_root / "zh-CN" / "a_memorix_fact_deduplication.prompt").write_text(
        "旧版占位符: {removed_legacy_arg}",
        encoding="utf-8",
    )
    fallback_transformed = prompt_shim.load_prompt(
        "a_memorix_fact_deduplication",
        locale="zh-CN",
        custom_prompts_root=custom_root,
        transform_template=lambda raw: "[FALLBACK_TRANSFORM]\n" + raw,
        payload='{"facts":[1]}',
    )
    assert fallback_transformed.startswith("[FALLBACK_TRANSFORM]\n")
    assert '{"facts":[1]}' in fallback_transformed

    # Duplicate stem across subdirectories in a locale must raise ValueError
    dup_root = tmp_path / "dup_prompts"
    (dup_root / "zh-CN" / "main").mkdir(parents=True, exist_ok=True)
    (dup_root / "zh-CN" / "instructions").mkdir(parents=True, exist_ok=True)
    (dup_root / "zh-CN" / "main" / "same_name.prompt").write_text("A", encoding="utf-8")
    (dup_root / "zh-CN" / "instructions" / "same_name.prompt").write_text("B", encoding="utf-8")
    with pytest.raises(ValueError, match="same_name"):
        prompt_shim.list_prompt_templates(locale="zh-CN", prompts_root=dup_root)


def test_fast_model_routing_and_astrbot_provider_bridge_f9674e17f(tmp_path: Path) -> None:
    """Verify lightweight tasks route to fast_model / fast_chat_provider_id and never heavy memory tasks."""
    heavy_only_api = SimpleNamespace(
        get_available_models=lambda: {
            "memory": config_shim.TaskConfig(task_name="memory", model="heavy-mem", model_list=["heavy-mem"]),
            "planner": config_shim.TaskConfig(task_name="planner", model="heavy-plan", model_list=["heavy-plan"]),
        }
    )
    with pytest.raises(RuntimeError, match="fast_model 或 utils"):
        resolve_fast_text_generation_model(heavy_only_api)

    async def _scenario() -> None:
        mock_ctx = MockAstrContext(data_dir=tmp_path)
        heavy_provider = MockChatProvider(
            provider_id="heavy-chat",
            default_response='{"summary":"","entities":[],"relations":[],"facts":[]}',
        )
        fast_provider = MockChatProvider(
            provider_id="fast-lite",
            response_generator=lambda prompt, _contexts: (
                '{"label":"negative","confidence":0.92,"reason":"明确纠错","target_queries":["杭州"]}'
                if "feedback" in str(prompt or "").lower() or "纠错" in str(prompt or "")
                else '{"query":"小明的居住城市是什么？","event_time":"2026-05-01"}'
            ),
        )
        mock_ctx.provider_manager.inst_map["heavy-chat"] = heavy_provider
        mock_ctx.provider_manager.inst_map["fast-lite"] = fast_provider

        bridge = AstrBotProviderBridge(
            astrbot_context=mock_ctx,
            chat_provider_id="heavy-chat",
            fast_chat_provider_id="fast-lite",
        )
        with llm_shim.use_provider_bridge(bridge):
            tasks = llm_shim.get_available_models()
            assert set(tasks) == {"memory", "utils", "fast_model"}
            assert tasks["fast_model"].model_list == ["fast-lite"]
            assert tasks["memory"].model_list == ["heavy-chat"]

            resolved_fast = resolve_fast_text_generation_model(llm_shim)
            assert resolved_fast.task_name == "fast_model"

            # 1) PersonProfileService classification model selects fast_model
            store = MetadataStore(tmp_path / "profile_fast_db")
            store.connect()
            try:
                profile_svc = PersonProfileService(metadata_store=store)
                cls_model = profile_svc._resolve_profile_classification_model()
                assert cls_model is not None
                assert cls_model.task_name == "fast_model"
            finally:
                store.close()

            # 2) FeedbackCorrectionService classifier uses fast_model -> fast-lite provider
            fb_kernel = SimpleNamespace(_feedback_classifier=None, _cfg=lambda _k, d=None: d)
            fb_service = MemoryFeedbackCorrectionService(fb_kernel)
            fb_res = await fb_service._classify_feedback(
                query_tool_id="qt_1",
                query_text="小明住在哪里",
                hit_briefs=[{"hash": "h1", "content": "小明住在北京"}],
                feedback_messages=["不对，我早就搬去上海了"],
            )
            assert fb_service._feedback_classifier.task_name == "fast_model"
            assert len(fast_provider.call_history) >= 1
            assert len(heavy_provider.call_history) == 0
            assert isinstance(fb_res, dict)

            # 3) RetrievalTuningManager NL query generation uses fast_model -> fast-lite provider
            before_fast_calls = len(fast_provider.call_history)
            tuning_plugin = SimpleNamespace(
                get_config=lambda k, d=None: d,
                metadata_store=None,
                graph_store=None,
                vector_store=None,
                embedding_manager=None,
            )
            tuning_mgr = RetrievalTuningManager(tuning_plugin)
            nl_text = await tuning_mgr._llm_call_text(
                "生成检索问句",
                request_type="A_Memorix.RetrievalTuning.NLCaseGen",
                use_fast_model=True,
            )
            assert "居住城市" in nl_text
            assert len(fast_provider.call_history) == before_fast_calls + 1
            assert len(heavy_provider.call_history) == 0

            # 4) Heavy memory task still routes to heavy-chat
            heavy_resp = await llm_shim.generate(
                llm_shim.LLMServiceRequest(task_name="memory", request_type="A_Memorix.ChatSummarization", prompt="总结")
            )
            assert heavy_resp.success is True
            assert len(heavy_provider.call_history) == 1

        # Fallback check: when fast_chat_provider_id is empty, resolve_fast_text_generation_model falls back to utils
        fallback_bridge = AstrBotProviderBridge(astrbot_context=mock_ctx, chat_provider_id="heavy-chat")
        with llm_shim.use_provider_bridge(fallback_bridge), config_shim.use_scope_config(
            {"provider": {"chat_provider_id": "heavy-chat", "fast_chat_provider_id": ""}}
        ):
            fallback_fast = resolve_fast_text_generation_model(llm_shim)
            assert fallback_fast.task_name == "utils"

    asyncio.run(_scenario())


def test_embedding_cache_lru_model_isolation_and_self_check_bypass_dfdba04b1_14316ff15(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify embedding.enable_cache wiring, LRU bounds, model isolation, defensive copies, and probe bypass."""
    monkeypatch.setattr(EmbeddingAPIAdapter, "_GLOBAL_TEXT_EMBEDDING_CACHE", {})

    # 1. Verify bootstrap honors embedding.enable_cache
    ctx_disabled = build_context(
        AppSettings(config={"storage": {"data_dir": str(tmp_path / "emb_off")}, "embedding": {"enable_cache": False}})
    )
    ctx_enabled = build_context(
        AppSettings(config={"storage": {"data_dir": str(tmp_path / "emb_on")}, "embedding": {"enable_cache": True}})
    )
    try:
        assert ctx_disabled.embedding_manager.enable_cache is False
        assert ctx_enabled.embedding_manager.enable_cache is True
    finally:
        asyncio.run(ctx_disabled.close())
        asyncio.run(ctx_enabled.close())

    # 2. Verify live vs cached requests, model isolation, NaN rejection, and self-check cache bypass
    async def _scenario() -> None:
        calls: list[tuple[str, Any]] = []
        fail_mode = {"nan": False}

        async def _fake_post(_self: Any, url: str, *, headers: Dict[str, str], json: Dict[str, Any]) -> Any:
            del headers
            model = str(json.get("model") or "")
            raw_in = json.get("input")
            items = raw_in if isinstance(raw_in, list) else [raw_in]
            calls.append((model, list(items)))
            rows = []
            for item in items:
                if fail_mode["nan"]:
                    rows.append({"embedding": [float("nan"), 0.0, 0.0, 0.0]})
                elif model == "model-A":
                    rows.append({"embedding": [1.0, 0.0, 0.0, float(len(str(item)))]})
                else:
                    rows.append({"embedding": [0.0, 1.0, 0.0, float(len(str(item)))]})
            return httpx.Response(200, json={"data": rows}, request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", new=_fake_post):
            adapter_a = EmbeddingAPIAdapter(
                default_dimension=4,
                enable_cache=True,
                base_url="https://emb-test.local/v1",
                openai_model="model-A",
                max_retries=1,
            )
            # Make _get_client raise AttributeError on SDK stub so raw httpx path is exercised deterministically
            monkeypatch.setattr(adapter_a, "_get_client", lambda: SimpleNamespace(embeddings=None))

            v1 = await adapter_a.encode("缓存命中测试", dimensions=4)
            count_after_first = len(calls)
            v1[0] = 999.0  # Mutating caller copy must NOT corrupt internal cache!

            v2 = await adapter_a.encode("缓存命中测试", dimensions=4)
            assert len(calls) == count_after_first
            assert np.isclose(v2[0], 1.0)

            # Different model identity must NOT read model-A's cached vector!
            adapter_b = EmbeddingAPIAdapter(
                default_dimension=4,
                enable_cache=True,
                base_url="https://emb-test.local/v1",
                openai_model="model-B",
                max_retries=1,
            )
            monkeypatch.setattr(adapter_b, "_get_client", lambda: SimpleNamespace(embeddings=None))
            vb = await adapter_b.encode("缓存命中测试", dimensions=4)
            assert np.isclose(vb[0], 0.0) and np.isclose(vb[1], 1.0)
            assert len(calls) > count_after_first

            # runtime_self_check MUST pass use_cache=False so live endpoint is probed!
            await adapter_a.encode("A_Memorix embedding runtime self-check", dimensions=4)
            calls_before_self_check = len(calls)
            check_report = await run_embedding_runtime_self_check(
                config={"embedding": {"dimension": 4}},
                vector_store=SimpleNamespace(dimension=4),
                embedding_manager=adapter_a,
                sample_text="A_Memorix embedding runtime self-check",
            )
            assert check_report["ok"] is True
            assert len(calls) > calls_before_self_check

            # NaN error vectors must never be cached
            fail_mode["nan"] = True
            nan_vec = await adapter_a.encode("临时故障文本", dimensions=4)
            assert np.isnan(nan_vec).all()
            fail_mode["nan"] = False
            recovered_vec = await adapter_a.encode("临时故障文本", dimensions=4)
            assert np.isfinite(recovered_vec).all()

    asyncio.run(_scenario())


def test_web_import_llm_total_attempt_limit_f02285b6f(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify web.import.llm_retry.max_attempts counts the initial attempt (total calls == max_attempts)."""
    for configured_max, expected_calls in ((1, 1), (3, 3)):
        plugin = SimpleNamespace(
            get_config=lambda key, default=None, _m=configured_max: {
                "storage.data_dir": str(tmp_path / f"imp_{_m}"),
                "web.import.timeout.llm_call_seconds": 5,
                "web.import.llm_retry.max_attempts": _m,
                "web.import.llm_retry.min_wait_seconds": 0.01,
                "web.import.llm_retry.max_wait_seconds": 0.01,
                "web.import.llm_retry.backoff_multiplier": 1.0,
            }.get(key, default)
        )
        manager = ImportTaskManager(plugin)
        retry_cfg = manager._llm_retry_config()
        assert retry_cfg["retries"] == configured_max - 1

        attempts = {"count": 0}

        async def _always_fail(_ctx: Any, _prompt: str, **_kwargs: Any) -> LLMResult:
            attempts["count"] += 1
            return LLMResult(success=False, text="", error="simulated transient 503")

        monkeypatch.setattr(web_import_manager, "generate_text", _always_fail)
        with pytest.raises(Exception):
            asyncio.run(manager._llm_call("extract entities", plugin))
        assert attempts["count"] == expected_calls


def test_cancellation_safe_to_thread_contextvar_and_bounded_shutdown_2563ded3f_a154cd551_270105d5e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify ContextVar propagation, repeated-cancel lock safety, and bounded shutdown resource retention."""

    async def _scenario() -> None:
        # 1) cancellation_safe_to_thread preserves scoped ContextVars AND holds locks across repeated cancel()
        started = threading.Event()
        release = threading.Event()
        scope_lock = asyncio.Lock()
        observed_in_thread: dict[str, Any] = {}

        def _sync_write() -> str:
            observed_in_thread["data_dir"] = str(config_shim.global_config.storage.data_dir)
            observed_in_thread["bridge"] = llm_shim.get_scoped_provider_bridge()
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("worker thread was not released")
            return "committed"

        sentinel_bridge = SimpleNamespace(enabled=True, chat_provider_id="scoped-thread-bridge")

        async def _locked_writer() -> str:
            with config_shim.use_scope_config({"storage": {"data_dir": "/scoped/thread/dir"}}), llm_shim.use_provider_bridge(
                sentinel_bridge
            ):
                async with scope_lock:
                    return await async_work.cancellation_safe_to_thread(_sync_write)

        writer_task = asyncio.create_task(_locked_writer(), name="scoped-writer")
        assert await asyncio.to_thread(started.wait, 2.0)
        assert observed_in_thread["data_dir"] == "/scoped/thread/dir"
        assert observed_in_thread["bridge"] is sentinel_bridge

        # Cancel twice while thread is still running -> lock MUST stay held!
        for _ in range(2):
            writer_task.cancel()
            await asyncio.sleep(0.01)
            assert not writer_task.done()
            assert scope_lock.locked()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writer_task
        assert not scope_lock.locked()

        # 2) Bounded shutdown on ScopeRuntimeManager & vector workers retains resources until write finishes
        monkeypatch.setattr(async_work, "TASK_STOP_TIMEOUT_SECONDS", 0.03)
        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "shutdown_data")}},
        )
        blocked_started = threading.Event()
        blocked_release = threading.Event()

        def _slow_io() -> None:
            blocked_started.set()
            blocked_release.wait(timeout=5)

        try:
            rt = await plugin.runtime_manager.get_runtime("aiocqhttp:group:shutdown_scope")
            stuck_worker = asyncio.create_task(
                async_work.cancellation_safe_to_thread(_slow_io),
                name="stuck-scope-worker",
            )
            rt.task_manager._workers.append(stuck_worker)
            assert await asyncio.to_thread(blocked_started.wait, 2.0)

            with pytest.raises(RuntimeError, match="stuck-scope-worker"):
                await plugin.runtime_manager.close_scope("aiocqhttp:group:shutdown_scope")

            # Scope runtime & metadata_store connection MUST remain open while write is still draining!
            assert "aiocqhttp:group:shutdown_scope" in plugin.runtime_manager._runtimes
            assert rt.context.metadata_store._conn is not None

            blocked_release.set()
            await asyncio.gather(stuck_worker, return_exceptions=True)
            await plugin.runtime_manager.close_scope("aiocqhttp:group:shutdown_scope")
            assert "aiocqhttp:group:shutdown_scope" not in plugin.runtime_manager._runtimes

            # 3) 270105d5e: Vector training worker protected during repeated shutdown
            vec_started, vec_release, vec_finished = threading.Event(), threading.Event(), threading.Event()
            vec_lock = asyncio.Lock()
            closed_flag: list[bool] = []

            def _warmup_index(*, force_train: bool = False) -> dict[str, bool]:
                assert force_train is True
                vec_started.set()
                try:
                    vec_release.wait(timeout=5)
                    return {"trained": True}
                finally:
                    vec_finished.set()

            kernel = SimpleNamespace(
                _background_lock=asyncio.Lock(),
                _background_tasks={},
                _background_stopping=False,
                _vector_persist_blocked_until_rebuild=False,
                _vector_rebuild_lock=vec_lock,
                _dual_vector_pools_enabled=lambda: False,
                _cfg=lambda _k, d: d,
                vector_store=SimpleNamespace(needs_training=lambda _t: True, warmup_index=_warmup_index),
                _save_vector_store=lambda _s: pytest.fail("cancelled training must not publish"),
                import_task_manager=None,
                retrieval_tuning_manager=None,
                _close_runtime=lambda: closed_flag.append(True),
            )
            bg_svc = MemoryBackgroundTaskService(kernel)
            kernel._stop_background_tasks = bg_svc._stop_background_tasks
            lifecycle = MemoryRuntimeLifecycleService(kernel)
            train_worker = asyncio.create_task(bg_svc._train_runtime_vector_indexes_once(), name="vec-train-worker")
            kernel._background_tasks["training"] = train_worker
            assert await asyncio.to_thread(vec_started.wait, 2.0)

            for _ in range(2):
                with pytest.raises(RuntimeError, match="vec-train-worker"):
                    await lifecycle._shutdown_serialized()
                assert vec_lock.locked()
                assert not closed_flag

            vec_release.set()
            with pytest.raises(asyncio.CancelledError):
                await train_worker
            assert vec_finished.is_set()
            assert not vec_lock.locked()
            await lifecycle._shutdown_serialized()
            assert closed_flag == [True]

            # Also verify ImportTaskManager shutdown keeps worker & temp_root on timeout
            imp_started, imp_release = threading.Event(), threading.Event()
            imp_cleaned: list[bool] = []
            imp_worker = asyncio.create_task(
                async_work.cancellation_safe_to_thread(lambda: (imp_started.set(), imp_release.wait(timeout=5))),
                name="imp-stuck-worker",
            )
            assert await asyncio.to_thread(imp_started.wait, 2.0)
            imp_mgr = object.__new__(ImportTaskManager)
            imp_mgr._lock = asyncio.Lock()
            imp_mgr._tasks = {}
            imp_mgr._queue = deque()
            imp_mgr._worker_task = imp_worker
            imp_mgr._cleanup_temp_root = lambda: imp_cleaned.append(True)
            with pytest.raises(RuntimeError, match="imp-stuck-worker"):
                await imp_mgr.shutdown()
            assert imp_mgr._worker_task is imp_worker
            assert not imp_cleaned
            imp_release.set()
            await asyncio.gather(imp_worker, return_exceptions=True)
            await imp_mgr.shutdown()
            assert imp_cleaned == [True]
        finally:
            blocked_release.set()
            await plugin.terminate()

    asyncio.run(_scenario())


def test_schema_v31_durable_image_compensation_batches_and_atomic_metadata_14316ff15_8ad00605d(
    tmp_path: Path,
) -> None:
    """Verify Schema 31 migration, persistent image compensation batch IDs, atomic metadata merge, and __delattr__."""
    assert SCHEMA_VERSION == 31
    store = MetadataStore(tmp_path / "schema31_db")
    store.connect()

    def _add_comp(occ_key: str, *, msg_id: str = "msg_batch_1") -> str:
        with store.transaction(immediate=True):
            asset = store.upsert_image_asset(
                content_hash=f"hash_{occ_key}",
                storage_key=f"{occ_key}.png",
                mime_type="image/png",
                byte_size=16,
                width=2,
                height=2,
            )
            occ = store.upsert_image_occurrence(
                asset_id=asset["asset_id"],
                external_ref=f"ref_{occ_key}",
                source_kind="chat",
                scope_type="chat",
                chat_id="chat_s31",
                message_id=msg_id,
                occurred_at=1700000100.0,
            )
            store.enqueue_image_description_compensation(
                occurrence_id=occ["occurrence_id"],
                description_hash=f"desc_{occ_key}",
            )
            return str(occ["occurrence_id"])

    try:
        # Simulate pre-v31 table missing summary_external_id -> reconnect must auto-migrate to v31!
        occ1 = _add_comp("img1")
        occ2 = _add_comp("img2")
        with store.transaction(immediate=True) as conn:
            conn.execute("ALTER TABLE image_description_compensations DROP COLUMN summary_external_id")
            conn.execute("DELETE FROM schema_migrations")
            conn.execute("INSERT INTO schema_migrations VALUES (29, 1.0)")
        store.close()
        store.connect()
        assert store.get_schema_version() == 31

        claimed_1 = store.claim_image_description_compensations(
            lease_token="lease_1",
            lease_seconds=30.0,
            max_attempts=5,
            limit=10,
        )
        assert len(claimed_1) == 2
        batch_ext_id = claimed_1[0]["summary_external_id"]
        assert batch_ext_id.startswith("image_description_compensation_batch:")
        assert claimed_1[1]["summary_external_id"] == batch_ext_id

        # Complete first, fail second, then enqueue a late third description for the same message
        assert store.complete_image_description_compensation(
            occurrence_id=occ1,
            description_hash="desc_img1",
            lease_token="lease_1",
            success=True,
        )
        assert store.complete_image_description_compensation(
            occurrence_id=occ2,
            description_hash="desc_img2",
            lease_token="lease_1",
            success=False,
            error="transient llm timeout",
        )
        occ3 = _add_comp("img3")

        claimed_2 = store.claim_image_description_compensations(
            lease_token="lease_2",
            lease_seconds=30.0,
            max_attempts=5,
            limit=10,
        )
        by_occ = {row["occurrence_id"]: row["summary_external_id"] for row in claimed_2}
        # Retried item MUST keep its durable batch ID; late arrival MUST get its own external ID!
        assert by_occ[occ2] == batch_ext_id
        assert by_occ[occ3] != batch_ext_id
        assert by_occ[occ3].startswith("image_description_compensation:")

        # 8ad00605d: Atomic paragraph metadata merge preserves concurrent/sequential disjoint keys & bindings
        p_hash = store.add_paragraph("原子合并测试段落", source="chat:s31", metadata={"chat_id": "c1", "tag_a": 1})
        merged_1 = store.update_paragraph_metadata(p_hash, {"chat_id": "c2", "tag_b": 2}, merge=True)
        assert merged_1 is not None
        assert set(merged_1["chat_ids"]) == {"c1", "c2"}
        assert merged_1["tag_a"] == 1 and merged_1["tag_b"] == 2

        # Soft-deleted paragraph refuses metadata update inside transaction
        store.mark_as_deleted([p_hash], "paragraph")
        assert store.update_paragraph_metadata(p_hash, {"tag_c": 3}, merge=True) is None

        # 14316ff15: KernelServiceBase.__delattr__ delegates symmetrically to _kernel
        kernel_ns = SimpleNamespace(temp_attr="override")
        svc_proxy = KernelServiceBase(kernel_ns)
        assert svc_proxy.temp_attr == "override"
        del svc_proxy.temp_attr
        assert not hasattr(kernel_ns, "temp_attr")
    finally:
        store.close()


def test_ingest_replay_validation_summary_facts_and_dedup_fixes_67dca5377_f6a327893_14489ac4d_64616e9ff(
    tmp_path: Path,
) -> None:
    """Verify external_id replay validation, summary atomic facts enforcement, superstring dedup, and recall budget."""
    # 1) 14489ac4d: apply_safe_content_dedup preserves longer superstring facts while deduping exact duplicates
    items = [
        SimpleNamespace(hash="h1", content="小明住在北京"),
        SimpleNamespace(hash="h2", content="小明住在北京，并且养了一只橘猫"),
        SimpleNamespace(hash="h3", content="小明住在北京"),
    ]
    deduped, dropped = apply_safe_content_dedup(items)
    assert [x.hash for x in deduped] == ["h1", "h2"]
    assert dropped == 1

    # 2) f6a327893: _normalize_summary_facts strict validation
    with pytest.raises(ValueError, match="数组"):
        _normalize_summary_facts("not-a-list")
    with pytest.raises(ValueError, match="对象"):
        _normalize_summary_facts(["bad-item"])
    with pytest.raises(ValueError, match="字符串"):
        _normalize_summary_facts([{"fact_id": 1, "text": "x"}])
    with pytest.raises(ValueError, match="不得重复"):
        _normalize_summary_facts([{"fact_id": "f1", "text": "A"}, {"fact_id": "f1", "text": "B"}])

    async def _scenario() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={"storage": {"data_dir": str(tmp_path / "consistency_data")}},
        )
        try:
            scope_key = "aiocqhttp:group:cons_1"
            rt = await plugin.runtime_manager.get_runtime(scope_key)
            store = rt.context.metadata_store

            # 3) 67dca5377: Serialize & validate external source replays on MemoryIngestService
            kernel_stub = SimpleNamespace(
                metadata_store=store,
                embedding_manager=rt.context.embedding_manager,
                vector_store=rt.context.vector_store,
                graph_store=rt.context.graph_store,
                paragraph_vector_service=rt.context.paragraph_vector_service,
                relation_write_service=rt.context.relation_write_service,
                relation_vectors_enabled=False,
                _is_chat_filtered=lambda **_kw: False,
                initialize=lambda: asyncio.sleep(0),
                _allow_metadata_only_write=lambda: True,
                _paragraph_store=lambda: None,
                _enqueue_paragraph_vector_backfill=lambda *_a, **_kw: None,
                _ensure_paragraph_vector=lambda *_a, **_kw: asyncio.sleep(0, result=(True, "")),
                _ensure_entity_vector=lambda *_a, **_kw: asyncio.sleep(0),
                _persist=lambda: None,
                _mark_person_active=lambda _p: None,
                _enqueue_person_profile_refresh=lambda *_a, **_kw: None,
                _cfg=lambda _k, d=None: d,
            )
            ingest_svc = MemoryIngestService(kernel_stub)
            first = await ingest_svc._ingest_text_raw(
                external_id="ext_note_1",
                source_type="note",
                text="阿尔法项目将于周五发布",
                chat_id="chat_A",
            )
            assert first["stored_ids"]
            # Idempotent identical replay succeeds
            replay_same = await ingest_svc._ingest_text_raw(
                external_id="ext_note_1",
                source_type="note",
                text="阿尔法项目将于周五发布",
                chat_id="chat_A",
            )
            assert replay_same["skipped_ids"]

            # Mismatched chat_id or text with same external_id MUST raise ValueError!
            with pytest.raises(ValueError, match="归属不一致"):
                await ingest_svc._ingest_text_raw(
                    external_id="ext_note_1",
                    source_type="note",
                    text="阿尔法项目将于周五发布",
                    chat_id="chat_B",
                )
            with pytest.raises(ValueError, match="正文不一致"):
                await ingest_svc._ingest_text_raw(
                    external_id="ext_note_1",
                    source_type="note",
                    text="完全不同的篡改正文",
                    chat_id="chat_A",
                )

            # 4) f6a327893 + 14316ff15: Non-empty summary without facts is rejected; empty summary omitting facts is accepted
            await plugin.ingest_service.ingest_message(
                scope_key=scope_key,
                session_id="s_facts",
                role="user",
                content="我周末准备去青岛看海",
                source="chat:aiocqhttp:s_facts",
                user_id="u_qd",
                group_id="s_facts",
                platform="aiocqhttp",
                sender_name="周明",
            )
            sum_mode = {"kind": "missing_facts"}
            mock_ctx._default_provider.response_generator = lambda _p, _c: (
                json.dumps({"summary": "周明周末准备去青岛看海。", "entities": ["周明"], "relations": [], "facts": []})
                if sum_mode["kind"] == "missing_facts"
                else json.dumps({"summary": "", "entities": [], "relations": []})
            )
            bad_sum = await plugin.summary_service.summarize_session(
                scope_key=scope_key,
                session_id="s_facts",
                source="chat_summary:s_facts",
            )
            assert bad_sum["success"] is False
            assert "非空 facts" in str(bad_sum.get("message") or bad_sum.get("detail") or "")

            sum_mode["kind"] = "empty_omitted_facts"
            with rt.context.scope_shims():
                importer = SummaryImporter(
                    vector_store=rt.context.vector_store,
                    graph_store=rt.context.graph_store,
                    metadata_store=store,
                    embedding_manager=rt.context.embedding_manager,
                    plugin_config={"plugin_instance": rt.context, **rt.context.config},
                )
                ok_empty = await importer.import_from_stream(
                    "s_facts",
                    metadata={"external_id": "empty_no_facts_key", "trigger_message_count": 1},
                )
            assert ok_empty.success is True
            assert ok_empty.skipped is True

            # 5) 64616e9ff: FactDeduplicator interleaves vector candidates when keyword candidates fill 8 slots
            seen_dedup_payloads: list[dict[str, Any]] = []

            class _CaptureDedupBridge:
                enabled = True
                chat_provider_id = "dedup-cap"

                async def generate_text(self, prompt: str, **_kw: Any) -> str:
                    payload_json = json.loads(prompt.split("输入：\n", 1)[1].strip())
                    seen_dedup_payloads.append(payload_json)
                    return json.dumps(
                        {
                            "decisions": [
                                {"fact_id": "f_new", "action": "keep", "target_id": None, "candidate_ids": []}
                            ],
                            "relation_support": {},
                        }
                    )

            for idx in range(8):
                p_kw = store.add_paragraph(f"候选关键词重叠记录 {idx} 杭州工程师", source="chat_summary:s_budget", metadata={"chat_id": "s_budget"})
                store.upsert_fact_claim(
                    scope_type="chat",
                    scope_id="s_budget",
                    fact_key=f"kw_{idx}",
                    value_text=f"杭州工程师条目 {idx}",
                    evidence_type="paragraph",
                    evidence_id=p_kw,
                )
            p_vec = store.add_paragraph(
                "语义召回专属段落：在钱塘江畔从事后端研发",
                source="chat_summary:s_budget",
                metadata={"chat_id": "s_budget"},
            )
            fake_vectors = SimpleNamespace(search=lambda _vec, k=8, allowed_ids=None: ([p_vec], [0.99]))
            with llm_shim.use_provider_bridge(_CaptureDedupBridge()):
                dedup = FactDeduplicator(store, rt.context.embedding_manager, fake_vectors)
                await dedup.plan(
                    scope_type="chat",
                    scope_id="s_budget",
                    facts=[{"fact_id": "f_new", "text": "杭州工程师研发工作"}],
                    source={"platform": "qq", "chat_id": "s_budget", "message_ids": ["m1"], "observed_at": 1700000200.0},
                    relations=[],
                )
            assert seen_dedup_payloads
            cands_for_new = seen_dedup_payloads[-1]["candidates"]["f_new"]
            assert len(cands_for_new) <= 8
            assert any("钱塘江畔从事后端研发" in c["text"] for c in cands_for_new), (
                f"Vector candidate was starved by keyword candidates: {cands_for_new}"
            )
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_chat_memory_migration_core_and_webui_e2e_fc4c9da12(tmp_path: Path) -> None:
    """Verify ChatMemoryMigration ledger + WebUI /v1/chat-migrations & /api/chat-migrations E2E + 1-click recover."""

    async def _scenario() -> None:
        plugin, _ = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "mig_e2e_data")},
                "image_memory": {"enabled": True, "require_embedding": False},
            },
        )
        scope_key = "aiocqhttp:group:mig_scope"
        try:
            rt = await plugin.runtime_manager.get_runtime(scope_key)
            store = rt.context.metadata_store

            # Seed source chat ("chat_old") with paragraph, fact_claim, episode, image + observation, and transcript
            src_para = store.add_paragraph(
                "旧群核心记忆：每周五晚八点举行代码评审会",
                source="chat_summary:chat_old",
                metadata={"scope_type": "chat", "chat_id": "chat_old", "chat_ids": ["chat_old"], "group_name": "研发旧群"},
            )
            await rt.context.paragraph_vector_service.ensure_paragraph_vector(
                src_para, "旧群核心记忆：每周五晚八点举行代码评审会"
            )
            store.upsert_fact_claim(
                scope_type="chat",
                scope_id="chat_old",
                fact_key="weekly_review",
                value_text="每周五晚八点代码评审",
                evidence_type="paragraph",
                evidence_id=src_para,
            )
            with store.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO episodes (episode_id, source, title, summary, created_at, updated_at) VALUES (?, ?, ?, ?, 10, 10)",
                    ("ep_old_1", "chat_summary:chat_old", "周五评审", "旧群约定每周五评审"),
                )
                conn.execute(
                    "INSERT INTO episode_paragraphs (episode_id, paragraph_hash, position) VALUES (?, ?, 0)",
                    ("ep_old_1", src_para),
                )
            store.upsert_transcript_session(
                session_id="chat_old",
                source="chat:aiocqhttp:chat_old",
                metadata={"group_id": "10001", "group_name": "研发旧群", "platform": "aiocqhttp"},
            )
            store.append_transcript_messages(
                session_id="chat_old",
                messages=[{"role": "user", "content": "大家记得周五晚八点代码评审！", "created_at": 100.0}],
            )

            img_res = await plugin.admin_service.image_admin(
                scope_key=scope_key,
                action="ingest",
                image_bytes=_encode_solid_rgb_png((30, 160, 90)),
                source_kind="chat",
                external_ref="chat:chat_old:msg_1:0",
                scope_type="chat",
                chat_id="chat_old",
                message_id="msg_1",
                component_path="0",
                occurred_at=105.0,
            )
            assert img_res["success"] is True
            await plugin.admin_service.image_admin(
                scope_key=scope_key,
                action="describe",
                content_hash=img_res["content_hash"],
                text="[图片：架构白板草图]",
            )

            # Seed target chat ("chat_new") with an existing paragraph + transcript to test overwrite confirmation & archive hiding
            dst_para = store.add_paragraph(
                "新群旧测试记录：即将被迁移覆盖归档的测试句子",
                source="chat_summary:chat_new",
                metadata={"scope_type": "chat", "chat_id": "chat_new", "chat_ids": ["chat_new"], "group_name": "研发新群"},
            )
            await rt.context.paragraph_vector_service.ensure_paragraph_vector(
                dst_para, "新群旧测试记录：即将被迁移覆盖归档的测试句子"
            )
            store.upsert_transcript_session(
                session_id="chat_new",
                source="chat:aiocqhttp:chat_new",
                metadata={"group_id": "20002", "group_name": "研发新群", "platform": "aiocqhttp"},
            )
            store.append_transcript_messages(
                session_id="chat_new",
                messages=[{"role": "user", "content": "新群建群测试消息", "created_at": 110.0}],
            )

            # Also seed an uncaptioned image-only chat ("chat_img_only") to verify list_chats discovers it
            img_only_res = await plugin.admin_service.image_admin(
                scope_key=scope_key,
                action="ingest",
                image_bytes=_encode_solid_rgb_png((90, 45, 180)),
                source_kind="chat",
                external_ref="chat:chat_img_only:msg_9:0",
                scope_type="chat",
                chat_id="chat_img_only",
                message_id="msg_9",
                component_path="0",
                occurred_at=108.0,
            )
            assert img_only_res["success"] is True

            # Build embedded FastAPI WebUI app for this scope and test real HTTP routes!
            webui_bundle = await plugin.webui_page_bridge._get_app(scope_key)
            client = webui_bundle.client

            # 1) GET /v1/chat-migrations/chats lists all chats with accurate paragraph & image counts
            chats_resp = await client.get("/v1/chat-migrations/chats")
            assert chats_resp.status_code == 200
            chats_by_id = {c["chat_id"]: c for c in chats_resp.json()["items"]}
            assert {"chat_old", "chat_new", "chat_img_only"} <= set(chats_by_id)
            assert chats_by_id["chat_img_only"]["image_occurrence_count"] == 1
            assert chats_by_id["chat_old"]["paragraph_count"] == 1
            assert chats_by_id["chat_old"]["fact_claim_count"] == 1
            assert chats_by_id["chat_old"]["episode_count"] == 1
            assert chats_by_id["chat_old"]["image_observation_count"] >= 1
            assert chats_by_id["chat_old"]["transcript_message_count"] == 1

            # 2) POST /v1/chat-migrations/preview
            preview_resp = await client.post(
                "/v1/chat-migrations/preview",
                json={
                    "source_chat_id": "chat_old",
                    "target_chat_id": "chat_new",
                    "migrate_transcript": True,
                    "note": "群升级迁移 E2E",
                },
            )
            assert preview_resp.status_code == 200
            preview_body = preview_resp.json()
            assert preview_body["success"] is True
            job = preview_body["job"]
            job_id = job["job_id"]
            assert job["target_has_existing_data"] is True
            assert job["source_counts"]["paragraphs"] == 1
            assert job["target_counts"]["paragraphs"] == 1

            # 3) Execute WITHOUT confirmation when target has existing data -> blocked with requires_confirmation=True!
            unconfirmed = await client.post(f"/v1/chat-migrations/{job_id}/execute", json={"confirmed": False})
            assert unconfirmed.status_code == 200
            assert unconfirmed.json()["success"] is False
            assert unconfirmed.json()["requires_confirmation"] is True

            # 4) Execute WITH confirmed=True -> atomic apply succeeds!
            exec_resp = await client.post(f"/v1/chat-migrations/{job_id}/execute", json={"confirmed": True})
            assert exec_resp.status_code == 200
            exec_body = exec_resp.json()
            assert exec_body["success"] is True, exec_body
            assert exec_body["job"]["status"] == "completed"
            assert exec_body["job"]["can_recover"] is True

            # Verify migrated scopes, archived target exclusion from QueryService & hit processing, and transcript move
            assert MemorySearchHitProcessingService._paragraph_scope_identity(store.get_paragraph(src_para)) == (
                "chat",
                {"chat_old", "chat_new"},
            )
            assert MemorySearchHitProcessingService._paragraph_scope_identity(store.get_paragraph(dst_para)) == (
                "unknown",
                set(),
            )
            q_service = QueryService(rt.context)
            search_res = await q_service.search(query="新群旧测试记录：即将被迁移覆盖归档的测试句子", top_k=10)
            assert not any(r.get("hash") == dst_para for r in search_res["results"])

            new_msgs = store.get_transcript_messages("chat_new", limit=20)
            assert len(new_msgs) == 1
            assert "周五晚八点代码评审" in new_msgs[0]["content"]

            # Post-migration list_chats MUST show chat_new with paragraph_count == 1!
            chats_after = {c["chat_id"]: c for c in (await client.get("/v1/chat-migrations/chats")).json()["items"]}
            assert chats_after["chat_new"]["paragraph_count"] == 1
            assert chats_after["chat_new"]["fact_claim_count"] == 1
            assert chats_after["chat_new"]["episode_count"] == 1

            # 5) Compat GET /api/chat-migrations/{job_id} and 1-click POST /v1/chat-migrations/{job_id}/recover
            compat_get = await client.get(f"/api/chat-migrations/{job_id}")
            assert compat_get.status_code == 200
            assert compat_get.json()["job"]["status"] == "completed"

            recover_resp = await client.post(f"/v1/chat-migrations/{job_id}/recover", json={})
            assert recover_resp.status_code == 200
            recover_body = recover_resp.json()
            assert recover_body["success"] is True, recover_body
            assert recover_body["job"]["status"] == "restored"

            # Verify lossless restoration of both ChatMemoryMigration tables and transcripts!
            assert MemorySearchHitProcessingService._paragraph_scope_identity(store.get_paragraph(src_para)) == (
                "chat",
                {"chat_old"},
            )
            assert MemorySearchHitProcessingService._paragraph_scope_identity(store.get_paragraph(dst_para)) == (
                "chat",
                {"chat_new"},
            )
            restored_old_msgs = store.get_transcript_messages("chat_old", limit=20)
            restored_new_msgs = store.get_transcript_messages("chat_new", limit=20)
            assert [m["content"] for m in restored_old_msgs] == ["大家记得周五晚八点代码评审！"]
            assert [m["content"] for m in restored_new_msgs] == ["新群建群测试消息"]
            restored_search = await q_service.search(query="新群旧测试记录：即将被迁移覆盖归档的测试句子", top_k=10)
            assert any(r.get("hash") == dst_para for r in restored_search["results"])

            # 6) Verify AdminService.dispatch_contract("memory_chat_migration", ...) works too
            snap_contract = await plugin.admin_service.dispatch_contract(
                scope_key=scope_key,
                component_name="memory_chat_migration",
                action="snapshot",
                payload={"source": "chat_old", "target": "chat_new"},
            )
            assert snap_contract["success"] is True
            assert snap_contract["source"]["paragraphs"] == 1
            assert snap_contract["target"]["paragraphs"] == 1

            # 7) Verify ChatMemoryMigration refuses restore if post-migration scope metadata edits happened
            mig = ChatMemoryMigration(store)
            s2 = mig.snapshot("chat_old", "chat_new")
            mig.apply("manual_job_mutated", "chat_old", "chat_new", s2["fingerprint"])
            with store.transaction(immediate=True) as conn:
                conn.execute("UPDATE paragraphs SET metadata = '{}' WHERE hash = ?", (src_para,))
            with pytest.raises(ValueError, match="被修改"):
                mig.restore("manual_job_mutated")
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())
