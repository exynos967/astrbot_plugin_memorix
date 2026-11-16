"""Scope runtime management for memorix plugin."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger("astrbot_plugin_memorix")

from .amemorix.bootstrap import build_context
from .amemorix.settings import DEFAULT_CONFIG, AppSettings
from .amemorix.task_manager import TaskManager
from .providers import AstrBotProviderBridge

_SCOPE_DIR_PATTERN = re.compile(r"[^0-9A-Za-z._-]+")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def get_astrbot_data_path() -> Optional[str]:
    """基于公开 StarTools API 安全推导 AstrBot 数据根目录，绝不依赖私有 astrbot.core.* 模块。"""
    try:
        from astrbot.api.star import StarTools

        plugin_dir = StarTools.get_data_dir("astrbot_plugin_memorix")
        if plugin_dir:
            resolved = Path(plugin_dir).resolve()
            if resolved.parent.name == "plugin_data":
                return str(resolved.parent.parent)
            return str(resolved.parent)
    except Exception:
        return None
    return None


def resolve_plugin_data_dir(
    *,
    plugin_config: Optional[Dict[str, Any]] = None,
    astrbot_context: Any = None,
    plugin_name: str = "astrbot_plugin_memorix",
    base_data_dir: Optional[Path | str] = None,
) -> Path:
    """统一解析插件持久化数据根目录，确保 MemorixPlugin 与 ScopeRuntimeManager 完全一致。"""
    name = str(plugin_name or "astrbot_plugin_memorix").strip() or "astrbot_plugin_memorix"
    cfg = plugin_config if isinstance(plugin_config, dict) else {}
    storage_cfg = cfg.get("storage")
    if isinstance(storage_cfg, dict):
        custom_dir = storage_cfg.get("data_dir")
        if custom_dir:
            return Path(str(custom_dir)).resolve()

    if base_data_dir is not None and str(base_data_dir).strip():
        return Path(base_data_dir).resolve()

    try:
        from astrbot.api.star import StarTools

        data_dir = StarTools.get_data_dir(name)
        if data_dir:
            return Path(data_dir).resolve()
    except Exception:
        pass

    ctx_data_dir = getattr(astrbot_context, "_data_dir", None) or getattr(astrbot_context, "data_dir", None)
    if callable(ctx_data_dir):
        try:
            ctx_data_dir = ctx_data_dir()
        except Exception:
            ctx_data_dir = None
    if ctx_data_dir:
        return (Path(str(ctx_data_dir)).resolve() / "memorix_data").resolve()

    data_root_raw = get_astrbot_data_path()
    if data_root_raw:
        return (Path(data_root_raw) / "plugin_data" / name).resolve()

    return (Path.cwd() / "data" / "plugin_data" / name).resolve()


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class LocalEmbeddingAdapter:
    """Deterministic local embedding fallback for offline mode."""

    def __init__(self, dimension: int):
        self.dimension = int(max(1, dimension))

    async def _detect_dimension(self) -> int:
        return self.dimension

    async def encode(self, texts, **kwargs):
        del kwargs
        return await asyncio.to_thread(self._encode_sync, texts)

    async def encode_batch(self, texts, **kwargs):
        return await self.encode(list(texts or []), **kwargs)

    def _encode_sync(self, texts):
        if isinstance(texts, str):
            return self._embed_text(texts)

        vectors = [self._embed_text(str(text)) for text in list(texts)]
        if not vectors:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return np.vstack(vectors).astype(np.float32)

    def get_embedding_dimension(self) -> int:
        return self.dimension

    def _embed_text(self, text: str) -> np.ndarray:
        payload = str(text or "")
        digest = hashlib.sha256(payload.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(self.dimension, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)


@dataclass(slots=True)
class ScopeRuntime:
    scope_key: str
    settings: AppSettings
    context: Any
    task_manager: TaskManager


class ScopeRuntimeManager:
    def __init__(
        self,
        *,
        plugin_name: str = "astrbot_plugin_memorix",
        plugin_config: Optional[Dict[str, Any]] = None,
        astrbot_context: Any = None,
        base_data_dir: Optional[Path | str] = None,
        exact_scope_dir: Optional[Path | str] = None,
        provider_bridge: Optional[Any] = None,
    ):
        self.plugin_name = plugin_name
        self.plugin_config = dict(plugin_config or {})
        self.astrbot_context = astrbot_context
        self.base_data_dir = Path(base_data_dir).resolve() if base_data_dir is not None else None
        self.exact_scope_dir = Path(exact_scope_dir).resolve() if exact_scope_dir is not None else None
        self.provider_bridge = provider_bridge
        self._runtimes: Dict[str, ScopeRuntime] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    def _resolve_plugin_data_dir(self, plugin_name: Optional[str] = None) -> Path:
        """安全解析插件数据根目录，优先读取显式配置与官方 StarTools，再回退上下文目录。"""
        name = str(plugin_name or self.plugin_name or "astrbot_plugin_memorix")
        return resolve_plugin_data_dir(
            plugin_config=self.plugin_config,
            astrbot_context=self.astrbot_context,
            plugin_name=name,
            base_data_dir=self.base_data_dir,
        )

    def _scope_base_dir(self) -> Path:
        base = self._resolve_plugin_data_dir(self.plugin_name) / "scopes"
        base.mkdir(parents=True, exist_ok=True)
        return base.resolve()

    def _scope_dir(self, scope_key: str) -> Path:
        if self.exact_scope_dir is not None:
            target = self.exact_scope_dir.resolve()
            target.mkdir(parents=True, exist_ok=True)
            self._write_scope_manifest(target, scope_key)
            return target
        base_resolved = self._scope_base_dir()
        canonical_key = str(scope_key or "").strip() or "default"
        safe_scope = self._sanitize_scope_dirname(canonical_key)
        target = (base_resolved / safe_scope).resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            fallback = f"scope_{hashlib.sha256(canonical_key.encode('utf-8')).hexdigest()[:16]}"
            logger.warning(
                "unsafe scope dir detected, fallback applied: scope=%s fallback=%s",
                scope_key,
                fallback,
            )
            target = base_resolved / fallback
        existing_key = self._read_scope_manifest(target)
        if existing_key and existing_key != canonical_key:
            fallback = f"scope_{hashlib.sha256(canonical_key.encode('utf-8')).hexdigest()[:16]}"
            logger.warning(
                "scope dir collision, fallback applied: scope=%s existing=%s fallback=%s",
                scope_key,
                existing_key,
                fallback,
            )
            target = (base_resolved / fallback).resolve()
        target.mkdir(parents=True, exist_ok=True)
        self._write_scope_manifest(target, canonical_key)
        logger.debug("resolved scope dir: scope=%s dir=%s", target.name, target)
        return target

    @staticmethod
    def _sanitize_scope_dirname(scope_key: str) -> str:
        text = re.sub(r"[\x00-\x1f\x7f]+", "_", str(scope_key or "default")).strip()
        text = text.replace("/", "_").replace("\\", "_").replace(":", "_")
        text = re.sub(r"\s+", "_", text)
        text = _SCOPE_DIR_PATTERN.sub("_", text)
        text = text.strip("._")
        while ".." in text:
            text = text.replace("..", "_")
        text = text.strip("._")
        if text in {"", ".", ".."}:
            return "default"
        stem = text.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            text = f"safe_{text}"
        return text[:128] or "default"


    @staticmethod
    def _read_scope_manifest(scope_dir: Path) -> str:
        manifest = scope_dir / ".scope.json"
        if not manifest.exists():
            return ""
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(payload.get("scope_key", "") or "").strip()

    @staticmethod
    def _write_scope_manifest(scope_dir: Path, scope_key: str) -> None:
        try:
            (scope_dir / ".scope.json").write_text(
                json.dumps({"scope_key": str(scope_key or "default")}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("write scope manifest failed: scope=%s dir=%s", scope_key, scope_dir, exc_info=True)

    @staticmethod
    def _infer_scope_key_from_dirname(dirname: str) -> str:
        name = str(dirname or "").strip()
        if "_group_" in name:
            platform, group_id = name.split("_group_", 1)
            if platform and group_id:
                return f"{platform}:group:{group_id}"
        if "_user_" in name:
            platform, user_id = name.split("_user_", 1)
            if platform and user_id:
                return f"{platform}:user:{user_id}"
        return name

    def list_scope_keys(self) -> list[str]:
        keys: list[str] = []

        def add(value: str) -> None:
            key = str(value or "").strip()
            if key and not self._is_internal_scope_key(key) and key not in keys:
                keys.append(key)

        for key in self._runtimes:
            add(key)

        try:
            base = self._scope_base_dir()
            for item in sorted(base.iterdir(), key=lambda path: path.name):
                if not item.is_dir():
                    continue
                manifest = item / ".scope.json"
                if manifest.exists():
                    try:
                        payload = json.loads(manifest.read_text(encoding="utf-8"))
                        add(str(payload.get("scope_key", "") or ""))
                        continue
                    except Exception:
                        logger.debug("read scope manifest failed: %s", manifest, exc_info=True)
                add(self._infer_scope_key_from_dirname(item.name))
        except Exception:
            logger.debug("list scope keys failed", exc_info=True)

        return keys

    @staticmethod
    def _is_internal_scope_key(scope_key: str) -> bool:
        platform = str(scope_key or "").split(":", 1)[0].strip().lower()
        return platform == "cron"

    def _build_scope_config(self, scope_key: str) -> Dict[str, Any]:
        cfg = _deep_merge(DEFAULT_CONFIG, self.plugin_config)
        cfg.setdefault("storage", {})
        cfg["storage"]["data_dir"] = str(self._scope_dir(scope_key))
        img_cfg = cfg.get("image_memory")
        if isinstance(img_cfg, dict):
            user_enabled = bool(
                img_cfg["configured_enabled"]
                if "configured_enabled" in img_cfg
                else img_cfg.get("enabled", True)
            )
            img_cfg = {**img_cfg, "configured_enabled": user_enabled}
            cfg["image_memory"] = img_cfg
            if user_enabled:
                from .engine.host_shims.embedding_shim import is_image_memory_enabled

                if not is_image_memory_enabled(config=cfg):
                    img_cfg["enabled"] = False
                    logger.info(
                        "image_memory.require_embedding is enabled and image embedding is unconfigured; disabling image memory for scope=%s",
                        scope_key,
                    )

        return cfg

    def _build_provider_bridge(self) -> Optional[Any]:
        if self.provider_bridge is not None:
            return self.provider_bridge
        if self.astrbot_context is None:
            return None
        provider_cfg = self.plugin_config.get("provider", {})
        if not isinstance(provider_cfg, dict):
            provider_cfg = {}
        return AstrBotProviderBridge(
            astrbot_context=self.astrbot_context,
            # 聊天模型可选 AstrBot 中已定义 provider（配置优先）。
            chat_provider_id=str(provider_cfg.get("chat_provider_id", "") or ""),
            fast_chat_provider_id=str(provider_cfg.get("fast_chat_provider_id", "") or ""),
        )

    def _patch_local_embedding(self, runtime: ScopeRuntime) -> None:
        dimension = int(runtime.settings.get("embedding.dimension", 1024) or 1024)
        adapter = LocalEmbeddingAdapter(dimension=dimension)
        runtime.context.embedding_manager = adapter
        runtime.context.retriever.embedding_manager = adapter
        if hasattr(runtime.context, "paragraph_vector_service"):
            runtime.context.paragraph_vector_service.embedding_manager = adapter
        if hasattr(runtime.context, "relation_write_service"):
            runtime.context.relation_write_service.embedding_manager = adapter
        if hasattr(runtime.context, "person_profile_service"):
            runtime.context.person_profile_service.embedding_manager = adapter
        logger.info("local embedding fallback enabled: scope=%s dim=%s", runtime.scope_key, dimension)

    async def get_runtime(self, scope_key: str) -> ScopeRuntime:
        if self._closing:
            raise RuntimeError("Memorix runtime manager is closing")
        key = str(scope_key or "").strip() or "default"
        existing = self._runtimes.get(key)
        if existing is not None:
            logger.debug("runtime cache hit: scope=%s", key)
            return existing

        async with self._lock:
            if self._closing:
                raise RuntimeError("Memorix runtime manager is closing")
            existing = self._runtimes.get(key)
            if existing is not None:
                logger.debug("runtime cache hit after lock: scope=%s", key)
                return existing

            cfg = self._build_scope_config(key)
            settings = AppSettings(config=cfg)
            provider_bridge = self._build_provider_bridge()
            from .engine.host_shims import config_shim, install_host_shims, llm_shim

            install_host_shims(config=cfg, provider_bridge=provider_bridge, scope_local=True)
            logger.info("create runtime: scope=%s data_dir=%s", key, settings.data_dir)
            with config_shim.use_scope_config(cfg), llm_shim.use_provider_bridge(provider_bridge):
                ctx = await asyncio.to_thread(build_context, settings)
            ctx.astrbot_context = self.astrbot_context
            ctx.provider_bridge = provider_bridge
            task_manager = TaskManager(ctx)
            runtime = ScopeRuntime(scope_key=key, settings=settings, context=ctx, task_manager=task_manager)

            try:
                embedding_enabled = bool(settings.get("embedding.enabled", False))
                if embedding_enabled:
                    endpoint_cfg = settings.get_openapi_endpoint_config()
                    logger.info(
                        "openapi embedding enabled: scope=%s base_url=%s model=%s",
                        key,
                        str(endpoint_cfg.get("base_url", "") or "<not-configured>"),
                        str(endpoint_cfg.get("model", "") or "<not-configured>"),
                    )
                else:
                    self._patch_local_embedding(runtime)

                from .services.graph_rename_service import GraphRenameService

                try:
                    await GraphRenameService(ctx).reconcile()
                except Exception as exc:
                    logger.warning("[memorix] graph rename reconcile failed non-fatally: scope=%s: %s", key, exc)
                await task_manager.start()
            except BaseException:
                try:
                    await task_manager.stop()
                finally:
                    await ctx.close()
                raise
            self._runtimes[key] = runtime
            logger.info(
                "runtime ready: scope=%s embedding_mode=%s",
                key,
                "openapi" if embedding_enabled else "local-fallback",
            )
            return runtime

    def get_known_scopes(self) -> list[str]:
        return list(self._runtimes.keys())

    def get_person_profile_policy(self) -> Dict[str, Any]:
        person_cfg = self.plugin_config.get("person_profile", {})
        if not isinstance(person_cfg, dict):
            person_cfg = {}
        return {
            "enabled": bool(person_cfg.get("enabled", True)),
            "known_scopes": list(self._runtimes.keys()),
        }

    async def apply_person_profile_policy(
        self,
        *,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        def _apply(cfg: Dict[str, Any]) -> None:
            person_cfg = cfg.get("person_profile")
            if not isinstance(person_cfg, dict):
                person_cfg = {}
                cfg["person_profile"] = person_cfg
            if enabled is not None:
                person_cfg["enabled"] = bool(enabled)

        async with self._lock:
            _apply(self.plugin_config)
            for runtime in self._runtimes.values():
                _apply(runtime.settings.config)
                runtime.context.config = runtime.settings.config

        policy = self.get_person_profile_policy()
        logger.info(
            "person profile policy updated: enabled=%s scopes=%s",
            policy["enabled"],
            len(policy["known_scopes"]),
        )
        return policy

    async def close_scope(self, scope_key: str) -> None:
        key = str(scope_key or "").strip() or "default"
        async with self._lock:
            runtime = self._runtimes.get(key)
        if runtime is None:
            return
        try:
            await runtime.task_manager.stop()
        except RuntimeError:
            logger.warning(
                "stop task manager timed out while background workers drain; deferring context close: scope=%s",
                runtime.scope_key,
                exc_info=True,
            )
            raise
        except Exception:
            logger.warning("stop task manager failed: scope=%s", runtime.scope_key, exc_info=True)
        async with self._lock:
            if self._runtimes.get(key) is runtime:
                self._runtimes.pop(key, None)
        try:
            await runtime.context.close()
        except Exception:
            logger.warning("close context failed: scope=%s", runtime.scope_key, exc_info=True)

    async def close_all(self, *, mark_closing: bool = True) -> None:
        async with self._lock:
            if mark_closing:
                self._closing = True
            runtimes = list(self._runtimes.items())

        logger.info("close all runtimes: count=%s", len(runtimes))
        pending_Error: Optional[RuntimeError] = None
        for key, runtime in runtimes:
            try:
                await runtime.task_manager.stop()
            except RuntimeError as exc:
                pending_Error = pending_Error or exc
                logger.warning(
                    "stop task manager timed out while background workers drain; keeping runtime alive: scope=%s",
                    runtime.scope_key,
                    exc_info=True,
                )
                continue
            except Exception:
                logger.warning("stop task manager failed: scope=%s", runtime.scope_key, exc_info=True)
            async with self._lock:
                if self._runtimes.get(key) is runtime:
                    self._runtimes.pop(key, None)
            try:
                await runtime.context.close()
            except Exception:
                logger.warning("close context failed: scope=%s", runtime.scope_key, exc_info=True)
        if pending_Error is not None:
            async with self._lock:
                self._closing = False
            raise pending_Error
        logger.info("all runtimes closed")

