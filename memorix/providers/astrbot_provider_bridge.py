"""Adapters that bridge Memorix with AstrBot native providers."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover - used in standalone/unit-test runtime
    import logging

    logger = logging.getLogger(__name__)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _invoke_maybe_async(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return await _maybe_await(fn(*args, **kwargs))


def _to_str_list(texts: Sequence[Any]) -> List[str]:
    return [str(item or "") for item in texts]


def _extract_provider_id(provider: Any) -> str:
    if provider is None:
        return ""

    for attr in ("provider_id", "id", "providerId"):
        value = getattr(provider, attr, None)
        if value:
            return str(value).strip()

    for attr in ("provider_config", "config", "_config"):
        cfg = getattr(provider, attr, None)
        if isinstance(cfg, dict):
            for key in ("provider_id", "id"):
                value = cfg.get(key)
                if value:
                    return str(value).strip()
    return ""


def _is_embedding_capable_provider(provider: Any) -> bool:
    if provider is None:
        return False
    return bool(
        callable(getattr(provider, "get_embeddings", None))
        or callable(getattr(provider, "get_embedding", None))
    )


def _extract_completion_text(resp: Any) -> str:
    if resp is None:
        return ""
    if isinstance(resp, dict):
        return str(resp.get("completion_text", "") or resp.get("text", "") or "")
    text = getattr(resp, "completion_text", None)
    if text is not None:
        return str(text or "")
    text = getattr(resp, "text", None)
    if text is not None:
        return str(text or "")
    return str(resp)


def _coerce_vector_rows(raw: Any) -> List[List[float]]:
    if raw is None:
        return []

    if isinstance(raw, np.ndarray):
        arr = raw.astype(np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr.tolist()

    if not isinstance(raw, list):
        return []
    if not raw:
        return []

    first = raw[0]
    if isinstance(first, (int, float)):
        return [[float(v) for v in raw]]

    rows: List[List[float]] = []
    for item in raw:
        if isinstance(item, np.ndarray):
            rows.append([float(v) for v in item.astype(np.float32).tolist()])
            continue
        if isinstance(item, (list, tuple)):
            rows.append([float(v) for v in item])
    return rows


def _align_dim(arr: np.ndarray, target_dim: int) -> np.ndarray:
    if arr.shape[1] == target_dim:
        return arr
    if arr.shape[1] > target_dim:
        return arr[:, :target_dim]

    pad = np.zeros((arr.shape[0], target_dim - arr.shape[1]), dtype=np.float32)
    return np.concatenate([arr, pad], axis=1)


class AstrBotProviderBridge:
    """AstrBot Context bridge for provider selection and invocation."""

    def __init__(
        self,
        *,
        astrbot_context: Any,
        chat_provider_id: str = "",
        embedding_provider_id: str = "",
    ) -> None:
        self._context = astrbot_context
        self.chat_provider_id = str(chat_provider_id or "").strip()
        self.embedding_provider_id = str(embedding_provider_id or "").strip()
        self._cached_embedding_provider: Any = None
        self._cached_embedding_provider_id: str = ""

    @property
    def enabled(self) -> bool:
        return self._context is not None

    async def _pick_default_chat_provider_id(self, unified_msg_origin: str = "") -> str:
        ctx = self._context
        if ctx is None:
            return ""

        get_using = getattr(ctx, "get_using_provider", None)
        if callable(get_using):
            origins_to_try: list[str | None] = [None]
            if unified_msg_origin:
                origins_to_try.insert(0, unified_msg_origin)
            for origin in origins_to_try:
                try:
                    provider = await _invoke_maybe_async(get_using, origin)
                    pid = _extract_provider_id(provider)
                    if pid:
                        return pid
                except Exception:
                    continue
            logger.debug("resolve default chat provider via get_using_provider failed", exc_info=True)
        return ""

    async def resolve_chat_provider_id(self, unified_msg_origin: str = "") -> str:
        # 聊天模型优先使用插件配置；未配置时回退当前会话 provider。
        if self.chat_provider_id:
            return self.chat_provider_id
        default_id = await self._pick_default_chat_provider_id(unified_msg_origin)
        if default_id:
            return default_id
        return ""

    async def get_embedding_provider(self) -> Any:
        if self._cached_embedding_provider is not None:
            return self._cached_embedding_provider

        ctx = self._context
        if ctx is None:
            return None

        candidate: Any = None
        pid = self.embedding_provider_id

        list_all = getattr(ctx, "get_all_embedding_providers", None)
        providers: List[Any] = []
        if callable(list_all):
            try:
                raw = await _invoke_maybe_async(list_all)
                if isinstance(raw, list):
                    providers = raw
            except Exception as exc:
                logger.warning("list embedding providers failed: %s", exc)

        if pid and providers:
            for provider in providers:
                if _extract_provider_id(provider) == pid and _is_embedding_capable_provider(provider):
                    candidate = provider
                    break

        get_by_id = getattr(ctx, "get_provider_by_id", None)
        if candidate is None and pid and callable(get_by_id):
            try:
                selected = await _invoke_maybe_async(get_by_id, pid)
                if _is_embedding_capable_provider(selected):
                    candidate = selected
                else:
                    logger.warning(
                        "configured embedding_provider_id points to non-embedding provider: provider_id=%s",
                        pid,
                    )
            except Exception as exc:
                logger.warning("load embedding provider by id failed: provider_id=%s err=%s", pid, exc)

        if candidate is None and providers:
            for provider in providers:
                if _is_embedding_capable_provider(provider):
                    candidate = provider
                    break

        if candidate is not None:
            self._cached_embedding_provider = candidate
            self._cached_embedding_provider_id = pid or _extract_provider_id(candidate)
            return candidate
        return None

    async def get_embedding_provider_id(self) -> str:
        if self.embedding_provider_id:
            return self.embedding_provider_id
        if self._cached_embedding_provider_id:
            return self._cached_embedding_provider_id
        provider = await self.get_embedding_provider()
        return _extract_provider_id(provider)

    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        unified_msg_origin: str = "",
    ) -> str:
        ctx = self._context
        if ctx is None:
            raise RuntimeError("AstrBot context is not available")

        provider_id = await self.resolve_chat_provider_id(unified_msg_origin)
        if not provider_id:
            raise RuntimeError("chat provider is not configured")

        llm_generate = getattr(ctx, "llm_generate", None)
        if not callable(llm_generate):
            raise RuntimeError("AstrBot context missing llm_generate")

        kwargs = {
            "chat_provider_id": provider_id,
            "prompt": str(prompt or ""),
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        try:
            resp = await _invoke_maybe_async(llm_generate, **kwargs)
        except TypeError:
            kwargs.pop("temperature", None)
            kwargs.pop("max_tokens", None)
            resp = await _invoke_maybe_async(llm_generate, **kwargs)
        return _extract_completion_text(resp)

    async def generate_embeddings(self, texts: Sequence[str]) -> List[List[float]]:
        provider = await self.get_embedding_provider()
        if provider is None:
            raise RuntimeError("embedding provider is not configured")

        payload = _to_str_list(texts)
        if not payload:
            return []

        get_embeddings = getattr(provider, "get_embeddings", None)
        if callable(get_embeddings):
            for args, kwargs in (((payload,), {}), ((), {"text": payload})):
                try:
                    data = await _invoke_maybe_async(get_embeddings, *args, **kwargs)
                    rows = _coerce_vector_rows(data)
                    if rows:
                        return rows
                except TypeError:
                    continue
                except Exception as exc:
                    logger.warning("embedding provider get_embeddings failed: %s", exc)
                    break

        get_embedding = getattr(provider, "get_embedding", None)
        if callable(get_embedding):
            rows: List[List[float]] = []
            for text in payload:
                data = None
                for args, kwargs in (((text,), {}), ((), {"text": text})):
                    try:
                        data = await _invoke_maybe_async(get_embedding, *args, **kwargs)
                        break
                    except TypeError:
                        continue
                row = _coerce_vector_rows(data)
                if row:
                    rows.append(row[0])
            if rows:
                return rows

        raise RuntimeError("embedding provider does not expose get_embeddings/get_embedding")

    async def detect_embedding_dimension(self, fallback: int) -> int:
        provider = await self.get_embedding_provider()
        if provider is None:
            return int(max(1, fallback))

        get_dim = getattr(provider, "get_dim", None)
        if callable(get_dim):
            try:
                dim = await _invoke_maybe_async(get_dim)
                dim_i = int(dim)
                if dim_i > 0:
                    return dim_i
            except Exception:
                logger.debug("embedding provider get_dim failed", exc_info=True)

        try:
            rows = await self.generate_embeddings(["dimension_probe"])
            if rows and rows[0]:
                return int(len(rows[0]))
        except Exception:
            logger.debug("embedding provider dimension probe failed", exc_info=True)
        return int(max(1, fallback))


class AstrBotEmbeddingAdapter:
    """Memorix embedding adapter powered by AstrBot embedding provider."""

    def __init__(
        self,
        *,
        provider_bridge: AstrBotProviderBridge,
        default_dimension: int = 1024,
        batch_size: int = 32,
        max_retries: int = 3,
    ) -> None:
        self.provider_bridge = provider_bridge
        self.default_dimension = max(1, int(default_dimension))
        self.batch_size = max(1, int(batch_size))
        self.max_retries = max(1, int(max_retries))
        self._dimension: Optional[int] = None
        self._dimension_detected = False

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return int(self._dimension)
        detected = await self.provider_bridge.detect_embedding_dimension(self.default_dimension)
        self._dimension = int(max(1, detected))
        self._dimension_detected = True
        return int(self._dimension)

    async def encode(
        self,
        texts: Any,
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del show_progress
        del normalize

        if isinstance(texts, str):
            text_list = [texts]
            single = True
        else:
            text_list = [str(item or "") for item in list(texts)]
            single = False

        target_dim = int(dimensions or await self._detect_dimension())
        if not text_list:
            empty = np.zeros((0, target_dim), dtype=np.float32)
            return empty[0] if single else empty

        use_batch = max(1, int(batch_size or self.batch_size))
        out_chunks: List[np.ndarray] = []
        for idx in range(0, len(text_list), use_batch):
            chunk = text_list[idx : idx + use_batch]
            chunk_arr: Optional[np.ndarray] = None
            last_exc: Optional[Exception] = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    rows = await self.provider_bridge.generate_embeddings(chunk)
                    arr = np.asarray(rows, dtype=np.float32)
                    if arr.ndim == 1:
                        arr = arr.reshape(1, -1)
                    if arr.shape[0] != len(chunk):
                        raise ValueError(
                            f"embedding result count mismatch: expect={len(chunk)} got={arr.shape[0]}"
                        )
                    chunk_arr = _align_dim(arr, target_dim)
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(min(3.0, 2 ** (attempt - 1)))
            if chunk_arr is None:
                logger.warning("astrbot embedding chunk failed, fill NaN: %s", last_exc)
                chunk_arr = np.full((len(chunk), target_dim), np.nan, dtype=np.float32)
            out_chunks.append(chunk_arr)

        out = np.concatenate(out_chunks, axis=0) if out_chunks else np.zeros((0, target_dim), dtype=np.float32)
        if out.ndim == 1:
            out = out.reshape(1, -1)
        return out[0] if single else out

    async def encode_batch(
        self,
        texts: Sequence[str],
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
        show_progress: bool = False,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del num_workers
        return await self.encode(
            texts=texts,
            batch_size=batch_size,
            show_progress=show_progress,
            dimensions=dimensions,
        )

    def get_embedding_dimension(self) -> int:
        if self._dimension is not None:
            return int(self._dimension)
        return int(self.default_dimension)

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_bridge.embedding_provider_id,
            "dimension": self.get_embedding_dimension(),
            "dimension_detected": self._dimension_detected,
            "batch_size": self.batch_size,
        }


class AstrBotLLMClient:
    """Summary-compatible chat client backed by AstrBot llm_generate."""

    def __init__(self, *, provider_bridge: AstrBotProviderBridge, max_retries: int = 3):
        self.provider_bridge = provider_bridge
        self.max_retries = max(1, int(max_retries))

    async def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self.provider_bridge.generate_text(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(6.0, 2 ** (attempt - 1)))
        if last_exc is not None:
            raise last_exc
        return ""

    async def complete_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> Tuple[bool, Dict[str, Any], str]:
        text = await self.complete(prompt, temperature=temperature, max_tokens=max_tokens)
        if not text:
            return False, {}, ""

        raw = text.strip()
        try:
            return True, json.loads(raw), raw
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    return True, json.loads(raw[start : end + 1]), raw
                except json.JSONDecodeError:
                    pass
        return False, {}, raw
