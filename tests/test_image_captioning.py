import asyncio
from types import SimpleNamespace

from astrbot_plugin_memorix.memorix.utils import message_formatting


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text="一只猫")


class _FakeContext:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.provider_ids: list[str] = []

    def get_provider_by_id(self, provider_id: str):
        self.provider_ids.append(provider_id)
        return self.provider

    def get_using_provider(self, _origin: str):
        raise AssertionError("指定 provider_id 时不应回退到当前会话 Provider")


def _caption_config(*, enabled: bool) -> dict:
    return {
        "ingest": {
            "image_caption": {
                "enabled": enabled,
                "provider_id": "vision-1",
                "prompt": "描述图片",
            }
        }
    }


def test_disabled_image_caption_skips_provider_and_cleans_cached_image(tmp_path, monkeypatch) -> None:
    provider = _FakeProvider()
    context = _FakeContext(provider)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(message_formatting, "Provider", _FakeProvider)

    result = asyncio.run(
        message_formatting.enrich_text_with_captions(
            "消息 [图片]",
            [str(image_path)],
            context,
            _caption_config(enabled=False),
            SimpleNamespace(unified_msg_origin="test:origin"),
        )
    )

    assert result == "消息 [图片]"
    assert provider.calls == []
    assert context.provider_ids == []
    assert not image_path.exists()


def test_enabled_image_caption_uses_configured_provider(tmp_path, monkeypatch) -> None:
    provider = _FakeProvider()
    context = _FakeContext(provider)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")

    async def passthrough_compress(path: str) -> str:
        return path

    monkeypatch.setattr(message_formatting, "Provider", _FakeProvider)
    monkeypatch.setattr(message_formatting, "compress_image", passthrough_compress)

    result = asyncio.run(
        message_formatting.enrich_text_with_captions(
            "消息 [图片]",
            [str(image_path)],
            context,
            _caption_config(enabled=True),
            SimpleNamespace(unified_msg_origin="test:origin"),
        )
    )

    assert result == "消息 [图片：一只猫]"
    assert len(provider.calls) == 1
    assert provider.calls[0]["prompt"] == "描述图片"
    assert context.provider_ids == ["vision-1"]
    assert not image_path.exists()
