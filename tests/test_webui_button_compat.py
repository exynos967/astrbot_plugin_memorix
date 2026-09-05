import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    core_mod = types.ModuleType("astrbot.core")
    utils_mod = types.ModuleType("astrbot.core.utils")
    path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    api_mod.logger = _Logger()
    path_mod.get_astrbot_data_path = lambda *args, **kwargs: str(ROOT / ".test-astrbot-data")
    astrbot_mod.api = api_mod
    astrbot_mod.core = core_mod
    core_mod.utils = utils_mod
    utils_mod.astrbot_path = path_mod
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.utils"] = utils_mod
    sys.modules["astrbot.core.utils.astrbot_path"] = path_mod


_install_astrbot_stub()

from astrbot_plugin_memorix.memorix.amemorix.routers import v1_router  # noqa: E402
from astrbot_plugin_memorix.memorix.webui.plugin_page_bridge import (  # noqa: E402
    PluginPageWebUIBridge,
    _WebV1TaskManager,
)


class _FakeMetadataStore:
    def __init__(self):
        self.tasks = {}

    def create_async_task(self, *, task_id, task_type, payload, status="queued"):
        self.tasks[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "payload": payload,
            "status": status,
        }
        return self.tasks[task_id]

    def get_async_task(self, task_id):
        return self.tasks.get(task_id)

    def update_async_task(self, task_id, **updates):
        self.tasks[task_id].update(updates)
        return self.tasks[task_id]

    def get_transcript_session(self, _session_id):
        return None


class _FakeImportTaskManager:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []
        self._tasks = {}

    def is_enabled(self):
        return self.enabled

    async def create_paste_task(self, payload):
        self.calls.append(("paste", payload))
        self._tasks["native-paste"] = {
            "task_id": "native-paste",
            "status": "queued",
            "params": payload,
        }
        return {"task_id": "native-paste", "status": "queued"}

    async def create_raw_scan_task(self, payload):
        self.calls.append(("raw_scan", payload))
        return {"task_id": "native-scan", "status": "queued"}


class _FakeCtx:
    def __init__(self):
        self.config = {"embedding": {"retry": {}}}
        self.metadata_store = _FakeMetadataStore()
        self.vector_store = object()
        self.graph_store = object()
        self.embedding_manager = object()
        self.astrbot_context = None
        self.provider_bridge = types.SimpleNamespace(enabled=True, chat_provider_id="")

    def get_config(self, _key, default=None):
        return default


class _FakeBridgeResponse:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self):
        return self.payload


def _install_plugin_web_request(payload):
    web_mod = types.ModuleType("astrbot.api.web")

    class _Request:
        async def json(self, *args, **kwargs):
            del args, kwargs
            return payload

    def jsonify(data):
        return _FakeBridgeResponse(data)

    web_mod.request = _Request()
    web_mod.json_response = jsonify
    sys.modules["astrbot.api.web"] = web_mod


def test_webui_import_button_prefers_native_import_manager():
    ctx = _FakeCtx()
    native_import_manager = _FakeImportTaskManager(enabled=True)
    manager = _WebV1TaskManager(ctx, import_task_manager=native_import_manager)

    result = asyncio.run(
        manager.enqueue_import_task({"mode": "text", "payload": "hello", "options": {"source": "manual-source"}})
    )

    assert result["task_id"] == "native-paste"
    assert native_import_manager.calls == [
        ("paste", {"content": "hello", "name": "manual-source", "knowledge_type": "", "source": "manual-source"})
    ]
    assert manager.get_task("native-paste")["status"] == "queued"


def test_webui_bridge_preserves_frontend_errors():
    _install_plugin_web_request({"method": "GET", "url": "/api/graph"})
    bridge = PluginPageWebUIBridge(
        runtime_manager=object(),
        scope_resolver=lambda: "default",
    )

    async def _failing_dispatch(*, method, url, body=None):
        del method, url, body
        raise RuntimeError("boom")

    bridge.dispatch = _failing_dispatch
    response = asyncio.run(bridge.handle_request())

    assert response.get_json() == {"status": "error", "message": "boom"}


def test_query_event_histogram_keeps_two_hour_window(monkeypatch):
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(query_events=[])))

    monkeypatch.setattr(v1_router.time, "time", lambda: 1000.0)
    v1_router._record_query_event(request, "aggregate")
    monkeypatch.setattr(v1_router.time, "time", lambda: 1090.0)
    v1_router._record_query_event(request, "entity")

    monkeypatch.setattr(v1_router.time, "time", lambda: 4600.0)
    assert v1_router._recent_query_counts(request, seconds=60)["total"] == 0

    trend = v1_router._query_event_histogram(request, seconds=7200, bucket_seconds=300)
    assert trend["seconds"] == 7200
    assert trend["bucket_seconds"] == 300
    assert trend["total"] == 2
    assert len(trend["buckets"]) == 24
