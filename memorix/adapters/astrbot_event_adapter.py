"""AstrBot event adapter."""

from __future__ import annotations

from dataclasses import dataclass

from ..scope_router import parse_unified_msg_origin


@dataclass(slots=True)
class MemorixEvent:
    scope_key: str
    platform: str
    unified_msg_origin: str
    session_id: str
    sender_id: str
    sender_name: str
    group_id: str
    group_name: str
    message_id: str
    message_text: str
    timestamp: int


class AstrbotEventAdapter:
    @staticmethod
    def _clean_text(value) -> str:
        text = str(value or "").strip()
        if text.upper() == "N/A":
            return ""
        return text

    @classmethod
    def _group_name_from_event(cls, message_obj) -> str:
        group = getattr(message_obj, "group", None)
        group_name = cls._clean_text(getattr(group, "group_name", ""))
        if group_name:
            return group_name

        raw_message = getattr(message_obj, "raw_message", None)
        getter = getattr(raw_message, "get", None)
        if callable(getter):
            group_name = cls._clean_text(getter("group_name", ""))
            if group_name:
                return group_name
        return cls._clean_text(getattr(raw_message, "group_name", ""))

    @staticmethod
    def from_event(event, scope_key: str) -> MemorixEvent:
        platform = str(getattr(event, "get_platform_name", lambda: "unknown")() or "unknown")
        unified_msg_origin = str(getattr(event, "unified_msg_origin", "") or "")
        original_message_type = ""
        original_session_id = ""
        if platform == "cron":
            parsed = parse_unified_msg_origin(unified_msg_origin)
            if parsed is not None:
                platform, original_message_type, original_session_id = parsed
        message_obj = getattr(event, "message_obj", None)
        session_id = str(getattr(message_obj, "session_id", "") or original_session_id or unified_msg_origin)
        sender_id = str(getattr(event, "get_sender_id", lambda: "")() or "")
        sender_name = str(getattr(event, "get_sender_name", lambda: "")() or "")
        group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
        if not group_id and original_message_type == "GroupMessage":
            group_id = original_session_id
        group_name = AstrbotEventAdapter._group_name_from_event(message_obj)
        message_id = str(getattr(message_obj, "message_id", "") or "")
        message_text = str(getattr(event, "message_str", "") or "").strip()
        try:
            timestamp = int(getattr(message_obj, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            timestamp = 0
        return MemorixEvent(
            scope_key=scope_key,
            platform=platform,
            unified_msg_origin=unified_msg_origin,
            session_id=session_id,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            group_name=group_name,
            message_id=message_id,
            message_text=message_text,
            timestamp=timestamp,
        )
