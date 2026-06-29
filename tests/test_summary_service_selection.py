import asyncio
import sys
import types


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    api_mod.logger = _Logger()
    astrbot_mod.api = api_mod
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


_install_astrbot_stub()

from astrbot_plugin_memorix.memorix.amemorix.llm_client import LLMClient  # noqa: E402
from astrbot_plugin_memorix.memorix.amemorix.services.summary_service import SummaryService  # noqa: E402
from astrbot_plugin_memorix.memorix.amemorix.settings import resolve_openapi_endpoint_config  # noqa: E402
from astrbot_plugin_memorix.memorix.core.utils.model_routing import generate_text  # noqa: E402


class _FakeProviderBridge:
    def __init__(self, chat_provider_id: str = "chat-default", embedding_provider_id: str = "embedding-default"):
        self.enabled = True
        self._context = object()
        self.chat_provider_id = chat_provider_id
        self.embedding_provider_id = embedding_provider_id


class _FakeCtx:
    def __init__(self, config, provider_bridge=None):
        self.config = config
        self.provider_bridge = provider_bridge
        self.vector_store = object()
        self.graph_store = object()
        self.metadata_store = types.SimpleNamespace(get_transcript_session=lambda _session_id: None)
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


def test_summary_service_uses_plugin_provider_bridge():
    ctx = _FakeCtx(
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
    ctx = _FakeCtx(
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

    ctx = types.SimpleNamespace(provider_bridge=None, llm_client=_UnexpectedLLMClient())

    result = asyncio.run(generate_text(ctx, "hello"))

    assert not result.success
    assert "provider bridge" in result.error
