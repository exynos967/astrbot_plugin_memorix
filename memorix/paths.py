"""插件本地路径解析 shim。

替代上游 ``A_memorix/paths.py`` 的 repo-relative 路径模型——插件没有宿主仓库根，
所有持久化路径以插件数据目录（``AMEMORIX_DATA_DIR`` 环境变量，默认 ``./data``）为根，
与 ``amemorix.settings.AppSettings.data_dir`` 保持一致。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

A_MEMORIX_SYSTEM_ID = "a_memorix"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def src_root() -> Path:
    # 插件无独立 src 目录，等价于包根。
    return package_root()


def repo_root() -> Path:
    # 插件无仓库根概念，回落到数据目录的父级，仅用于相对路径解析锚点。
    return default_data_dir().parent


def config_path() -> Path:
    return default_data_dir() / "config" / f"{A_MEMORIX_SYSTEM_ID}.toml"


def default_data_dir() -> Path:
    return Path(os.getenv("AMEMORIX_DATA_DIR", "./data")).resolve()


def artifacts_root() -> Path:
    return default_data_dir() / "artifacts"


def schema_path() -> Path:
    return package_root() / "config_schema.json"


def web_root() -> Path:
    return package_root() / "web"


def scripts_root() -> Path:
    return package_root() / "scripts"


def resolve_repo_path(
    raw_path: Union[str, Path, None],
    *,
    fallback: Optional[Path] = None,
) -> Path:
    if raw_path is None:
        return (fallback or default_data_dir()).resolve()

    raw_value = str(raw_path).strip()
    if not raw_value:
        return (fallback or default_data_dir()).resolve()

    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    # 相对路径锚定到插件数据目录（上游锚定到仓库根）。
    return (default_data_dir() / candidate).resolve()
