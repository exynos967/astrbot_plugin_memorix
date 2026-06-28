import asyncio
from types import SimpleNamespace

import numpy as np
from astrbot_plugin_memorix.main import MEMORY_INJECTION_MARKER, MemorixPlugin
from astrbot_plugin_memorix.memorix.adapters.astrbot_event_adapter import AstrbotEventAdapter
from astrbot_plugin_memorix.memorix.amemorix.context import AppContext
from astrbot_plugin_memorix.memorix.amemorix.services.query_service import QueryService
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import SummaryImporter
from astrbot_plugin_memorix.memorix.services.content_router import MemoryContentRouter
from astrbot_plugin_memorix.memorix.services.ingest_service import IngestService
from astrbot_plugin_memorix.memorix.services.person_fact_writeback_service import (
    PersonFactWritebackItem,
    PersonFactWritebackService,
)
from astrbot_plugin_memorix.memorix.tools import MemorixIngestTextTool, _format_search_result_for_llm, build_memorix_tools
from astrbot_plugin_memorix.memorix.utils.profile_injection import build_profile_injection_text


class DummyContext:
    def __init__(self):
        self.added = []
        self.removed = []
        self._tool_manager = SimpleNamespace(remove_func=self.removed.append)

    def add_llm_tools(self, *tools):
        self.added.extend(tools)

    def get_llm_tool_manager(self):
        return self._tool_manager


class _FakeProviderRequest(SimpleNamespace):
    async def assemble_context(self):
        content = []
        if self.prompt:
            content.append({"type": "text", "text": self.prompt})
        for part in self.extra_user_content_parts:
            content.append(part.model_dump_for_context())
        return {"role": "user", "content": content}


class DummyPlugin:
    def _resolve_scope(self, event):
        del event
        return "group:test"


class _FakeMessageObj:
    session_id = "session-1"
    message_id = "msg-current"
    timestamp = 123
    message = []
    group = SimpleNamespace(group_id="group-1", group_name="测试群")


class _FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:group-1"
    message_obj = _FakeMessageObj()
    message_str = "我喜欢什么游戏？"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_sender_id(self):
        return "user-1"

    def get_sender_name(self):
        return "小明"

    def get_group_id(self):
        return "group-1"

    def get_self_id(self):
        return "bot-1"


class _FakeAt:
    type = "At"
    qq = "user-2"
    name = "小红"


class _FakeReply:
    type = "Reply"
    sender_id = "user-3"
    sender_nickname = "小蓝"


class _FakeGroupMessageObj(_FakeMessageObj):
    message = [_FakeAt(), _FakeReply()]


class _FakeGroupEvent(_FakeEvent):
    message_obj = _FakeGroupMessageObj()


class _FakeProfileService:
    def __init__(self):
        self.upsert_calls = []
        self.query_calls = []

    async def is_injection_enabled(self, **kwargs):
        del kwargs
        return True

    async def upsert_registry_from_event(self, **kwargs):
        self.upsert_calls.append(kwargs)
        return {"success": True}

    async def query(self, **kwargs):
        self.query_calls.append(kwargs)
        names = {
            "aiocqhttp:user-1": "小明",
            "aiocqhttp:user-2": "小红",
            "aiocqhttp:user-3": "小蓝",
        }
        name = names.get(kwargs["person_id"], kwargs["person_id"])
        return {
            "success": True,
            "person_id": kwargs["person_id"],
            "person_name": name,
            "profile_text": f"{name}喜欢深夜玩 RPG，也常聊游戏偏好。",
        }


class _FakeQueryService:
    def __init__(self):
        self.calls = []

    async def auto_search(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "query_type": "search",
            "query": kwargs["query"],
            "count": 2,
            "results": [
                {
                    "hash": "current",
                    "type": "paragraph",
                    "score": 1.0,
                    "content": "当前消息不应被注入。",
                    "metadata": {"message_id": "msg-current"},
                },
                {
                    "hash": "old",
                    "type": "paragraph",
                    "score": 0.7,
                    "content": "小明之前说自己喜欢 RPG。",
                    "metadata": {"message_id": "msg-old"},
                },
            ],
        }


