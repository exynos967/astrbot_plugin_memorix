import asyncio
import json
import types

from astrbot_plugin_memorix.memorix.services.profile_service import ProfileService


class _FakeMetadataStore:
    def __init__(self):
        self.record = {
            "person_id": "aiocqhttp:10001",
            "person_name": "手工名称",
            "nickname": "手工昵称",
            "user_id": "10001",
            "platform": "aiocqhttp",
            "group_nick_name": json.dumps(
                [{"group_id": "old-group", "session_id": "old-session", "group_nick_name": "旧名", "updated_at": 1.0}],
                ensure_ascii=False,
            ),
            "memory_points": json.dumps(["喜欢羽毛球"], ensure_ascii=False),
            "metadata": {"custom": "keep", "host_identity": {"alias_names": ["旧名"]}},
        }
        self.saved_payload = None

    def get_person_registry(self, person_id: str):
        if person_id == self.record["person_id"]:
            return dict(self.record)
        return None

    def upsert_person_registry(self, **kwargs):
        self.saved_payload = kwargs
        self.record = dict(kwargs)
        return kwargs


class _FakeRuntimeManager:
    def __init__(self, metadata_store):
        self.runtime = types.SimpleNamespace(context=types.SimpleNamespace(metadata_store=metadata_store))

    async def get_runtime(self, _scope_key: str):
        return self.runtime


def test_upsert_registry_from_event_preserves_manual_fields_and_merges_aliases():
    metadata_store = _FakeMetadataStore()
    service = ProfileService(_FakeRuntimeManager(metadata_store))

    asyncio.run(
        service.upsert_registry_from_event(
            scope_key="aiocqhttp:group:123",
            platform="aiocqhttp",
            sender_id="10001",
            sender_name="新名片",
            group_id="group-1",
            session_id="session-1",
            unified_msg_origin="umo-1",
            timestamp=1700000000.0,
        )
    )

    payload = metadata_store.saved_payload
    assert payload is not None
    assert payload["person_name"] == "手工名称"
    assert payload["nickname"] == "手工昵称"
    assert payload["memory_points"] == json.dumps(["喜欢羽毛球"], ensure_ascii=False)

    aliases = payload["group_nick_name"]
    alias_names = {item["group_nick_name"] for item in aliases}
    assert alias_names == {"旧名", "新名片"}

    host_identity = payload["metadata"]["host_identity"]
    assert payload["metadata"]["custom"] == "keep"
    assert host_identity["sender_id"] == "10001"
    assert set(host_identity["alias_names"]) == {"旧名", "新名片"}
