"""Person profile wrapper service."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from ..amemorix.services.person_profile_service import PersonProfileApiService
from ..app_context import ScopeRuntimeManager


class ProfileService:
    def __init__(self, runtime_manager: ScopeRuntimeManager):
        self.runtime_manager = runtime_manager

    @staticmethod
    def _parse_group_aliases(raw_value: Any) -> list[Dict[str, Any]]:
        if not raw_value:
            return []
        values = raw_value
        if isinstance(raw_value, str):
            try:
                values = json.loads(raw_value)
            except Exception:
                values = [raw_value]
        if not isinstance(values, list):
            return []

        aliases: list[Dict[str, Any]] = []
        for item in values:
            if isinstance(item, dict):
                name = str(item.get("group_nick_name", "") or "").strip()
                if not name:
                    continue
                aliases.append(
                    {
                        "group_id": str(item.get("group_id", "") or "").strip(),
                        "session_id": str(item.get("session_id", "") or "").strip(),
                        "group_nick_name": name,
                        "updated_at": item.get("updated_at"),
                    }
                )
                continue
            text = str(item or "").strip()
            if text:
                aliases.append({"group_nick_name": text})
        return aliases

    @staticmethod
    def _merge_group_aliases(
        existing: Any,
        *,
        group_id: str,
        session_id: str,
        sender_name: str,
        updated_at: float,
    ) -> list[Dict[str, Any]]:
        aliases = ProfileService._parse_group_aliases(existing)
        name = str(sender_name or "").strip()
        if not name:
            return aliases

        target_group = str(group_id or "").strip()
        target_session = str(session_id or "").strip()
        matched = False
        for item in aliases:
            alias_name = str(item.get("group_nick_name", "") or "").strip()
            alias_group = str(item.get("group_id", "") or "").strip()
            alias_session = str(item.get("session_id", "") or "").strip()
            if alias_name == name and alias_group == target_group and alias_session == target_session:
                item["updated_at"] = updated_at
                matched = True
                break
        if not matched:
            aliases.append(
                {
                    "group_id": target_group,
                    "session_id": target_session,
                    "group_nick_name": name,
                    "updated_at": updated_at,
                }
            )
        return aliases

    async def upsert_registry_from_event(
        self,
        *,
        scope_key: str,
        platform: str,
        sender_id: str,
        sender_name: str,
        group_id: str = "",
        group_name: str = "",
        session_id: str = "",
        unified_msg_origin: str = "",
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        person_id = f"{platform}:{sender_id}"
        metadata_store = runtime.context.metadata_store
        existing = metadata_store.get_person_registry(person_id) or {}
        now = float(timestamp) if timestamp else time.time()

        existing_meta = existing.get("metadata")
        merged_meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
        host_identity = merged_meta.get("host_identity")
        if not isinstance(host_identity, dict):
            host_identity = {}

        alias_names = host_identity.get("alias_names")
        if not isinstance(alias_names, list):
            alias_names = []
        current_name = str(sender_name or "").strip()
        if current_name and current_name not in alias_names:
            alias_names.append(current_name)

        host_identity.update(
            {
                "sender_id": str(sender_id or "").strip(),
                "session_id": str(session_id or "").strip(),
                "group_id": str(group_id or "").strip(),
                "group_name": str(group_name or "").strip(),
                "unified_msg_origin": str(unified_msg_origin or "").strip(),
                "alias_names": alias_names[-10:],
                "last_seen_at": now,
            }
        )
        merged_meta["host_identity"] = host_identity
        merged_meta["scope_key"] = scope_key

        merged_group_aliases = self._merge_group_aliases(
            existing.get("group_nick_name"),
            group_id=group_id,
            session_id=session_id,
            sender_name=current_name,
            updated_at=now,
        )

        existing_person_name = str(existing.get("person_name", "") or "").strip()
        existing_nickname = str(existing.get("nickname", "") or "").strip()
        resolved_name = existing_person_name or current_name or str(sender_id or "").strip()
        resolved_nickname = existing_nickname or current_name or resolved_name

        return runtime.context.metadata_store.upsert_person_registry(
            person_id=person_id,
            person_name=resolved_name,
            nickname=resolved_nickname,
            user_id=sender_id,
            platform=platform,
            group_nick_name=merged_group_aliases,
            memory_points=existing.get("memory_points"),
            last_know=now,
            metadata=merged_meta,
        )

    async def query(
        self,
        *,
        scope_key: str,
        person_id: str = "",
        person_keyword: str = "",
        top_k: int = 12,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        service = PersonProfileApiService(runtime.context)
        return await service.query(
            person_id=person_id,
            person_keyword=person_keyword,
            top_k=top_k,
            force_refresh=force_refresh,
            source_note="astrbot:profile_query",
        )

    async def is_injection_enabled(
        self,
        *,
        scope_key: str,
        session_id: str,
        user_id: str,
    ) -> bool:
        del session_id, user_id
        runtime = await self.runtime_manager.get_runtime(scope_key)
        return bool(runtime.context.get_config("person_profile.enabled", True))

    async def set_override(self, *, scope_key: str, person_id: str, override_text: str, updated_by: str = "astrbot"):
        runtime = await self.runtime_manager.get_runtime(scope_key)
        service = PersonProfileApiService(runtime.context)
        return await service.set_override(person_id=person_id, override_text=override_text, updated_by=updated_by)

    async def delete_override(self, *, scope_key: str, person_id: str):
        runtime = await self.runtime_manager.get_runtime(scope_key)
        service = PersonProfileApiService(runtime.context)
        return await service.delete_override(person_id=person_id)

    async def upsert_registry(self, *, scope_key: str, payload: Dict[str, Any]):
        runtime = await self.runtime_manager.get_runtime(scope_key)
        service = PersonProfileApiService(runtime.context)
        return await service.upsert_registry(payload)

    async def list_registry(
        self,
        *,
        scope_key: str,
        keyword: str = "",
        page: int = 1,
        page_size: Optional[int] = None,
    ):
        runtime = await self.runtime_manager.get_runtime(scope_key)
        service = PersonProfileApiService(runtime.context)
        return await service.list_registry(keyword=keyword, page=page, page_size=page_size)
