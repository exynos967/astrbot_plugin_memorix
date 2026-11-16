"""
Config Host Shim.
Bridges src.config.config and src.config.model_configs to AstrBot configuration.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, Optional

_scoped_config: ContextVar[Optional[Dict[str, Any]]] = ContextVar("memorix_scoped_config", default=None)


@contextmanager
def use_scope_config(config_dict: Optional[Dict[str, Any]]) -> Iterator[None]:
    """在当前异步上下文中绑定作用域级配置快照，避免多作用域并发覆盖全局配置。"""
    token = _scoped_config.set(dict(config_dict) if isinstance(config_dict, dict) else None)
    try:
        yield
    finally:
        _scoped_config.reset(token)


@dataclass
class APIProvider:
    name: str = "default"
    base_url: str = ""
    api_key: str = ""
    provider_type: str = "openai"


@dataclass
class TaskEmbeddingConfig:
    model_list: list[str] = field(default_factory=list)


@dataclass
class ModelTaskConfig:
    embedding: TaskEmbeddingConfig = field(default_factory=TaskEmbeddingConfig)
    image_embedding: TaskEmbeddingConfig = field(default_factory=TaskEmbeddingConfig)
    fast_model: TaskEmbeddingConfig = field(default_factory=TaskEmbeddingConfig)
    utils: TaskEmbeddingConfig = field(default_factory=TaskEmbeddingConfig)


@dataclass
class ModelInfo:
    model_name: str = "default"
    provider: str = "default"
    api_provider: str = "default"
    max_tokens: int = 4096
    temperature: float = 0.2
    metadata: Dict[str, Any] = field(default_factory=dict)
    model_task_config: ModelTaskConfig = field(default_factory=ModelTaskConfig)
    api_providers: list[APIProvider] = field(default_factory=lambda: [APIProvider()])
    models: list[Any] = field(default_factory=list)


@dataclass
class TaskConfig:
    task_name: str = ""
    model: str = "default"
    provider: str = "default"
    model_list: list[str] = field(default_factory=lambda: ["astrbot_default"])
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.2
    selection_strategy: Optional[str] = None
    hard_timeout: Optional[float] = None
    options: Dict[str, Any] = field(default_factory=dict)


class ConfigProxy:
    """动态安全配置代理，支持链式属性访问、字典读取与协程上下文隔离。"""

    def __init__(self, data: Optional[Any] = None, *, _is_root: bool = False) -> None:
        self._data: Dict[str, Any] = data if isinstance(data, dict) else {}
        self._is_root = _is_root

    def _current_data(self) -> Dict[str, Any]:
        if self._is_root:
            scoped = _scoped_config.get()
            if isinstance(scoped, dict):
                return scoped
        return self._data

    def __getattr__(self, name: str) -> Any:
        data = self._current_data()
        if name in data:
            val = data[name]
            return ConfigProxy(val) if isinstance(val, dict) else val
        # 兼容 upstream A_memorix 通过 global_config.a_memorix.* 读取插件配置树
        if name == "a_memorix":
            return ConfigProxy(data)
        # 默认友好对象
        if name == "nickname":
            return "AstrBot"
        if name == "bot":
            return ConfigProxy({"nickname": "AstrBot"})
        return ConfigProxy({})

    def __getitem__(self, item: str) -> Any:
        data = self._current_data()
        if item in data:
            val = data[item]
            return ConfigProxy(val) if isinstance(val, dict) else val
        if item == "a_memorix":
            return ConfigProxy(data)
        return ConfigProxy({})

    def __contains__(self, item: str) -> bool:
        return item in self._current_data()

    def get(self, key: str, default: Any = None) -> Any:
        return self._current_data().get(key, default)

    def __bool__(self) -> bool:
        return bool(self._current_data())

    def __str__(self) -> str:
        return str(self._current_data())

    def __repr__(self) -> str:
        return f"ConfigProxy({self._current_data()})"


class HostConfigManager:
    """提供统一模型配置探测、获取与热重载回调注册。"""

    def __init__(self, plugin_config: Optional[Dict[str, Any]] = None) -> None:
        self.plugin_config = plugin_config or {}
        self._reload_callbacks: list[Callable[[], Any]] = []

    def _active_config(self) -> Dict[str, Any]:
        scoped = _scoped_config.get()
        if isinstance(scoped, dict):
            return scoped
        return self.plugin_config

    def get_model_config(self, task_name: str = "") -> ModelInfo:
        cfg = self._active_config()
        provider_cfg = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        chat_provider = str(
            provider_cfg.get("chat_provider_id") or cfg.get("chat_provider_id") or "default"
        ).strip() or "default"
        fast_provider = str(
            provider_cfg.get("fast_chat_provider_id") or cfg.get("fast_chat_provider_id") or ""
        ).strip()

        emb_cfg = cfg.get("embedding") if isinstance(cfg.get("embedding"), dict) else {}
        emb_openapi = emb_cfg.get("openapi") if isinstance(emb_cfg.get("openapi"), dict) else {}
        emb_model = str(emb_openapi.get("model") or "").strip()
        emb_list = [emb_model] if bool(emb_cfg.get("enabled", False)) and emb_model else []

        img_cfg = cfg.get("image_memory") if isinstance(cfg.get("image_memory"), dict) else {}
        img_openapi = img_cfg.get("openapi") if isinstance(img_cfg.get("openapi"), dict) else {}
        img_url = str(img_openapi.get("base_url") or "").strip()
        img_model = str(img_openapi.get("model") or "").strip()
        img_enabled = bool(img_openapi["enabled"]) if "enabled" in img_openapi else bool(img_url and img_model)
        img_list = [img_model] if img_enabled and bool(img_url and img_model) else []

        clean_task = str(task_name or "").strip()
        selected_provider = fast_provider if (clean_task == "fast_model" and fast_provider) else chat_provider
        return ModelInfo(
            model_name=str(cfg.get("chat_model") or selected_provider or "default"),
            provider=selected_provider,
            model_task_config=ModelTaskConfig(
                embedding=TaskEmbeddingConfig(model_list=emb_list),
                image_embedding=TaskEmbeddingConfig(model_list=img_list),
                fast_model=TaskEmbeddingConfig(model_list=[fast_provider] if fast_provider else []),
                utils=TaskEmbeddingConfig(model_list=[chat_provider] if chat_provider else []),
            ),
        )

    def get_default_model(self) -> ModelInfo:
        return self.get_model_config()

    def register_reload_callback(self, callback: Callable[[], Any]) -> None:
        if callable(callback) and callback not in self._reload_callbacks:
            self._reload_callbacks.append(callback)

    def unregister_reload_callback(self, callback: Callable[[], Any]) -> None:
        try:
            self._reload_callbacks.remove(callback)
        except ValueError:
            pass

    def notify_reload(self) -> None:
        for callback in list(self._reload_callbacks):
            try:
                callback()
            except Exception:
                pass


global_config = ConfigProxy(_is_root=True)
config_manager = HostConfigManager()


def get_effective_config_dict() -> Dict[str, Any]:
    """返回当前上下文生效的配置字典（优先协程级作用域配置，回退全局配置）。"""
    scoped = _scoped_config.get()
    if isinstance(scoped, dict):
        return scoped
    if isinstance(global_config._data, dict) and global_config._data:
        return global_config._data
    if isinstance(config_manager.plugin_config, dict):
        return config_manager.plugin_config
    return {}


def update_global_config(
    config_dict: Dict[str, Any],
    *,
    preserve_storage_data_dir: bool = False,
) -> None:
    """动态原地更新全局配置，确保已导入引用立即感知最新配置且不被单作用域路径污染。"""
    normalized = copy.deepcopy(config_dict) if isinstance(config_dict, dict) else {}
    if preserve_storage_data_dir:
        prev_storage = global_config._data.get("storage")
        if isinstance(prev_storage, dict) and "data_dir" in prev_storage:
            next_storage = dict(normalized.get("storage") or {}) if isinstance(normalized.get("storage"), dict) else {}
            next_storage["data_dir"] = prev_storage["data_dir"]
            normalized["storage"] = next_storage
        elif isinstance(normalized.get("storage"), dict):
            next_storage = dict(normalized["storage"])
            next_storage.pop("data_dir", None)
            if next_storage:
                normalized["storage"] = next_storage
            else:
                normalized.pop("storage", None)
    global_config._data.clear()
    global_config._data.update(normalized)
    config_manager.plugin_config = normalized
    config_manager.notify_reload()


