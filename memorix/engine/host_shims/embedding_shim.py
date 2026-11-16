"""
Host Embedding Shim: 桥接 A_memorix 核心的图片与文本 EmbeddingServiceClient 调用。

提供可扩展的图片嵌入回调钩子与可选 OpenAI-compatible 多模态图片嵌入支持；
未配置远程图片嵌入服务时显式抛出可读 RuntimeError（供 HostImageEmbedder.probe 安全降级为精确哈希图片记忆），
彻底杜绝落入 _RecursiveStub 触发不可 await 的 TypeError。
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextlib
import contextvars
import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, Sequence, TypeVar

import httpx

from ...amemorix.settings import resolve_openapi_endpoint_config
from . import config_shim

_T = TypeVar("_T")


@dataclass
class EmbeddingResult:
    embedding: List[float]
    model_name: str = ""
    model_identifier: str = ""
    api_provider: str = ""


@dataclass
class ImageEmbeddingResult:
    embedding: List[float]
    model_name: str = ""
    model_identifier: str = ""
    api_provider: str = ""
    mime_type: str = "image/png"
    preprocess_version: str = "identity_v1"
    request_protocol_hash: str = ""


ImageEmbedderCallable = Callable[..., Any]

_active_image_embedder: Optional[ImageEmbedderCallable] = None
_scope_image_embedder_var: contextvars.ContextVar[Optional[ImageEmbedderCallable]] = contextvars.ContextVar(
    "memorix_scope_image_embedder",
    default=None,
)


def set_active_image_embedder(embedder: Optional[ImageEmbedderCallable]) -> None:
    global _active_image_embedder
    _active_image_embedder = embedder


def get_active_image_embedder() -> Optional[ImageEmbedderCallable]:
    scoped = _scope_image_embedder_var.get()
    if scoped is not None:
        return scoped
    return _active_image_embedder


@contextlib.contextmanager
def use_image_embedder(embedder: Optional[ImageEmbedderCallable]) -> Iterator[None]:
    if embedder is None:
        yield
        return
    token = _scope_image_embedder_var.set(embedder)
    try:
        yield
    finally:
        _scope_image_embedder_var.reset(token)


def resolve_embedding_model_fingerprint(
    *,
    model_name: str,
    model_identifier: str,
    api_provider: str,
    dimension: int,
) -> Dict[str, Any]:
    comparable = {
        "dimension": int(dimension),
        "modality": "text",
        "model": str(model_identifier or model_name or "").strip(),
        "provider": str(api_provider or "").strip(),
        "version": 1,
    }
    encoded = json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**comparable, "hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}


def _normalize_openai_embeddings_url(base_url: str) -> str:
    clean = str(base_url or "").strip().rstrip("/")
    if not clean:
        return ""
    if clean.endswith("/embeddings"):
        return clean
    if clean.endswith("/v1"):
        return f"{clean}/embeddings"
    return f"{clean}/v1/embeddings"


def is_image_embedding_configured(
    task_name: str = "image_embedding",
    *,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """静态检查当前作用域或给定配置是否已配置可用的多模态图片嵌入模型（不发起网络请求）。"""
    if callable(get_active_image_embedder()):
        return True
    cfg = config if isinstance(config, dict) else config_shim.get_effective_config_dict()
    endpoint_cfg = resolve_openapi_endpoint_config(cfg, section="image_memory")
    url = _normalize_openai_embeddings_url(str(endpoint_cfg.get("base_url", "") or ""))
    model = str(endpoint_cfg.get("model", "") or "").strip()
    remote_enabled = bool(endpoint_cfg["enabled"]) if "enabled" in endpoint_cfg else bool(url and model)
    if remote_enabled and bool(url and model):
        return True
    clean_task = str(task_name or "image_embedding").strip() or "image_embedding"
    try:
        model_cfg = config_shim.config_manager.get_model_config(clean_task)
        task_cfg = getattr(getattr(model_cfg, "model_task_config", None), clean_task, None)
        model_list = getattr(task_cfg, "model_list", None)
        if isinstance(model_list, list) and any(str(item or "").strip() for item in model_list):
            return True
    except Exception:
        pass
    return False


def resolve_image_memory_status(*, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """解析图片记忆当前生效状态，区分手动关闭与未配置图片嵌入模型导致的自动暂停。"""
    cfg = config if isinstance(config, dict) else config_shim.get_effective_config_dict()
    img_cfg = cfg.get("image_memory", {}) if isinstance(cfg.get("image_memory"), dict) else {}
    configured_enabled = (
        bool(img_cfg["configured_enabled"])
        if "configured_enabled" in img_cfg
        else bool(img_cfg.get("enabled", True))
    )
    require_embedding = bool(img_cfg.get("require_embedding", True))
    task_name = str(img_cfg.get("task_name", "image_embedding") or "image_embedding").strip() or "image_embedding"
    emb_configured = is_image_embedding_configured(task_name, config=cfg)

    if not configured_enabled:
        return {
            "enabled": False,
            "configured_enabled": False,
            "require_embedding": require_embedding,
            "embedding_configured": emb_configured,
            "paused_by_missing_embedding": False,
            "status": "disabled",
            "reason": "manually_disabled",
            "message": "已在设置中手动关闭图片记忆",
        }
    if require_embedding and not emb_configured:
        return {
            "enabled": False,
            "configured_enabled": True,
            "require_embedding": True,
            "embedding_configured": False,
            "paused_by_missing_embedding": True,
            "status": "disabled",
            "reason": "missing_image_embedding",
            "message": "未配置多模态图片嵌入模型，图片记忆已自动暂停（防识图报错）",
        }
    return {
        "enabled": True,
        "configured_enabled": True,
        "require_embedding": require_embedding,
        "embedding_configured": emb_configured,
        "paused_by_missing_embedding": False,
        "status": "ready" if emb_configured else "exact_hash_only",
        "reason": "ready" if emb_configured else "exact_hash_only",
        "message": "多模态图片记忆已就绪" if emb_configured else "未配置图片嵌入模型，当前以同图精确哈希模式运行",
    }


def is_image_memory_enabled(*, config: Optional[Dict[str, Any]] = None) -> bool:
    """判断图片记忆是否启用（默认要求已配置图片嵌入模型，对齐上游 407deaf90 防报错门控）。"""
    return bool(resolve_image_memory_status(config=config)["enabled"])


def _extract_first_embedding_vector(payload: Any) -> List[float]:
    from ...core.embedding.api_adapter import _coerce_embedding_rows

    return _coerce_embedding_rows(payload)[0]


def _run_coro_sync(coro_factory: Callable[[], Awaitable[_T]]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    copied_ctx = contextvars.copy_context()

    def _runner() -> _T:
        return copied_ctx.run(lambda: asyncio.run(coro_factory()))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()


class EmbeddingServiceClient:
    """兼容上游 src.services.embedding_service.EmbeddingServiceClient 的门面客户端。"""

    def __init__(self, task_name: str = "embedding", request_type: str = "", session_id: str = "") -> None:
        self.task_name = str(task_name or "embedding")
        self.request_type = str(request_type or "")
        self.session_id = str(session_id or "").strip()

    @staticmethod
    def _effective_cfg() -> Dict[str, Any]:
        return config_shim.get_effective_config_dict()

    async def embed_text(self, embedding_input: str, *, session_id: str = "") -> EmbeddingResult:
        del session_id
        cfg = self._effective_cfg()
        emb_cfg = cfg.get("embedding", {}) if isinstance(cfg.get("embedding"), dict) else {}
        dimension = max(1, int(emb_cfg.get("dimension", 1024) or 1024))
        endpoint_cfg = resolve_openapi_endpoint_config(cfg, section="embedding")
        url = _normalize_openai_embeddings_url(str(endpoint_cfg.get("base_url", "") or ""))
        model = str(endpoint_cfg.get("model", "") or "").strip()
        if "enabled" in emb_cfg:
            remote_enabled = bool(emb_cfg["enabled"])
        elif "enabled" in endpoint_cfg:
            remote_enabled = bool(endpoint_cfg["enabled"])
        else:
            remote_enabled = bool(url and model)

        if remote_enabled:
            api_key = str(endpoint_cfg.get("api_key", "") or "").strip()
            timeout_s = float(endpoint_cfg.get("timeout_seconds", 30.0) or 30.0)
            if not url or not model:
                raise RuntimeError("已启用远程文本 Embedding，但未完整配置 base_url 或 model")
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, headers=headers, json={"model": model, "input": str(embedding_input or "")})
                resp.raise_for_status()
                body = resp.json()
            vec = _extract_first_embedding_vector(body)
            openapi_sub = emb_cfg.get("openapi") if isinstance(emb_cfg.get("openapi"), dict) else {}
            provider_label = (
                "openapi"
                if str(openapi_sub.get("base_url") or openapi_sub.get("model") or "").strip()
                else "openai"
            )
            return EmbeddingResult(
                embedding=[float(x) for x in vec],
                model_name=model,
                model_identifier=model,
                api_provider=provider_label,
            )

        from ...app_context import LocalEmbeddingAdapter

        vec_arr = await LocalEmbeddingAdapter(dimension=dimension).encode(str(embedding_input or ""))
        return EmbeddingResult(
            embedding=[float(x) for x in vec_arr.reshape(-1).tolist()],
            model_name="local_deterministic",
            model_identifier="local_deterministic",
            api_provider="local",
        )

    def embed_text_sync(self, embedding_input: str, *, session_id: str = "") -> EmbeddingResult:
        return _run_coro_sync(lambda: self.embed_text(embedding_input, session_id=session_id))

    async def embed_texts(
        self,
        embedding_inputs: List[str],
        max_concurrent: int | None = None,
        *,
        session_id: str = "",
        return_exceptions: bool = False,
    ) -> List[EmbeddingResult | BaseException]:
        if not embedding_inputs:
            return []
        sem = asyncio.Semaphore(max(1, int(max_concurrent or 1)))

        async def _one(text: str) -> EmbeddingResult:
            async with sem:
                return await self.embed_text(text, session_id=session_id)

        results = await asyncio.gather(*(_one(t) for t in embedding_inputs), return_exceptions=return_exceptions)
        return list(results)

    def embed_texts_sync(
        self,
        embedding_inputs: List[str],
        max_concurrent: int | None = None,
        *,
        session_id: str = "",
        return_exceptions: bool = False,
    ) -> List[EmbeddingResult | BaseException]:
        return _run_coro_sync(
            lambda: self.embed_texts(
                embedding_inputs,
                max_concurrent=max_concurrent,
                session_id=session_id,
                return_exceptions=return_exceptions,
            )
        )

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        preprocess_version: str,
        session_id: str = "",
    ) -> ImageEmbeddingResult:
        if not image_bytes:
            raise ValueError("图片嵌入输入不能为空")
        clean_mime = str(mime_type or "").strip().lower()
        if not clean_mime.startswith("image/"):
            raise ValueError("图片嵌入输入必须提供 image/* MIME 类型")

        eff_session = str(session_id or self.session_id or "").strip()
        custom_fn = get_active_image_embedder()
        if callable(custom_fn):
            candidate_kwargs = {
                "mime_type": clean_mime,
                "preprocess_version": preprocess_version,
                "session_id": eff_session,
            }
            try:
                sig = inspect.signature(custom_fn)
                has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                accepted_kwargs = (
                    candidate_kwargs
                    if has_var_kw
                    else {k: v for k, v in candidate_kwargs.items() if k in sig.parameters}
                )
            except (TypeError, ValueError):
                accepted_kwargs = candidate_kwargs
            raw = custom_fn(bytes(image_bytes), **accepted_kwargs)
            if inspect.isawaitable(raw):
                raw = await raw
            if isinstance(raw, ImageEmbeddingResult):
                return raw
            if isinstance(raw, dict):
                return ImageEmbeddingResult(
                    embedding=[float(x) for x in raw.get("embedding", [])],
                    model_name=str(raw.get("model_name") or "custom_image_embedder"),
                    model_identifier=str(raw.get("model_identifier") or raw.get("model_name") or "custom_image_embedder"),
                    api_provider=str(raw.get("api_provider") or "astrbot_custom"),
                    mime_type=clean_mime,
                    preprocess_version=preprocess_version,
                    request_protocol_hash=str(raw.get("request_protocol_hash") or ""),
                )
            if isinstance(raw, Sequence):
                return ImageEmbeddingResult(
                    embedding=[float(x) for x in raw],
                    model_name="custom_image_embedder",
                    model_identifier="custom_image_embedder",
                    api_provider="astrbot_custom",
                    mime_type=clean_mime,
                    preprocess_version=preprocess_version,
                )

        cfg = self._effective_cfg()
        img_cfg = cfg.get("image_memory", {}) if isinstance(cfg.get("image_memory"), dict) else {}
        configured_enabled = (
            bool(img_cfg["configured_enabled"])
            if "configured_enabled" in img_cfg
            else bool(img_cfg.get("enabled", True))
        )
        require_embedding = bool(img_cfg.get("require_embedding", True))
        endpoint_cfg = resolve_openapi_endpoint_config(cfg, section="image_memory")
        url = _normalize_openai_embeddings_url(str(endpoint_cfg.get("base_url", "") or ""))
        model = str(endpoint_cfg.get("model", "") or "").strip()
        remote_img_enabled = (
            bool(endpoint_cfg["enabled"])
            if "enabled" in endpoint_cfg
            else bool(url and model)
        )
        if configured_enabled and remote_img_enabled:
            if not url or not model:
                raise RuntimeError("已启用图片向量嵌入，但未完整配置 image_memory.openapi.base_url 或 model")
            if is_image_memory_enabled(config=cfg):
                api_key = str(endpoint_cfg.get("api_key", "") or "").strip()
                timeout_s = float(endpoint_cfg.get("timeout_seconds", 30.0) or 30.0)
                data_uri = f"data:{clean_mime};base64,{base64.b64encode(bytes(image_bytes)).decode('ascii')}"
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                protocol_id = hashlib.sha256(f"openai_multimodal_data_uri_v1:{url}:{model}".encode("utf-8")).hexdigest()[:16]
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    resp = await client.post(
                        url,
                        headers=headers,
                        json={"model": model, "input": [{"type": "image_url", "image_url": {"url": data_uri}}]},
                    )
                    resp.raise_for_status()
                    body = resp.json()
                vec = _extract_first_embedding_vector(body)
                return ImageEmbeddingResult(
                    embedding=[float(x) for x in vec],
                    model_name=model,
                    model_identifier=model,
                    api_provider="openapi",
                    mime_type=clean_mime,
                    preprocess_version=preprocess_version,
                    request_protocol_hash=f"sha256:{protocol_id}",
                )

        if not configured_enabled:
            raise RuntimeError("已在设置中手动关闭图片记忆，未启用多模态图片向量嵌入模型")
        if require_embedding:
            raise RuntimeError("未配置多模态图片向量嵌入模型，图片记忆已自动暂停")
        raise RuntimeError("未配置多模态图片向量嵌入模型，当前仅启用精确哈希与元数据图片记忆")
