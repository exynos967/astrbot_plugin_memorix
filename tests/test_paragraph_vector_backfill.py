import asyncio

import numpy as np
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.paragraph_vector_service import ParagraphVectorWriteService


class _FakeVectorStore:
    def __init__(self):
        self.ids = set()

    def __contains__(self, item):
        return item in self.ids

    def add(self, vectors, ids):
        assert vectors.shape == (1, 4)
        self.ids.update(ids)
        return len(ids)


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


def test_paragraph_vector_backfill_marks_missing_paragraph_ready(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        paragraph_hash = metadata_store.add_paragraph(content="需要回填向量的段落", source="test")
        vector_store = _FakeVectorStore()
        service = ParagraphVectorWriteService(
            metadata_store=metadata_store,
            vector_store=vector_store,
            embedding_manager=_FakeEmbeddingManager(),
        )

        result = asyncio.run(service.backfill_missing_vectors(batch_size=10))

        assert result["written"] == 1
        assert paragraph_hash in vector_store.ids
        assert metadata_store.get_paragraph(paragraph_hash)["vector_index"] == 1
        assert metadata_store.list_paragraphs_for_vector_backfill(limit=10) == []
    finally:
        metadata_store.close()
