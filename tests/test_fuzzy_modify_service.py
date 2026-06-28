from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from astrbot_plugin_memorix.memorix.amemorix.services import fuzzy_modify_service
from astrbot_plugin_memorix.memorix.amemorix.services.fuzzy_modify_service import (
    FuzzyModifyService,
)
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.model_routing import LLMResult


def _build_config(*, enabled: bool) -> Dict[str, Any]:
    """构造 integration.fuzzy_modify 配置字典，其余键取默认。"""
    return {
        "integration": {
            "fuzzy_modify": {
                "enabled": enabled,
                "candidate_limit": 20,
                "confirm_threshold": 0.85,
                "auto_execute_enabled": False,
                "max_targets": 10,
                "allow_global_scope": False,
            }
        },
    }


def _build_ctx(store: MetadataStore, config: Dict[str, Any], paragraph_hash: str) -> SimpleNamespace:
    """构造最小可用 ctx：metadata_store 真实，person_profile_service 返回指向段落的证据。"""

    class _FakeProfileService:
        async def query_person_profile(
            self,
            *,
            person_id: str = "",
            person_keyword: str = "",
            top_k: int = 12,
            ttl_seconds: float = 60.0,
            force_refresh: bool = False,
            source_note: str = "",
        ) -> Dict[str, Any]:
            return {
                "success": True,
                "person_id": person_id or "alice",
                "relation_edges": [],
                "vector_evidence": [
                    {
                        "hash": paragraph_hash,
                        "type": "paragraph",
                        "content": "Alice lives in Beijing",
                        "metadata": {"source": "chat"},
                    }
                ],
            }

    async def _noop_save_all() -> None:
        return None

    ctx = SimpleNamespace(
        metadata_store=store,
        config=config,
        person_profile_service=_FakeProfileService(),
        paragraph_vector_service=None,
        relation_write_service=None,
        episode_service=None,
        retriever=None,
        vector_store=None,
        embedding_manager=None,
        save_all=_noop_save_all,
    )
    ctx.get_config = _make_get_config(config)
    return ctx


def _make_get_config(config: Dict[str, Any]):
    def _get_config(key: str, default: Any = None) -> Any:
        current: Any = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    return _get_config


def _install_llm_mock(monkeypatch: pytest.MonkeyPatch, plan_payload: Dict[str, Any]) -> None:
    """把 generate_text 替换为返回构造 JSON plan 的桩，不打真实 LLM。"""

    async def _fake_generate_text(ctx: Any, prompt: str, *, request_type: str = "") -> LLMResult:
        return LLMResult(success=True, text=json.dumps(plan_payload, ensure_ascii=False))

    monkeypatch.setattr(fuzzy_modify_service, "generate_text", _fake_generate_text)


def _test_impl(monkeypatch, tmp_path):
    """共享真实路径主体：preview → list → execute → rollback。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        paragraph_hash = store.add_paragraph(
            content="Alice lives in Beijing",
            source="chat",
            metadata={"source": "chat"},
            knowledge_type="factual",
        )

        plan_payload = {
            "confidence": 0.9,
            "risk_level": "low",
            "reason": "user_correction",
            "operations": [
                {
                    "action": "mark_superseded",
                    "candidate_id": f"paragraph:{paragraph_hash}",
                    "reason": "用户指出 Alice 实际住上海",
                    "valid_to": None,
                }
            ],
        }
        _install_llm_mock(monkeypatch, plan_payload)

        config = _build_config(enabled=True)
        ctx = _build_ctx(store, config, paragraph_hash)

        service = FuzzyModifyService(runtime_manager=None, plugin_config=config)

        # preview
        preview = asyncio.run(
            service.preview(
                ctx,
                request_text="Alice 实际住上海",
                scope="person_profile",
                person_id="alice",
                requested_by="tester",
            )
        )
        assert preview["success"] is True
        plan_id = preview["plan_id"]
        assert plan_id

        # list
        listed = asyncio.run(service.list(ctx, status="awaiting_confirmation"))
        assert listed["success"] is True
        assert any(item["plan_id"] == plan_id for item in listed["items"])

        # execute（confirmed=True，跳过确认阈值）
        executed = asyncio.run(service.execute(ctx, plan_id=plan_id, confirmed=True, requested_by="tester"))
        assert executed["success"] is True
        assert executed["plan"]["status"] == "executed"

        # rollback
        rolled = asyncio.run(service.rollback(ctx, plan_id=plan_id, requested_by="tester"))
        assert rolled["success"] is True
        assert rolled["plan"]["status"] == "rolled_back"
    finally:
        store.close()


def test_fuzzy_modify_full_roundtrip(monkeypatch, tmp_path):
    _test_impl(monkeypatch, tmp_path)


def test_fuzzy_modify_disabled_returns_error(tmp_path):
    """enabled=False 时所有公共方法应直接返回 fuzzy_modify_disabled。"""
    store = MetadataStore(tmp_path)
    store.connect()
    try:
        config = _build_config(enabled=False)
        ctx = SimpleNamespace(
            metadata_store=store,
            config=config,
            person_profile_service=None,
            paragraph_vector_service=None,
            relation_write_service=None,
            episode_service=None,
            retriever=None,
            vector_store=None,
            embedding_manager=None,
        )
        ctx.get_config = _make_get_config(config)

        service = FuzzyModifyService(runtime_manager=None, plugin_config=config)

        preview = asyncio.run(
            service.preview(ctx, request_text="x", scope="person_profile", person_id="alice")
        )
        assert preview == {"success": False, "error": "fuzzy_modify_disabled"}

        executed = asyncio.run(service.execute(ctx, plan_id="any", confirmed=True))
        assert executed == {"success": False, "error": "fuzzy_modify_disabled"}

        listed = asyncio.run(service.list(ctx))
        assert listed == {"success": False, "error": "fuzzy_modify_disabled"}

        got = asyncio.run(service.get(ctx, plan_id="any"))
        assert got == {"success": False, "error": "fuzzy_modify_disabled"}

        rolled = asyncio.run(service.rollback(ctx, plan_id="any"))
        assert rolled == {"success": False, "error": "fuzzy_modify_disabled"}

        reconciled = asyncio.run(service.reconcile(ctx))
        assert reconciled == {"success": False, "error": "fuzzy_modify_disabled"}
    finally:
        store.close()


def test_fuzzy_modify_service_public_methods_exist():
    """接口完整性：六个公共方法应存在于服务类上。"""
    for name in ("preview", "execute", "get", "list", "rollback", "reconcile"):
        assert callable(getattr(FuzzyModifyService, name, None)), f"缺失公共方法: {name}"


def test_fuzzy_modify_default_config_values():
    """fuzzy_modify 默认安全值应满足硬性约束。"""
    from astrbot_plugin_memorix.memorix.amemorix.settings import DEFAULT_CONFIG

    fuzzy_cfg = DEFAULT_CONFIG["integration"]["fuzzy_modify"]
    assert fuzzy_cfg["enabled"] is False
    assert fuzzy_cfg["auto_execute_enabled"] is False
    assert fuzzy_cfg["confirm_threshold"] == 0.85
    assert fuzzy_cfg["allow_global_scope"] is False
