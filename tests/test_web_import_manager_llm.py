from __future__ import annotations

import asyncio
from types import SimpleNamespace

from astrbot_plugin_memorix.memorix.core.utils import web_import_manager
from astrbot_plugin_memorix.memorix.core.utils.model_routing import LLMResult
from astrbot_plugin_memorix.memorix.core.utils.web_import_manager import ImportTaskManager


def test_web_import_manager_llm_call_uses_plugin_llm_result_text(monkeypatch, tmp_path):
    plugin = SimpleNamespace()

    values = {
        "storage.data_dir": str(tmp_path),
        "web.import.timeout.llm_call_seconds": 0,
        "web.import.llm_retry.max_attempts": 0,
    }

    def get_config(key, default=None):
        return values.get(key, default)

    plugin.get_config = get_config
    seen = {}

    async def fake_generate_text(ctx, prompt, **kwargs):
        seen["ctx"] = ctx
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return LLMResult(success=True, text='{"entities": ["Alice"], "relations": []}')

    monkeypatch.setattr(web_import_manager, "generate_text", fake_generate_text)

    manager = ImportTaskManager(plugin)
    result = asyncio.run(manager._llm_call("extract this", plugin))

    assert result == {"entities": ["Alice"], "relations": []}
    assert seen["ctx"] is plugin
    assert seen["prompt"] == "extract this"
    assert seen["kwargs"]["request_type"] == "A_Memorix.WebImport"