EXPECTED_TOOL_NAMES = [
    "search_memory",
    "ingest_summary",
    "ingest_text",
    "get_person_profile",
    "maintain_memory",
    "memory_stats",
    "memory_graph_admin",
    "memory_source_admin",
    "memory_episode_admin",
    "memory_profile_admin",
    "memory_runtime_admin",
    "memory_import_admin",
    "memory_tuning_admin",
    "memory_feedback_admin",
    "memory_v5_admin",
    "memory_delete_admin",
    "memory_fuzzy_modify_admin",
]


def test_build_memorix_tools_matches_maibot_core_names():
    names = [tool.name for tool in build_memorix_tools(DummyPlugin())]
    assert names == EXPECTED_TOOL_NAMES


def test_search_memory_tool_schema_guides_time_modes():
    tools = build_memorix_tools(DummyPlugin())
    search_tool = next(tool for tool in tools if tool.name == "search_memory")

    assert "默认使用 mode=search" in search_tool.description
    assert "time/hybrid 必须" in search_tool.description
    assert "不会自动降级" in search_tool.description
    assert "没有时间条件时不要用 hybrid/time" in search_tool.parameters["properties"]["mode"]["description"]
    assert "至少填它或 time_end" in search_tool.parameters["properties"]["time_start"]["description"]
    assert "至少填它或 time_start" in search_tool.parameters["properties"]["time_end"]["description"]


def test_plugin_registers_and_removes_llm_tools():
    ctx = DummyContext()
    plugin = MemorixPlugin(ctx, {"scope": {"mode": "group_global"}})

    # Avoid starting the embedded WebUI bridge in this unit test.
    plugin.webui_page_bridge.register = lambda *args, **kwargs: None

    asyncio.run(plugin.initialize())
    assert [tool.name for tool in ctx.added] == EXPECTED_TOOL_NAMES

    plugin._remove_llm_tools()
    assert ctx.removed == [tool.name for tool in ctx.added]


def test_astrbot_event_adapter_extracts_group_name():
    adapted = AstrbotEventAdapter.from_event(_FakeEvent(), "aiocqhttp:group:group-1")

    assert adapted.group_id == "group-1"
    assert adapted.group_name == "测试群"


def test_astrbot_event_adapter_restores_cron_original_session():
    class _CronMessageObj:
        session_id = "799462158"
        message_id = "cron-msg"
        timestamp = 123
        message = []

    class _CronEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:799462158"
        message_obj = _CronMessageObj()
        message_str = "定时任务结果"

        def get_platform_name(self):
            return "cron"

        def get_sender_id(self):
            return "799462158"

        def get_sender_name(self):
            return "Scheduler"

        def get_group_id(self):
            return ""

    adapted = AstrbotEventAdapter.from_event(_CronEvent(), "aiocqhttp:group:799462158")

    assert adapted.platform == "aiocqhttp"
    assert adapted.session_id == "799462158"
    assert adapted.group_id == "799462158"


def test_llm_request_injects_profile_and_auto_memory():
    plugin = MemorixPlugin(
        DummyContext(),
        {
            "scope": {"mode": "group_global"},
            "retrieval": {"top_k_final": 10, "auto_inject": {"enabled": True, "top_k": 5}},
            "person_profile": {"enabled": True},
        },
    )
    plugin.profile_service = _FakeProfileService()
    plugin.query_service = _FakeQueryService()

    async def _enabled(_adapted, _user_id=""):
        return True

    plugin._is_adapted_chat_enabled = _enabled
    request = _FakeProviderRequest(prompt="我喜欢什么游戏？", system_prompt="原始系统提示", extra_user_content_parts=[])

    asyncio.run(plugin.on_llm_request(_FakeEvent(), request))

    assert request.system_prompt == "原始系统提示"
    assert request.prompt == "我喜欢什么游戏？"
    assert len(request.extra_user_content_parts) == 1
    injected_text = request.extra_user_content_parts[0].text
    assert request.extra_user_content_parts[0].model_dump_for_context().get("_no_save") is True
    assembled = asyncio.run(request.assemble_context())
    # 真实 astrbot assemble_context 顺序：prompt 在前，extra_user_content_parts 在后。
    assert assembled["content"][0] == {"type": "text", "text": "我喜欢什么游戏？"}
    assert assembled["content"][1]["text"] == injected_text
    assert assembled["content"][1].get("_no_save") is True
    assert MEMORY_INJECTION_MARKER in injected_text
    assert "【人物画像-内部参考】" in injected_text
    assert "小明喜欢深夜玩 RPG" in injected_text
    assert "【长期记忆-自动检索】" in injected_text
    assert "小明之前说自己喜欢 RPG" in injected_text
    assert "当前消息不应被注入" not in injected_text

    assert plugin.query_service.calls[0]["scope_key"] == "aiocqhttp:group:group-1"
    assert plugin.query_service.calls[0]["stream_id"] == "session-1"
    assert plugin.query_service.calls[0]["source"] == "chat:aiocqhttp:session-1"
    assert plugin.query_service.calls[0]["strict_source"] is True
    assert plugin.query_service.calls[0]["enforce_chat_filter"] is True
    assert plugin.profile_service.upsert_calls[0]["group_name"] == "测试群"


