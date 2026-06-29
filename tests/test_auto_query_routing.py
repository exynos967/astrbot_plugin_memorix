import asyncio
import sys
import types
from datetime import datetime


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

from astrbot_plugin_memorix.memorix.amemorix.services.query_service import QueryService  # noqa: E402
from astrbot_plugin_memorix.memorix.core.utils.search_execution_service import SearchExecutionResult  # noqa: E402
from astrbot_plugin_memorix.memorix.core.utils.time_parser import (  # noqa: E402
    extract_query_time_intent,
    parse_query_time_range,
)
from astrbot_plugin_memorix.memorix.services.content_router import MemoryContentRouter  # noqa: E402


class _FakeCtx:
    def __init__(self):
        self.config = {
            "retrieval": {
                "enable_ppr": True,
                "auto_route": {"enabled": True, "enable_time_intent": True},
            }
        }
        self.retriever = object()
        self.threshold_filter = object()
        self.graph_store = object()
        self.metadata_store = object()

    def get_config(self, key: str, default=None):
        current = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current


def test_extract_query_time_intent_supports_pure_time_query():
    intent = extract_query_time_intent(
        "你还记得我昨天说过什么吗",
        now=datetime(2026, 3, 7, 12, 0, 0),
    )
    assert intent is not None
    assert intent.query_type == "time"
    assert intent.cleaned_query == ""


def test_parse_query_time_range_supports_named_window():
    ts_from, ts_to = parse_query_time_range("上周", None)
    assert ts_from is not None
    assert ts_to is not None
    assert ts_from < ts_to


def test_auto_search_routes_to_time(monkeypatch):
    captured = {}

    async def _fake_execute(**kwargs):
        request = kwargs["request"]
        captured["query_type"] = request.query_type
        captured["query"] = request.query
        captured["time_from"] = request.time_from
        captured["time_to"] = request.time_to
        return SearchExecutionResult(
            success=True,
            query_type=request.query_type,
            query=request.query,
            top_k=request.top_k or 10,
            time_from=request.time_from,
            time_to=request.time_to,
            results=[],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(
        "astrbot_plugin_memorix.memorix.amemorix.services.query_service.SearchExecutionService.execute",
        _fake_execute,
    )
    service = QueryService(_FakeCtx())
    result = asyncio.run(service.auto_search(query="你还记得我昨天说过什么吗"))
    assert captured["query_type"] == "time"
    assert captured["query"] == ""
    assert result["query_type"] == "time"


def test_auto_search_routes_to_hybrid(monkeypatch):
    captured = {}

    async def _fake_execute(**kwargs):
        request = kwargs["request"]
        captured["query_type"] = request.query_type
        captured["query"] = request.query
        return SearchExecutionResult(
            success=True,
            query_type=request.query_type,
            query=request.query,
            top_k=request.top_k or 10,
            time_from=request.time_from,
            time_to=request.time_to,
            results=[],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(
        "astrbot_plugin_memorix.memorix.amemorix.services.query_service.SearchExecutionService.execute",
        _fake_execute,
    )
    service = QueryService(_FakeCtx())
    result = asyncio.run(service.auto_search(query="上周我提过的显卡预算是多少"))
    assert captured["query_type"] == "hybrid"
    assert "显卡预算" in captured["query"]
    assert result["query_type"] == "hybrid"


def test_auto_search_falls_back_to_search_when_no_time_intent(monkeypatch):
    captured = {}

    async def _fake_execute(**kwargs):
        request = kwargs["request"]
        captured["query_type"] = request.query_type
        captured["query"] = request.query
        return SearchExecutionResult(
            success=True,
            query_type=request.query_type,
            query=request.query,
            top_k=request.top_k or 10,
            results=[],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(
        "astrbot_plugin_memorix.memorix.amemorix.services.query_service.SearchExecutionService.execute",
        _fake_execute,
    )
    service = QueryService(_FakeCtx())
    result = asyncio.run(service.auto_search(query="我喜欢什么游戏"))
    assert captured["query_type"] == "search"
    assert captured["query"] == "我喜欢什么游戏"
    assert result["query_type"] == "search"


def test_content_router_keeps_transcript_only_default():
    router = MemoryContentRouter({"ingest": {"memory_write_mode": "transcript_only"}})
    route = router.route_message(role="user", text="我喜欢深夜打游戏")
    assert route.store_transcript is True
    assert route.write_direct is False
    assert route.fact_candidate is True


def test_content_router_auto_directs_fact_candidate_only():
    router = MemoryContentRouter({"ingest": {"memory_write_mode": "auto"}})
    fact_route = router.route_message(role="user", text="我喜欢深夜打游戏，也经常玩 RPG")
    chat_route = router.route_message(role="user", text="今天这个天气真不错")
    assistant_route = router.route_message(role="assistant", text="我喜欢帮你记录信息")

    assert fact_route.write_direct is True
    assert fact_route.reason == "auto_fact_candidate"
    assert chat_route.write_direct is False
    assert assistant_route.write_direct is False


def test_content_router_can_drop_ephemeral_transcript():
    router = MemoryContentRouter(
        {"ingest": {"memory_write_mode": "auto", "content_router": {"drop_ephemeral_transcript": True}}}
    )
    route = router.route_message(role="user", text="哈哈")
    assert route.store_transcript is False
    assert route.write_direct is False
    assert route.reason == "ephemeral"
