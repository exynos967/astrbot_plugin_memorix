import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from astrbot_plugin_memorix.memorix.core.retrieval.graph_evidence import calibrate_weights, grounding_factor
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.services.memory_projection_service import MemoryProjectionService


@pytest.fixture
def store(tmp_path):
    value = MetadataStore(tmp_path)
    value.connect()
    try:
        yield value
    finally:
        value.close()


def test_nested_crud_commit_does_not_escape_rollback(store):
    with pytest.raises(RuntimeError, match="abort"):
        with store.transaction(immediate=True):
            store.add_paragraph("应回滚的段落")
            with store.transaction():
                store.add_entity("应回滚的实体")
            raise RuntimeError("abort")
    assert store.count_paragraphs() == 0
    assert store.count_entities() == 0
    assert store._conn.execute("SELECT COUNT(*) FROM memory_projection_jobs").fetchone()[0] == 0


def test_worker_connection_cannot_commit_another_thread(store):
    connection = store._conn
    with store.transaction(immediate=True):
        store.add_paragraph("尚未提交")
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_connection, count = pool.submit(lambda: (store._conn, store.count_paragraphs())).result()
        assert worker_connection is not connection
        assert count == 0
    assert store.count_paragraphs() == 1


def test_delete_and_restore_shared_relation_evidence(store):
    first = store.add_paragraph("Alice 认识 Bob", source="one")
    second = store.add_paragraph("另一个来源也确认 Alice 认识 Bob", source="two")
    entity = store.add_entity("Alice", source_paragraph=first)
    relation = store.add_relation("Alice", "认识", "Bob", source_paragraph=first)
    store._conn.execute("INSERT OR IGNORE INTO paragraph_relations VALUES (?, ?)", (second, relation))
    store._conn.commit()
    preview = store.preview_memory_delete("paragraph", [first])
    assert preview["counts"] == {"paragraph": 1, "entity": 0, "relation": 0}
    result = store.delete_memories("paragraph", [first])
    assert store.get_paragraph(first)["is_deleted"] == 1
    assert store.get_relation(relation) is not None
    restored = store.restore_memory_operation(result["operation_id"])
    assert restored["restored"] == 1
    assert store._conn.execute(
        "SELECT 1 FROM paragraph_entities WHERE paragraph_hash = ? AND entity_hash = ?", (first, entity)
    ).fetchone()
    assert store._conn.execute(
        "SELECT 1 FROM paragraph_relations WHERE paragraph_hash = ? AND relation_hash = ?", (first, relation)
    ).fetchone()


def test_old_restore_cannot_undo_a_later_delete(store):
    paragraph = store.add_paragraph("反复删除恢复")
    first = store.delete_memories("paragraph", [paragraph])
    store.restore_paragraph_by_hash(paragraph)
    second = store.delete_memories("paragraph", [paragraph])
    store.restore_memory_operation(first["operation_id"])
    assert store.get_paragraph(paragraph)["is_deleted"] == 1
    assert store.memory_deletion_owner("paragraph", paragraph) == second["operation_id"]
    store.restore_memory_operation(second["operation_id"])
    assert store.get_paragraph(paragraph)["is_deleted"] == 0


def test_failed_delete_rolls_back_state_audit_and_jobs(store, monkeypatch):
    paragraph = store.add_paragraph("原子删除")
    store._conn.execute("DELETE FROM memory_projection_jobs")
    store._conn.commit()

    def fail(*args):
        raise RuntimeError("outbox failure")

    monkeypatch.setattr(store, "enqueue_memory_projection", fail)
    with pytest.raises(RuntimeError, match="outbox failure"):
        store.delete_memories("paragraph", [paragraph])
    assert store.get_paragraph(paragraph)["is_deleted"] == 0
    assert store.list_delete_operations() == []
    assert store._conn.execute("SELECT COUNT(*) FROM memory_projection_jobs").fetchone()[0] == 0


def test_old_episode_result_cannot_replace_new_revision(store):
    store.add_paragraph("第一条消息", source="chat")
    job = store.claim_episode_rebuild("config-a", source="chat")
    assert job
    store.add_paragraph("生成期间的新消息", source="chat")
    result = store.publish_episode_rebuild(job, [], "config-a")
    assert result["status"] == "superseded"
    newer = store.claim_episode_rebuild("config-a", source="chat")
    assert newer["claimed_revision"] > job["claimed_revision"]
    assert store.publish_episode_rebuild(newer, [], "config-a")["status"] == "done"


def test_expired_episode_lease_is_reclaimed_and_old_owner_cannot_publish(store):
    store.enqueue_episode_rebuilds(["chat"])
    old = store.claim_episode_rebuild("a", source="chat")
    store._conn.execute("UPDATE episode_rebuild_sources SET lease_until = 0 WHERE source = 'chat'")
    store._conn.commit()
    new = store.claim_episode_rebuild("a", source="chat")
    assert new["lease_token"] != old["lease_token"]
    assert store.publish_episode_rebuild(old, [], "a")["status"] == "superseded"
    assert store.publish_episode_rebuild(new, [], "b")["status"] == "superseded"


def test_projection_failure_is_durable_and_new_revision_is_not_acknowledged(store):
    paragraph = store.add_paragraph("重试索引")
    service = MemoryProjectionService(SimpleNamespace(metadata_store=store, graph_mutation_lock=asyncio.Lock()))

    async def fail(*args):
        raise RuntimeError("index offline")

    service._project = fail
    assert asyncio.run(service.reconcile())["pending"] == 1
    row = store._conn.execute("SELECT * FROM memory_projection_jobs").fetchone()
    assert row["attempts"] == 1
    assert row["last_error"] == "index offline"
    store.enqueue_memory_projection("paragraph", paragraph)

    async def changed(*args):
        store.enqueue_memory_projection("paragraph", paragraph)

    service._project = changed
    assert asyncio.run(service.reconcile())["pending"] == 1


def test_clear_includes_manual_facts_and_keeps_later_manual_retraction(store):
    claim = store.upsert_fact_claim(
        scope_type="person", scope_id="p1", fact_key="city", value_text="北京", authority="manual"
    )
    store.set_person_profile_alias_override(person_id="p1", aliases=["Alice"])
    deleted = store.delete_memories("clear", [])
    assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
    assert store.get_person_profile_alias_override("p1") is None
    store.retract_fact_claim(claim["claim_id"], reason="later_manual_retraction")
    store.set_person_profile_alias_override(person_id="p1", aliases=["新别名"])
    store.restore_memory_operation(deleted["operation_id"])
    assert store.get_fact_claim(claim["claim_id"])["status"] == "retracted"
    assert store.get_person_profile_alias_override("p1")["aliases"] == ["新别名"]


def test_graph_grounding_and_weight_calibration():
    evidence = {
        "type": "relation",
        "hash": "r",
        "subject": "Alice",
        "predicate": "认识",
        "object": "Bob",
        "normalized_score": 1.0,
    }
    grounded = grounding_factor(evidence, {"content": "Ａｌｉｃｅ 认识 Bob"})
    ungrounded = grounding_factor(evidence, {"content": "无关内容"})
    assert grounded > ungrounded
    candidates = [{"scores": {"graph_evidence": 1.0}, "evidence": [{**evidence, "grounding_factor": ungrounded}]}]
    semantic, sparse, graph, _estimate = calibrate_weights(
        candidates, semantic_weight=0.3, sparse_weight=0.1, graph_weight=0.6, scan_limit=5
    )
    assert graph < 0.6
    assert semantic + sparse + graph == pytest.approx(1.0)