def test_llm_request_profile_injection_collects_at_and_reply_candidates():
    plugin = MemorixPlugin(
        DummyContext(),
        {
            "scope": {"mode": "group_global"},
            "retrieval": {"auto_inject": {"enabled": False}},
            "person_profile": {"enabled": True, "injection_max_profiles": 3},
        },
    )
    plugin.profile_service = _FakeProfileService()
    plugin.query_service = _FakeQueryService()

    async def _enabled(_adapted, _user_id=""):
        return True

    plugin._is_adapted_chat_enabled = _enabled
    request = SimpleNamespace(prompt="帮我看看大家喜欢什么", system_prompt="", extra_user_content_parts=[])

    asyncio.run(plugin.on_llm_request(_FakeGroupEvent(), request))

    injected_text = request.extra_user_content_parts[0].text
    assert "来源: recent_speaker" in injected_text
    assert "来源: at_user" in injected_text
    assert "来源: reply_sender" in injected_text
    assert "小明喜欢深夜玩 RPG" in injected_text
    assert "小红喜欢深夜玩 RPG" in injected_text
    assert "小蓝喜欢深夜玩 RPG" in injected_text
    assert [call["person_id"] for call in plugin.profile_service.query_calls] == [
        "aiocqhttp:user-1",
        "aiocqhttp:user-2",
        "aiocqhttp:user-3",
    ]


def test_profile_injection_text_compacts_structured_profile():
    text = build_profile_injection_text(
        "\n".join(
            [
                "# 人物画像",
                "人物ID: aiocqhttp:user-1",
                "主称呼: 小明",
                "",
                "## 身份设定",
                "- 学生",
                "",
                "## 关系设定",
                "- 和机器人熟悉",
                "",
                "## 稳定了解",
                "- 喜欢 RPG",
                "",
                "## 相处偏好",
                "- 喜欢直接建议",
                "",
                "## 近期互动",
                "- 昨天聊过显卡",
                "- 今天聊过游戏",
                "- 过旧的近期互动",
                "",
                "## 不确定信息",
                "- 可能喜欢 FPS",
                "",
                "## 维护备注",
                "- 自动画像仅供内部参考",
            ]
        )
    )

    assert "## 身份设定" in text
    assert "- 喜欢 RPG" in text
    assert "- 今天聊过游戏" in text
    assert "过旧的近期互动" not in text
    assert "## 不确定信息" not in text
    assert "## 维护备注" not in text


def test_llm_request_injection_respects_chat_filter():
    plugin = MemorixPlugin(
        DummyContext(),
        {
            "scope": {"mode": "group_global"},
            "retrieval": {"auto_inject": {"enabled": True}},
            "person_profile": {"enabled": True},
        },
    )
    plugin.profile_service = _FakeProfileService()
    plugin.query_service = _FakeQueryService()

    async def _disabled(_adapted, _user_id=""):
        return False

    plugin._is_adapted_chat_enabled = _disabled
    request = SimpleNamespace(prompt="我喜欢什么游戏？", system_prompt="原始系统提示", extra_user_content_parts=[])

    asyncio.run(plugin.on_llm_request(_FakeEvent(), request))

    assert request.system_prompt == "原始系统提示"
    assert request.extra_user_content_parts == []
    assert plugin.profile_service.query_calls == []
    assert plugin.query_service.calls == []


