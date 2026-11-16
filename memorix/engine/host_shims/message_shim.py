"""
Message & Identity Host Shim.
Bridges src.common.data_models.message_component_data_model, src.services.message_service,
src.common.message_repository, src.chat.utils.utils, and src.person_info.person_info.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Union

from . import config_shim


class BaseMessageComponentModel:
    """宿主消息组件轻量基类，确保 isinstance(..., Component) 类型安全。"""

    format_name: str = "unknown"


class TextComponent(BaseMessageComponentModel):
    format_name = "text"

    def __init__(self, text: str = "") -> None:
        self.text = str(text or "")
        self.type = "text"


class ImageComponent(BaseMessageComponentModel):
    format_name = "image"

    def __init__(self, *, binary_hash: str = "", content: str = "", binary_data: bytes = b"") -> None:
        self.binary_hash = str(binary_hash or "")
        self.content = str(content or "")
        self.binary_data = bytes(binary_data or b"")
        self.type = "image"


class EmojiComponent(BaseMessageComponentModel):
    format_name = "emoji"

    def __init__(self, *, binary_hash: str = "", content: str = "", binary_data: bytes = b"") -> None:
        self.binary_hash = str(binary_hash or "")
        self.content = str(content or "")
        self.binary_data = bytes(binary_data or b"")
        self.type = "emoji"


class VoiceComponent(BaseMessageComponentModel):
    format_name = "voice"

    def __init__(self, *, binary_hash: str = "", content: str = "", binary_data: bytes = b"") -> None:
        self.binary_hash = str(binary_hash or "")
        self.content = str(content or "")
        self.binary_data = bytes(binary_data or b"")
        self.type = "voice"


class FileComponent(BaseMessageComponentModel):
    format_name = "file"

    def __init__(self, *, name: str = "", content: str = "", **kwargs: Any) -> None:
        del kwargs
        self.name = str(name or "")
        self.content = str(content or "")
        self.type = "file"

    def to_plain_text(self) -> str:
        return self.content or (f"[文件:{self.name}]" if self.name else "[文件]")


class AtComponent(BaseMessageComponentModel):
    format_name = "at"

    def __init__(
        self,
        target_user_id: str = "",
        target_user_nickname: str = "",
        target_user_cardname: str = "",
    ) -> None:
        self.target_user_id = str(target_user_id or "")
        self.target_user_nickname = str(target_user_nickname or "")
        self.target_user_cardname = str(target_user_cardname or "")
        self.type = "at"


class ReplyComponent(BaseMessageComponentModel):
    format_name = "reply"

    def __init__(
        self,
        target_message_id: str = "",
        target_message_sender_id: str = "",
        target_message_sender_nickname: str = "",
        target_message_sender_cardname: str = "",
        target_message_content: str = "",
    ) -> None:
        self.target_message_id = str(target_message_id or "")
        self.target_message_sender_id = str(target_message_sender_id or "")
        self.target_message_sender_nickname = str(target_message_sender_nickname or "")
        self.target_message_sender_cardname = str(target_message_sender_cardname or "")
        self.target_message_content = str(target_message_content or "")
        self.type = "reply"


class ForwardNodeComponent(BaseMessageComponentModel):
    format_name = "forward"

    def __init__(self, forward_components: Optional[Sequence[Any]] = None) -> None:
        self.forward_components = list(forward_components or [])
        self.type = "forward"


class DictComponent(BaseMessageComponentModel):
    format_name = "dict"

    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        self.data = dict(data or {})
        self.type = "dict"


StandardMessageComponents = Union[
    TextComponent,
    ImageComponent,
    EmojiComponent,
    VoiceComponent,
    FileComponent,
    AtComponent,
    ReplyComponent,
    ForwardNodeComponent,
    DictComponent,
]


def _render_without_reply_components(
    message: Any,
    components: Sequence[Any],
    depth: int = 0,
) -> str:
    parts: List[str] = []
    for component in components or []:
        if isinstance(component, ReplyComponent):
            continue
        if isinstance(component, TextComponent):
            parts.append(component.text)
        elif isinstance(component, (ImageComponent, EmojiComponent, VoiceComponent)):
            if component.content:
                parts.append(component.content)
        elif isinstance(component, AtComponent):
            target = component.target_user_cardname or component.target_user_nickname or component.target_user_id
            if target:
                parts.append(f"@{target}")
        elif isinstance(component, FileComponent):
            parts.append(component.to_plain_text())
        elif isinstance(component, ForwardNodeComponent):
            nodes: List[str] = []
            for node in component.forward_components:
                sender = (
                    getattr(node, "user_cardname", "")
                    or getattr(node, "user_nickname", "")
                    or getattr(node, "user_id", "")
                    or "未知用户"
                )
                body = _render_without_reply_components(message, getattr(node, "content", []), depth + 1)
                nodes.append(f"{'-' * (depth + 1) * 2} 【{sender}】: {body}")
            parts.append("【合并转发消息: \n" + "\n".join(nodes) + "\n】")
        else:
            fallback = str(getattr(component, "text", "") or getattr(component, "content", "") or "").strip()
            if fallback:
                parts.append(fallback)
    return " ".join(p for p in parts if p)


def find_messages(*args: Any, **kwargs: Any) -> List[Any]:
    del args, kwargs
    return []


def count_messages(*args: Any, **kwargs: Any) -> int:
    del args, kwargs
    return 0


def get_messages_by_time(*args: Any, **kwargs: Any) -> List[Any]:
    del args, kwargs
    return []


def get_messages_by_time_in_chat(*args: Any, **kwargs: Any) -> List[Any]:
    del args, kwargs
    return []


def get_person_id(platform: str, user_id: str) -> str:
    raw_platform = str(platform or "").strip()
    identity_platform = raw_platform.split("-", 1)[1] if "-" in raw_platform else raw_platform
    return f"{identity_platform.strip()}:{str(user_id or '').strip()}"


def is_bot_self(platform: str, user_id: str) -> bool:
    del platform
    uid = str(user_id or "").strip()
    if not uid:
        return False
    if uid.lower() in {"assistant", "bot"}:
        return True
    cfg = config_shim.get_effective_config_dict()
    bot_cfg = cfg.get("bot") if isinstance(cfg.get("bot"), dict) else {}
    configured_ids = {
        str(bot_cfg.get("user_id") or "").strip(),
        str(bot_cfg.get("qq_account") or "").strip(),
        str(bot_cfg.get("self_id") or "").strip(),
    }
    configured_ids.discard("")
    return uid in configured_ids
