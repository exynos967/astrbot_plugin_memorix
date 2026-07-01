import asyncio
import sqlite3
import sys
import time
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

from astrbot_plugin_memorix.memorix.amemorix.task_manager import TaskManager  # noqa: E402


class _FakeMetadataStore:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE TABLE transcript_messages (session_id TEXT, created_at REAL)"
        )
        self._tasks = {}
        self._summary_state = None

    def add_messages(self, session_id: str, count: int, created_at_start: float) -> None:
        rows = [(session_id, created_at_start + idx) for idx in range(count)]
        self._conn.executemany(
            "INSERT INTO transcript_messages (session_id, created_at) VALUES (?, ?)",
            rows,
        )
        self._conn.commit()

    def get_transcript_session(self, session_id: str):
        return {"session_id": session_id, "metadata": {"group_id": "g1", "user_id": "u1"}}

    def get_transcript_summary_state(self, _session_id: str):
        return self._summary_state

    def create_async_task(self, *, task_id: str, task_type: str, payload=None):
        task = {"task_id": task_id, "task_type": task_type, "payload": payload or {}}
        self._tasks[task_id] = task
        return task


class _FakeCtx:
    def __init__(self, metadata_store):
        self.metadata_store = metadata_store
        self.vector_store = object()
        self.graph_store = object()
        self.embedding_manager = object()
        self.provider_bridge = None
        self.astrbot_context = None
        self.config = {
            "embedding": {"openapi": {}},
            "summarization": {
                "enabled": True,
                "context_length": 50,
                "auto_import": {
                    "enabled": True,
                    "after_reply_only": True,
                    "min_new_messages": 12,
                    "cooldown_minutes": 30,
                },
            },
        }

    def get_config(self, key: str, default=None):
        current = self.config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def is_chat_enabled(self, **_kwargs):
        return True


def test_maybe_enqueue_auto_summary_respects_threshold_and_pending_state():
    metadata_store = _FakeMetadataStore()
    base_time = time.time() - 3600
    metadata_store.add_messages("s1", 11, base_time)
    manager = TaskManager(_FakeCtx(metadata_store))

    first = asyncio.run(manager.maybe_enqueue_auto_summary(session_id="s1"))
    assert first["queued"] is False
    assert first["reason"] == "insufficient_new_messages"

    metadata_store.add_messages("s1", 1, base_time + 11)
    second = asyncio.run(manager.maybe_enqueue_auto_summary(session_id="s1"))
    assert second["queued"] is True
    assert second["task_id"]

    third = asyncio.run(manager.maybe_enqueue_auto_summary(session_id="s1"))
    assert third["queued"] is False
    assert third["reason"] == "already_pending"


def test_maybe_enqueue_auto_summary_respects_cooldown():
    metadata_store = _FakeMetadataStore()
    base_time = time.time() - 3600
    metadata_store.add_messages("s2", 20, base_time)
    metadata_store._summary_state = {
        "session_id": "s2",
        "last_summary_at": time.time() - 60,
        "last_message_created_at": None,
    }
    manager = TaskManager(_FakeCtx(metadata_store))

    result = asyncio.run(manager.maybe_enqueue_auto_summary(session_id="s2"))
    assert result["queued"] is False
    assert result["reason"] == "cooldown"
