import sqlite3

import pytest
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import SCHEMA_VERSION, MetadataStore


def test_connect_patches_legacy_episode_position_column(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            paragraph_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE episode_paragraphs (
            episode_id TEXT NOT NULL,
            paragraph_hash TEXT NOT NULL,
            PRIMARY KEY (episode_id, paragraph_hash)
        );
        INSERT INTO paragraphs(hash, content, created_at, updated_at, source, word_count, is_deleted)
        VALUES ('p1', 'hello', 1.0, 1.0, 'source-a', 1, 0);
        INSERT INTO episodes(episode_id, source, title, summary, paragraph_count, created_at, updated_at)
        VALUES ('e1', 'source-a', 'title', 'summary', 1, 1.0, 1.0);
        INSERT INTO episode_paragraphs(episode_id, paragraph_hash) VALUES ('e1', 'p1');
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
        assert store.get_episode_paragraphs("e1")[0]["position"] == 0
    finally:
        store.close()


def test_connect_patches_legacy_transcript_position_column(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE entities (
            hash TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            vector_index INTEGER,
            appearance_count INTEGER DEFAULT 1,
            created_at REAL,
            metadata TEXT,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            vector_index INTEGER,
            confidence REAL DEFAULT 1.0,
            created_at REAL,
            source_paragraph TEXT,
            metadata TEXT
        );
        CREATE TABLE transcript_sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            metadata_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE transcript_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at REAL NOT NULL
        );
        INSERT INTO transcript_sessions(session_id, source, metadata_json, created_at, updated_at)
        VALUES ('s1', 'chat', '{{}}', 1.0, 1.0);
        INSERT INTO transcript_messages(session_id, role, content, metadata_json, created_at)
        VALUES ('s1', 'user', 'hello', '{{}}', 1.0);
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(transcript_messages)").fetchall()}
        assert "position" in columns
        assert store.get_transcript_messages("s1")[0]["position"] == 0
    finally:
        store.close()


def test_transcript_summary_state_cursor_roundtrip(tmp_path):
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        store.upsert_transcript_session(session_id="s1", source="chat", metadata={})
        store.append_transcript_messages(
            session_id="s1",
            messages=[
                {"role": "user", "content": "hello", "created_at": 10.0},
                {"role": "assistant", "content": "hi", "created_at": 11.0},
            ],
        )

        state = store.mark_transcript_summary_complete(
            session_id="s1",
            task_id="task-1",
            metadata={"trigger": "test"},
        )

        assert state["session_id"] == "s1"
        assert state["last_task_id"] == "task-1"
        assert state["last_message_created_at"] == 11.0
        assert state["summary_count"] == 1
        assert state["metadata"]["trigger"] == "test"

        second = store.mark_transcript_summary_complete(session_id="s1", last_message_created_at=12.0)
        assert second["summary_count"] == 2
        assert second["last_message_created_at"] == 12.0
    finally:
        store.close()


def test_existing_version_db_still_gets_episode_position_patch(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            paragraph_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE episode_paragraphs (
            episode_id TEXT NOT NULL,
            paragraph_hash TEXT NOT NULL,
            PRIMARY KEY (episode_id, paragraph_hash)
        );
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
    finally:
        store.close()

    reopened = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in reopened.execute("PRAGMA table_info(episode_paragraphs)").fetchall()}
        assert "position" in columns
    finally:
        reopened.close()


def test_connect_patches_037_metadata_columns_and_summary_state(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES ({SCHEMA_VERSION}, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE entities (
            hash TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            vector_index INTEGER,
            appearance_count INTEGER DEFAULT 1,
            created_at REAL,
            metadata TEXT,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            vector_index INTEGER,
            confidence REAL DEFAULT 1.0,
            created_at REAL,
            source_paragraph TEXT,
            metadata TEXT
        );
        CREATE TABLE person_registry (
            person_id TEXT PRIMARY KEY,
            person_name TEXT,
            nickname TEXT,
            user_id TEXT,
            platform TEXT,
            group_nick_name TEXT,
            memory_points TEXT,
            last_know REAL,
            metadata TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE transcript_sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            metadata TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE transcript_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts REAL,
            metadata TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE transcript_summary_state (
            session_id TEXT PRIMARY KEY,
            last_summary_at REAL,
            last_message_created_at REAL,
            updated_at REAL
        );
        INSERT INTO person_registry(
            person_id, person_name, nickname, user_id, platform, group_nick_name,
            memory_points, last_know, metadata, created_at, updated_at
        ) VALUES ('p1', 'Alice', 'ali', 'u1', 'aiocqhttp', '["群名"]', '["point"]', 1.0, '{{"legacy":true}}', 1.0, 2.0);
        INSERT INTO transcript_sessions(session_id, source, metadata, created_at, updated_at)
        VALUES ('s1', 'chat', '{{"scope":"old"}}', 1.0, 2.0);
        INSERT INTO transcript_messages(session_id, role, content, ts, metadata, created_at)
        VALUES ('s1', 'user', 'hello', 1.5, '{{"kind":"old-msg"}}', 1.5);
        INSERT INTO transcript_messages(session_id, role, content, ts, metadata, created_at)
        VALUES ('s1', 'assistant', 'world', 2.5, '{{"kind":"old-reply"}}', 2.5);
        INSERT INTO transcript_summary_state(session_id, last_summary_at, last_message_created_at, updated_at)
        VALUES ('s1', 2.0, 2.5, 3.0);
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        transcript_session_columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(transcript_sessions)").fetchall()
        }
        transcript_message_columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(transcript_messages)").fetchall()
        }
        transcript_state_columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(transcript_summary_state)").fetchall()
        }
        person_columns = {row[1] for row in store._conn.execute("PRAGMA table_info(person_registry)").fetchall()}

        assert "metadata_json" in transcript_session_columns
        assert {"position", "metadata_json"} <= transcript_message_columns
        assert {"last_task_id", "summary_count", "metadata_json", "created_at"} <= transcript_state_columns
        assert "metadata_json" in person_columns

        assert store.get_transcript_session("s1")["metadata"]["scope"] == "old"
        messages = store.get_transcript_messages("s1")
        assert [item["position"] for item in messages] == [0, 1]
        assert [item["metadata"]["kind"] for item in messages] == ["old-msg", "old-reply"]
        assert store.get_person_registry("p1")["metadata"]["legacy"] is True

        state = store.get_transcript_summary_state("s1")
        assert state["last_task_id"] == ""
        assert state["summary_count"] == 0
        assert state["created_at"] == 3.0

        assert store.append_transcript_messages(
            session_id="s1",
            messages=[{"role": "assistant", "content": "hi", "created_at": 4.0}],
        ) == 1
        messages = store.get_transcript_messages("s1")
        assert [item["position"] for item in messages] == [0, 1, 2]

        updated_state = store.mark_transcript_summary_complete(
            session_id="s1",
            last_message_created_at=4.0,
            task_id="task-2",
        )
        assert updated_state["last_task_id"] == "task-2"
        assert updated_state["summary_count"] == 1
    finally:
        store.close()


# 历史 schema 迁移测试：用于校验老库平滑迁移与离线脚本兜底路径。
_VNEXT_TABLES = (
    "episodes",
    "episode_paragraphs",
    "episode_pending_paragraphs",
    "episode_rebuild_sources",
    "paragraph_vector_backfill",
    "memory_feedback_tasks",
    "memory_feedback_action_logs",
    "paragraph_stale_relation_marks",
    "person_profile_refresh_queue",
    "external_memory_refs",
    "memory_v5_operations",
    "delete_operations",
    "delete_operation_items",
    "relation_hash_aliases",
)


@pytest.mark.skipif(SCHEMA_VERSION < 13, reason="vendored core 未升级到 2.0.0 (SCHEMA_VERSION>=13)")
def test_v8_legacy_db_migrates_to_current_schema(tmp_path):
    """模拟历史 v8 库，离线迁移后应出现全部 vNext 表且老数据保留。"""

    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES (8, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            vector_index INTEGER,
            confidence REAL DEFAULT 1.0,
            created_at REAL,
            source_paragraph TEXT,
            metadata TEXT
        );
        INSERT INTO paragraphs(hash, content, created_at, updated_at, source, word_count)
        VALUES ('p1', 'legacy paragraph', 1.0, 1.0, 'source-a', 2);
        INSERT INTO relations(hash, subject, predicate, object, confidence, created_at)
        VALUES ('r1', 'Alice', 'likes', 'cats', 0.9, 1.0);
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    # enforce_schema=False 走离线迁移路径，避免 v8 库在 _assert_schema_compatible 直接抛错。
    store.connect(enforce_schema=False)
    try:
        store._migrate_schema()
        store.rebuild_relation_hash_aliases()
        store.normalize_paragraph_knowledge_types()
        store.set_schema_version(SCHEMA_VERSION)
        if store._conn is not None:
            store._conn.commit()
    finally:
        store.close()

    after = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in after.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in _VNEXT_TABLES:
            assert table in tables, f"迁移后缺失 vNext 表: {table}"

        version_row = after.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert int(version_row[0] or 0) == SCHEMA_VERSION

        # 老数据应保留。
        kept = after.execute("SELECT content FROM paragraphs WHERE hash='p1'").fetchone()
        assert kept is not None and kept[0] == "legacy paragraph"
        kept_rel = after.execute("SELECT subject FROM relations WHERE hash='r1'").fetchone()
        assert kept_rel is not None and kept_rel[0] == "Alice"
    finally:
        after.close()


def test_schema_13_db_runtime_auto_migrates_to_15(tmp_path):
    """模拟上一版 schema=13 库，connect() 应自动迁移到当前 schema=15。"""

    assert SCHEMA_VERSION == 15
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO schema_migrations(version, applied_at) VALUES (13, 1.0);
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            vector_index INTEGER,
            created_at REAL,
            updated_at REAL,
            metadata TEXT,
            source TEXT,
            word_count INTEGER,
            event_time REAL,
            event_time_start REAL,
            event_time_end REAL,
            time_granularity TEXT,
            time_confidence REAL DEFAULT 1.0,
            knowledge_type TEXT DEFAULT 'mixed',
            is_permanent BOOLEAN DEFAULT 0,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            vector_index INTEGER,
            confidence REAL DEFAULT 1.0,
            created_at REAL,
            source_paragraph TEXT,
            metadata TEXT
        );
        CREATE TABLE paragraph_stale_relation_marks (
            paragraph_hash TEXT NOT NULL,
            relation_hash TEXT NOT NULL,
            reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (paragraph_hash, relation_hash)
        );
        INSERT INTO paragraphs(hash, content, created_at, updated_at, source, word_count)
        VALUES ('p13', 'schema 13 paragraph', 1.0, 1.0, 'source-13', 3);
        """
    )
    conn.commit()
    conn.close()

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        assert store.get_schema_version() == 15
        tables = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "memory_fuzzy_modify_plans" in tables

        stale_columns = {
            row[1]
            for row in store._conn.execute("PRAGMA table_info(paragraph_stale_relation_marks)").fetchall()
        }
        assert {"source_type", "source_id", "source_operation_id"} <= stale_columns

        kept = store._conn.execute("SELECT content FROM paragraphs WHERE hash='p13'").fetchone()
        assert kept is not None and kept[0] == "schema 13 paragraph"
    finally:
        store.close()


@pytest.mark.skipif(SCHEMA_VERSION < 13, reason="vendored core 未升级到 2.0.0 (SCHEMA_VERSION>=13)")
def test_message_api_shim_reads_transcript_window(tmp_path):
    """feedback 垫片 MessageAPI 应从 transcript 表按时间窗取消息并解析身份。"""

    from astrbot_plugin_memorix.memorix.amemorix.common.message_api import MessageAPI

    store = MetadataStore(tmp_path)
    store.connect()
    try:
        store.upsert_transcript_session(
            session_id="s1",
            source="chat",
            metadata={"user_id": "u1", "group_id": "g1", "platform": "aiocqhttp"},
        )
        store.append_transcript_messages(
            session_id="s1",
            messages=[
                {"role": "user", "content": "earlier", "created_at": 5.0},
                {"role": "user", "content": "wrong, he is from Beijing", "created_at": 10.0},
                {"role": "user", "content": "/cmd", "created_at": 11.0},
            ],
        )

        api = MessageAPI(store)
        messages = api.get_messages_by_time_in_chat(
            chat_id="s1", start_time=6.0, end_time=12.0, limit=50, filter_command=True
        )
        contents = [m.processed_plain_text for m in messages]
        assert "earlier" not in contents  # 早于窗口
        assert "wrong, he is from Beijing" in contents
        assert "/cmd" not in contents  # 指令被过滤
        assert all(m.session_id == "s1" for m in messages)

        identity = api.get_existing_session_by_session_id("s1")
        assert identity is not None
        assert identity.user_id == "u1"
        assert identity.group_id == "g1"
    finally:
        store.close()
