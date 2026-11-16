"""Round 3 offline regression tests for host shims (embedding/prompt) and v2.0 summarization alignment."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from astrbot_plugin_memorix.memorix.core.image.component_paths import build_chat_external_ref
from astrbot_plugin_memorix.memorix.core.image.embedder import HostImageEmbedder, _encode_solid_rgb_png
from astrbot_plugin_memorix.memorix.core.runtime.services.correction_admin_service import (
    MemoryCorrectionAdminService,
)
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.fact_deduplication import FactDeduplicator
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import SummaryImporter
from astrbot_plugin_memorix.memorix.engine.host_shims import (
    config_shim,
    embedding_shim,
    llm_shim,
    prompt_shim,
)
from astrbot_plugin_memorix.tests.mocks.astrbot_mocks import (
    MockAstrMessageEvent,
    MockImage,
    MockPlain,
    create_test_plugin_instance,
)


def test_embedding_shim_without_use_image_embedder_unconfigured_and_scoped_openapi() -> None:
    """Verify EmbeddingServiceClient & HostImageEmbedder on production path WITHOUT use_image_embedder."""
    assert embedding_shim.get_active_image_embedder() is None

    async def _scenario() -> None:
        # 1. Unconfigured scope: embed_text falls back to deterministic vector; embed_image raises RuntimeError (NOT AttributeError)
        unconfigured = {
            "embedding": {"dimension": 16, "enabled": False, "openapi": {"enabled": False}},
            "image_memory": {"enabled": True, "openapi": {"enabled": False}},
        }
        with config_shim.use_scope_config(unconfigured):
            eff = config_shim.get_effective_config_dict()
            assert eff["embedding"]["dimension"] == 16

            client = embedding_shim.EmbeddingServiceClient(task_name="embedding")
            text_res = await client.embed_text("离线确定性文本向量测试")
            assert len(text_res.embedding) == 16
            assert np.isclose(np.linalg.norm(text_res.embedding), 1.0, atol=1e-5)

            with pytest.raises(RuntimeError, match="未配置多模态图片向量嵌入模型"):
                await client.embed_image(
                    _encode_solid_rgb_png((10, 20, 30)),
                    mime_type="image/png",
                    preprocess_version="identity_v1",
                )

            probe_report = await HostImageEmbedder(probe_retry_seconds=0.0).probe()
            assert probe_report["available"] is False
            assert probe_report["reason"] == "RuntimeError"

            # Boundary validations on embed_image and embed_text
            with pytest.raises(ValueError, match="图片嵌入输入不能为空"):
                await client.embed_image(b"", mime_type="image/png", preprocess_version="identity_v1")
            with pytest.raises(ValueError, match="image/"):
                await client.embed_image(b"abc", mime_type="application/pdf", preprocess_version="identity_v1")

        # Missing base_url/model when enabled=True must raise explicit RuntimeError
        misconfigured = {
            "embedding": {"enabled": True, "openapi": {"enabled": True, "base_url": "", "model": ""}},
            "image_memory": {"enabled": True, "openapi": {"enabled": True, "base_url": "", "model": ""}},
        }
        with config_shim.use_scope_config(misconfigured):
            client = embedding_shim.EmbeddingServiceClient()
            with pytest.raises(RuntimeError, match="base_url"):
                await client.embed_text("hello")
            with pytest.raises(RuntimeError, match="base_url"):
                await client.embed_image(
                    _encode_solid_rgb_png((1, 2, 3)),
                    mime_type="image/png",
                    preprocess_version="identity_v1",
                )

        # 2. Configured openapi under use_scope_config WITHOUT use_image_embedder (mocked httpx)
        captured_requests: List[Dict[str, Any]] = []

        async def _fake_post(self_client: Any, url: str, *, headers: Dict[str, str], json: Dict[str, Any]) -> Any:
            del self_client
            captured_requests.append({"url": url, "headers": dict(headers), "json": json})
            raw_input = json["input"]
            if isinstance(raw_input, str):
                vec = [0.6, 0.8, 0.0, 0.0]
            else:
                data_url = raw_input[0]["image_url"]["url"]
                # Produce deterministic distinct vectors for distinct PNG payloads so HostImageEmbedder.probe() succeeds
                seed = sum(data_url.encode("utf-8")) % 97
                angle = (seed + 1) * 0.03
                vec = [float(np.cos(angle)), float(np.sin(angle)), 0.25, 0.1]
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"data": [{"embedding": vec}]}, request=request)

        scoped_openapi_cfg = {
            "embedding": {
                "dimension": 4,
                "openapi": {
                    "enabled": True,
                    "base_url": "https://scoped-text-embed.local/v1",
                    "api_key": "sk-text-secret",
                    "model": "text-emb-v2",
                },
            },
            "image_memory": {
                "enabled": True,
                "openapi": {
                    "enabled": True,
                    "base_url": "https://scoped-img-embed.local/v1/",
                    "api_key": "sk-img-secret",
                    "model": "multimodal-emb-v2",
                },
            },
        }

        with patch.object(httpx.AsyncClient, "post", new=_fake_post):
            with config_shim.use_scope_config(scoped_openapi_cfg):
                client = embedding_shim.EmbeddingServiceClient(task_name="image_embedding")
                txt = await client.embed_text("作用域级 OpenAPI 文本向量")
                assert txt.model_name == "text-emb-v2"
                assert len(txt.embedding) == 4
                assert captured_requests[-1]["url"] == "https://scoped-text-embed.local/v1/embeddings"
                assert captured_requests[-1]["headers"]["Authorization"] == "Bearer sk-text-secret"

                embedder = HostImageEmbedder(probe_retry_seconds=0.0)
                probe = await embedder.probe()
                assert probe["available"] is True, probe
                assert probe["status"] == "ready"
                assert probe["dimension"] == 4

                embedded = await embedder.embed(_encode_solid_rgb_png((90, 120, 200)), mime_type="image/png")
                assert embedded["embedding"].shape == (4,)
                assert np.isclose(np.linalg.norm(embedded["embedding"]), 1.0, atol=1e-5)
                assert captured_requests[-1]["url"] == "https://scoped-img-embed.local/v1/embeddings"
                assert captured_requests[-1]["headers"]["Authorization"] == "Bearer sk-img-secret"
                assert captured_requests[-1]["json"]["model"] == "multimodal-emb-v2"
                assert captured_requests[-1]["json"]["input"][0]["image_url"]["url"].startswith(
                    "data:image/png;base64,"
                )

    asyncio.run(_scenario())


def test_prompt_shim_contract_across_all_templates_and_service_callers(tmp_path: Path) -> None:
    """Verify prompt_shim matches MaiBot prompt_i18n across all 4 bundled templates and service callers."""
    expected_placeholders = {
        "a_memorix_chat_summary": [
            "bot_name",
            "personality_context",
            "previous_summary_context",
            "chat_history",
            "image_evidence_catalog",
            "identity_catalog",
        ],
        "a_memorix_fact_deduplication": ["payload"],
        "memory_fuzzy_modify_plan": ["request_payload"],
        "heuristic_memory_impression": ["chat_identity", "message_window"],
    }

    for name, expected_fields in expected_placeholders.items():
        tpl = prompt_shim.load_prompt_template(name, locale="zh-CN")
        assert isinstance(tpl, prompt_shim.ResolvedPromptTemplate)
        assert tpl.path.is_file()
        assert tpl.customized is False
        assert tpl.locale == "zh-CN"
        assert prompt_shim.extract_prompt_placeholders(tpl.template) == set(expected_fields)
        # Calling load_prompt with locale only (no format kwargs) returns the raw template
        raw = prompt_shim.load_prompt(name, locale="zh-CN")
        assert raw == tpl.template

    # Interpolating each template replaces all placeholders and unescapes JSON {{ / }} braces
    summary_rendered = prompt_shim.load_prompt(
        "a_memorix_chat_summary",
        locale="zh-CN",
        bot_name="麦麦",
        personality_context="你的性格设定是：温柔严谨",
        previous_summary_context="- 小明住在北京",
        chat_history='日期：2026-05-01\n[10:00:00] [U1]："我下周搬去杭州"',
        image_evidence_catalog="无",
        identity_catalog='[{"ref":"[U1]","name":"小明","aliases":["小明"],"is_bot":false}]',
    )
    assert "麦麦。你的性格设定是：温柔严谨" in summary_rendered
    assert "我下周搬去杭州" in summary_rendered
    assert '{"summary":' in summary_rendered
    assert '{{"summary":' not in summary_rendered
    assert "{chat_history}" not in summary_rendered

    dedup_rendered = prompt_shim.load_prompt(
        "a_memorix_fact_deduplication",
        payload='{"facts":[{"fact_id":"f1","text":"小明搬去杭州"}]}',
    )
    assert '{"facts":[{"fact_id":"f1","text":"小明搬去杭州"}]}' in dedup_rendered
    assert "{payload}" not in dedup_rendered

    fuzzy_rendered = prompt_shim.load_prompt(
        "memory_fuzzy_modify_plan",
        request_payload='{"request_text":"把住址改成杭州"}',
    )
    assert '{"request_text":"把住址改成杭州"}' in fuzzy_rendered
    assert "{request_payload}" not in fuzzy_rendered
    assert '"confidence": 0.0' in fuzzy_rendered
    assert "{{" not in fuzzy_rendered

    impression_rendered = prompt_shim.load_prompt(
        "heuristic_memory_impression",
        chat_identity="测试群(g1)",
        message_window="小明: 周末去西湖徒步",
    )
    assert "测试群(g1)" in impression_rendered
    assert "周末去西湖徒步" in impression_rendered
    assert "{chat_identity}" not in impression_rendered

    # Missing placeholder raises KeyError; path traversal raises ValueError; custom root & cache revision work
    with pytest.raises(KeyError):
        prompt_shim.load_prompt("a_memorix_fact_deduplication", wrong_key="x")
    with pytest.raises(ValueError):
        prompt_shim.load_prompt_template("../outside")

    custom_dir = tmp_path / "custom_prompts"
    (custom_dir / "zh-CN").mkdir(parents=True, exist_ok=True)
    (custom_dir / "zh-CN" / "heuristic_memory_impression.prompt").write_text(
        "自定义印象：{chat_identity} | {message_window}",
        encoding="utf-8",
    )
    rev_before = prompt_shim.get_prompt_cache_revision()
    prompt_shim.clear_prompt_cache()
    assert prompt_shim.get_prompt_cache_revision() == rev_before + 1
    custom_tpl = prompt_shim.load_prompt_template("heuristic_memory_impression", custom_prompts_root=custom_dir)
    assert custom_tpl.customized is True
    assert custom_tpl.locale == "zh-CN"
    assert (
        prompt_shim.load_prompt(
            "heuristic_memory_impression",
            custom_prompts_root=custom_dir,
            chat_identity="群A",
            message_window="窗口B",
        )
        == "自定义印象：群A | 窗口B"
    )

    legacy_dir = tmp_path / "legacy_custom"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "heuristic_memory_impression.prompt").write_text(
        "旧版自定义：{chat_identity}-{message_window}",
        encoding="utf-8",
    )
    legacy_tpl = prompt_shim.load_prompt_template(
        "heuristic_memory_impression",
        custom_prompts_root=legacy_dir,
        include_legacy_custom=True,
    )
    assert legacy_tpl.customized is True

    # Verify FactDeduplicator & CorrectionAdminService actually send interpolated JSON payloads to LLM
    async def _verify_services() -> None:
        seen_prompts: List[str] = []

        class _CaptureBridge:
            enabled = True
            chat_provider_id = "capture-mock"

            async def generate_text(self, prompt: str, **_kwargs: Any) -> str:
                seen_prompts.append(prompt)
                if "mark_superseded" in prompt:
                    return json.dumps(
                        {
                            "confidence": 0.9,
                            "risk_level": "low",
                            "reason": "用户更正城市",
                            "operations": [],
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "decisions": [
                            {"fact_id": "f1", "action": "keep", "target_id": None, "candidate_ids": []}
                        ],
                        "relation_support": {"0": ["f1"]},
                    },
                    ensure_ascii=False,
                )

        store = MetadataStore(tmp_path / "svc_prompt_db")
        store.connect()
        try:
            with llm_shim.use_provider_bridge(_CaptureBridge()):
                dedup = FactDeduplicator(store)
                await dedup.plan(
                    scope_type="chat",
                    scope_id="chat_1",
                    facts=[{"fact_id": "f1", "text": "小红在杭州工作", "person_ids": ["qq:u2"]}],
                    source={"platform": "qq", "chat_id": "chat_1", "message_ids": ["m1"], "observed_at": 1700000000.0},
                    relations=[{"subject": "小红", "predicate": "工作于", "object": "杭州"}],
                )
                assert seen_prompts
                dedup_prompt = seen_prompts[-1]
                assert "小红在杭州工作" in dedup_prompt
                assert "{payload}" not in dedup_prompt

                correction_svc = MemoryCorrectionAdminService(SimpleNamespace(_fuzzy_modify_planner=None))
                await correction_svc._build_fuzzy_modify_llm_plan(
                    request_text="把小红的工作城市改为上海",
                    scope="person",
                    person_id="qq:u2",
                    candidates=[
                        {
                            "candidate_id": "cand_1",
                            "target_type": "paragraph",
                            "evidence_type": "person_fact",
                            "hash": "hash_abc",
                            "content": "小红在杭州工作",
                            "source": "person_fact:qq:u2",
                            "metadata": {},
                        }
                    ],
                )
                fuzzy_prompt = seen_prompts[-1]
                assert "把小红的工作城市改为上海" in fuzzy_prompt
                assert "hash_abc" in fuzzy_prompt
                assert "{request_payload}" not in fuzzy_prompt
                assert "{{" not in fuzzy_prompt
        finally:
            store.close()

    asyncio.run(_verify_services())


def test_summary_importer_single_definition_and_v2_pipeline_alignment(tmp_path: Path) -> None:
    """Verify no duplicate import_from_stream (F811) and full v2.0 summarization alignment end-to-end."""
    importer_path = (
        Path(__file__).resolve().parent.parent / "memorix" / "core" / "utils" / "summary_importer.py"
    )
    tree = ast.parse(importer_path.read_text(encoding="utf-8"), filename=str(importer_path))
    importer_cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SummaryImporter"
    )
    stream_defs = [
        node.lineno
        for node in importer_cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "import_from_stream"
    ]
    assert len(stream_defs) == 1, f"Expected exactly 1 import_from_stream definition, got lines {stream_defs}"

    async def _scenario() -> None:
        img_file = tmp_path / "trip_ticket.png"
        img_file.write_bytes(_encode_solid_rgb_png((40, 140, 220)))

        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "v2_sum_data")},
                "image_memory": {"enabled": True, "require_embedding": False, "vlm": {"auto_describe": False}},
                "person_fact_writeback": {"enabled": False},
            },
        )
        prompts_seen: List[str] = []
        mode = {"step": "window1"}

        def _responder(prompt: str, _ctx: Dict[str, Any]) -> str:
            prompts_seen.append(prompt)
            if "判断同一归属范围内的事实是否重复" in prompt:
                payload_str = prompt.split("输入：\n", 1)[1].strip()
                payload_obj = json.loads(payload_str)
                decisions = []
                for fact in payload_obj["facts"]:
                    cands = payload_obj["candidates"].get(fact["fact_id"], [])
                    if "去杭州出差" in fact["text"] and cands:
                        decisions.append(
                            {
                                "fact_id": fact["fact_id"],
                                "action": "reuse",
                                "target_id": cands[0]["id"],
                                "candidate_ids": [],
                            }
                        )
                    else:
                        decisions.append(
                            {
                                "fact_id": fact["fact_id"],
                                "action": "keep",
                                "target_id": None,
                                "candidate_ids": [],
                            }
                        )
                rel_support = {
                    str(i): [payload_obj["facts"][-1]["fact_id"]]
                    for i in range(len(payload_obj.get("relations", [])))
                }
                return json.dumps({"decisions": decisions, "relation_support": rel_support}, ensure_ascii=False)

            if mode["step"] == "window1":
                return json.dumps(
                    {
                        "summary": "[U1]计划下周去杭州出差。",
                        "entities": ["[U1]", "杭州"],
                        "relations": [{"subject": "[U1]", "predicate": "计划前往", "object": "杭州"}],
                        "facts": [
                            {
                                "fact_id": "f1",
                                "text": "[U1]计划下周去杭州出差",
                                "image_evidence": [{"message_id": "msg_img_1", "component_path": "1"}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            if mode["step"] == "window2":
                return json.dumps(
                    {
                        "summary": "[U1]计划下周去杭州出差，并预订了西湖边的酒店。",
                        "entities": ["[U1]", "杭州", "西湖酒店"],
                        "relations": [{"subject": "[U1]", "predicate": "预订", "object": "西湖酒店"}],
                        "facts": [
                            {"fact_id": "f1", "text": "[U1]计划下周去杭州出差", "image_evidence": []},
                            {"fact_id": "f2", "text": "[U1]预订了西湖酒店", "image_evidence": []},
                        ],
                    },
                    ensure_ascii=False,
                )
            if mode["step"] == "empty":
                return json.dumps(
                    {"summary": "", "entities": [], "relations": [], "facts": []},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "summary": "[U1]提到了未知图片。",
                    "entities": ["[U1]"],
                    "relations": [],
                    "facts": [
                        {
                            "fact_id": "f_bad",
                            "text": "[U1]提到了未知图片",
                            "image_evidence": [{"message_id": "nonexistent_msg", "component_path": "99"}],
                        }
                    ],
                },
                ensure_ascii=False,
            )

        mock_ctx._default_provider.response_generator = _responder

        try:
            ev1 = MockAstrMessageEvent(
                message_str="这是去杭州出差的车票",
                sender_id="u_lin",
                sender_name="林晓",
                group_id="g_sum",
                session_id="g_sum",
                components=[MockPlain("这是去杭州出差的车票"), MockImage(path=str(img_file))],
            )
            ev1.message_obj.message_id = "msg_img_1"
            scope_key = plugin._resolve_scope(ev1)
            cap1 = await plugin.message_ingestion.capture_event_once(ev1)
            await plugin.message_ingestion._record_message_background(ev1, cap1)

            rt = await plugin.runtime_manager.get_runtime(scope_key)
            store = rt.context.metadata_store
            before_msg_count = len(store.get_transcript_messages("g_sum", limit=50))
            assert before_msg_count == 1

            # Window 1: Summarize via AstrBot SummaryService -> v2.0 prompt, [U1] restoration, fact_claims, image_memory_links
            res1 = await plugin.summary_service.summarize_session(
                scope_key=scope_key,
                session_id="g_sum",
                source="chat_summary:g_sum",
            )
            assert res1["success"] is True, res1
            # Ensure summarize_session did NOT re-append duplicate transcript rows
            assert len(store.get_transcript_messages("g_sum", limit=50)) == before_msg_count
            assert any("身份映射：" in p and "message_id=msg_img_1, component_path=1" in p for p in prompts_seen)

            paragraphs_w1 = store.get_paragraphs_by_source("chat_summary:g_sum")
            assert len(paragraphs_w1) == 1
            assert "林晓计划下周去杭州出差" in paragraphs_w1[0]["content"]
            assert "[U1]" not in paragraphs_w1[0]["content"]

            occ = store.get_image_occurrence_by_ref(
                build_chat_external_ref(chat_id="g_sum", message_id="msg_img_1", component_path="1")
            )
            assert occ is not None
            links = store.query(
                "SELECT * FROM image_memory_links WHERE occurrence_id = ? AND target_id = ?",
                (occ["occurrence_id"], paragraphs_w1[0]["hash"]),
            )
            assert len(links) == 1
            assert links[0]["link_kind"] == "fact_evidence"

            # Window 2: Append new chat message, verify previous_summary_context & FactDeduplicator reuse of f1 + keep of f2
            ev2 = MockAstrMessageEvent(
                message_str="另外我已经预订了西湖酒店",
                sender_id="u_lin",
                sender_name="林晓",
                group_id="g_sum",
                session_id="g_sum",
                components=[MockPlain("另外我已经预订了西湖酒店")],
            )
            ev2.message_obj.message_id = "msg_txt_2"
            cap2 = await plugin.message_ingestion.capture_event_once(ev2)
            await plugin.message_ingestion._record_message_background(ev2, cap2)
            mode["step"] = "window2"
            prompts_seen.clear()

            with rt.context.scope_shims():
                importer = SummaryImporter(
                    vector_store=rt.context.vector_store,
                    graph_store=rt.context.graph_store,
                    metadata_store=store,
                    embedding_manager=rt.context.embedding_manager,
                    plugin_config={"plugin_instance": rt.context, **rt.context.config},
                )
                stream_res2 = await importer.import_from_stream(
                    "g_sum",
                    metadata={"external_id": "win_2", "trigger_message_count": 2},
                )
            assert stream_res2.success is True, stream_res2.detail
            assert stream_res2.skipped is False
            assert any("历史净化摘要回顾" in p and "林晓计划下周去杭州出差" in p for p in prompts_seen)

            para_w2 = store.get_paragraph(stream_res2.paragraph_hash)
            assert para_w2 is not None
            # Reused fact f1 must NOT be rewritten into the new incremental paragraph; only f2 is kept!
            assert para_w2["content"] == "林晓预订了西湖酒店"
            checkpoint_w2 = store.get_summary_checkpoint("win_2")
            assert checkpoint_w2 is not None and checkpoint_w2["phase"] == "complete"

            # Empty increment window via import_from_stream -> skipped=True and checkpoint recorded
            mode["step"] = "empty"
            with rt.context.scope_shims():
                empty_res = await importer.import_from_stream(
                    "g_sum",
                    metadata={"external_id": "win_empty", "trigger_message_count": 2},
                )
            assert empty_res.success is True
            assert empty_res.skipped is True
            assert store.get_summary_checkpoint("win_empty") is not None

            # Out-of-window image_evidence must be rejected cleanly without corrupting storage
            mode["step"] = "bad_image"
            with rt.context.scope_shims():
                bad_res = await importer.import_from_stream(
                    "g_sum",
                    metadata={"external_id": "win_bad_img", "trigger_message_count": 2},
                )
            assert bad_res.success is False
            assert "窗口外图片证据" in bad_res.detail

            # Offline deterministic fallback when provider_bridge is disabled/absent
            offline_importer = SummaryImporter(
                vector_store=rt.context.vector_store,
                graph_store=rt.context.graph_store,
                metadata_store=store,
                embedding_manager=rt.context.embedding_manager,
                plugin_config={"summarization": {"default_knowledge_type": "narrative"}},
                llm_client=None,
            )
            offline_res = await offline_importer.import_from_transcript(
                session_id="offline_sess",
                messages=[{"role": "user", "content": "离线无模型回退验证"}],
                source="chat_summary:offline_sess",
            )
            assert offline_res.success is True
            offline_paras = store.get_paragraphs_by_source("chat_summary:offline_sess")
            assert len(offline_paras) == 1
            assert "离线无模型回退验证" in offline_paras[0]["content"]
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_embedding_shim_legacy_openai_config_gateway_shapes_and_sync_methods() -> None:
    """Verify legacy [embedding.openai], OpenAI-compatible gateway shapes, and ContextVar-safe sync methods."""
    seen_urls: List[str] = []
    mode = {"shape": "embeddings_matrix"}

    async def _fake_post(self_client: Any, url: str, *, headers: Dict[str, str], json: Dict[str, Any]) -> Any:
        del self_client, headers
        seen_urls.append(url)
        request = httpx.Request("POST", url)
        if mode["shape"] == "embeddings_matrix":
            return httpx.Response(200, json={"embeddings": [[0.0, 1.0, 0.0, 0.0]]}, request=request)
        if mode["shape"] == "single_embedding":
            return httpx.Response(200, json={"embedding": [1.0, 0.0, 0.0, 0.0]}, request=request)
        return httpx.Response(200, json={"data": []}, request=request)

    legacy_cfg = {
        "embedding": {
            "dimension": 4,
            "openai": {
                "base_url": "https://legacy-openai-gateway.local",
                "api_key": "sk-legacy",
                "model": "legacy-text-emb",
            },
        }
    }

    with patch.object(httpx.AsyncClient, "post", new=_fake_post):
        with config_shim.use_scope_config(legacy_cfg):
            client = embedding_shim.EmbeddingServiceClient()
            # Sync call outside any running asyncio loop
            sync_one = client.embed_text_sync("legacy openai sync test")
            assert sync_one.model_name == "legacy-text-emb"
            assert sync_one.api_provider == "openai"
            assert sync_one.embedding == [0.0, 1.0, 0.0, 0.0]
            assert seen_urls[-1] == "https://legacy-openai-gateway.local/v1/embeddings"

            # Sync batch call INSIDE an active asyncio event loop must preserve scoped ContextVar config
            async def _inside_running_loop() -> None:
                mode["shape"] = "single_embedding"
                batch = client.embed_texts_sync(["a", "b"], max_concurrent=2)
                assert len(batch) == 2
                assert all(
                    isinstance(item, embedding_shim.EmbeddingResult) and item.embedding == [1.0, 0.0, 0.0, 0.0]
                    for item in batch
                )

                mode["shape"] = "empty"
                with pytest.raises(ValueError, match="no embedding rows"):
                    await client.embed_text("empty gateway response")

            asyncio.run(_inside_running_loop())


def test_prompt_shim_resolve_path_bcp47_scoped_custom_prompts_and_inplace_edit(tmp_path: Path) -> None:
    """Verify resolve_prompt_path returns Path, BCP-47 locale casing, per-scope custom_prompts, and mtime cache."""
    import src.common.prompt_i18n as shimmed_prompt_i18n  # type: ignore[import-not-found]

    resolved_path = shimmed_prompt_i18n.resolve_prompt_path("a_memorix_chat_summary", locale="zh_cn")
    assert isinstance(resolved_path, Path)
    assert resolved_path.is_file()

    tpl_bcp47 = shimmed_prompt_i18n.load_prompt_template("a_memorix_chat_summary", locale="zh_cn")
    assert tpl_bcp47.locale == "zh-CN"

    # Scope A has custom heuristic_memory_impression.prompt in <data_dir>/custom_prompts/zh-CN; Scope B uses default
    scope_a_data = tmp_path / "scope_a_data"
    scope_b_data = tmp_path / "scope_b_data"
    custom_prompt_file = scope_a_data / "custom_prompts" / "zh-CN" / "heuristic_memory_impression.prompt"
    custom_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    custom_prompt_file.write_text("作用域A定制v1：{chat_identity} => {message_window}", encoding="utf-8")

    with config_shim.use_scope_config({"storage": {"data_dir": str(scope_a_data)}}):
        # Internal callers do NOT pass custom_prompts_root explicitly
        rendered_v1 = shimmed_prompt_i18n.load_prompt(
            "heuristic_memory_impression",
            locale="zh_cn",
            chat_identity="群101",
            message_window="你好",
        )
        assert rendered_v1 == "作用域A定制v1：群101 => 你好"

        # In-place edit updates size/mtime and must take effect WITHOUT clear_prompt_cache()
        custom_prompt_file.write_text(
            "作用域A定制v2（已原地热更新）：{chat_identity} :: {message_window}",
            encoding="utf-8",
        )
        rendered_v2 = shimmed_prompt_i18n.load_prompt(
            "heuristic_memory_impression",
            chat_identity="群101",
            message_window="再见",
        )
        assert rendered_v2 == "作用域A定制v2（已原地热更新）：群101 :: 再见"

        # Outdated custom placeholder falls back cleanly to bundled built-in template
        custom_prompt_file.write_text("过期占位符：{obsolete_placeholder_xyz}", encoding="utf-8")
        fallback_rendered = shimmed_prompt_i18n.load_prompt(
            "heuristic_memory_impression",
            chat_identity="群101",
            message_window="回退成功",
        )
        assert "群101" in fallback_rendered
        assert "回退成功" in fallback_rendered
        assert "{obsolete_placeholder_xyz}" not in fallback_rendered

    with config_shim.use_scope_config({"storage": {"data_dir": str(scope_b_data)}}):
        tpl_b = shimmed_prompt_i18n.load_prompt_template("heuristic_memory_impression")
        assert tpl_b.customized is False


def test_summary_importer_active_llm_failure_does_not_corrupt_memory_and_recovers_on_retry(tmp_path: Path) -> None:
    """Active LLM errors or truncated JSON must fail cleanly (no raw transcript pollution) and recover on retry."""

    async def _scenario() -> None:
        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "llm_fail_safe_data")},
                "person_fact_writeback": {"enabled": False},
            },
        )
        stage = {"mode": "raise_error"}

        def _responder(prompt: str, _ctx: Dict[str, Any]) -> str:
            if "判断同一归属范围内的事实是否重复" in prompt:
                payload_obj = json.loads(prompt.split("输入：\n", 1)[1].strip())
                return json.dumps(
                    {
                        "decisions": [
                            {"fact_id": f["fact_id"], "action": "keep", "target_id": None, "candidate_ids": []}
                            for f in payload_obj["facts"]
                        ],
                        "relation_support": {},
                    },
                    ensure_ascii=False,
                )
            if stage["mode"] == "raise_error":
                raise RuntimeError("模拟上游 LLM HTTP 502 网关超时")
            if stage["mode"] == "truncated_json":
                return '{"summary": "[U1]准备搬去苏'
            return json.dumps(
                {
                    "summary": "[U1]准备下个月搬去苏州定居。",
                    "entities": ["[U1]", "苏州"],
                    "relations": [],
                    "facts": [{"fact_id": "f1", "text": "[U1]准备下个月搬去苏州定居", "image_evidence": []}],
                },
                ensure_ascii=False,
            )

        mock_ctx._default_provider.response_generator = _responder

        try:
            ev = MockAstrMessageEvent(
                message_str="哈哈我准备下个月搬去苏州定居啦！",
                sender_id="u_su",
                sender_name="陈默",
                group_id="g_retry",
                session_id="g_retry",
            )
            ev.message_obj.message_id = "msg_retry_1"
            scope_key = plugin._resolve_scope(ev)
            cap = await plugin.message_ingestion.capture_event_once(ev)
            await plugin.message_ingestion._record_message_background(ev, cap)

            rt = await plugin.runtime_manager.get_runtime(scope_key)
            store = rt.context.metadata_store

            # 1) Active LLM throws error -> must return success=False, zero paragraphs, zero checkpoints
            fail_res1 = await plugin.summary_service.summarize_session(
                scope_key=scope_key,
                session_id="g_retry",
                source="chat_summary:g_retry",
            )
            assert fail_res1["success"] is False
            assert store.get_paragraphs_by_source("chat_summary:g_retry") == []
            assert store.query("SELECT * FROM chat_summary_checkpoints WHERE chat_id = ?", ("g_retry",)) == []

            # 2) Active LLM returns truncated JSON -> must return success=False, zero raw-transcript paragraphs
            stage["mode"] = "truncated_json"
            fail_res2 = await plugin.summary_service.summarize_session(
                scope_key=scope_key,
                session_id="g_retry",
                source="chat_summary:g_retry",
            )
            assert fail_res2["success"] is False
            assert store.get_paragraphs_by_source("chat_summary:g_retry") == []
            assert store.get_summary_checkpoint_count("g_retry") == 0

            # 3) Active LLM recovers -> cleanly writes purified v2.0 summary
            stage["mode"] = "healthy"
            ok_res = await plugin.summary_service.summarize_session(
                scope_key=scope_key,
                session_id="g_retry",
                source="chat_summary:g_retry",
            )
            assert ok_res["success"] is True, ok_res
            paras = store.get_paragraphs_by_source("chat_summary:g_retry")
            assert len(paras) == 1
            assert paras[0]["content"] == "陈默准备下个月搬去苏州定居"
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())


def test_summary_importer_purged_images_and_reused_non_paragraph_fact_claim(tmp_path: Path) -> None:
    """Purged images must not leak into catalog/links, and reusing non-paragraph fact_claims must not raise StopIteration."""

    async def _scenario() -> None:
        img_file = tmp_path / "purged_secret.png"
        img_file.write_bytes(_encode_solid_rgb_png((200, 50, 50)))

        plugin, mock_ctx = create_test_plugin_instance(
            tmp_path,
            custom_config={
                "storage": {"data_dir": str(tmp_path / "purged_and_reuse_data")},
                "image_memory": {"enabled": True, "require_embedding": False, "vlm": {"auto_describe": False}},
                "person_fact_writeback": {"enabled": False},
            },
        )
        prompts_seen: List[str] = []

        def _responder(prompt: str, _ctx: Dict[str, Any]) -> str:
            prompts_seen.append(prompt)
            if "episode segmentation engine" in prompt:
                return json.dumps(
                    {
                        "episodes": [
                            {
                                "title": "手冲咖啡日常",
                                "summary": "林晓提到平时喜欢喝手冲咖啡。",
                                "paragraph_indices": [1],
                                "participants": ["林晓"],
                                "keywords": ["手冲咖啡"],
                                "time_confidence": 0.9,
                                "llm_confidence": 0.95,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "判断同一归属范围内的事实是否重复" in prompt:
                payload_obj = json.loads(prompt.split("输入：\n", 1)[1].strip())
                decisions = []
                for fact in payload_obj["facts"]:
                    cands = payload_obj["candidates"].get(fact["fact_id"], [])
                    if "上海出差" in fact["text"] and cands:
                        decisions.append(
                            {
                                "fact_id": fact["fact_id"],
                                "action": "reuse",
                                "target_id": cands[0]["id"],
                                "candidate_ids": [],
                            }
                        )
                    else:
                        decisions.append(
                            {
                                "fact_id": fact["fact_id"],
                                "action": "keep",
                                "target_id": None,
                                "candidate_ids": [],
                            }
                        )
                return json.dumps({"decisions": decisions, "relation_support": {}}, ensure_ascii=False)

            return json.dumps(
                {
                    "summary": "[U1]经常去上海出差，并且喜欢喝手冲咖啡。",
                    "entities": ["[U1]", "上海"],
                    "relations": [],
                    "facts": [
                        {"fact_id": "f_reuse_admin", "text": "[U1]经常去上海出差", "image_evidence": []},
                        {"fact_id": "f_new", "text": "[U1]喜欢喝手冲咖啡", "image_evidence": []},
                    ],
                },
                ensure_ascii=False,
            )

        mock_ctx._default_provider.response_generator = _responder

        try:
            ev = MockAstrMessageEvent(
                message_str="我经常去上海出差，平时喜欢喝手冲咖啡",
                sender_id="u_lin2",
                sender_name="林晓",
                group_id="g_edge",
                session_id="g_edge",
                components=[MockPlain("我经常去上海出差，平时喜欢喝手冲咖啡"), MockImage(path=str(img_file))],
            )
            ev.message_obj.message_id = "msg_purged_1"
            scope_key = plugin._resolve_scope(ev)
            cap = await plugin.message_ingestion.capture_event_once(ev)
            await plugin.message_ingestion._record_message_background(ev, cap)

            rt = await plugin.runtime_manager.get_runtime(scope_key)
            store = rt.context.metadata_store

            ext_ref = build_chat_external_ref(chat_id="g_edge", message_id="msg_purged_1", component_path="1")
            occ = store.get_image_occurrence_by_ref(ext_ref)
            assert occ is not None
            # Add observation and mark occurrence as purged inside an explicit committed transaction
            with store.transaction(immediate=True) as conn:
                store.add_image_observation(
                    occurrence_id=occ["occurrence_id"],
                    source_kind="user_statement",
                    confirm_status="confirmed",
                    text="应当被彻底遗忘的机密图片描述",
                    conn=conn,
                )
                conn.execute(
                    "UPDATE image_occurrences SET status = 'purged' WHERE occurrence_id = ?",
                    (occ["occurrence_id"],),
                )

            # Seed an active manual fact_claim in scope_type='chat' WITHOUT any 'paragraph' fact_evidence
            seeded_claim = store.upsert_fact_claim(
                scope_type="chat",
                scope_id="g_edge",
                fact_key="manual:shanghai_trip",
                value_text="林晓经常去上海出差",
                authority="manual",
                evidence_type="manual",
                evidence_id="manual_op_001",
                reason="manual_seed_without_paragraph",
            )
            assert store.get_fact_evidence(seeded_claim["claim_id"])[0]["evidence_type"] == "manual"

            with rt.context.scope_shims():
                importer = SummaryImporter(
                    vector_store=rt.context.vector_store,
                    graph_store=rt.context.graph_store,
                    metadata_store=store,
                    embedding_manager=rt.context.embedding_manager,
                    plugin_config={"plugin_instance": rt.context, **rt.context.config},
                )
                res = await importer.import_from_stream(
                    "g_edge",
                    metadata={"external_id": "win_edge_1", "trigger_message_count": 1},
                )

            # Must NOT leak purged image or its observations into summary prompt!
            assert res.success is True, res.detail
            assert not any("应当被彻底遗忘的机密图片描述" in p for p in prompts_seen)
            assert not any("message_id=msg_purged_1, component_path=1" in p for p in prompts_seen)

            para = store.get_paragraph(res.paragraph_hash)
            assert para is not None
            assert "林晓喜欢喝手冲咖啡" in para["content"]

            # Verify TaskManager / FeedbackService v2.0 leased EpisodeService.process_rebuild_batch works cleanly
            with rt.context.scope_shims():
                ep_batch = await rt.context.episode_service.process_rebuild_batch(
                    limit=5,
                    max_retry=3,
                    max_wait_seconds=0.0,
                )
            assert ep_batch["failed"] == 0, ep_batch
            assert ep_batch["rebuilt"] == 1, ep_batch
            assert ep_batch["episode_count"] == 1, ep_batch

            # Verify _finish_fact_summary checkpoint replay:
            # 1) Reusing a fact_claim without 'paragraph' evidence must NOT raise RuntimeError(StopIteration)
            # 2) Keeping a fact whose image occurrence was purged after planning must NOT create image_memory_links
            replay_payload = {
                "plan": {
                    "version": 1,
                    "scope_type": "chat",
                    "scope_id": "g_edge",
                    "source": {
                        "platform": "qq",
                        "chat_id": "g_edge",
                        "message_ids": ["msg_purged_1"],
                        "observed_at": 1.0,
                        "observation_id": "obs_replay_1",
                    },
                    "facts": [
                        {
                            "fact_id": "f_reuse_manual",
                            "text": "林晓经常去上海出差",
                            "person_ids": [],
                            "image_evidence": [{"message_id": "msg_purged_1", "component_path": "1"}],
                        },
                        {
                            "fact_id": "f_replay",
                            "text": "林晓周末去书店",
                            "person_ids": [],
                            "image_evidence": [{"message_id": "msg_purged_1", "component_path": "1"}],
                        },
                    ],
                    "decisions": [
                        {
                            "fact_id": "f_reuse_manual",
                            "action": "reuse",
                            "target": {"claim_id": seeded_claim["claim_id"], "paragraph_hash": res.paragraph_hash},
                            "candidate_ids": [seeded_claim["claim_id"]],
                        },
                        {"fact_id": "f_replay", "action": "keep", "target": {}, "candidate_ids": []},
                    ],
                    "relation_support": {},
                },
                "metadata": {"external_id": "win_edge_replay", "trigger_message_count": 2},
                "entities": ["林晓"],
                "relations": [],
                "time_meta": {},
                "allowed_images": [["msg_purged_1", "1"]],
            }
            with rt.context.scope_shims():
                replay_res = await importer._finish_fact_summary(replay_payload)
            assert replay_res.success is True, replay_res.detail
            replay_para = store.get_paragraph(replay_res.paragraph_hash)
            assert replay_para is not None
            assert replay_para["content"] == "林晓周末去书店"
            purged_links = store.query(
                "SELECT * FROM image_memory_links WHERE occurrence_id = ?",
                (occ["occurrence_id"],),
            )
            assert purged_links == []
        finally:
            await plugin.terminate()

    asyncio.run(_scenario())

