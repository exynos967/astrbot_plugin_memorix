import asyncio
from types import SimpleNamespace

import pytest
from astrbot_plugin_memorix.memorix.amemorix.services.import_service import ImportService
from astrbot_plugin_memorix.memorix.amemorix.services.import_task_manager import ImportFileRecord, ImportTaskManager
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.strategies.chat_log import ChatLogStrategy
from astrbot_plugin_memorix.memorix.core.utils.import_payloads import (
    ImportPayloadValidationError,
    normalize_paragraph_import_item,
)
from astrbot_plugin_memorix.memorix.core.utils.person_profile_service import PersonProfileService
from astrbot_plugin_memorix.memorix.services.fact_admin_service import FactAdminService
from astrbot_plugin_memorix.memorix.services.graph_rename_service import GraphRenameService


@pytest.fixture
def store(tmp_path):
    metadata = MetadataStore(tmp_path)
    metadata.connect()
    try:
        yield metadata
    finally:
        metadata.close()


def test_v21_upgrade_keeps_paragraphs_and_local_queue(store):
    paragraph = store.add_paragraph("原有聊天证据", source="chat:test")
    store.enqueue_episode_pending(paragraph, source="chat:test")
    store._conn.execute("DROP TABLE person_profile_alias_overrides")
    store._conn.execute("DELETE FROM schema_migrations WHERE version >= 22")
    store._conn.execute("INSERT OR REPLACE INTO schema_migrations VALUES (21, 1.0)")
    store._conn.commit()
    store._assert_schema_compatible(db_existed=True)
    assert store.get_schema_version() == 23
    assert store.get_paragraph(paragraph)["content"] == "原有聊天证据"
    assert store._conn.execute("SELECT COUNT(*) FROM episode_pending_paragraphs").fetchone()[0] == 1
    store.set_person_profile_alias_override(person_id="user", aliases=[" Alice ", "alice", "艾丽丝"])
    assert store.get_person_profile_alias_override("user")["aliases"] == ["Alice", "艾丽丝"]


def test_fact_conflicts_require_explicit_supersession(store):
    first = store.upsert_fact_claim(scope_type="person", scope_id="p1", fact_key="city", value_text="北京", cardinality="single")
    second = store.upsert_fact_claim(scope_type="person", scope_id="p1", fact_key="city", value_text="上海", cardinality="single")
    assert first["status"] == "active"
    assert second["status"] == "conflicted"
    revised = store.upsert_fact_claim(
        scope_type="person", scope_id="p1", fact_key="city", value_text="上海", cardinality="single",
        supersedes_claim_ids=[first["claim_id"]],
    )
    assert revised["status"] == "active"
    assert store.get_fact_claim(first["claim_id"])["status"] == "superseded"


