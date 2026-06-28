import asyncio
from types import SimpleNamespace

import numpy as np
from astrbot_plugin_memorix.memorix.amemorix.llm_client import LLMClient
from astrbot_plugin_memorix.memorix.amemorix.services.summary_service import SummaryService
from astrbot_plugin_memorix.memorix.amemorix.settings import resolve_openapi_endpoint_config
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.model_routing import generate_text
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import SummaryImporter


class _FakeVectorStore:
    def __init__(self):
        self.ids = set()

    def __contains__(self, item):
        return item in self.ids

    def add(self, vectors, ids):
        del vectors
        self.ids.update(ids)

    def save(self):
        return None


class _RecordingGraphStore:
    def __init__(self):
        self.nodes = []
        self.edges = []

    def add_nodes(self, nodes):
        for node in nodes:
            if node not in self.nodes:
                self.nodes.append(node)
        return len(nodes)

    def add_edges(self, edges, weights=None, relation_hashes=None):
        del weights, relation_hashes
        for source, target in edges:
            self.edges.append((source, target))
        return len(edges)

    def save(self):
        return None


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


class _RoleEntityLLM:
    async def complete_json(self, prompt, temperature=0.2, max_tokens=1200):
        del prompt, temperature, max_tokens
        return (
            True,
            {
                "summary": "用户喜欢 RPG。",
                "entities": ["用户", "RPG"],
                "relations": [{"subject": "用户", "predicate": "喜欢", "object": "RPG"}],
            },
            "",
        )


class _FakeProviderBridge:
    def __init__(
        self,
        chat_provider_id: str = "chat-default",
        embedding_provider_id: str = "embedding-default",
    ):
        self.enabled = True
        self._context = object()
        self.chat_provider_id = chat_provider_id
        self.embedding_provider_id = embedding_provider_id


class _FakeSummaryCtx:
    def __init__(self, config, provider_bridge=None):
        self.config = config
        self.provider_bridge = provider_bridge
        self.vector_store = object()
        self.graph_store = object()
        self.metadata_store = SimpleNamespace(get_transcript_session=lambda _session_id: None)
        self.embedding_manager = object()
        self.astrbot_context = None

    def get_config(self, key: str, default=None):
        current = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current


def test_summary_import_keeps_llm_entities_and_derives_time_meta(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    graph_store = _RecordingGraphStore()
    try:
        importer = SummaryImporter(
            vector_store=_FakeVectorStore(),
            graph_store=graph_store,
            metadata_store=metadata_store,
            embedding_manager=_FakeEmbeddingManager(),
            plugin_config={"summarization": {"default_knowledge_type": "narrative"}},
            llm_client=_RoleEntityLLM(),
        )

        ok, message = asyncio.run(
            importer.import_from_transcript(
                session_id="s1",
                messages=[
                    {
                        "role": "user",
                        "content": "我喜欢 RPG",
                        "timestamp": 100.0,
                        "metadata": {"sender_name": "小明", "sender_id": "u1", "platform": "qq"},
                    },
                    {"role": "assistant", "content": "我记住了", "timestamp": 160.0},
                ],
                source="chat_summary:s1",
                context_length=5,
            )
        )

        assert ok is True, message
        assert "用户" in graph_store.nodes
        assert ("用户", "RPG") in graph_store.edges
        assert len(metadata_store.get_relations(subject="用户", object="RPG")) == 1

        paragraphs = metadata_store.get_paragraphs_by_source("chat_summary:s1")
        assert len(paragraphs) == 1
        assert paragraphs[0]["event_time_start"] == 100.0
        assert paragraphs[0]["event_time_end"] == 160.0
        assert paragraphs[0]["time_granularity"] == "minute"
        assert paragraphs[0]["time_confidence"] == 0.95
    finally:
        metadata_store.close()


def test_summary_service_uses_plugin_provider_bridge():
    ctx = _FakeSummaryCtx(
        config={
            "embedding": {"retry": {"max_attempts": 2}},
            "summarization": {},
        },
        provider_bridge=_FakeProviderBridge(),
    )

    service = SummaryService(ctx)

    assert service.llm_client.provider_bridge.chat_provider_id == "chat-default"
    assert service.llm_client.provider_bridge.embedding_provider_id == "embedding-default"


def test_summary_service_without_provider_bridge_uses_deterministic_fallback():
    ctx = _FakeSummaryCtx(
        config={
            "embedding": {
                "openapi": {
                    "base_url": "https://example.com/v1",
                    "api_key": "key",
                    "chat_model": "chat-fallback",
                }
            },
            "summarization": {},
        },
        provider_bridge=None,
    )

    service = SummaryService(ctx)

    assert service.llm_client is None


def test_openapi_endpoint_config_does_not_fallback_to_env(monkeypatch):
    monkeypatch.setenv("OPENAPI_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAPI_EMBEDDING_MODEL", "env-embedding")

    endpoint = resolve_openapi_endpoint_config({"embedding": {"openapi": {}}}, section="embedding")

    assert endpoint.get("base_url", "") == ""
    assert endpoint.get("api_key", "") == ""
    assert endpoint.get("model", "") == ""


def test_llm_client_does_not_fallback_to_env(monkeypatch):
    monkeypatch.setenv("OPENAPI_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    client = LLMClient()

    assert client.base_url == ""
    assert client.api_key == ""
    assert client.model == ""


def test_generate_text_does_not_use_ctx_llm_client_without_provider_bridge():
    class _UnexpectedLLMClient:
        async def complete(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("ctx.llm_client fallback should not be used")

    ctx = SimpleNamespace(provider_bridge=None, llm_client=_UnexpectedLLMClient())

    result = asyncio.run(generate_text(ctx, "hello"))

    assert not result.success
    assert "provider bridge" in result.error