def test_search_memory_result_is_formatted_for_llm():
    text = _format_search_result_for_llm(
        {
            "query_type": "search",
            "query": "自亦飞瑶",
            "count": 2,
            "results": [
                {
                    "hash": "a9f54621d5a34581bfe6ce3cf89e099d24e3070dd7759d5dadf451fa9a4c30db",
                    "type": "paragraph",
                    "score": 0.5,
                    "content": "小真寻表示不知自亦飞瑶是谁。",
                    "metadata": {
                        "time_meta": {
                            "effective_start_text": "2026/05/21 12:48",
                            "effective_end_text": "2026/05/21 12:48",
                        }
                    },
                },
                {
                    "hash": "12130686cbb3c531c5d3b5c34cf09563d85a185b8c2d5e1fd3a2ac68ab513bb4",
                    "type": "paragraph",
                    "score": 0.0,
                    "content": "成员询问自亦飞瑶，未查到具体行为。",
                },
            ],
            "scope": "aiocqhttp",
            "chat_id": "722568590",
        },
        limit=10,
    )

    assert "【Memorix 长期记忆检索结果】" in text
    assert "命中：2 条" in text
    assert "证据列表：" in text
    assert "不要编造" in text
    assert "小真寻表示不知自亦飞瑶是谁" in text
    assert "未找到匹配的长期记忆" not in text


def test_content_router_auto_directs_fact_candidate_only():
    router = MemoryContentRouter({"ingest": {"memory_write_mode": "auto"}})
    fact_route = router.route_message(role="user", text="我喜欢深夜打游戏，也经常玩 RPG")
    chat_route = router.route_message(role="user", text="今天这个天气真不错")
    assistant_route = router.route_message(role="assistant", text="我喜欢帮你记录信息")

    assert fact_route.store_transcript is True
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


class _FakeRuntimeManager:
    def __init__(self, ctx):
        self.ctx = ctx

    async def get_runtime(self, _scope_key):
        return SimpleNamespace(context=self.ctx)


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


class _FakeGraphStore:
    def save(self):
        return None


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


class _StaticFactService(PersonFactWritebackService):
    async def _complete(self, ctx, prompt, item):
        del ctx, prompt, item
        return '["小明喜欢深夜打游戏"]'


