from __future__ import annotations

from astrbot_plugin_memorix.memorix.core.storage.metadata_store import (
    SCHEMA_VERSION,
    MetadataStore,
)


def test_schema_version_is_15() -> None:
    """SCHEMA 15 升级后模块级版本号应为 15。"""
    assert SCHEMA_VERSION == 15


def test_fuzzy_modify_plans_table_and_indexes_exist(tmp_path) -> None:
    """connect() 后应建出 fuzzy 计划表及其三个索引。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        conn = store._conn
        assert conn is not None
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "memory_fuzzy_modify_plans" in tables

        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_memory_fuzzy_modify_plans_created" in indexes
        assert "idx_memory_fuzzy_modify_plans_status_updated" in indexes
        assert "idx_memory_fuzzy_modify_plans_target" in indexes
    finally:
        store.close()


def test_paragraph_stale_relation_marks_has_source_columns(tmp_path) -> None:
    """paragraph_stale_relation_marks 表应含 source_type/source_id/source_operation_id 三列。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(paragraph_stale_relation_marks)").fetchall()
        }
        assert {"source_type", "source_id", "source_operation_id"} <= columns
    finally:
        store.close()


def test_fuzzy_modify_plan_crud_roundtrip(tmp_path) -> None:
    """create → get → list → update 状态流转可读回正确值。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        created = store.create_fuzzy_modify_plan(
            request_text="Alice 实际住上海",
            scope="person_profile",
            plan={"operations": [{"action": "mark_superseded"}]},
            preview={"requires_confirmation": True},
            target_person_id="alice",
            target_chat_id="",
            status="awaiting_confirmation",
            confidence=0.9,
            requested_by="tester",
            reason="user_correction",
            plan_id="plan_test_1",
        )
        assert created["plan_id"] == "plan_test_1"
        assert created["status"] == "awaiting_confirmation"
        assert created["plan"]["operations"][0]["action"] == "mark_superseded"

        fetched = store.get_fuzzy_modify_plan("plan_test_1")
        assert fetched is not None
        assert fetched["request_text"] == "Alice 实际住上海"
        assert fetched["confidence"] == 0.9

        listed = store.list_fuzzy_modify_plans(statuses=["awaiting_confirmation"])
        assert any(item["plan_id"] == "plan_test_1" for item in listed)

        updated = store.update_fuzzy_modify_plan("plan_test_1", status="applied")
        assert updated is not None
        assert updated["status"] == "applied"

        refetched = store.get_fuzzy_modify_plan("plan_test_1")
        assert refetched is not None
        assert refetched["status"] == "applied"
    finally:
        store.close()


def test_fuzzy_modify_plan_scope_filter(tmp_path) -> None:
    """list 按 scope 过滤生效。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        store.create_fuzzy_modify_plan(
            request_text="r1",
            scope="memory",
            plan={},
            plan_id="plan_scope_mem",
        )
        store.create_fuzzy_modify_plan(
            request_text="r2",
            scope="person_profile",
            plan={},
            plan_id="plan_scope_profile",
        )
        only_memory = store.list_fuzzy_modify_plans(scope="memory")
        assert {item["plan_id"] for item in only_memory} == {"plan_scope_mem"}
        only_profile = store.list_fuzzy_modify_plans(scope="person_profile")
        assert {item["plan_id"] for item in only_profile} == {"plan_scope_profile"}
    finally:
        store.close()
