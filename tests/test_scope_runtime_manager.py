import json

from astrbot_plugin_memorix.memorix import app_context
from astrbot_plugin_memorix.memorix.app_context import ScopeRuntimeManager


def test_list_scope_keys_hides_internal_cron_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(app_context, "get_astrbot_data_path", lambda: str(tmp_path))
    scopes_dir = tmp_path / "plugin_data" / "astrbot_plugin_memorix" / "scopes"

    normal_scope = scopes_dir / "aiocqhttp_group_123"
    normal_scope.mkdir(parents=True)
    (normal_scope / ".scope.json").write_text(
        json.dumps({"scope_key": "aiocqhttp:group:123"}),
        encoding="utf-8",
    )

    cron_scope = scopes_dir / "cron_group_123"
    cron_scope.mkdir()
    (cron_scope / ".scope.json").write_text(
        json.dumps({"scope_key": "cron:group:123"}),
        encoding="utf-8",
    )

    manager = ScopeRuntimeManager(plugin_name="astrbot_plugin_memorix", plugin_config={})

    assert manager.list_scope_keys() == ["aiocqhttp:group:123"]


def test_scope_dir_collision_uses_hash_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(app_context, "get_astrbot_data_path", lambda: str(tmp_path))
    manager = ScopeRuntimeManager(plugin_name="astrbot_plugin_memorix", plugin_config={})

    first = manager._scope_dir("aiocqhttp:group:foo:bar")
    second = manager._scope_dir("aiocqhttp:group:foo_bar")

    assert first != second
    assert (first / ".scope.json").exists()
    assert (second / ".scope.json").exists()
