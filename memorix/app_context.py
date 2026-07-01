"""Scope runtime management for memorix plugin."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .amemorix.bootstrap import build_context
from .amemorix.settings import DEFAULT_CONFIG, AppSettings
from .amemorix.task_manager import TaskManager
from .providers import AstrBotProviderBridge

_SCOPE_DIR_PATTERN = re.compile(r"[^0-9A-Za-z._-]+")


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
        plugin_name: str,
        plugin_config: Dict[str, Any],
        astrbot_context: Any = None,
    ):
        self.plugin_name = plugin_name
        self.plugin_config = dict(plugin_config or {})
        self.astrbot_context = astrbot_context
        self._runtimes: Dict[str, ScopeRuntime] = {}
        self._lock = asyncio.Lock()

    def _scope_base_dir(self) -> Path:
        data_root_raw = get_astrbot_data_path()
        data_root = Path(data_root_raw) if data_root_raw else (Path.cwd() / "data")
        base = data_root / "plugin_data" / self.plugin_name / "scopes"
        base.mkdir(parents=True, exist_ok=True)
        return base.resolve()

    def _scope_dir(self, scope_key: str) -> Path:
        base_resolved = self._scope_base_dir()
        safe_scope = self._sanitize_scope_dirname(scope_key)
        target = (base_resolved / safe_scope).resolve()
        try:
            target.relative_to(base_resolved)
        except ValueError:
            fallback = f"scope_{hashlib.sha256(str(scope_key or '').encode('utf-8')).hexdigest()[:16]}"
            logger.warning(
                "unsafe scope dir detected, fallback applied: scope=%s fallback=%s",
                scope_key,
                fallback,
            )
            target = base_resolved / fallback
        target.mkdir(parents=True, exist_ok=True)
        self._write_scope_manifest(target, scope_key)
        logger.debug("resolved scope dir: scope=%s dir=%s", target.name, target)
        return target

    @staticmethod
    def _sanitize_scope_dirname(scope_key: str) -> str:
        text = str(scope_key or "default").strip()
        text = text.replace("/", "_").replace("\\", "_").replace(":", "_")
        text = re.sub(r"\s+", "_", text)
        text = _SCOPE_DIR_PATTERN.sub("_", text)
        text = text.strip("._")
        if ".." in text:
            text = text.replace("..", "_")
        if text in {"", ".", ".."}:
            return "default"
        return text[:128] or "default"

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

        return cfg

    def _build_provider_bridge(self) -> Optional[AstrBotProviderBridge]:
        if self.astrbot_context is None:
            return None
        provider_cfg = self.plugin_config.get("provider", {})
        if not isinstance(provider_cfg, dict):
            provider_cfg = {}
        return AstrBotProviderBridge(
            astrbot_context=self.astrbot_context,
            # 聊天模型可选 AstrBot 中已定义 provider（配置优先）。
            chat_provider_id=str(provider_cfg.get("chat_provider_id", "") or ""),
            embedding_provider_id="",
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
        key = str(scope_key or "default")
        existing = self._runtimes.get(key)
        if existing is not None:
            logger.debug("runtime cache hit: scope=%s", key)
            return existing

        async with self._lock:
            existing = self._runtimes.get(key)
            if existing is not None:
                logger.debug("runtime cache hit after lock: scope=%s", key)
                return existing

            cfg = self._build_scope_config(key)
            settings = AppSettings(config=cfg)
            logger.info("create runtime: scope=%s data_dir=%s", key, settings.data_dir)
            ctx = build_context(settings)
            ctx.astrbot_context = self.astrbot_context
            provider_bridge = self._build_provider_bridge()
            ctx.provider_bridge = provider_bridge
            task_manager = TaskManager(ctx)
            runtime = ScopeRuntime(scope_key=key, settings=settings, context=ctx, task_manager=task_manager)

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

            await task_manager.start()
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

    async def close_all(self) -> None:
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()

        logger.info("close all runtimes: count=%s", len(runtimes))
        for runtime in runtimes:
            try:
                await runtime.task_manager.stop()
            except Exception:
                logger.warning("stop task manager failed: scope=%s", runtime.scope_key, exc_info=True)
            try:
                await runtime.context.close()
            except Exception:
                logger.warning("close context failed: scope=%s", runtime.scope_key, exc_info=True)
        logger.info("all runtimes closed")
