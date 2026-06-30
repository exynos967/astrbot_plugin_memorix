"""
OpenAI-compatible embedding adapter.

This adapter keeps the old EmbeddingAPIAdapter interface while removing host
runtime dependencies.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Union

import httpx
import numpy as np
from openai import AsyncOpenAI

from ...amemorix.common.logging import get_logger

logger = get_logger("A_Memorix.EmbeddingAPIAdapter")


def _normalize_openai_base_url(raw: str) -> str:
    """Normalize OpenAI-compatible base URL.

    AstrBot plugin config documents that users may omit the trailing `/v1`.
    The OpenAI SDK does not add it automatically; without this normalization
    many compatible gateways return a plain string/error for `/embeddings`,
    which later surfaces as `AttributeError: 'str' object has no attribute data`.
    """
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


class EmbeddingAPIAdapter:
    def __init__(
        self,
        batch_size: int = 32,
        max_concurrent: int = 5,
        default_dimension: int = 1024,
        enable_cache: bool = False,
        model_name: str = "auto",
        retry_config: Optional[dict] = None,
        base_url: str = "",
        api_key: str = "",
        openai_model: str = "",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ):
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_dimension = max(1, int(default_dimension))
        self.enable_cache = bool(enable_cache)
        self.model_name = str(model_name or "auto")
        self.timeout_seconds = float(timeout_seconds or 30.0)
        self.max_retries = max(1, int(max_retries))

        self.base_url = _normalize_openai_base_url(base_url)
        self.api_key = str(api_key or "").strip()
        if openai_model:
            self.openai_model = str(openai_model).strip()
        elif self.model_name and self.model_name.lower() != "auto":
            self.openai_model = self.model_name
        else:
            self.openai_model = ""

        self.retry_config = retry_config or {}
        # `embedding.openapi.max_retries` is exposed in AstrBot UI; cap the
        # hidden A_memorix retry policy with it so one bad embedding gateway
        # cannot occupy the whole 60s AstrBot tool budget.
        configured_attempts = int(self.retry_config.get("max_attempts", self.max_retries))
        self.max_attempts = max(1, min(configured_attempts, self.max_retries))
        self.max_wait_seconds = max(0.1, min(5.0, float(self.retry_config.get("max_wait_seconds", 30))))
        self.min_wait_seconds = max(0.1, min(self.max_wait_seconds, float(self.retry_config.get("min_wait_seconds", 1))))

        self._dimension: Optional[int] = None
        self._dimension_detected = False
        # 是否向远端发送 `dimensions` 参数。默认 True 保持旧行为（OpenAI 等支持该参数
        # 的服务商借此返回稳定维度）；_detect_dimensions_support 探测若发现 provider 拒绝
        # 该参数（如 SiliconFlow 返回 20015 parameter is invalid），则置 False，后续 encode
        # 永不发送，避免每次写入都失败（issue #22）。
        self._supports_dimensions: bool = True
        self._dimensions_support_detected: bool = False
        self._client: Optional[AsyncOpenAI] = None

        self._total_encoded = 0
        self._total_errors = 0
        self._total_time = 0.0

        logger.info(
            "Embedding adapter initialized: model=%s, default_dim=%s, base_url=%s",
            self.openai_model,
            self.default_dimension,
            self.base_url or "<not-configured>",
        )

    def _get_client(self) -> AsyncOpenAI:
        if not self.base_url:
            raise RuntimeError("Embedding API base_url is not configured")
        if not self.openai_model:
            raise RuntimeError("Embedding API model is not configured")
        if self._client is None:
            kwargs = {
                "api_key": self.api_key or "EMPTY",
                "timeout": self.timeout_seconds,
                "max_retries": 0,  # retries are handled by adapter policy
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
        payload = {"model": self.openai_model, "input": inputs}
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
                self.min_wait_seconds * (2 ** (attempt - 1)),
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
        """探测 provider 是否接受 `dimensions` 请求参数（与维度探测解耦，issue #22）。

        用一条 probe 文本带 dimensions 试请求：成功 → True；被拒（如 SiliconFlow 20015
        parameter is invalid）→ False。结果缓存，进程生命周期内只探一次。

        独立于 _detect_dimension 存在的原因：auto_detect_dimension 关闭时 _detect_dimension
        不跑，但 dimensions 兼容性必须探——否则 SiliconFlow 仍每次写入撞 20015。encode 入口
        保证此方法至少跑一次，不依赖任何 auto_detect 开关。
        """
        if self._dimensions_support_detected:
            return self._supports_dimensions
        try:
            probed = await self._request_embeddings("dimension_probe", dimensions=self.default_dimension)
            self._supports_dimensions = bool(probed and probed[0])
        except Exception as exc:
            logger.debug("Dimensions param probe failed, treat as unsupported: %s", exc)
            self._supports_dimensions = False
        self._dimensions_support_detected = True
        return self._supports_dimensions

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return self._dimension

        # 先探 dimensions 兼容性（顺带复用第一次带 dimensions 的请求结果）。
        supports = await self._detect_dimensions_support()
        if supports:
            # 带 dimensions 探测成功，直接取其向量维度。
            try:
                probed = await self._request_embeddings("dimension_probe", dimensions=self.default_dimension)
                if probed and probed[0]:
                    self._dimension = len(probed[0])
                    self._dimension_detected = True
                    return self._dimension
            except Exception as exc:
                logger.debug("Dimension probe with requested dimension failed: %s", exc)

        # Provider rejected `dimensions`（_supports_dimensions 已置 False）。用模型原生
        # 维度重探，原生维度对写入是权威的（issue #22）。
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

    async def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del show_progress  # kept for compatibility
        del normalize  # API already returns normalized-ish vectors by model behavior
        start = time.time()

        if isinstance(texts, str):
            input_texts = [texts]
            single = True
        else:
            input_texts = list(texts)
            single = False

        target_dim = dimensions
        if target_dim is None:
            if not self._dimension_detected:
                await self._detect_dimension()
            target_dim = self._dimension or self.default_dimension
        target_dim = int(target_dim)

        # dimensions 兼容性探测与维度探测解耦：即便 auto_detect_dimension 关闭、
        # _detect_dimension 不跑，这里也保证至少探一次 SiliconFlow 等不支持 dimensions
        # 参数的 provider 被识别，避免每次写入撞 20015（issue #22）。
        if not self._dimensions_support_detected:
            await self._detect_dimensions_support()

        if not input_texts:
            empty = np.zeros((0, target_dim), dtype=np.float32)
            return empty[0] if single else empty

        use_batch = max(1, int(batch_size or self.batch_size))
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _encode_chunk(chunk: List[str]) -> np.ndarray:
            async with semaphore:
                try:
                    # 仅当 provider 真正接受 `dimensions` 时才发送。_detect_dimension 探测过
                    # 一次并缓存结果：SiliconFlow 等不支持的 provider 探测即失败 → 标志 False →
                    # 此处不发，避免每次写入撞 20015 parameter is invalid（issue #22）。
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

        tasks = []
        for idx in range(0, len(input_texts), use_batch):
            tasks.append(_encode_chunk(input_texts[idx : idx + use_batch]))
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
            )
        finally:
            self.max_concurrent = old

    def get_embedding_dimension(self) -> int:
        if self._dimension is not None:
            return int(self._dimension)
        return int(self.default_dimension)

    def get_embedding_fingerprint(self, *, dimension: Optional[int] = None) -> Dict[str, Any]:
        """embedding 指纹校验暂未启用（本期 vendored 无消费者），返回空 dict 降级。

        上游用此指纹做向量池 embedding 一致性校验；vendored 重写为 AsyncOpenAI+httpx
        无 provider，且 dual-pool 不依赖指纹，留接口空位待后续启用。
        """
        return {}

    def get_model_info(self) -> dict:
        avg_time = self._total_time / self._total_encoded if self._total_encoded > 0 else 0.0
        return {
            "model_name": self.openai_model,
            "dimension": self.get_embedding_dimension(),
            "dimension_detected": self._dimension_detected,
            "batch_size": self.batch_size,
            "max_concurrent": self.max_concurrent,
            "base_url": self.base_url,
            "total_encoded": self._total_encoded,
            "total_errors": self._total_errors,
            "avg_time_per_text": avg_time,
        }

    @property
    def is_model_loaded(self) -> bool:
        return True

    def __repr__(self) -> str:
        return (
            "EmbeddingAPIAdapter("
            f"model={self.openai_model}, "
            f"dim={self.get_embedding_dimension()}, "
            f"encoded={self._total_encoded})"
        )


def create_embedding_api_adapter(
    batch_size: int = 32,
    max_concurrent: int = 5,
    default_dimension: int = 1024,
    model_name: str = "auto",
    retry_config: Optional[dict] = None,
    base_url: str = "",
    api_key: str = "",
    openai_model: str = "",
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
) -> EmbeddingAPIAdapter:
    return EmbeddingAPIAdapter(
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        default_dimension=default_dimension,
        model_name=model_name,
        retry_config=retry_config,
        base_url=base_url,
        api_key=api_key,
        openai_model=openai_model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
