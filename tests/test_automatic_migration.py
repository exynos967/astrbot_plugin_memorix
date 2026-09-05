import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from astrbot_plugin_memorix.memorix.core.storage import migration
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import SCHEMA_VERSION, MetadataStore


def legacy_database(path, *, version=8, wal=False):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executescript("""
        CREATE TABLE paragraphs (
            hash TEXT PRIMARY KEY, content TEXT NOT NULL, vector_index INTEGER,
            created_at REAL, metadata TEXT, source TEXT, word_count INTEGER,
            is_permanent BOOLEAN DEFAULT 0, knowledge_type TEXT
        );
        CREATE TABLE relations (
            hash TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL,
            object TEXT NOT NULL, vector_index INTEGER, confidence REAL DEFAULT 1,
            created_at REAL, metadata TEXT, source_paragraph TEXT,
            is_inactive BOOLEAN DEFAULT 0
        );
        INSERT INTO paragraphs(hash, content, source, knowledge_type)
            VALUES ('paragraph-old', '旧记忆不会丢失', 'chat:test', 'legacy_unknown');
        INSERT INTO relations(hash, subject, predicate, object) VALUES ('relation-old', 'Alice', '认识', 'Bob');
    """)
    if version is not None:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        conn.execute("INSERT INTO schema_migrations VALUES (?, 1.0)", (version,))
    conn.commit()
    return conn


@pytest.mark.parametrize("version", [None, 8, 13])
def test_supported_databases_upgrade_on_connect_with_backup(tmp_path, version):
    db = tmp_path / "metadata.db"
    legacy_database(db, version=version).close()
    store = MetadataStore(tmp_path)
    try:
        store.connect()
        assert store.get_schema_version() == SCHEMA_VERSION
        assert store.get_paragraph("paragraph-old")["content"] == "旧记忆不会丢失"
        assert store.get_paragraph("paragraph-old")["knowledge_type"] == "mixed"
        for table in ("relations", "paragraphs"):
            columns = {row[1] for row in store._conn.execute(f"PRAGMA table_info({table})")}
            assert {"is_permanent", "last_accessed", "access_count"} <= columns
        backup = Path(store.migration_report["backup_path"])
        with closing(sqlite3.connect(backup)) as old:
            assert old.execute("SELECT content FROM paragraphs").fetchone()[0] == "旧记忆不会丢失"
            assert "last_accessed" not in {row[1] for row in old.execute("PRAGMA table_info(paragraphs)")}
    finally:
        store.close()
    again = MetadataStore(tmp_path)
    try:
        again.connect()
        assert again.migration_report is None
        assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    finally:
        again.close()


def test_backup_includes_committed_wal_pages(tmp_path):
    db = tmp_path / "metadata.db"
    original = legacy_database(db, wal=True)
    original.execute("INSERT INTO paragraphs(hash, content) VALUES ('wal-row', '尚未 checkpoint 的记忆')")
    original.commit()
    store = MetadataStore(tmp_path)
    try:
        store.connect()
        with closing(sqlite3.connect(store.migration_report["backup_path"])) as backup:
            assert (
                backup.execute("SELECT content FROM paragraphs WHERE hash='wal-row'").fetchone()[0]
                == "尚未 checkpoint 的记忆"
            )
    finally:
        store.close()
        original.close()


def test_v22_upgrade_preserves_native_data(tmp_path):
    original = MetadataStore(tmp_path)
    original.connect()
    paragraph = original.add_paragraph("上一版的记忆", source="scope:a")
    original._conn.execute("DELETE FROM schema_migrations")
    original._conn.execute("INSERT INTO schema_migrations VALUES (22, 1.0)")
    original._conn.commit()
    original.close()
    current = MetadataStore(tmp_path)
    try:
        current.connect()
        assert current.migration_report["from_version"] == 22
        assert current.get_schema_version() == SCHEMA_VERSION
        assert current.get_paragraph(paragraph)["content"] == "上一版的记忆"
    finally:
        current.close()


def test_failure_rolls_back_schema_data_and_version(tmp_path, monkeypatch):
    db = tmp_path / "metadata.db"
    legacy_database(db).close()
    store = MetadataStore(tmp_path)

    def fail_after_ddl():
        store._conn.execute("CREATE TABLE partial_migration (id INTEGER)")
        store._conn.execute("UPDATE paragraphs SET content='不应提交'")
        store._conn.commit()
        raise RuntimeError("simulated DDL failure")

    monkeypatch.setattr(store, "_migrate_schema", fail_after_ddl)
    with pytest.raises(RuntimeError, match="事务已回滚"):
        store.connect()
    assert store._conn is None
    with closing(sqlite3.connect(db)) as old:
        assert old.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
        assert old.execute("SELECT content FROM paragraphs").fetchone()[0] == "旧记忆不会丢失"
        assert old.execute("SELECT 1 FROM sqlite_master WHERE name='partial_migration'").fetchone() is None
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_backup_failure_prevents_schema_writes(tmp_path, monkeypatch):
    db = tmp_path / "metadata.db"
    legacy_database(db).close()

    def fail_backup(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(migration, "backup_database", fail_backup)
    store = MetadataStore(tmp_path)
    with pytest.raises(RuntimeError, match="disk full"):
        store.connect()
    with closing(sqlite3.connect(db)) as old:
        assert old.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
        assert "last_accessed" not in {row[1] for row in old.execute("PRAGMA table_info(paragraphs)")}


def test_future_version_is_rejected_before_opening_a_writer(tmp_path):
    db = tmp_path / "metadata.db"
    legacy_database(db, version=SCHEMA_VERSION + 1).close()
    before = db.read_bytes()
    with pytest.raises(RuntimeError, match="禁止自动降级"):
        MetadataStore(tmp_path).connect()
    assert db.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_current_version_with_incomplete_structure_is_rejected(tmp_path):
    db = tmp_path / "metadata.db"
    legacy_database(db, version=SCHEMA_VERSION).close()
    before = db.read_bytes()
    with pytest.raises(RuntimeError, match="缺少字段"):
        MetadataStore(tmp_path).connect()
    assert db.read_bytes() == before


def test_unrecognized_unversioned_database_is_not_modified(tmp_path):
    db = tmp_path / "metadata.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE paragraphs (hash TEXT PRIMARY KEY, content TEXT)")
        conn.commit()
    before = db.read_bytes()
    with pytest.raises(RuntimeError, match="无法识别旧库结构"):
        MetadataStore(tmp_path).connect()
    assert db.read_bytes() == before


def test_validation_failure_cannot_commit_new_version(tmp_path, monkeypatch):
    db = tmp_path / "metadata.db"
    legacy_database(db).close()

    def invalid_schema(conn):
        raise RuntimeError("invalid migrated schema")

    monkeypatch.setattr(migration, "validate_migrated_schema", invalid_schema)
    with pytest.raises(RuntimeError, match="invalid migrated schema"):
        MetadataStore(tmp_path).connect()
    with closing(sqlite3.connect(db)) as old:
        assert old.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
        assert old.execute("SELECT 1 FROM sqlite_master WHERE name='fact_claims'").fetchone() is None
