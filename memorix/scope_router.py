"""Scope routing strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCOPE_PATTERN = re.compile(r"[^0-9A-Za-z:._-]+")


def parse_unified_msg_origin(value: str) -> tuple[str, str, str] | None:
    parts = str(value or "").split(":", 2)
    if len(parts) != 3:
        return None
    platform, message_type, session_id = (part.strip() for part in parts)
    if not platform or not message_type or not session_id:
        return None
    return platform, message_type, session_id


@dataclass(slots=True)
class ScopeRouter:
    mode: str = "group_global"

    def resolve(self, event) -> str:
        mode = str(self.mode or "group_global").strip().lower()
        platform = self._safe_str(getattr(event, "get_platform_name", lambda: "unknown")()) or "unknown"
        sender = self._safe_str(getattr(event, "get_sender_id", lambda: "unknown")()) or "unknown"
        group = self._safe_str(getattr(event, "get_group_id", lambda: "")())
        umo = self._safe_str(getattr(event, "unified_msg_origin", ""))

        if platform == "cron":
            parsed = parse_unified_msg_origin(umo)
            if parsed is not None:
                platform, message_type, session_id = parsed
                sender = session_id or sender
                group = session_id if message_type == "GroupMessage" else ""

        if mode not in {"umo", "user_global", "group_global", "platform_global"}:
            mode = "group_global"

        if mode == "umo":
            return self._sanitize(umo or f"{platform}:{sender}")
        if mode == "user_global":
            return self._sanitize(f"{platform}:user:{sender}")
        if mode == "platform_global":
            return self._sanitize(platform)
        if group:
            return self._sanitize(f"{platform}:group:{group}")
        return self._sanitize(f"{platform}:user:{sender}")

    @staticmethod
    def _safe_str(value) -> str:
        return str(value or "").strip()

    @staticmethod
    def _sanitize(raw: str) -> str:
        text = str(raw or "default").strip()
        text = text.replace("/", "_").replace("\\", "_")
        text = re.sub(r"\s+", "_", text)
        text = _SCOPE_PATTERN.sub("_", text)
        text = text.strip("._")
        if ".." in text:
            text = text.replace("..", "_")
        if text in {"", ".", ".."}:
            return "default"
        return text or "default"
