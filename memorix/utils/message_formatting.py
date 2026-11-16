"""AstrBot message normalization for Memorix ingest.

Keep raw platform payloads out of long-term memory.  This module builds a
processed_plain_text from AstrBot's message chain, preserving useful
context while avoiding binary/raw CQ payloads.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from astrbot.api import logger

try:  # pragma: no cover - exercised with real AstrBot runtime
    from astrbot.api.message_components import (
        At,
        AtAll,
        Face,
        File,
        Forward,
        Image,
        Json,
        Node,
        Nodes,
        Plain,
        Record,
        Reply,
        Video,
    )
    from astrbot.api.provider import Provider
except ImportError:  # pragma: no cover - unit-test stubs may not expose full AstrBot core
    class _MissingComponent:
        pass

    At = AtAll = Face = File = Forward = Image = Json = Node = Nodes = Plain = Record = Reply = Video = _MissingComponent
    Provider = _MissingComponent


async def _compress_image(url_or_path: str, *args: Any, **kwargs: Any) -> str:
    del args, kwargs
    try:
        from astrbot.core.utils.media_utils import compress_image as real_compress
        return await real_compress(url_or_path)
    except Exception:
        return url_or_path


compress_image = _compress_image


class _Settings:
    def with_overrides(self, _overrides: dict[str, Any] | None = None) -> "_Settings":
        return self


def _resolve_quoted_settings() -> Any:
    try:
        from astrbot.core.utils.quoted_message.settings import SETTINGS as real_settings
        return real_settings
    except Exception:
        return _Settings()


SETTINGS = _resolve_quoted_settings()


class ReplyChainParser:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        try:
            from astrbot.core.utils.quoted_message.chain_parser import ReplyChainParser as RealParser
            self._real = RealParser(*args, **kwargs)
        except Exception:
            self._real = None

    def extract_text_from_reply_component(self, reply: Any) -> str | None:
        if self._real is not None and hasattr(self._real, "extract_text_from_reply_component"):
            try:
                return self._real.extract_text_from_reply_component(reply)
            except Exception:
                pass
        return None


class OneBotPayloadParser:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        try:
            from astrbot.core.utils.quoted_message.chain_parser import OneBotPayloadParser as RealParser
            self._real = RealParser(*args, **kwargs)
        except Exception:
            self._real = None

    def parse_get_forward_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._real is not None and hasattr(self._real, "parse_get_forward_payload"):
            try:
                return self._real.parse_get_forward_payload(payload)
            except Exception:
                pass
        return {"text": None, "image_refs": [], "forward_ids": []}


class OneBotClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        try:
            from astrbot.core.utils.quoted_message.onebot_client import OneBotClient as RealClient
            self._real = RealClient(*args, **kwargs)
        except Exception:
            self._real = None

    async def get_forward_msg(self, forward_id: str | int) -> dict[str, Any] | None:
        if self._real is not None and hasattr(self._real, "get_forward_msg"):
            try:
                return await self._real.get_forward_msg(forward_id)
            except Exception:
                pass
        return None


class ImageResolver:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        try:
            from astrbot.core.utils.quoted_message.image_resolver import ImageResolver as RealResolver
            self._real = RealResolver(*args, **kwargs)
        except Exception:
            self._real = None

    async def resolve_for_llm(self, image_refs: list[str]) -> list[str]:
        if self._real is not None and hasattr(self._real, "resolve_for_llm"):
            try:
                return await self._real.resolve_for_llm(image_refs)
            except Exception:
                pass
        return image_refs


async def extract_quoted_message_text(*args: Any, **kwargs: Any) -> str | None:
    try:
        from astrbot.core.utils.quoted_message.extractor import extract_quoted_message_text as real_fn
        return await real_fn(*args, **kwargs)
    except Exception:
        return None


async def extract_quoted_message_images(*args: Any, **kwargs: Any) -> list[str]:
    try:
        from astrbot.core.utils.quoted_message.extractor import extract_quoted_message_images as real_fn
        return await real_fn(*args, **kwargs)
    except Exception:
        return []


_PLACEHOLDER_ONLY_RE = re.compile(
    r"^(?:\s*(?:\[(?:图片|image|表情|表情包|语音消息|voice|视频|video|转发消息|forward message|文件|file)(?::[^\]]*)?\]\s*)+)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class MessageFormatOptions:
    include_image_caption: bool = False
    image_caption_provider_id: str = ""
    image_caption_prompt: str = "请简洁描述这张图片中对长期记忆有价值的内容。"
    image_caption_max_count: int = 1
    max_forward_fetch: int = 8
    max_text_chars: int = 2000
    skip_placeholder_only: bool = True


@dataclass(slots=True)
class FormattedMessage:
    text: str
    raw_text: str
    component_types: list[str]
    image_count: int = 0
    forward_count: int = 0
    reply_count: int = 0


class AstrBotMessageFormatter:
    def __init__(
        self,
        *,
        event: Any,
        context: Any = None,
        options: Optional[MessageFormatOptions] = None,
    ) -> None:
        self.event = event
        self.context = context
        self.options = options or MessageFormatOptions()
        self.component_types: list[str] = []
        self.image_count = 0
        self.forward_count = 0
        self.reply_count = 0
        self._captioned_images = 0
        self._quoted_settings = SETTINGS.with_overrides({"max_forward_fetch": self.options.max_forward_fetch})

    async def format(self) -> FormattedMessage:
        raw_text = str(getattr(self.event, "message_str", "") or "").strip()
        message_obj = getattr(self.event, "message_obj", None)
        chain = self._coerce_chain(getattr(message_obj, "message", None))
        parts = await self._format_chain(chain)
        text = self._normalize_text(" ".join(part for part in parts if part))
        if not text:
            text = self._normalize_text(raw_text)
        if self.options.skip_placeholder_only and self._is_placeholder_only(text):
            text = ""
        if self.options.max_text_chars > 0 and len(text) > self.options.max_text_chars:
            text = f"{text[: self.options.max_text_chars - 1]}…"
        return FormattedMessage(
            text=text,
            raw_text=raw_text,
            component_types=self.component_types,
            image_count=self.image_count,
            forward_count=self.forward_count,
            reply_count=self.reply_count,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").replace("\u200b", " ")).strip()

    @staticmethod
    def _is_placeholder_only(text: str) -> bool:
        clean = str(text or "").strip()
        return bool(clean and _PLACEHOLDER_ONLY_RE.match(clean))

    @staticmethod
    def _coerce_chain(chain: Any) -> list[Any]:
        if isinstance(chain, list):
            return chain
        if isinstance(chain, tuple):
            return list(chain)
        nested = getattr(chain, "chain", None)
        if isinstance(nested, list):
            return nested
        if isinstance(nested, tuple):
            return list(nested)
        return []

    async def _format_chain(self, chain: list[Any], *, depth: int = 0) -> list[str]:
        if depth > 4:
            return []
        parts: list[str] = []
        for comp in chain:
            self.component_types.append(str(getattr(comp, "type", comp.__class__.__name__)))
            text = await self._format_component(comp, depth=depth)
            if text:
                parts.append(text)
        return parts

    @staticmethod
    def _is_image_component(comp: Any) -> bool:
        if isinstance(comp, Image):
            return True
        raw_type = getattr(comp, "type", "")
        comp_type = str(getattr(raw_type, "value", raw_type) or "").strip().lower()
        if comp_type == "image":
            return True
        return type(comp).__name__.lower() in {"image", "imagecomponent", "mockimage"}

    async def _format_component(self, comp: Any, *, depth: int = 0) -> str:
        if isinstance(comp, Plain):
            return str(comp.text or "").strip()
        if isinstance(comp, AtAll):
            return "@全体成员"
        if isinstance(comp, At):
            name = str(getattr(comp, "name", "") or "").strip()
            qq = str(getattr(comp, "qq", "") or "").strip()
            if name and qq and name != qq:
                return f"@{name}({qq})"
            return f"@{name or qq}" if (name or qq) else "@某人"
        if isinstance(comp, Face):
            return f"[表情:{getattr(comp, 'id', '')}]"
        if self._is_image_component(comp):
            self.image_count += 1
            caption = await self._caption_image_component(comp)
            return f"[图片：{caption}]" if caption else "[图片]"
        if isinstance(comp, Record):
            return "[语音消息]"
        if isinstance(comp, Video):
            return "[视频]"
        if isinstance(comp, File):
            name = str(getattr(comp, "name", "") or "").strip()
            return f"[文件:{name}]" if name else "[文件]"
        if isinstance(comp, Reply):
            self.reply_count += 1
            return await self._format_reply(comp)
        if isinstance(comp, Forward):
            self.forward_count += 1
            return await self._format_forward(comp)
        if isinstance(comp, Node):
            sender = str(getattr(comp, "name", "") or getattr(comp, "uin", "") or "未知用户").strip()
            inner_chain = self._coerce_chain(getattr(comp, "content", None))
            inner = " ".join(await self._format_chain(inner_chain, depth=depth + 1)).strip()
            return f"{sender}: {inner}" if inner else ""
        if isinstance(comp, Nodes):
            self.forward_count += 1
            lines = []
            for node in getattr(comp, "nodes", []) or []:
                line = await self._format_component(node, depth=depth + 1)
                if line:
                    lines.append(line)
            return "【合并转发消息:\n" + "\n".join(lines) + "\n】" if lines else "[转发消息]"
        if isinstance(comp, Json):
            data = getattr(comp, "data", None)
            if isinstance(data, dict):
                for key in ("text", "prompt", "title"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return "[JSON消息]"
        fallback_text = str(getattr(comp, "text", "") or "").strip()
        return fallback_text or f"[{getattr(comp, 'type', comp.__class__.__name__)}]"

    async def _format_reply(self, reply: Reply) -> str:
        sender = str(getattr(reply, "sender_nickname", "") or getattr(reply, "sender_id", "") or "").strip()
        text = ""
        try:
            text = str(await extract_quoted_message_text(self.event, reply, settings=self._quoted_settings) or "").strip()
        except Exception:
            logger.debug("[memorix] extract quoted text failed", exc_info=True)
        if not text:
            parser = ReplyChainParser(settings=self._quoted_settings)
            text = str(parser.extract_text_from_reply_component(reply) or getattr(reply, "message_str", "") or "").strip()
        if self.options.include_image_caption:
            try:
                image_refs = await extract_quoted_message_images(self.event, reply, settings=self._quoted_settings)
                captions = [caption for caption in [await self._caption_image_ref(ref) for ref in image_refs] if caption]
                if captions:
                    text = f"{text} 图片：{'；'.join(captions)}".strip()
            except Exception:
                logger.debug("[memorix] extract quoted image captions failed", exc_info=True)
        if not text:
            return "[回复了一条消息]"
        return f"[回复了{sender}的消息: {text}]" if sender else f"[回复消息: {text}]"

    async def _format_forward(self, forward: Forward) -> str:
        forward_id = str(getattr(forward, "id", "") or "").strip()
        if not forward_id:
            return "[转发消息]"
        try:
            client = OneBotClient(self.event, settings=self._quoted_settings)
            payload = await client.get_forward_msg(forward_id)
            if not payload:
                return "[转发消息]"
            parsed = OneBotPayloadParser(settings=self._quoted_settings).parse_get_forward_payload(payload)
            text = str(parsed.get("text") or "").strip()
            if self.options.include_image_caption:
                resolver = ImageResolver(self.event, client)
                image_refs = await resolver.resolve_for_llm(parsed.get("image_refs") or [])
                captions = [caption for caption in [await self._caption_image_ref(ref) for ref in image_refs] if caption]
                if captions:
                    text = f"{text}\n图片：{'；'.join(captions)}".strip()
            return f"【合并转发消息:\n{text}\n】" if text else "[转发消息]"
        except Exception:
            logger.debug("[memorix] format forward message failed", exc_info=True)
            return "[转发消息]"

    async def _caption_image_component(self, image: Image) -> str:
        if not self.options.include_image_caption:
            return ""
        try:
            return await self._caption_image_ref(await image.convert_to_file_path())
        except Exception:
            logger.debug("[memorix] image caption component failed", exc_info=True)
            return ""

    async def _caption_image_ref(self, image_ref: str) -> str:
        if not self.options.include_image_caption:
            return ""
        if self._captioned_images >= max(0, int(self.options.image_caption_max_count)):
            return ""
        provider = await self._resolve_caption_provider()
        if provider is None:
            return ""
        try:
            image_path = await compress_image(str(image_ref))
            resp = await provider.text_chat(
                prompt=self.options.image_caption_prompt,
                session_id=uuid.uuid4().hex,
                image_urls=[image_path],
                persist=False,
            )
            caption = str(getattr(resp, "completion_text", "") or "").strip()
            if caption:
                self._captioned_images += 1
            return self._normalize_text(caption)
        except Exception:
            logger.debug("[memorix] image caption provider call failed", exc_info=True)
            return ""

    async def _resolve_caption_provider(self) -> Optional[Provider]:
        if self.context is None:
            return None
        provider = None
        provider_id = self.options.image_caption_provider_id.strip()
        try:
            if provider_id:
                provider = self.context.get_provider_by_id(provider_id)
            if provider is None:
                get_current = getattr(self.context, "get_current_chat_provider_id", None)
                if callable(get_current):
                    current_id = await get_current(self.event.unified_msg_origin)
                    if current_id:
                        provider = self.context.get_provider_by_id(current_id)
            if provider is None:
                get_using_async = getattr(self.context, "get_using_provider_async", None)
                if callable(get_using_async):
                    provider = await get_using_async(self.event.unified_msg_origin)
            if provider is None:
                get_using_sync = getattr(self.context, "get_using_provider", None)
                if callable(get_using_sync):
                    provider = get_using_sync(self.event.unified_msg_origin)
        except Exception:
            return None
        return provider if _is_valid_vision_provider(provider) else None


async def format_astrbot_event_message(
    event: Any,
    *,
    context: Any = None,
    options: Optional[MessageFormatOptions] = None,
) -> FormattedMessage:
    return await AstrBotMessageFormatter(event=event, context=context, options=options).format()


def message_format_options_from_config(config: dict[str, Any] | None) -> MessageFormatOptions:
    ingest_cfg = config.get("ingest", {}) if isinstance(config, dict) else {}
    if not isinstance(ingest_cfg, dict):
        ingest_cfg = {}
    image_caption_cfg = ingest_cfg.get("image_caption", {}) if isinstance(ingest_cfg.get("image_caption"), dict) else {}
    return MessageFormatOptions(
        include_image_caption=bool(image_caption_cfg.get("enabled", False)),
        image_caption_provider_id=str(image_caption_cfg.get("provider_id", "") or ""),
        image_caption_prompt=str(
            image_caption_cfg.get("prompt", "请简洁描述这张图片中对长期记忆有价值的内容。")
            or "请简洁描述这张图片中对长期记忆有价值的内容。"
        ),
        image_caption_max_count=max(0, int(image_caption_cfg.get("max_count", 1) or 1)),
        max_forward_fetch=max(1, int(ingest_cfg.get("max_forward_fetch", 8) or 8)),
        max_text_chars=max(200, int(ingest_cfg.get("max_message_chars", 2000) or 2000)),
        skip_placeholder_only=bool(ingest_cfg.get("skip_placeholder_only", True)),
    )


def _coerce_event_chain(event: Any) -> list[Any]:
    """Extract message component chain from event."""
    message_obj = getattr(event, "message_obj", None)
    chain = getattr(message_obj, "message", []) or []
    if isinstance(chain, (list, tuple)):
        return list(chain)
    nested = getattr(chain, "chain", None)
    if isinstance(nested, (list, tuple)):
        return list(nested)
    return []


# 图片缓存目录清理阈值：单目录文件上限与过期时长（避免无界增长，Sourcery #3）。
_IMG_CACHE_MAX_FILES = 200
_IMG_CACHE_MAX_AGE_SECONDS = 2 * 60 * 60


def _cleanup_image_cache(cache_dir: str) -> None:
    """清理图片缓存目录：删过期文件，超量时删最旧的（best-effort，失败静默）。"""
    try:
        entries: list[tuple[str, float]] = []
        now = time.time()
        with os.scandir(cache_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if now - mtime > _IMG_CACHE_MAX_AGE_SECONDS:
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
                    continue
                entries.append((entry.path, mtime))
        if len(entries) > _IMG_CACHE_MAX_FILES:
            entries.sort(key=lambda item: item[1])
            for path, _ in entries[: len(entries) - _IMG_CACHE_MAX_FILES]:
                try:
                    os.remove(path)
                except OSError:
                    pass
    except OSError:
        pass


@dataclass(slots=True)
class CapturedChatImage:
    component_path: str
    safe_path: str = ""
    image_bytes: bytes | None = None
    content_hash: str = ""


def _read_bounded_file_bytes(path: str, max_bytes: int) -> bytes | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > max_bytes:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


async def _read_image_component_bytes(comp: Any, *, max_bytes: int) -> bytes | None:
    """在不创建临时缓存文件的前提下读取图片组件字节。"""
    converter = getattr(comp, "convert_to_file_path", None)
    if callable(converter):
        try:
            resolved = await converter()
            if resolved:
                data = _read_bounded_file_bytes(str(resolved), max_bytes)
                if data:
                    return data
        except Exception:
            pass

    for attr in ("path", "file", "url"):
        raw = str(getattr(comp, attr, "") or "").strip()
        if not raw:
            continue
        if raw.startswith("base64://"):
            import base64 as _b64

            payload = raw.removeprefix("base64://").strip()
            if payload:
                try:
                    decoded = _b64.b64decode(payload)
                    if 0 < len(decoded) <= max_bytes:
                        return decoded
                except Exception:
                    pass
            continue
        local_candidate = raw
        if local_candidate.startswith("file:///"):
            local_candidate = local_candidate[8:] if os.name == "nt" else local_candidate[7:]
        elif local_candidate.startswith("file://"):
            local_candidate = local_candidate[7:]
        data = _read_bounded_file_bytes(local_candidate, max_bytes)
        if data:
            return data
    return None


async def _resolve_image_component_source(comp: Any, cache_dir: str) -> tuple[str, bool]:
    """解析图片组件的本地可读文件路径；返回 (path, is_ephemeral_generated)。"""
    converter = getattr(comp, "convert_to_file_path", None)
    if callable(converter):
        try:
            resolved = await converter()
            if resolved and os.path.isfile(str(resolved)):
                return str(resolved), False
        except Exception:
            pass

    for attr in ("path", "file", "url"):
        raw = str(getattr(comp, attr, "") or "").strip()
        if not raw:
            continue
        if raw.startswith("base64://"):
            import base64 as _b64

            payload = raw.removeprefix("base64://").strip()
            if payload:
                tmp_dst = os.path.join(cache_dir, f"{uuid.uuid4().hex}.bin")
                with open(tmp_dst, "wb") as fh:
                    fh.write(_b64.b64decode(payload))
                return tmp_dst, True
            continue
        local_candidate = raw
        if local_candidate.startswith("file:///"):
            local_candidate = local_candidate[8:] if os.name == "nt" else local_candidate[7:]
        elif local_candidate.startswith("file://"):
            local_candidate = local_candidate[7:]
        if os.path.isfile(local_candidate):
            return local_candidate, False
    return "", False


async def capture_event_images_to_safe_dir(
    event: Any,
    *,
    copy_to_cache: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
) -> list[CapturedChatImage]:
    """按持久消息树稳定组件路径捕获聊天图片（可按需跳过落盘临时缓存）。"""
    from ..core.image.component_paths import iter_message_image_components

    captured: list[CapturedChatImage] = []
    chain = _coerce_event_chain(event)
    if not chain:
        return captured
    bounded_max = max(1024, int(max_bytes or (10 * 1024 * 1024)))
    if not copy_to_cache:
        for comp_path, comp in iter_message_image_components(chain, include_emoji=False):
            try:
                data = await _read_image_component_bytes(comp, max_bytes=bounded_max)
                if not data:
                    continue
                captured.append(
                    CapturedChatImage(
                        component_path=comp_path,
                        safe_path="",
                        image_bytes=data,
                        content_hash=hashlib.sha256(data).hexdigest(),
                    )
                )
            except Exception:
                logger.debug("[memorix] read image bytes failed", exc_info=True)
        return captured

    cache_dir = os.path.join(tempfile.gettempdir(), "astrbot_memorix_img_cache")
    os.makedirs(cache_dir, exist_ok=True)
    _cleanup_image_cache(cache_dir)
    for comp_path, comp in iter_message_image_components(chain, include_emoji=False):
        try:
            src, already_in_cache = await _resolve_image_component_source(comp, cache_dir)
            if not src:
                continue
            if already_in_cache:
                dst = src
            else:
                ext = os.path.splitext(src)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                    ext = ".jpg"
                dst = os.path.join(cache_dir, f"{uuid.uuid4().hex}{ext}")
                shutil.copy2(src, dst)
            data = _read_bounded_file_bytes(dst, bounded_max)
            captured.append(
                CapturedChatImage(
                    component_path=comp_path,
                    safe_path=dst,
                    image_bytes=data,
                    content_hash=hashlib.sha256(data).hexdigest() if data else "",
                )
            )
        except Exception:
            logger.debug("[memorix] copy image to cache failed", exc_info=True)
    return captured


async def copy_images_to_safe_dir(event: Any) -> list[str]:
    """Copy Image component files to a pipeline-safe cache directory."""
    return [item.safe_path for item in await capture_event_images_to_safe_dir(event, copy_to_cache=True) if item.safe_path]


def _is_valid_vision_provider(provider: Any) -> bool:
    if provider is None:
        return False
    if isinstance(provider, Provider):
        return True
    return Provider.__name__ == "_MissingComponent" and callable(getattr(provider, "text_chat", None))


async def resolve_vision_provider(context: Any, config: dict[str, Any], event: Any) -> Provider | None:
    """Resolve the vision-capable provider from config/context."""
    if context is None:
        return None
    ingest_cfg = config.get("ingest", {}) if isinstance(config.get("ingest"), dict) else {}
    caption_cfg = ingest_cfg.get("image_caption", {}) if isinstance(ingest_cfg.get("image_caption"), dict) else {}
    provider_id = str(caption_cfg.get("provider_id", "") or "").strip()
    provider: Any = None
    try:
        if provider_id:
            provider = context.get_provider_by_id(provider_id)
        if provider is None:
            get_current = getattr(context, "get_current_chat_provider_id", None)
            if callable(get_current):
                current_id = await get_current(event.unified_msg_origin)
                if current_id:
                    provider = context.get_provider_by_id(current_id)
        if provider is None:
            get_using_async = getattr(context, "get_using_provider_async", None)
            if callable(get_using_async):
                provider = await get_using_async(event.unified_msg_origin)
        if provider is None:
            get_using_sync = getattr(context, "get_using_provider", None)
            if callable(get_using_sync):
                provider = get_using_sync(event.unified_msg_origin)
    except Exception:
        return None
    return provider if _is_valid_vision_provider(provider) else None


def _remove_quiet(path: str | None) -> None:
    if path:
        try:
            os.remove(path)
        except Exception:
            pass


async def enrich_text_with_captions_detailed(
    text: str,
    safe_paths: list[str],
    context: Any,
    config: dict[str, Any],
    event: Any,
) -> tuple[str, list[str]]:
    """Replace [图片] placeholders with vision API captions and return (enriched_text, per_image_captions)."""
    captions: list[str] = ["" for _ in safe_paths]
    if not safe_paths:
        return text, captions
    opts = message_format_options_from_config(config)
    if not opts.include_image_caption:
        for p in safe_paths:
            _remove_quiet(p)
        return text, captions
    provider = await resolve_vision_provider(context, config, event)
    if provider is None:
        for p in safe_paths:
            _remove_quiet(p)
        return text, captions
    max_count = max(0, int(opts.image_caption_max_count))
    captioned = 0
    current = text
    remaining_paths: list[str] = []
    for idx, image_path in enumerate(safe_paths):
        if max_count > 0 and captioned >= max_count:
            remaining_paths.append(image_path)
            continue
        compressed = None
        try:
            compressed = await compress_image(image_path)
            resp = await provider.text_chat(
                prompt=opts.image_caption_prompt,
                session_id=uuid.uuid4().hex,
                image_urls=[compressed],
                persist=False,
            )
            caption = str(getattr(resp, "completion_text", "") or "").strip()
            if caption:
                filtered = "".join(c for c in caption if ord(c) >= 0x20 or c in "\n\r\t").strip()
                if filtered:
                    captions[idx] = filtered
                    current = current.replace("[图片]", f"[图片：{filtered}]", 1)
                    captioned += 1
        except Exception:
            logger.debug("[memorix] background image caption failed", exc_info=True)
        finally:
            _remove_quiet(compressed if compressed != image_path else None)
            _remove_quiet(image_path)
    for p in remaining_paths:
        _remove_quiet(p)
    return current, captions


async def enrich_text_with_captions(
    text: str,
    safe_paths: list[str],
    context: Any,
    config: dict[str, Any],
    event: Any,
) -> str:
    """Replace [图片] placeholders with vision API captions in background task."""
    enriched, _ = await enrich_text_with_captions_detailed(text, safe_paths, context, config, event)
    return enriched