def test_fact_and_profile_refresh_share_a_transaction(store, monkeypatch):
    def fail_refresh(**kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(store, "enqueue_person_profile_refresh", fail_refresh)
    service = FactAdminService(SimpleNamespace(metadata_store=store, get_config=lambda key, default: default))
    with pytest.raises(RuntimeError, match="queue unavailable"):
        asyncio.run(service.memory_fact_admin(action="create", scope_id="p1", fact_key="city", value_text="北京"))
    assert store.list_fact_claims(scope_type="person", scope_id="p1") == []


def test_soft_delete_restores_fact_evidence_but_keeps_later_manual_retraction(store):
    paragraph = store.add_paragraph("人物证据", metadata={"person_ids": ["p1"]})
    claim = store.upsert_fact_claim(
        scope_type="person", scope_id="p1", fact_key="city", value_text="北京",
        evidence_type="paragraph", evidence_id=paragraph,
    )
    store.mark_as_deleted([paragraph], "paragraph")
    assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
    assert store.get_fact_evidence(claim["claim_id"]) == []
    assert store.restore_paragraph_by_hash(paragraph)
    assert store.get_fact_claim(claim["claim_id"])["status"] == "active"
    assert len(store.get_fact_evidence(claim["claim_id"])) == 1
    store.mark_as_deleted([paragraph], "paragraph")
    store.restore_fact_claim(claim["claim_id"], reason="manual_review")
    store.retract_fact_claim(claim["claim_id"], reason="manual_retraction")
    store.restore_paragraph_by_hash(paragraph)
    assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"


def test_rename_preserves_paragraph_links_and_records_projection_work(store):
    paragraph = store.add_paragraph("Alice 认识 Bob", source="chat:test")
    old_entity = store.add_entity("Alice", source_paragraph=paragraph)
    old_relation = store.add_relation("Alice", "认识", "Bob", source_paragraph=paragraph)
    result = GraphRenameService(SimpleNamespace(metadata_store=store))._rename_node("Alice", "Alicia")
    assert result["success"]
    new_relation = result["relation_hash_map"][old_relation]
    assert store.get_relation(old_relation) is None
    assert store.get_relation(new_relation)["subject"] == "Alicia"
    assert store._conn.execute("SELECT COUNT(*) FROM paragraph_entities WHERE entity_hash = ?", (old_entity,)).fetchone()[0] == 0
    assert store._conn.execute("SELECT relation_hash FROM paragraph_relations WHERE paragraph_hash = ?", (paragraph,)).fetchone()[0] == new_relation
    assert store._conn.execute("SELECT COUNT(*) FROM graph_pending_renames").fetchone()[0] == 1


def test_chat_log_preserves_an_oversized_message():
    message = "[12:00] Alice: " + "完整消息" * 100 + "\n"
    strategy = ChatLogStrategy("chat.txt", window_size=200, overlap=0)
    chunks = strategy.split(message + "[12:01] Bob: 收到\n")
    assert chunks[0].chunk.text == message
    assert strategy.oversized_message_count == 1


def test_import_person_ids_are_validated_and_preserved():
    result = normalize_paragraph_import_item({"content": "人物证据", "person_ids": [" p1 ", "p1", "p2"]}, default_source="test")
    assert result["person_ids"] == ["p1", "p2"]
    with pytest.raises(ImportPayloadValidationError, match="person_ids"):
        normalize_paragraph_import_item({"content": "人物证据", "person_ids": [123]}, default_source="test")


def test_repeated_chunk_retry_preserves_original_index():
    manager = object.__new__(ImportTaskManager)
    file = ImportFileRecord("file", "data.json", "paste", "json", retry_mode="chunk", retry_chunk_indexes=[2])
    units = manager._build_units('["第一段", "第二段", "第三段"]', file)
    assert len(units) == 1
    assert units[0]["index"] == 2
    assert units[0]["payload"]["content"] == "第三段"


def test_automatic_reingest_does_not_undo_manual_retraction(store):
    paragraph = store.add_paragraph("人物事实")
    payload = dict(scope_type="person", scope_id="p1", fact_key="city", value_text="北京", evidence_type="paragraph", evidence_id=paragraph)
    claim = store.upsert_fact_claim(**payload)
    store.retract_fact_claim(claim["claim_id"], reason="manual_retraction")
    store.delete_paragraph_atomic(paragraph)
    assert store.add_paragraph("人物事实") == paragraph
    assert store.upsert_fact_claim(**payload)["status"] == "retracted"


def test_registry_traits_are_not_reinjected_as_facts(store):
    service = PersonProfileService(metadata_store=store)
    text = service._build_profile_text("p1", "Alice", ["Alice"], [], [], ["已被撤回的旧事实"])
    assert "已被撤回的旧事实" not in text


def test_reimport_merges_person_associations(store):
    service = ImportService(SimpleNamespace(metadata_store=store))

    async def skip_vector(*args):
        return {"vector_state": "ready", "vector_written": True, "warning": ""}

    service._ensure_paragraph_vector = skip_vector

    async def run():
        first = await service.import_paragraph(content="人物证据", person_ids=["p1"])
        second = await service.import_paragraph(content="人物证据", person_ids=["p2"])
        assert first["hash"] == second["hash"]
        assert store.get_paragraph(first["hash"])["metadata"]["person_ids"] == ["p1", "p2"]

    asyncio.run(run())


def test_rename_merge_preserves_mention_counts(store):
    paragraph = store.add_paragraph("Alice 和 Alicia 是同一个人")
    old = store.add_entity("Alice", source_paragraph=paragraph)
    target = store.add_entity("Alicia", source_paragraph=paragraph)
    store._conn.execute("UPDATE paragraph_entities SET mention_count = 2 WHERE entity_hash = ?", (old,))
    store._conn.commit()
    result = GraphRenameService(SimpleNamespace(metadata_store=store))._rename_node("Alice", "Alicia")
    assert result["success"]
    assert store._conn.execute("SELECT mention_count FROM paragraph_entities WHERE entity_hash = ?", (target,)).fetchone()[0] == 3


def test_manual_alias_collision_does_not_pick_a_person(store):
    service = PersonProfileService(metadata_store=store)
    store.set_person_profile_alias_override(person_id="p1", aliases=["Alice"])
    assert service.resolve_person_id("ALICE") == "p1"
    store.set_person_profile_alias_override(person_id="p2", aliases=["Alice"])
    result = asyncio.run(service.query_person_profile(person_keyword="Alice"))
    assert result["success"] is False
    assert "person_id" in result["error"]
