import asyncio
import sys
import types
from dataclasses import dataclass

import numpy as np


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

from astrbot_plugin_memorix.memorix.core.utils.person_profile_service import PersonProfileService  # noqa: E402
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore  # noqa: E402
from astrbot_plugin_memorix.memorix.services.person_fact_writeback_service import (  # noqa: E402
    PersonFactWritebackItem,
    PersonFactWritebackService,
)


@dataclass
class _FakeResult:
    hash_value: str
    result_type: str
    score: float
    content: str
    metadata: dict


class _FakeRetriever:
    async def retrieve(self, query: str, top_k: int):
        del query, top_k
        return [
            _FakeResult(
                hash_value="p_related",
                result_type="paragraph",
                score=0.91,
                content="小明今天去看了展览。",
                metadata={},
            ),
            _FakeResult(
                hash_value="p_noise",
                result_type="paragraph",
                score=0.98,
                content="今天下雨，路上很堵。",
                metadata={},
            ),
            _FakeResult(
                hash_value="r_related",
                result_type="relation",
                score=0.77,
                content="小明 喜欢 羽毛球",
                metadata={"subject": "小明", "object": "羽毛球"},
            ),
            _FakeResult(
                hash_value="r_noise",
                result_type="relation",
                score=0.88,
                content="小红 住在 北京",
                metadata={"subject": "小红", "object": "北京"},
            ),
        ]


class _FakeMetadataStore:
    pass


def test_collect_vector_evidence_filters_unrelated_items():
    service = PersonProfileService(
        metadata_store=_FakeMetadataStore(),
        retriever=_FakeRetriever(),
    )
    evidence = asyncio.run(service._collect_vector_evidence(["小明"], top_k=8))
    hashes = {item.get("hash") for item in evidence}
    assert hashes == {"p_related", "r_related"}


class _FakeRuntimeManager:
    def __init__(self, ctx):
        self.ctx = ctx

    async def get_runtime(self, _scope_key):
        return types.SimpleNamespace(context=self.ctx)


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


def test_parse_fact_list_extracts_json_array_from_text():
    facts = PersonFactWritebackService._parse_fact_list('结果如下：\n["A 喜欢猫", "A 喜欢猫", "短"]')
    assert facts == ["A 喜欢猫"]


def test_person_fact_writeback_stores_paragraph_and_registry_points(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        ctx = types.SimpleNamespace(
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
    finally:
        metadata_store.close()
