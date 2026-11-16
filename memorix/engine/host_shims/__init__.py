"""
Host Shims Registration & Dynamic sys.modules Injection.

零侵入接管 upstream A_memorix 核心代码对宿主 src.* 的全部调用，彻底消除 maibot_stubs。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional

from . import async_shim, config_shim, embedding_shim, llm_shim, logger_shim, message_shim, prompt_shim

_installed = False
_installed_modules: list[str] = []


class _RecursiveStub(types.ModuleType):
    """递归通用桩，用于未显式覆盖的宿主内部边角符号。"""

    def __init__(self, name: str = "stub") -> None:
        super().__init__(name)
        self.__file__ = f"<host_shim:{name}>"
        self.__path__ = []

    def __getattr__(self, name: str) -> Any:
        child_name = f"{self.__name__}.{name}"
        if child_name in sys.modules:
            return sys.modules[child_name]
        sub = _RecursiveStub(child_name)
        setattr(self, name, sub)
        sys.modules[child_name] = sub
        return sub

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _RecursiveStub(f"{self.__name__}()")

    def __iter__(self) -> Any:
        return iter([])


_ALLOWED_SRC_PREFIXES = (
    "src.common",
    "src.services",
    "src.config",
    "src.chat",
    "src.person_info",
    "src.llm_models",
)


def _candidate_memorix_prefixes() -> list[str]:
    prefixes: list[str] = []
    pkg = str(__package__ or "").strip()
    if pkg:
        parts = pkg.split(".")
        if len(parts) >= 2 and parts[-2:] == ["engine", "host_shims"]:
            current_memorix_pkg = ".".join(parts[:-2])
            if current_memorix_pkg:
                prefixes.append(current_memorix_pkg)
    for fallback in ("data.plugins.astrbot_plugin_memorix.memorix", "astrbot_plugin_memorix.memorix", "memorix", "amemorix"):
        if fallback not in prefixes:
            prefixes.append(fallback)
    return prefixes


class _SrcMetaPathFinder:
    """元路径查找器：动态转发 src.A_memorix.* 到真实模块，收敛拦截已知白名单 src.*。"""

    _memorix_host_shim_finder = True

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname == "src.A_memorix" or fullname.startswith("src.A_memorix."):
            for prefix in _candidate_memorix_prefixes():
                target_name = fullname.replace("src.A_memorix", prefix, 1)
                try:
                    mod = importlib.import_module(target_name)
                    sys.modules[fullname] = mod
                    if fullname not in _installed_modules:
                        _installed_modules.append(fullname)
                    _bind_parent(fullname, mod)
                    spec = getattr(mod, "__spec__", None)
                    if spec is not None:
                        return spec
                except (ImportError, AttributeError):
                    continue
            return None

        # 仅针对明确定义的白名单子树返回桩，禁止贪婪拦截任意第三方或不存在的 src.* 模块
        if any(fullname == p or fullname.startswith(f"{p}.") for p in _ALLOWED_SRC_PREFIXES):
            from importlib.machinery import ModuleSpec

            return ModuleSpec(fullname, _SrcMetaPathLoader(fullname), is_package=True)

        return None


class _SrcMetaPathLoader:

    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec: Any) -> types.ModuleType:
        if self.name in sys.modules:
            return sys.modules[self.name]
        mod = _RecursiveStub(self.name)
        if self.name not in _installed_modules:
            _installed_modules.append(self.name)
        return mod

    def exec_module(self, module: types.ModuleType) -> None:
        pass


_meta_finder = _SrcMetaPathFinder()


def _ensure_pkg(fullname: str) -> types.ModuleType:
    if fullname in sys.modules:
        return sys.modules[fullname]
    mod = types.ModuleType(fullname)
    mod.__path__ = []
    mod.__file__ = f"<host_shim:{fullname}>"
    sys.modules[fullname] = mod
    _bind_parent(fullname, mod)
    _installed_modules.append(fullname)
    return mod


def _ensure_mod(fullname: str) -> types.ModuleType:
    if fullname in sys.modules:
        return sys.modules[fullname]
    mod = types.ModuleType(fullname)
    mod.__file__ = f"<host_shim:{fullname}>"
    sys.modules[fullname] = mod
    _bind_parent(fullname, mod)
    _installed_modules.append(fullname)
    return mod


def _bind_parent(fullname: str, mod: types.ModuleType) -> None:
    if "." in fullname:
        parent_name, child_name = fullname.rsplit(".", 1)
        if parent_name in sys.modules:
            setattr(sys.modules[parent_name], child_name, mod)


def _refresh_memorix_aliases() -> None:
    _ensure_pkg("src.A_memorix")
    try:
        from ... import core as memorix_core
    except Exception:
        return

    existing_core = sys.modules.get("src.A_memorix.core")
    if existing_core is not memorix_core:
        for mod_key in [k for k in list(sys.modules) if k.startswith("src.A_memorix.")]:
            sys.modules.pop(mod_key, None)

    sys.modules["src.A_memorix"] = sys.modules.get("amemorix", memorix_core)
    sys.modules["src.A_memorix.core"] = memorix_core
    _bind_parent("src.A_memorix", memorix_core)


def _sync_meta_finder() -> None:
    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if finder is _meta_finder or not getattr(finder, "_memorix_host_shim_finder", False)
    ]
    if _meta_finder not in sys.meta_path:
        sys.meta_path.insert(0, _meta_finder)


def _bind_shim_exports() -> None:
    """将当前命名空间下的 shim 实现绑定到进程级 src.* 桥接模块，确保热重载或多命名空间切换后无陈旧引用。"""
    log_mod = _ensure_mod("src.common.logger")
    log_mod.get_logger = logger_shim.get_logger
    log_mod.AstrBotLoggerAdapter = logger_shim.AstrBotLoggerAdapter

    async_mod = _ensure_mod("src.common.async_work")
    async_mod.run_sync_work = async_shim.run_sync_work
    async_mod.cancellation_safe_to_thread = async_shim.cancellation_safe_to_thread
    async_mod.cancel_and_wait_for_tasks = async_shim.cancel_and_wait_for_tasks
    async_mod.TASK_STOP_TIMEOUT_SECONDS = async_shim.TASK_STOP_TIMEOUT_SECONDS

    prompt_mod = _ensure_mod("src.common.prompt_i18n")
    prompt_mod.load_prompt = prompt_shim.load_prompt
    prompt_mod.extract_prompt_placeholders = prompt_shim.extract_prompt_placeholders
    prompt_mod.load_prompt_template = prompt_shim.load_prompt_template
    prompt_mod.resolve_prompt_path = prompt_shim.resolve_prompt_path
    prompt_mod.get_prompts_root = prompt_shim.get_prompts_root
    prompt_mod.get_custom_prompts_root = prompt_shim.get_custom_prompts_root
    prompt_mod.get_builtin_prompt_name = prompt_shim.get_builtin_prompt_name
    prompt_mod.get_custom_prompt_storage_name = prompt_shim.get_custom_prompt_storage_name
    prompt_mod.normalize_prompt_name = prompt_shim.normalize_prompt_name
    prompt_mod.normalize_prompt_category = prompt_shim.normalize_prompt_category
    prompt_mod.normalize_locale = prompt_shim.normalize_locale
    prompt_mod.discover_prompt_locales = prompt_shim.discover_prompt_locales
    prompt_mod.iter_prompt_files = prompt_shim.iter_prompt_files
    prompt_mod.list_prompt_templates = prompt_shim.list_prompt_templates
    prompt_mod.get_prompt_template_info = prompt_shim.get_prompt_template_info
    prompt_mod.is_strict_prompt_i18n_mode = prompt_shim.is_strict_prompt_i18n_mode
    prompt_mod.PromptMetadata = prompt_shim.PromptMetadata
    prompt_mod.PromptTemplateInfo = prompt_shim.PromptTemplateInfo
    prompt_mod.ResolvedPromptTemplate = prompt_shim.ResolvedPromptTemplate
    prompt_mod.PROMPTS_DIR = prompt_shim.PROMPTS_DIR
    prompt_mod.PROMPTS_ROOT = prompt_shim.PROMPTS_ROOT
    prompt_mod.clear_prompt_cache = prompt_shim.clear_prompt_cache
    prompt_mod.get_prompt_cache_revision = prompt_shim.get_prompt_cache_revision

    paths_mod = _ensure_mod("src.common.paths")
    paths_mod.get_instance_root = lambda: Path.cwd()

    cfg_mod = _ensure_mod("src.config.config")
    cfg_mod.global_config = config_shim.global_config
    cfg_mod.config_manager = config_shim.config_manager

    img_cfg_mod = _ensure_mod("src.config.image_memory")
    img_cfg_mod.is_image_embedding_configured = embedding_shim.is_image_embedding_configured
    img_cfg_mod.is_image_memory_enabled = embedding_shim.is_image_memory_enabled
    img_cfg_mod.resolve_image_memory_status = embedding_shim.resolve_image_memory_status

    mcfg_mod = _ensure_mod("src.config.model_configs")
    mcfg_mod.APIProvider = config_shim.APIProvider
    mcfg_mod.ModelInfo = config_shim.ModelInfo
    mcfg_mod.TaskConfig = config_shim.TaskConfig

    llm_mod = _ensure_mod("src.services.llm_service")
    llm_mod.LLMServiceClient = llm_shim.LLMServiceClient
    llm_mod.generate = llm_shim.generate
    llm_mod.get_available_models = llm_shim.get_available_models
    llm_mod.LLMServiceRequest = llm_shim.LLMServiceRequest
    llm_mod.LLMServiceResult = llm_shim.LLMServiceResult
    llm_mod.LLMGenerationOptions = llm_shim.LLMGenerationOptions

    llm_data_mod = _ensure_mod("src.common.data_models.llm_service_data_models")
    llm_data_mod.LLMServiceResult = llm_shim.LLMServiceResult
    llm_data_mod.LLMResponseResult = llm_shim.LLMResponseResult
    llm_data_mod.LLMServiceRequest = llm_shim.LLMServiceRequest
    llm_data_mod.LLMGenerationOptions = llm_shim.LLMGenerationOptions

    emb_mod = _ensure_mod("src.services.embedding_service")
    emb_mod.EmbeddingServiceClient = embedding_shim.EmbeddingServiceClient
    emb_mod.EmbeddingResult = embedding_shim.EmbeddingResult
    emb_mod.ImageEmbeddingResult = embedding_shim.ImageEmbeddingResult
    emb_mod.resolve_embedding_model_fingerprint = embedding_shim.resolve_embedding_model_fingerprint
    emb_mod.is_image_embedding_configured = embedding_shim.is_image_embedding_configured
    emb_mod.is_image_memory_enabled = embedding_shim.is_image_memory_enabled

    try:
        from ...core.utils import chat_migration_gate as _cmg

        gate_mod = _ensure_mod("src.services.chat_migration_gate")
        gate_mod.ChatMigrationGate = _cmg.ChatMigrationGate
        gate_mod.chat_migration_gate = _cmg.chat_migration_gate
        gate_mod.get_chat_migration_gate = _cmg.get_chat_migration_gate
        gate_mod.settled_thread = _cmg.settled_thread
    except Exception:
        pass

    emb_data_mod = _ensure_mod("src.common.data_models.embedding_service_data_models")
    emb_data_mod.EmbeddingResult = embedding_shim.EmbeddingResult
    emb_data_mod.ImageEmbeddingResult = embedding_shim.ImageEmbeddingResult

    msg_comp_mod = _ensure_mod("src.common.data_models.message_component_data_model")
    msg_comp_mod.BaseMessageComponentModel = message_shim.BaseMessageComponentModel
    msg_comp_mod.TextComponent = message_shim.TextComponent
    msg_comp_mod.ImageComponent = message_shim.ImageComponent
    msg_comp_mod.EmojiComponent = message_shim.EmojiComponent
    msg_comp_mod.VoiceComponent = message_shim.VoiceComponent
    msg_comp_mod.FileComponent = message_shim.FileComponent
    msg_comp_mod.AtComponent = message_shim.AtComponent
    msg_comp_mod.ReplyComponent = message_shim.ReplyComponent
    msg_comp_mod.ForwardNodeComponent = message_shim.ForwardNodeComponent
    msg_comp_mod.DictComponent = message_shim.DictComponent
    msg_comp_mod.StandardMessageComponents = message_shim.StandardMessageComponents

    msg_repo_mod = _ensure_mod("src.common.message_repository")
    msg_repo_mod.find_messages = message_shim.find_messages
    msg_repo_mod.count_messages = message_shim.count_messages

    msg_svc_mod = _ensure_mod("src.services.message_service")
    msg_svc_mod._render_without_reply_components = message_shim._render_without_reply_components
    msg_svc_mod.get_messages_by_time = message_shim.get_messages_by_time
    msg_svc_mod.get_messages_by_time_in_chat = message_shim.get_messages_by_time_in_chat

    chat_utils_mod = _ensure_mod("src.chat.utils.utils")
    chat_utils_mod.is_bot_self = message_shim.is_bot_self

    person_mod = _ensure_mod("src.person_info.person_info")
    person_mod.get_person_id = message_shim.get_person_id


def install_host_shims(
    context: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    provider_bridge: Optional[Any] = None,
    *,
    scope_local: bool = False,
) -> None:
    """在当前 Python 进程中安装非侵入式宿主 Shim（支持 AstrBot 热重载幂等重绑与多作用域隔离）。"""
    global _installed
    del context
    if provider_bridge is not None:
        if not scope_local or llm_shim.get_active_provider_bridge() is None:
            llm_shim.set_active_provider_bridge(provider_bridge)
    if config is not None:
        if scope_local:
            if not config_shim.global_config._data:
                config_shim.update_global_config(config, preserve_storage_data_dir=True)
        else:
            config_shim.update_global_config(config)

    if _installed:
        _bind_shim_exports()
        _refresh_memorix_aliases()
        _sync_meta_finder()
        return

    # 1. 注册核心包空间
    pkg_list = [
        "src",
        "src.common",
        "src.common.database",
        "src.common.data_models",
        "src.services",
        "src.config",
        "src.chat",
        "src.chat.message_receive",
        "src.chat.utils",
        "src.person_info",
        "src.llm_models",
        "src.llm_models.payload_content",
        "src.llm_models.model_client",
    ]
    for p in pkg_list:
        _ensure_pkg(p)

    # 2. 绑定核心 shim 导出符号
    _bind_shim_exports()

    # 3. 注入 chat & person_info 兜底模块
    chat_mgr_mod = _ensure_mod("src.chat.message_receive.chat_manager")
    chat_mgr_mod.chat_manager = _RecursiveStub("chat_manager")

    chat_msg_mod = _ensure_mod("src.chat.message_receive.message")
    chat_msg_mod.SessionMessage = _RecursiveStub("SessionMessage")

    # 4. 注入 database stubs
    db_mod = _ensure_mod("src.common.database.database")
    db_mod.get_db_session = _RecursiveStub("get_db_session")

    db_model_mod = _ensure_mod("src.common.database.database_model")
    db_model_mod.PersonInfo = _RecursiveStub("PersonInfo")
    db_model_mod.Messages = _RecursiveStub("Messages")

    # 5. 映射 src.A_memorix 别名（热重载时自动解绑旧实例并指向当前插件 core）
    _refresh_memorix_aliases()

    # 11. 注入可选第三方库兜底（若当前环境未安装）
    if "aiohttp" not in sys.modules and importlib.util.find_spec("aiohttp") is None:
        aio_mod = types.ModuleType("aiohttp")
        aio_mod.ClientError = type("ClientError", (Exception,), {})
        aio_mod.ClientSession = _RecursiveStub("ClientSession")
        aio_mod.ClientTimeout = _RecursiveStub("ClientTimeout")
        sys.modules["aiohttp"] = aio_mod
        _installed_modules.append("aiohttp")

    if "json_repair" not in sys.modules and importlib.util.find_spec("json_repair") is None:
        jr_mod = types.ModuleType("json_repair")
        jr_mod.repair_json = lambda s, **kw: s
        sys.modules["json_repair"] = jr_mod
        _installed_modules.append("json_repair")

    if "sqlalchemy" not in sys.modules and importlib.util.find_spec("sqlalchemy") is None:
        sq_mod = types.ModuleType("sqlalchemy")
        sq_mod.or_ = _RecursiveStub("or_")
        sq_mod.select = _RecursiveStub("select")
        sys.modules["sqlalchemy"] = sq_mod
        _installed_modules.append("sqlalchemy")

    if "sqlmodel" not in sys.modules and importlib.util.find_spec("sqlmodel") is None:
        sm_mod = types.ModuleType("sqlmodel")
        sm_mod.select = _RecursiveStub("select")
        sys.modules["sqlmodel"] = sm_mod
        _installed_modules.append("sqlmodel")

    # 12. 插入并去重兜底 meta_path 拦截器
    _sync_meta_finder()

    _installed = True


def uninstall_host_shims() -> None:
    """清理注入的宿主 Shim 模块与钩子（主要用于测试复位）。"""
    global _installed
    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if finder is not _meta_finder and not getattr(finder, "_memorix_host_shim_finder", False)
    ]
    for mod_name in reversed(_installed_modules):
        sys.modules.pop(mod_name, None)
    _installed_modules.clear()
    _installed = False


# 在模块导入时默认就绪
install_host_shims()