def test_person_fact_writeback_stores_paragraph_and_registry_points(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        ctx = SimpleNamespace(
            metadata_store=metadata_store,
            vector_store=_FakeVectorStore(),
            graph_store=_FakeGraphStore(),
            embedding_manager=_FakeEmbeddingManager(),
            provider_bridge=None,
            llm_client=None,
        )
        service = _StaticFactService(
            _FakeRuntimeManager(ctx),
            {
                "person_fact_writeback": {
                    "enabled": True,
                    "update_registry_memory_points": True,
                }
            },
        )
        item = PersonFactWritebackItem(
            scope_key="default",
            session_id="s1",
            user_text="我喜欢深夜打游戏",
            assistant_text="我记住了你喜欢深夜打游戏。",
            user_id="u1",
            group_id="g1",
            group_name="测试群",
            platform="qq",
            sender_name="小明",
            message_id="m1",
            timestamp=123.0,
        )

        asyncio.run(service._handle_item(item))

        record = metadata_store.get_person_registry("qq:u1")
        assert record is not None
        assert "小明喜欢深夜打游戏" in record["memory_points"]
        paragraphs = metadata_store.get_paragraphs_by_source("person_fact:s1:qq:u1")
        assert len(paragraphs) == 1
        assert "小明喜欢深夜打游戏" in paragraphs[0]["content"]
        assert paragraphs[0]["metadata"]["group_name"] == "测试群"
    finally:
        metadata_store.close()


class _RejectingChatCtx:
    config = {"retrieval": {"temporal": {"default_top_k": 10}}}

    def get_config(self, key: str, default=None):
        current = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def is_chat_enabled(self, **_kwargs):
        return False


def test_chat_filter_matches_maibot_empty_list_semantics():
    ctx = AppContext.__new__(AppContext)

    ctx.config = {"filter": {"enabled": True, "mode": "blacklist", "chats": []}}
    assert ctx.is_chat_enabled(stream_id="s1", group_id="g1", user_id="u1") is True

    ctx.config = {"filter": {"enabled": True, "mode": "whitelist", "chats": []}}
    assert ctx.is_chat_enabled(stream_id="s1", group_id="g1", user_id="u1") is False

    ctx.config = {"filter": {"enabled": True, "mode": "blacklist", "chats": ["group:g1"]}}
    assert ctx.is_chat_enabled(stream_id="s1", group_id="g1", user_id="u1") is False
    assert ctx.is_chat_enabled(stream_id="s1", group_id="g2", user_id="u1") is True

    ctx.config = {"filter": {"enabled": True, "mode": "whitelist", "chats": ["user:u1", "stream:s2"]}}
    assert ctx.is_chat_enabled(stream_id="s1", group_id="g1", user_id="u1") is True
    assert ctx.is_chat_enabled(stream_id="s2", group_id="g2", user_id="u2") is True
    assert ctx.is_chat_enabled(stream_id="s3", group_id="g3", user_id="u3") is False


def test_ingest_message_respects_chat_filter_before_storage():
    service = IngestService(
        _FakeRuntimeManager(_RejectingChatCtx()),
        {"ingest": {"memory_write_mode": "both", "skip_empty_text": True}},
    )

    result = asyncio.run(
        service.ingest_message(
            scope_key="default",
            session_id="s1",
            role="user",
            content="我喜欢 RPG",
            source="chat:test:s1",
            user_id="u1",
            group_id="g1",
        )
    )

    assert result["skipped"] is True
    assert result["reason"] == "chat_filtered"
    assert result["result"]["transcript"]["stored"] is False


def test_ingest_message_records_group_name_in_transcript_metadata(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        ctx = SimpleNamespace(metadata_store=metadata_store)
        service = IngestService(
            _FakeRuntimeManager(ctx),
            {"ingest": {"memory_write_mode": "transcript_only", "skip_empty_text": True}},
        )

        result = asyncio.run(
            service.ingest_message(
                scope_key="default",
                session_id="s1",
                role="user",
                content="今天聊 RPG",
                source="chat:test:s1",
                user_id="u1",
                group_id="g1",
                group_name="测试群",
                platform="qq",
                unified_msg_origin="qq:GroupMessage:g1",
            )
        )

        assert result["result"]["transcript"]["stored"] is True
        session = metadata_store.get_transcript_session("s1")
        assert session["metadata"]["group_name"] == "测试群"
        messages = metadata_store.get_transcript_messages("s1")
        assert messages[0]["metadata"]["group_name"] == "测试群"
    finally:
        metadata_store.close()


def test_summary_importer_preserves_group_name_metadata(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        metadata_store.upsert_transcript_session(
            session_id="s1",
            source="chat:test:s1",
            metadata={
                "group_id": "g1",
                "group_name": "测试群",
                "platform": "qq",
                "unified_msg_origin": "qq:GroupMessage:g1",
            },
        )
        metadata_store.append_transcript_messages(
            session_id="s1",
            messages=[{"role": "user", "content": "今天聊 RPG"}],
        )
        importer = SummaryImporter(
            vector_store=_FakeVectorStore(),
            graph_store=_FakeGraphStore(),
            metadata_store=metadata_store,
            embedding_manager=_FakeEmbeddingManager(),
            plugin_config={"summarization": {"default_knowledge_type": "narrative"}},
            llm_client=None,
        )

        ok, message = asyncio.run(
            importer.import_from_transcript(
                session_id="s1",
                messages=[],
                source="chat_summary:s1",
                context_length=5,
            )
        )

        assert ok is True, message
        assert metadata_store.get_transcript_session("s1")["metadata"]["group_name"] == "测试群"
        paragraphs = metadata_store.get_paragraphs_by_source("chat_summary:s1")
        assert paragraphs[0]["metadata"]["group_name"] == "测试群"
    finally:
        metadata_store.close()


def test_person_fact_writeback_respects_chat_filter():
    class _ExplodingMetadataStore:
        def get_person_registry(self, *_args, **_kwargs):
            raise AssertionError("filtered chat should not touch person registry")

    ctx = _RejectingChatCtx()
    ctx.metadata_store = _ExplodingMetadataStore()
    service = PersonFactWritebackService(
        _FakeRuntimeManager(ctx),
        {"person_fact_writeback": {"enabled": True}},
    )
    item = PersonFactWritebackItem(
        scope_key="default",
        session_id="s1",
        user_text="我喜欢 RPG",
        assistant_text="我记住了。",
        user_id="u1",
        group_id="g1",
        platform="qq",
    )

    asyncio.run(service._handle_item(item))


def test_cron_llm_response_skips_person_fact_writeback():
    class _CronMessageObj:
        session_id = "799462158"
        message_id = "cron-msg"
        timestamp = 123
        message = []

    class _CronEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:799462158"
        message_obj = _CronMessageObj()
        message_str = "定时任务结果"

        def get_platform_name(self):
            return "cron"

        def get_sender_id(self):
            return "799462158"

        def get_sender_name(self):
            return "Scheduler"

        def get_group_id(self):
            return ""

    class _PersonFactQueue:
        async def enqueue(self, **_kwargs):
            raise AssertionError("cron event should not enqueue person fact writeback")

    class _SummaryService:
        async def maybe_enqueue_auto_summary(self, **_kwargs):
            return {"queued": False}

    plugin = MemorixPlugin(DummyContext(), {"scope": {"mode": "group_global"}})
    plugin.person_fact_writeback_service = _PersonFactQueue()
    plugin.summary_service = _SummaryService()

    async def _enabled(_adapted, _user_id=""):
        return True

    async def _format(_event):
        return "定时任务结果"

    async def _ingest(_event, _role, _text):
        return True

    plugin._is_adapted_chat_enabled = _enabled
    plugin._format_event_text_for_memory = _format
    plugin._ingest_event_message = _ingest

    asyncio.run(plugin.on_llm_response(_CronEvent(), SimpleNamespace(completion_text="已完成")))


def test_tool_sender_upsert_skips_cron_event():
    class _CronMessageObj:
        session_id = "799462158"
        message_id = "cron-msg"
        timestamp = 123
        message = []

    class _CronEvent:
        unified_msg_origin = "aiocqhttp:GroupMessage:799462158"
        message_obj = _CronMessageObj()
        message_str = "定时任务结果"

        def get_platform_name(self):
            return "cron"

        def get_sender_id(self):
            return "799462158"

        def get_sender_name(self):
            return "Scheduler"

        def get_group_id(self):
            return ""

    class _ProfileService:
        async def upsert_registry_from_event(self, **_kwargs):
            raise AssertionError("cron tool call should not upsert current sender")

    plugin = SimpleNamespace(profile_service=_ProfileService())
    tool = MemorixIngestTextTool(plugin=plugin)

    asyncio.run(tool._upsert_current_sender(_CronEvent(), "aiocqhttp:group:799462158", respect_filter=False))


def test_episode_and_aggregate_queries_respect_chat_filter():
    service = QueryService(_RejectingChatCtx())

    episode = asyncio.run(
        service.episode(
            query="RPG",
            stream_id="s1",
            group_id="g1",
            user_id="u1",
            enforce_chat_filter=True,
        )
    )
    aggregate = asyncio.run(
        service.aggregate(
            query="RPG",
            stream_id="s1",
            group_id="g1",
            user_id="u1",
            enforce_chat_filter=True,
        )
    )

    assert episode["filtered"] is True
    assert episode["results"] == []
    assert aggregate["filtered"] is True
    assert aggregate["mixed_results"] == []


def test_passive_event_ingest_skips_filtered_chat():
    class _Context:
        pass

    class _ProfileService:
        called = False

        async def upsert_registry_from_event(self, **_kwargs):
            self.called = True

    class _IngestService:
        called = False

        async def ingest_message(self, **_kwargs):
            self.called = True
            return {"success": True, "skipped": False}

    class _Event:
        unified_msg_origin = "umo:s1"
        message_str = "我喜欢 RPG"
        message_obj = SimpleNamespace(session_id="s1", message_id="m1", timestamp=123)

        def get_platform_name(self):
            return "qq"

        def get_sender_id(self):
            return "u1"

        def get_sender_name(self):
            return "小明"

        def get_group_id(self):
            return "g1"

        def get_self_id(self):
            return "bot"

    plugin = MemorixPlugin(_Context(), {"scope": {"mode": "group_global"}})
    plugin.runtime_manager = _FakeRuntimeManager(_RejectingChatCtx())
    plugin.profile_service = _ProfileService()
    plugin.ingest_service = _IngestService()

    result = asyncio.run(plugin._ingest_event_message(_Event(), "user", "我喜欢 RPG"))

    assert result is False
    assert plugin.profile_service.called is False
    assert plugin.ingest_service.called is False
