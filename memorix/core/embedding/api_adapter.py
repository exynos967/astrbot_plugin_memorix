"""
OpenAI-compatible & Offline Fallback Embedding API Adapter (A_memorix v2.0.0 aligned).

Unifies AstrBot OpenAI-compatible gateway support (including automatic `dimensions`
parameter compatibility probing for gateways such as SiliconFlow, issue #22),
deterministic offline fallback when no remote endpoint is configured, and A_memorix
v2.0.0 vector space fingerprinting (`get_embedding_fingerprint`, `get_embedding_configuration_key`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import numpy as np
from openai import AsyncOpenAI
from src.common.logger import get_logger

logger = get_logger("A_Memorix.EmbeddingAPIAdapter")


def _normalize_openai_base_url(raw: Optional[str]) -> str:
    """Normalize OpenAI-compatible base URL (append /v1 when omitted)."""
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/v1"):
        return text
    return f"{text}/v1"


def _coerce_embedding_rows(payload: Any) -> List[List[float]]:
    """Parse OpenAI and common OpenAI-compatible embedding response shapes."""
    if isinstance(payload, str):
        raise ValueError(f"embedding endpoint returned plain text: {payload[:200]}")

    data = payload
    if hasattr(payload, "model_dump"):
        data = payload.model_dump()
    elif hasattr(payload, "dict"):
        data = payload.dict()

    if isinstance(data, dict):
        if "data" in data:
            data = data["data"]
        elif "embeddings" in data:
            data = data["embeddings"]
        elif "embedding" in data:
            data = [data["embedding"]]

    rows: List[List[float]] = []
    if isinstance(data, list):
        for item in data:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            elif hasattr(item, "dict"):
                item = item.dict()
            if hasattr(item, "embedding"):
                item = getattr(item, "embedding")
            elif isinstance(item, dict) and "embedding" in item:
                item = item["embedding"]
            if not isinstance(item, list):
                raise ValueError(f"invalid embedding item type: {type(item).__name__}")
            rows.append([float(value) for value in item])

    if not rows:
        raise ValueError("embedding endpoint returned no embedding rows")
    return rows


def _deterministic_local_vector(text: str, dimension: int) -> np.ndarray:
    """Deterministic unit-norm local vector fallback when no remote endpoint is configured."""
    dim = max(1, int(dimension))
    digest = hashlib.sha256(str(text or "").encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.astype(np.float32)


class EmbeddingAPIAdapter:
    """OpenAI-compatible embedding adapter with v2.0.0 fingerprint & offline fallback support."""

    _GLOBAL_DIMENSION_CACHE: Dict[str, Tuple[int, str, str]] = {}
    TEXT_EMBEDDING_CACHE_MAX_ENTRIES = 2048
    _GLOBAL_TEXT_EMBEDDING_CACHE: Dict[Tuple[str, int, str], np.ndarray] = {}
    _TEXT_CACHE_LOCK = Lock()

    def __init__(
        self,
        batch_size: int = 32,
        max_concurrent: int = 5,
        default_dimension: int = 1024,
        enable_cache: bool = False,
        model_name: str = "auto",
        dimension_request_mode: str = "explicit",
        retry_config: Optional[dict] = None,
        base_url: Optional[str] = "",
        api_key: Optional[str] = "",
        openai_model: Optional[str] = "",
        timeout_seconds: Optional[float] = 30.0,
        max_retries: Optional[int] = 3,
        **kwargs: Any,
    ) -> None:
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_dimension = max(1, int(default_dimension))
        self.enable_cache = bool(enable_cache)
        self.model_name = str(model_name or "auto")
        self.dimension_request_mode = self._normalize_dimension_request_mode(dimension_request_mode)
        self.timeout_seconds = float(timeout_seconds or 30.0)
        self.max_retries = max(1, int(max_retries if max_retries is not None else 3))
        self.extra_kwargs = kwargs

        self.base_url = _normalize_openai_base_url(base_url)
        self.api_key = str(api_key or "").strip()
        if openai_model:
            self.openai_model = str(openai_model).strip()
        elif self.model_name and self.model_name.lower() != "auto":
            self.openai_model = self.model_name
        else:
            self.openai_model = ""

        self.retry_config = retry_config or {}
        configured_attempts = int(self.retry_config.get("max_attempts", self.max_retries))
        self.max_attempts = max(1, min(configured_attempts, self.max_retries))
        self.max_wait_seconds = max(0.1, min(5.0, float(self.retry_config.get("max_wait_seconds", 30))))
        self.min_wait_seconds = max(
            0.1,
            min(self.max_wait_seconds, float(self.retry_config.get("min_wait_seconds", 1))),
        )
        self.backoff_multiplier = max(1.0, float(self.retry_config.get("backoff_multiplier", 2)))

        self._dimension: Optional[int] = None
        self._dimension_detected = False
        self._supports_dimensions: bool = self.dimension_request_mode != "never"
        self._dimensions_support_detected: bool = self.dimension_request_mode == "never"
        self._client: Optional[AsyncOpenAI] = None

        self._total_encoded = 0
        self._total_errors = 0
        self._total_time = 0.0

        logger.debug(
            "Embedding adapter initialized: model=%s, default_dim=%s, base_url=%s, mode=%s",
            self.openai_model or self.model_name,
            self.default_dimension,
            self.base_url or "<offline-local>",
            self.dimension_request_mode,
        )

    @staticmethod
    def _normalize_dimension_request_mode(mode: str) -> str:
        normalized = str(mode or "explicit").strip().lower()
        if normalized in {"auto", "explicit", "on_demand", "on-demand"}:
            return "explicit"
        if normalized in {"always", "force", "forced"}:
            return "always"
        if normalized in {"never", "none", "natural"}:
            return "never"
        return "explicit"

    @staticmethod
    def _fingerprint_hash(payload: Dict[str, Any]) -> str:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _is_remote_configured(self) -> bool:
        return bool(self.base_url and self.openai_model)

    def _get_client(self) -> AsyncOpenAI:
        if not self.base_url:
            raise RuntimeError("Embedding API base_url is not configured")
        if not self.openai_model:
            raise RuntimeError("Embedding API model is not configured")
        if self._client is None:
            kwargs: Dict[str, Any] = {
                "api_key": self.api_key or "EMPTY",
                "timeout": self.timeout_seconds,
                "max_retries": 0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _request_embeddings(
        self,
        inputs: Union[str, List[str]],
        dimensions: Optional[int] = None,
    ) -> List[List[float]]:
        client = self._get_client()
        payload: Dict[str, Any] = {"model": self.openai_model, "input": inputs}
        if dimensions is not None:
            payload["dimensions"] = int(dimensions)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await client.embeddings.create(**payload)
                return _coerce_embedding_rows(resp)
            except AttributeError as exc:
                try:
                    return await self._request_embeddings_raw(payload)
                except Exception as raw_exc:
                    last_error = raw_exc
                    logger.debug("OpenAI SDK embedding parse failed before raw fallback: %s", exc)
            except Exception as exc:
                last_error = exc

            if attempt >= self.max_attempts:
                break
            wait_s = min(
                self.max_wait_seconds,
                self.min_wait_seconds * (self.backoff_multiplier ** (attempt - 1)),
            )
            logger.warning(
                "Embedding request failed (attempt %s/%s), retry in %.1fs: %s",
                attempt,
                self.max_attempts,
                wait_s,
                last_error,
            )
            await asyncio.sleep(wait_s)

        assert last_error is not None
        raise last_error

    async def _request_embeddings_raw(self, payload: dict) -> List[List[float]]:
        if not self.base_url:
            raise ValueError("raw embedding request requires base_url")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url.rstrip('/')}/embeddings"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            try:
                data: Any = resp.json()
            except ValueError as exc:
                raise ValueError(f"embedding endpoint returned non-json body: {resp.text[:200]}") from exc
        return _coerce_embedding_rows(data)

    async def _detect_dimensions_support(self) -> bool:
        """Probe whether the remote provider accepts the `dimensions` request parameter."""
        if self._dimensions_support_detected:
            return self._supports_dimensions
        if not self._is_remote_configured():
            self._supports_dimensions = False
            self._dimensions_support_detected = True
            return False
        try:
            probed = await self._request_embeddings("dimension_probe", dimensions=self.default_dimension)
            self._supports_dimensions = bool(probed and probed[0])
            if probed and probed[0] and not self._dimension_detected:
                self._dimension = len(probed[0])
                self._dimension_detected = True
        except Exception as exc:
            logger.debug("Dimensions param probe failed, treat as unsupported: %s", exc)
            self._supports_dimensions = False
        self._dimensions_support_detected = True
        return self._supports_dimensions

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return self._dimension

        if not self._is_remote_configured():
            self._dimension = self.default_dimension
            self._dimension_detected = True
            return self.default_dimension

        supports = await self._detect_dimensions_support()
        if self._dimension_detected and self._dimension is not None:
            return self._dimension

        if supports:
            try:
                probed = await self._request_embeddings("dimension_probe", dimensions=self.default_dimension)
                if probed and probed[0]:
                    self._dimension = len(probed[0])
                    self._dimension_detected = True
                    return self._dimension
            except Exception as exc:
                logger.debug("Dimension probe with requested dimension failed: %s", exc)

        try:
            probed = await self._request_embeddings("dimension_probe", dimensions=None)
            if probed and probed[0]:
                self._dimension = len(probed[0])
                self._dimension_detected = True
                return self._dimension
        except Exception as exc:
            logger.warning("Dimension detection failed, fallback to default: %s", exc)

        self._dimension = self.default_dimension
        self._dimension_detected = True
        return self.default_dimension

    def set_embedding_dimension(self, dimension: int, *, detected: bool = True) -> None:
        """Pin effective embedding dimension to the current vector store."""
        safe_dimension = max(1, int(dimension))
        self._dimension = safe_dimension
        self._dimension_detected = bool(detected)
        self.default_dimension = safe_dimension

    def get_requested_dimension(self) -> int:
        if self._dimension is not None:
            return int(self._dimension)
        return int(self.default_dimension)

    def get_embedding_dimension(self) -> int:
        if self._dimension is not None:
            return int(self._dimension)
        return int(self.default_dimension)

    def get_embedding_fingerprint(
        self,
        *,
        dimension: Optional[int] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return A_memorix v2.0.0 compatible vector space fingerprint."""
        effective_dimension = max(1, int(dimension or self.get_embedding_dimension()))
        effective_model = str(model_name or self.openai_model or self.model_name or "local_fallback").strip()
        provider = "openai_compatible" if self._is_remote_configured() else "local_fallback"
        compare_payload: Dict[str, Any] = {
            "model": effective_model,
            "provider": provider,
            "dimension": effective_dimension,
            "dimension_request_mode": self.dimension_request_mode,
            "base_url": self.base_url.rstrip("/") if self.base_url else "",
        }
        return {
            "version": 2,
            "hash": self._fingerprint_hash(compare_payload),
            "model": effective_model,
            "provider": provider,
            "dimension": effective_dimension,
            "dimension_request_mode": self.dimension_request_mode,
            "source": "observed" if self._dimension_detected else "configured",
            "model_identifier": effective_model,
            "base_url": compare_payload["base_url"],
        }

    def get_embedding_configuration_key(self) -> str:
        return str(self.get_embedding_fingerprint()["hash"])

    def _should_include_dimension(self, dimensions: Optional[int], include_dimension: bool = True) -> bool:
        del dimensions
        if not include_dimension:
            return False
        if self.dimension_request_mode == "never":
            return False
        return bool(self._supports_dimensions)

    def _embedding_cache_key(
        self,
        text: str,
        dimensions: Optional[int] = None,
        *,
        model_name: Optional[str] = None,
        include_dimension: bool = True,
    ) -> Tuple[str, int, str]:
        """按实际模型、请求维度语义和正文隔离缓存，不把备用结果归到候选列表。"""
        requested_dimension = max(1, int(dimensions or self.get_requested_dimension()))
        identity = self._fingerprint_hash(
            {
                "model": self.get_embedding_fingerprint(
                    dimension=requested_dimension,
                    model_name=model_name,
                )["hash"],
                "include_dimension": self._should_include_dimension(dimensions, include_dimension),
            }
        )
        return (identity, int(requested_dimension), str(text or ""))

    def _read_cached_vector(self, cache_key: Tuple[str, int, str]) -> Optional[np.ndarray]:
        with self._TEXT_CACHE_LOCK:
            cached = self._GLOBAL_TEXT_EMBEDDING_CACHE.pop(cache_key, None)
            if cached is None:
                return None
            self._GLOBAL_TEXT_EMBEDDING_CACHE[cache_key] = cached
            return cached.copy()

    def _write_cached_vector(self, cache_key: Tuple[str, int, str], vector: np.ndarray) -> None:
        if not np.all(np.isfinite(vector)):
            return
        with self._TEXT_CACHE_LOCK:
            self._GLOBAL_TEXT_EMBEDDING_CACHE.pop(cache_key, None)
            self._GLOBAL_TEXT_EMBEDDING_CACHE[cache_key] = vector.copy()
            while len(self._GLOBAL_TEXT_EMBEDDING_CACHE) > self.TEXT_EMBEDDING_CACHE_MAX_ENTRIES:
                del self._GLOBAL_TEXT_EMBEDDING_CACHE[next(iter(self._GLOBAL_TEXT_EMBEDDING_CACHE))]

    async def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
        dimensions: Optional[int] = None,
        *,
        use_cache: bool = True,
    ) -> np.ndarray:
        del show_progress
        del normalize
        start = time.time()
        cache_enabled = bool(self.enable_cache and use_cache)

        if isinstance(texts, str):
            input_texts = [texts]
            single = True
        else:
            input_texts = [str(item or "") for item in list(texts)]
            single = False

        target_dim = dimensions
        if target_dim is None:
            if not self._dimension_detected:
                await self._detect_dimension()
            target_dim = self._dimension or self.default_dimension
        target_dim = max(1, int(target_dim))

        if not input_texts:
            empty = np.zeros((0, target_dim), dtype=np.float32)
            return empty[0] if single else empty

        if not self._is_remote_configured():
            rows: List[np.ndarray] = []
            for item in input_texts:
                if cache_enabled:
                    cache_key = self._embedding_cache_key(item, target_dim, include_dimension=False)
                    cached_vec = self._read_cached_vector(cache_key)
                    if cached_vec is not None:
                        rows.append(cached_vec)
                        continue
                    vec = _deterministic_local_vector(item, target_dim)
                    self._write_cached_vector(cache_key, vec)
                    rows.append(vec)
                else:
                    rows.append(_deterministic_local_vector(item, target_dim))
            out = np.vstack(rows).astype(np.float32)
            self._total_encoded += len(input_texts)
            self._total_time += max(0.0, time.time() - start)
            return out[0] if single else out

        if not self._dimensions_support_detected:
            await self._detect_dimensions_support()

        use_batch = max(1, int(batch_size or self.batch_size))
        semaphore = asyncio.Semaphore(self.max_concurrent)
        include_dim_flag = bool(self._supports_dimensions)

        async def _encode_chunk(chunk: List[str]) -> np.ndarray:
            if not cache_enabled:
                async with semaphore:
                    try:
                        send_dim = target_dim if self._supports_dimensions else None
                        vectors = await self._request_embeddings(chunk, dimensions=send_dim)
                        arr = np.asarray(vectors, dtype=np.float32)
                        if arr.ndim == 1:
                            arr = arr.reshape(1, -1)
                        return arr
                    except Exception as exc:
                        self._total_errors += len(chunk)
                        logger.error("Embedding chunk failed: %s", exc)
                        return np.full((len(chunk), target_dim), np.nan, dtype=np.float32)

            resolved: List[Optional[np.ndarray]] = [None] * len(chunk)
            uncached_positions_by_text: Dict[str, List[int]] = {}
            unique_uncached_texts: List[str] = []
            for idx_in_chunk, item_text in enumerate(chunk):
                cache_key = self._embedding_cache_key(
                    item_text,
                    target_dim,
                    include_dimension=include_dim_flag,
                )
                cached_vec = self._read_cached_vector(cache_key)
                if cached_vec is not None:
                    resolved[idx_in_chunk] = cached_vec
                else:
                    if item_text not in uncached_positions_by_text:
                        unique_uncached_texts.append(item_text)
                        uncached_positions_by_text[item_text] = []
                    uncached_positions_by_text[item_text].append(idx_in_chunk)

            if unique_uncached_texts:
                async with semaphore:
                    try:
                        send_dim = target_dim if self._supports_dimensions else None
                        vectors = await self._request_embeddings(unique_uncached_texts, dimensions=send_dim)
                        arr = np.asarray(vectors, dtype=np.float32)
                        if arr.ndim == 1:
                            arr = arr.reshape(1, -1)
                        if arr.shape[0] != len(unique_uncached_texts):
                            raise ValueError(
                                f"embedding row count mismatch: expected {len(unique_uncached_texts)}, got {arr.shape[0]}"
                            )
                        for item_text, vec in zip(unique_uncached_texts, arr):
                            cache_key = self._embedding_cache_key(
                                item_text,
                                target_dim,
                                include_dimension=include_dim_flag,
                            )
                            self._write_cached_vector(cache_key, vec)
                            for pos in uncached_positions_by_text[item_text]:
                                resolved[pos] = vec.copy()
                    except Exception as exc:
                        failed_count = sum(len(v) for v in uncached_positions_by_text.values())
                        self._total_errors += failed_count
                        logger.error("Embedding chunk failed: %s", exc)
                        nan_vec = np.full((target_dim,), np.nan, dtype=np.float32)
                        for positions in uncached_positions_by_text.values():
                            for pos in positions:
                                resolved[pos] = nan_vec.copy()

            return np.vstack([v if v is not None else np.full((target_dim,), np.nan, dtype=np.float32) for v in resolved]).astype(np.float32)

        tasks = [
            _encode_chunk(input_texts[idx : idx + use_batch])
            for idx in range(0, len(input_texts), use_batch)
        ]
        chunks = await asyncio.gather(*tasks)
        out = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, target_dim), dtype=np.float32)

        self._total_encoded += len(input_texts)
        self._total_time += max(0.0, time.time() - start)
        if out.ndim == 1:
            out = out.reshape(1, -1)
        return out[0] if single else out

    async def encode_batch(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
        show_progress: bool = False,
        dimensions: Optional[int] = None,
        *,
        use_cache: bool = True,
    ) -> np.ndarray:
        old = self.max_concurrent
        if num_workers is not None:
            self.max_concurrent = max(1, int(num_workers))
        try:
            return await self.encode(
                texts=texts,
                batch_size=batch_size,
                show_progress=show_progress,
                dimensions=dimensions,
                use_cache=use_cache,
            )
        finally:
            self.max_concurrent = old

    def get_model_info(self) -> dict:
        avg_time = self._total_time / self._total_encoded if self._total_encoded > 0 else 0.0
        effective_dimension = self.get_embedding_dimension()
        return {
            "model_name": self.openai_model or self.model_name,
            "dimension": effective_dimension,
            "configured_dimension": int(self.default_dimension),
            "requested_dimension": int(self.get_requested_dimension()),
            "detected_dimension": int(self._dimension or 0),
            "dimension_detected": self._dimension_detected,
            "dimension_request_mode": self.dimension_request_mode,
            "enable_cache": self.enable_cache,
            "batch_size": self.batch_size,
            "max_concurrent": self.max_concurrent,
            "base_url": self.base_url,
            "total_encoded": self._total_encoded,
            "total_errors": self._total_errors,
            "avg_time_per_text": avg_time,
        }

    def get_statistics(self) -> dict:
        return self.get_model_info()

    @property
    def is_model_loaded(self) -> bool:
        return True

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        closer = getattr(client, "close", None)
        if callable(closer):
            result = closer()
            if asyncio.iscoroutine(result):
                await result

    def __repr__(self) -> str:
        return (
            "EmbeddingAPIAdapter("
            f"model={self.openai_model or self.model_name}, "
            f"dim={self.get_embedding_dimension()}, "
            f"encoded={self._total_encoded})"
        )


def create_embedding_api_adapter(
    batch_size: int = 32,
    max_concurrent: int = 5,
    default_dimension: int = 1024,
    enable_cache: bool = False,
    model_name: str = "auto",
    dimension_request_mode: str = "explicit",
    retry_config: Optional[dict] = None,
    base_url: Optional[str] = "",
    api_key: Optional[str] = "",
    openai_model: Optional[str] = "",
    timeout_seconds: Optional[float] = 30.0,
    max_retries: Optional[int] = 3,
    **kwargs: Any,
) -> EmbeddingAPIAdapter:
    return EmbeddingAPIAdapter(
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        default_dimension=default_dimension,
        enable_cache=enable_cache,
        model_name=model_name,
        dimension_request_mode=dimension_request_mode,
        retry_config=retry_config,
        base_url=base_url,
        api_key=api_key,
        openai_model=openai_model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        **kwargs,
    )
