"""
Prompt Loader Host Shim.
Bridges src.common.prompt_i18n to standalone bundled prompt templates with
recursive locale/category discovery, .meta.toml/.meta.json metadata, and
scoped custom_prompts overrides.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any

import tomllib

from . import config_shim

logger = logging.getLogger("memorix.prompt_shim")

PROMPTS_DIR = (Path(__file__).resolve().parents[3] / "prompts").resolve()
PROMPTS_ROOT = PROMPTS_DIR
LEGACY_PROMPTS_DIR = (Path(__file__).parent / "prompts").resolve()
PROMPT_EXTENSIONS = (".prompt",)
SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
DEFAULT_LOCALE = "zh-CN"
STRICT_ENV_KEYS = ("MAIBOT_PROMPT_I18N_STRICT", "MAIBOT_I18N_STRICT")
STRICT_ENV_VALUES = {"1", "true", "yes", "on"}
_CUSTOM_PROMPT_STORAGE_NAMES = {
    "reply_group": "retro_replyer",
    "reply_private": "retro_private_replyer",
    "reply_private_followup": "retro_private_replyer_self",
}
_RENAMED_PROMPTS = {old_name: name for name, old_name in _CUSTOM_PROMPT_STORAGE_NAMES.items()}
_FORMATTER = Formatter()
_PROMPT_CACHE_REVISION = 0


@dataclass(frozen=True)
class PromptMetadata:
    display_name: str = ""
    advanced: bool = False
    description: str = ""


@dataclass(frozen=True)
class PromptTemplateInfo:
    path: Path
    metadata: PromptMetadata


@dataclass(frozen=True)
class ResolvedPromptTemplate:
    path: Path
    template: str
    customized: bool = False
    locale: str | None = DEFAULT_LOCALE


def get_prompts_root(prompts_root: Path | None = None) -> Path:
    return (Path(prompts_root).expanduser() if prompts_root is not None else PROMPTS_DIR).resolve()


def get_custom_prompts_root(
    custom_prompts_root: Path | None = None,
    prompts_root: Path | None = None,
) -> Path:
    if custom_prompts_root is not None:
        return Path(custom_prompts_root).expanduser().resolve()
    if prompts_root is not None:
        return (Path(prompts_root).expanduser().resolve().parent / "data" / "custom_prompts").resolve()
    cfg = config_shim.get_effective_config_dict()
    prompts_cfg = cfg.get("prompts") if isinstance(cfg.get("prompts"), dict) else {}
    raw_custom = str(prompts_cfg.get("custom_prompts_root") or cfg.get("custom_prompts_root") or "").strip()
    if raw_custom:
        return Path(raw_custom).expanduser().resolve()
    storage_cfg = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
    raw_data_dir = str(storage_cfg.get("data_dir") or "").strip()
    if raw_data_dir:
        return (Path(raw_data_dir).expanduser().resolve() / "custom_prompts").resolve()
    return (PROMPTS_DIR.parent / "data" / "custom_prompts").resolve()


def get_builtin_prompt_name(name: str) -> str:
    return _RENAMED_PROMPTS.get(name, name)


def get_custom_prompt_storage_name(name: str) -> str:
    return _CUSTOM_PROMPT_STORAGE_NAMES.get(name, name)


def normalize_prompt_name(name: str) -> str:
    candidate_name = str(name or "").strip()
    for suffix in PROMPT_EXTENSIONS:
        if candidate_name.endswith(suffix):
            candidate_name = candidate_name[: -len(suffix)]
            break
    if candidate_name in {".", ".."} or not candidate_name or not SAFE_SEGMENT_PATTERN.fullmatch(candidate_name):
        raise ValueError(f"非法 Prompt 模板名称: {name!r}")
    return get_builtin_prompt_name(candidate_name)


def normalize_prompt_category(category: str | None) -> str | None:
    if category is None:
        return None
    parts = [part for part in str(category).strip().split("/") if part]
    if not parts:
        raise ValueError(f"非法 Prompt 分类: {category!r}")
    for part in parts:
        if part in {".", ".."} or not SAFE_SEGMENT_PATTERN.fullmatch(part):
            raise ValueError(f"非法 Prompt 分类: {category!r}")
    return "/".join(parts)


def normalize_locale(locale: str | None) -> str:
    cleaned = str(locale or "").strip().replace("_", "-")
    if not cleaned:
        return DEFAULT_LOCALE
    parts = [part for part in cleaned.split("-") if part]
    if not parts:
        return DEFAULT_LOCALE
    normalized_parts: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            normalized_parts.append(part.lower())
        elif len(part) == 2:
            normalized_parts.append(part.upper())
        elif len(part) == 4:
            normalized_parts.append(part.title())
        else:
            normalized_parts.append(part)
    return "-".join(normalized_parts)


_normalize_locale = normalize_locale


def _effective_locale(locale: str | None) -> str:
    if locale is not None and str(locale).strip():
        return normalize_locale(locale)
    cfg = config_shim.get_effective_config_dict()
    prompts_cfg = cfg.get("prompts") if isinstance(cfg.get("prompts"), dict) else {}
    configured_locale = str(prompts_cfg.get("locale") or cfg.get("locale") or "").strip()
    return normalize_locale(configured_locale or DEFAULT_LOCALE)


def is_strict_prompt_i18n_mode() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return any(os.getenv(env_key, "").strip().lower() in STRICT_ENV_VALUES for env_key in STRICT_ENV_KEYS)


def extract_prompt_placeholders(prompt_text: str) -> set[str]:
    """从 Prompt 模板文本中提取全部 str.format 占位符标识（忽略 {{ / }} 转义花括号）。"""
    placeholders: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(str(prompt_text or "")):
        if not field_name:
            continue
        placeholders.add(field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0])
    return placeholders


def discover_prompt_locales(prompts_root: Path | None = None) -> list[str]:
    resolved_prompts_root = get_prompts_root(prompts_root)
    if not resolved_prompts_root.exists():
        return []
    return sorted(path.name for path in resolved_prompts_root.iterdir() if path.is_dir())


def iter_prompt_files(directory: Path, recursive: bool = True) -> list[Path]:
    if not directory.exists():
        return []
    search = directory.rglob if recursive else directory.glob
    prompt_files: list[Path] = []
    for suffix in PROMPT_EXTENSIONS:
        prompt_files.extend(path.resolve() for path in search(f"*{suffix}") if path.is_file())
    return sorted(set(prompt_files))


def _raise_duplicate_prompt_name(name: str, first_path: Path, second_path: Path, prompts_root: Path) -> None:
    try:
        path_a = first_path.relative_to(prompts_root).as_posix()
    except ValueError:
        path_a = first_path.as_posix()
    try:
        path_b = second_path.relative_to(prompts_root).as_posix()
    except ValueError:
        path_b = second_path.as_posix()
    raise ValueError(f"检测到重复的 Prompt 模板名称 '{name}': {path_a} 与 {path_b}")


def _coerce_metadata(raw_metadata: Any) -> PromptMetadata:
    if not isinstance(raw_metadata, dict):
        return PromptMetadata()
    display_name = raw_metadata.get("display_name", "")
    advanced = raw_metadata.get("advanced", False)
    description = raw_metadata.get("description", "")
    return PromptMetadata(
        display_name=display_name if isinstance(display_name, str) else "",
        advanced=advanced if isinstance(advanced, bool) else False,
        description=description if isinstance(description, str) else "",
    )


def _read_metadata_file(metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.is_file():
        return {}
    try:
        raw_text = metadata_path.read_text(encoding="utf-8")
        if metadata_path.suffix == ".json":
            metadata = json.loads(raw_text)
        else:
            metadata = tomllib.loads(raw_text)
    except Exception as exc:
        logger.warning("读取 Prompt 元信息文件 %s 失败: %s", metadata_path, exc)
        return {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _extract_template_metadata(metadata: dict[str, Any], prompt_name: str) -> dict[str, Any]:
    templates = metadata.get("templates")
    if isinstance(templates, dict) and isinstance(templates.get(prompt_name), dict):
        return dict(templates[prompt_name])
    prompt_metadata = metadata.get(prompt_name)
    if isinstance(prompt_metadata, dict):
        return dict(prompt_metadata)
    return metadata if any(key in metadata for key in ("display_name", "advanced", "description")) else {}


def _load_prompt_metadata(prompt_path: Path, metadata_cache: dict[Path, dict[str, Any]]) -> PromptMetadata:
    prompt_name = prompt_path.stem
    metadata_sources = (
        prompt_path.with_name(f"{prompt_name}.meta.toml"),
        prompt_path.with_name(f"{prompt_name}.meta.json"),
        prompt_path.parent / ".meta.toml",
        prompt_path.parent / ".meta.json",
    )
    merged_metadata: dict[str, Any] = {}
    for metadata_path in reversed(metadata_sources):
        if metadata_path not in metadata_cache:
            metadata_cache[metadata_path] = _read_metadata_file(metadata_path)
        raw_metadata = metadata_cache[metadata_path]
        merged_metadata.update(_extract_template_metadata(raw_metadata, prompt_name))
    return _coerce_metadata(merged_metadata)


def _scan_prompt_directory(
    directory: Path,
    prompts_root: Path,
    *,
    recursive: bool = True,
) -> dict[str, PromptTemplateInfo]:
    prompt_paths: dict[str, PromptTemplateInfo] = {}
    metadata_cache: dict[Path, dict[str, Any]] = {}
    for prompt_path in iter_prompt_files(directory, recursive=recursive):
        prompt_name = prompt_path.stem
        existing_info = prompt_paths.get(prompt_name)
        if existing_info is not None:
            _raise_duplicate_prompt_name(prompt_name, existing_info.path, prompt_path, prompts_root)
        prompt_paths[prompt_name] = PromptTemplateInfo(
            path=prompt_path,
            metadata=_load_prompt_metadata(prompt_path, metadata_cache),
        )
    return prompt_paths


def _iter_prompt_template_layers(prompts_root: Path, requested_locale: str) -> list[Path]:
    prompt_layers: list[Path] = [prompts_root / DEFAULT_LOCALE]
    if requested_locale != DEFAULT_LOCALE:
        prompt_layers.append(prompts_root / requested_locale)
    return prompt_layers


def _iter_locale_candidates(requested_locale: str) -> list[str]:
    locale_candidates: list[str] = [requested_locale]
    if requested_locale != DEFAULT_LOCALE:
        locale_candidates.append(DEFAULT_LOCALE)
    return locale_candidates


def _resolve_legacy_custom_prompt_path(name: str, custom_prompts_root: Path) -> Path | None:
    for suffix in PROMPT_EXTENSIONS:
        candidate_path = (custom_prompts_root / f"{name}{suffix}").resolve()
        if candidate_path.is_file():
            return candidate_path
    return None


def list_prompt_templates(locale: str | None = None, prompts_root: Path | None = None) -> dict[str, PromptTemplateInfo]:
    resolved_prompts_root = get_prompts_root(prompts_root)
    requested_locale = _effective_locale(locale)

    prompt_paths: dict[str, PromptTemplateInfo] = {}
    for directory in _iter_prompt_template_layers(resolved_prompts_root, requested_locale):
        prompt_paths.update(_scan_prompt_directory(directory, resolved_prompts_root))

    if not prompt_paths and resolved_prompts_root.exists():
        prompt_paths.update(_scan_prompt_directory(resolved_prompts_root, resolved_prompts_root, recursive=False))
    return prompt_paths


@lru_cache(maxsize=None)
def _prompt_paths_for_locale(root: Path, locale: str, revision: int) -> dict[str, PromptTemplateInfo]:
    del revision
    return _scan_prompt_directory(root / locale, root)


def _lookup_builtin_template_info(
    normalized_name: str,
    locale_candidate: str,
    resolved_prompts_root: Path,
) -> PromptTemplateInfo | None:
    cached_map = _prompt_paths_for_locale(resolved_prompts_root, locale_candidate, _PROMPT_CACHE_REVISION)
    info = cached_map.get(normalized_name)
    if info is not None and info.path.is_file():
        return info
    if resolved_prompts_root != PROMPTS_DIR:
        fresh_map = _scan_prompt_directory(resolved_prompts_root / locale_candidate, resolved_prompts_root)
        return fresh_map.get(normalized_name)
    return None


def get_prompt_template_info(
    name: str,
    locale: str | None = None,
    prompts_root: Path | None = None,
) -> PromptTemplateInfo | None:
    root = get_prompts_root(prompts_root)
    normalized_name = normalize_prompt_name(name)
    for candidate in _iter_locale_candidates(_effective_locale(locale)):
        info = _lookup_builtin_template_info(normalized_name, candidate, root)
        if info is not None:
            return info
    flat_map = _scan_prompt_directory(root, root, recursive=False)
    return flat_map.get(normalized_name)


def _iter_direct_candidates(base_dir: Path, name: str, category: str | None) -> list[Path]:
    candidates: list[Path] = []
    for suffix in PROMPT_EXTENSIONS:
        if category is not None:
            candidates.append((base_dir / category / f"{name}{suffix}").resolve())
        candidates.append((base_dir / f"{name}{suffix}").resolve())
    return candidates


def _resolve_prompt_entry(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    include_legacy_custom: bool = False,
    include_custom: bool = True,
) -> tuple[Path, bool, str | None]:
    resolved_prompts_root = get_prompts_root(prompts_root)
    resolved_custom_root = get_custom_prompts_root(custom_prompts_root, prompts_root)
    normalized_name = normalize_prompt_name(name)
    normalized_category = normalize_prompt_category(category)
    requested_locale = _effective_locale(locale)
    locale_candidates = _iter_locale_candidates(requested_locale)

    for loc in locale_candidates:
        if include_custom:
            storage_name = get_custom_prompt_storage_name(normalized_name)
            custom_dir = resolved_custom_root / loc
            for candidate in _iter_direct_candidates(custom_dir, storage_name, normalized_category):
                if candidate.is_file():
                    return candidate, True, loc
            if storage_name != normalized_name:
                for candidate in _iter_direct_candidates(custom_dir, normalized_name, normalized_category):
                    if candidate.is_file():
                        return candidate, True, loc
            if include_legacy_custom:
                legacy_custom_path = _resolve_legacy_custom_prompt_path(storage_name, resolved_custom_root)
                if legacy_custom_path is not None:
                    return legacy_custom_path, True, None

        locale_dir = resolved_prompts_root / loc
        if normalized_category is not None:
            for suffix in PROMPT_EXTENSIONS:
                cat_candidate = (locale_dir / normalized_category / f"{normalized_name}{suffix}").resolve()
                if cat_candidate.is_file():
                    return cat_candidate, False, loc

        info = _lookup_builtin_template_info(normalized_name, loc, resolved_prompts_root)
        if info is not None:
            return info.path, False, loc

    for candidate in _iter_direct_candidates(resolved_prompts_root, normalized_name, normalized_category):
        if candidate.is_file():
            return candidate, False, requested_locale

    if prompts_root is None and LEGACY_PROMPTS_DIR != resolved_prompts_root:
        for candidate in _iter_direct_candidates(LEGACY_PROMPTS_DIR, normalized_name, normalized_category):
            if candidate.is_file():
                return candidate, False, requested_locale

    raise FileNotFoundError(
        f"未找到 Memorix 核心 Prompt 模板: {normalized_name} (locale={requested_locale}, 搜索路径: {resolved_prompts_root})"
    )


def resolve_prompt_path(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    include_legacy_custom: bool = False,
    include_custom: bool = True,
) -> Path:
    prompt_path, _customized, _resolved_locale = _resolve_prompt_entry(
        name=name,
        locale=locale,
        category=category,
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
        include_legacy_custom=include_legacy_custom,
        include_custom=include_custom,
    )
    return prompt_path


@lru_cache(maxsize=None)
def _read_prompt_template_cached(prompt_path: Path, mtime_ns: int, file_size: int, revision: int) -> str:
    del mtime_ns, file_size, revision
    return prompt_path.read_text(encoding="utf-8")


def _read_prompt_template(prompt_path: Path) -> str:
    resolved = prompt_path.resolve()
    stat = resolved.stat()
    return _read_prompt_template_cached(resolved, stat.st_mtime_ns, stat.st_size, _PROMPT_CACHE_REVISION)


def load_prompt_template(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    include_legacy_custom: bool = False,
    include_custom: bool = True,
) -> ResolvedPromptTemplate:
    """加载原始 Prompt 模板对象（兼容 upstream src.common.prompt_i18n.load_prompt_template）。"""
    prompt_path, customized, resolved_locale = _resolve_prompt_entry(
        name=name,
        locale=locale,
        category=category,
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
        include_legacy_custom=include_legacy_custom,
        include_custom=include_custom,
    )
    return ResolvedPromptTemplate(
        path=prompt_path,
        template=_read_prompt_template(prompt_path),
        customized=customized,
        locale=resolved_locale,
    )


def _format_prompt_template(name: str, template: str, **kwargs: object) -> str:
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        missing = exc.args[0]
        error = KeyError(f"Prompt 模板 '{name}' 缺少占位符参数: {missing}")
        if is_strict_prompt_i18n_mode():
            raise error from exc
        logger.error("%s", error)
        return template
    except Exception as exc:
        logger.error("格式化 Prompt 模板 '%s' 失败: %s", name, exc)
        if is_strict_prompt_i18n_mode():
            raise
        return template


def load_prompt(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    transform_template: Callable[[str], str] | None = None,
    **kwargs: Any,
) -> str:
    """加载 Prompt 模板并执行可选转换与 str.format 参数替换插值。"""
    normalized_name = normalize_prompt_name(name)
    resolved_template = load_prompt_template(
        name=normalized_name,
        locale=locale,
        category=category,
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
    )
    template = resolved_template.template
    if transform_template is not None:
        template = transform_template(template)
    if resolved_template.customized and kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError, AttributeError):
            default_template = load_prompt_template(
                name=normalized_name,
                locale=locale,
                category=category,
                prompts_root=prompts_root,
                custom_prompts_root=custom_prompts_root,
                include_custom=False,
            )
            fallback_template = default_template.template
            if transform_template is not None:
                fallback_template = transform_template(fallback_template)
            logger.warning("自定义 Prompt 模板 '%s' 占位符已过期，已回退内置模板", normalized_name)
            return _format_prompt_template(normalized_name, fallback_template, **kwargs)
    return _format_prompt_template(normalized_name, template, **kwargs)


def clear_prompt_cache() -> None:
    global _PROMPT_CACHE_REVISION
    _PROMPT_CACHE_REVISION += 1
    _read_prompt_template_cached.cache_clear()
    _prompt_paths_for_locale.cache_clear()


def get_prompt_cache_revision() -> int:
    return _PROMPT_CACHE_REVISION
