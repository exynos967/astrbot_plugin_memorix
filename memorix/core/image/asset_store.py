from __future__ import annotations

import hashlib
import io
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_FORMAT_TO_MIME = {
    "BMP": "image/bmp",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class InspectedImage:
    byte_size: int
    extension: str
    height: int
    mime_type: str
    width: int
    frame_count: int


def _inspect_without_pil(payload: bytes, *, max_pixels: int = 40_000_000) -> tuple[str, int, int, int]:
    """Pure-Python stdlib fallback inspector for PNG, JPEG, BMP, and WEBP when Pillow is unavailable."""
    size = len(payload)
    safe_max_pixels = max(1, int(max_pixels))
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        pos = 8
        width = height = 0
        frame_count = 1
        saw_ihdr = False
        saw_idat = False
        saw_iend = False
        idat_parts: list[bytes] = []
        while pos < size:
            if pos + 12 > size:
                raise ValueError("无法解析图片内容")
            chunk_len = struct.unpack(">I", payload[pos : pos + 4])[0]
            chunk_type = payload[pos + 4 : pos + 8]
            data_end = pos + 8 + chunk_len
            if data_end + 4 > size:
                raise ValueError("无法解析图片内容")
            chunk_data = payload[pos + 8 : data_end]
            expected_crc = struct.unpack(">I", payload[data_end : data_end + 4])[0]
            actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                raise ValueError("无法解析图片内容")
            if not saw_ihdr:
                if chunk_type != b"IHDR" or chunk_len != 13:
                    raise ValueError("无法解析图片内容")
                width, height = struct.unpack(">II", chunk_data[:8])
                if width <= 0 or height <= 0:
                    raise ValueError("无法解析图片内容")
                if int(width) * int(height) > safe_max_pixels:
                    raise ValueError(f"图片像素超过限制: {width}x{height}")
                saw_ihdr = True
            elif chunk_type == b"acTL" and chunk_len >= 4:
                frame_count = max(1, int(struct.unpack(">I", chunk_data[:4])[0]))
            elif chunk_type == b"IDAT":
                saw_idat = True
                idat_parts.append(chunk_data)
            elif chunk_type == b"IEND":
                if chunk_len != 0 or data_end + 4 != size:
                    raise ValueError("无法解析图片内容")
                saw_iend = True
                break
            pos = data_end + 4
        if not (saw_ihdr and saw_idat and saw_iend):
            raise ValueError("无法解析图片内容")
        # 8 bytes/pixel (16-bit RGBA) + 1 filter byte/row + slack bound to block zlib bombs
        max_decompressed = min(512 * 1024 * 1024, int(width) * int(height) * 8 + int(height) + 65536)
        decompressor = zlib.decompressobj()
        total_out = 0
        try:
            for part in idat_parts:
                chunk_out = decompressor.decompress(part, max_decompressed - total_out + 1)
                total_out += len(chunk_out)
                if total_out > max_decompressed or decompressor.unconsumed_tail:
                    raise ValueError(f"图片像素超过限制: {width}x{height}")
            tail_out = decompressor.flush()
            total_out += len(tail_out)
            if total_out > max_decompressed or not decompressor.eof:
                raise ValueError("无法解析图片内容")
        except zlib.error as exc:
            raise ValueError("无法解析图片内容") from exc
        return "PNG", int(width), int(height), frame_count

    if payload.startswith(b"\xff\xd8") and size >= 4:
        pos = 2
        sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        width = height = 0
        saw_sof = False
        saw_sos = False
        while pos < size:
            while pos < size and payload[pos] != 0xFF:
                pos += 1
            while pos < size and payload[pos] == 0xFF:
                pos += 1
            if pos >= size:
                break
            marker = payload[pos]
            pos += 1
            if marker == 0xD9:
                break
            if marker in {0x00, 0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > size:
                raise ValueError("无法解析图片内容")
            seg_len = struct.unpack(">H", payload[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > size:
                raise ValueError("无法解析图片内容")
            if marker in sof_markers:
                if seg_len < 7:
                    raise ValueError("无法解析图片内容")
                height, width = struct.unpack(">HH", payload[pos + 3 : pos + 7])
                saw_sof = True
            elif marker == 0xDA:
                saw_sos = True
                break
            pos += seg_len
        eoi_idx = payload.rfind(b"\xff\xd9")
        if not (saw_sof and saw_sos and width > 0 and height > 0 and eoi_idx >= pos):
            raise ValueError("无法解析图片内容")
        return "JPEG", int(width), int(height), 1

    if payload.startswith(b"BM") and size >= 26:
        declared_size, _reserved, pixel_offset, dib_size = struct.unpack("<IIII", payload[2:18])
        if pixel_offset < 14 + dib_size or pixel_offset >= size or (declared_size > 0 and size < min(declared_size, pixel_offset + 1)):
            raise ValueError("无法解析图片内容")
        if dib_size == 12 and size >= 26:
            width, height = struct.unpack("<HH", payload[18:22])
            if width <= 0 or height <= 0:
                raise ValueError("无法解析图片内容")
            return "BMP", int(width), int(height), 1
        if dib_size >= 40 and size >= 14 + dib_size:
            width, raw_height = struct.unpack("<ii", payload[18:26])
            if width <= 0 or raw_height == 0:
                raise ValueError("无法解析图片内容")
            return "BMP", int(width), abs(int(raw_height)), 1
        raise ValueError("无法解析图片内容")

    if size >= 20 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        riff_size = struct.unpack("<I", payload[4:8])[0]
        if riff_size + 8 > size or riff_size < 12:
            raise ValueError("无法解析图片内容")
        riff_end = 8 + riff_size
        fourcc = payload[12:16]
        chunk_size = struct.unpack("<I", payload[16:20])[0]
        if 20 + chunk_size > riff_end:
            raise ValueError("无法解析图片内容")
        if fourcc == b"VP8X" and chunk_size >= 10 and size >= 30:
            width = 1 + int.from_bytes(payload[24:27], "little")
            height = 1 + int.from_bytes(payload[27:30], "little")
            flags = payload[20]
            frames = 1
            if flags & 0x02:
                anmf_count = 0
                cpos = 20 + chunk_size + (chunk_size & 1)
                while cpos + 8 <= riff_end:
                    cfourcc = payload[cpos : cpos + 4]
                    csz = struct.unpack("<I", payload[cpos + 4 : cpos + 8])[0]
                    cend = cpos + 8 + csz
                    if cend > riff_end:
                        raise ValueError("无法解析图片内容")
                    if cfourcc == b"ANMF":
                        anmf_count += 1
                    cpos = cend + (csz & 1)
                frames = max(1, anmf_count)
            return "WEBP", width, height, frames
        if fourcc == b"VP8 " and chunk_size >= 10 and size >= 30:
            if payload[23:26] != b"\x9d\x01\x2a":
                raise ValueError("无法解析图片内容")
            width = int.from_bytes(payload[26:28], "little") & 0x3FFF
            height = int.from_bytes(payload[28:30], "little") & 0x3FFF
            return "WEBP", width, height, 1
        if fourcc == b"VP8L" and chunk_size >= 5 and size >= 25:
            if payload[20] != 0x2F:
                raise ValueError("无法解析图片内容")
            bits = int.from_bytes(payload[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return "WEBP", width, height, 1

    raise ValueError("无法解析图片内容")


class ImageAssetStore:
    """在受控目录中按原始入库字节的 SHA-256 保存静态图片。"""

    def __init__(self, assets_root: Path, *, max_bytes: int, max_pixels: int) -> None:
        self.assets_root = Path(assets_root).resolve()
        self.max_bytes = max(1, int(max_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self.assets_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def content_hash(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    def inspect(self, image_bytes: bytes) -> InspectedImage:
        payload = bytes(image_bytes)
        if not payload:
            raise ValueError("图片内容为空")
        if len(payload) > self.max_bytes:
            raise ValueError(f"图片超过大小限制: {len(payload)} > {self.max_bytes}")

        try:
            from PIL import Image as PILImage
            from PIL import UnidentifiedImageError
        except ModuleNotFoundError:
            image_format, width, height, frame_count = _inspect_without_pil(payload, max_pixels=self.max_pixels)
        else:
            try:
                with PILImage.open(io.BytesIO(payload)) as image:
                    image.verify()
                with PILImage.open(io.BytesIO(payload)) as image:
                    image_format = str(image.format or "").upper()
                    if image_format not in _FORMAT_TO_MIME:
                        raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")
                    width, height = int(image.width), int(image.height)
                    frame_count = int(getattr(image, "n_frames", 1) or 1)
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError("无法解析图片内容") from exc
        if width <= 0 or height <= 0 or width * height > self.max_pixels:
            raise ValueError(f"图片像素超过限制: {width}x{height}")
        return InspectedImage(
            byte_size=len(payload),
            extension="jpg" if image_format == "JPEG" else image_format.lower(),
            height=height,
            mime_type=_FORMAT_TO_MIME[image_format],
            width=width,
            frame_count=frame_count,
        )

    def _resolve(self, storage_key: str) -> Path:
        key = str(storage_key or "").strip().replace("\\", "/")
        if not key or Path(key).is_absolute() or ".." in Path(key).parts:
            raise ValueError("图片资产键非法")
        target = (self.assets_root / key).resolve()
        if target.parent != self.assets_root:
            raise ValueError("图片资产键越出存储目录")
        return target

    def publish(self, image_bytes: bytes, inspected: InspectedImage) -> tuple[str, str]:
        payload = bytes(image_bytes)
        digest = self.content_hash(payload)
        storage_key = f"{digest}.{inspected.extension}"
        target = self._resolve(storage_key)
        if target.is_file():
            if target.stat().st_size != len(payload) or self._hash_file(target) != digest:
                raise RuntimeError(f"内容寻址图片资产发生冲突: {storage_key}")
            return digest, storage_key
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", suffix=".tmp", dir=self.assets_root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return digest, storage_key

    @staticmethod
    def _hash_stream(handle: BinaryIO) -> str:
        hasher = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
        return hasher.hexdigest()

    @classmethod
    def _hash_file(cls, path: Path) -> str:
        with path.open("rb") as handle:
            return cls._hash_stream(handle)

    def read_bytes(self, storage_key: str) -> bytes:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path.read_bytes()

    def open(self, storage_key: str) -> BinaryIO:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path.open("rb")

    def path_for_read(self, storage_key: str) -> Path:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path

    def delete(self, storage_key: str) -> bool:
        path = self._resolve(storage_key)
        if not path.exists():
            return False
        path.unlink()
        return True
