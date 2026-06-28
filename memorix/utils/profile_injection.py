"""Helpers for compact person profile injection text."""

from __future__ import annotations

from typing import Iterable

PROFILE_SECTION_TITLES = (
    "身份设定",
    "关系设定",
    "稳定了解",
    "相处偏好",
    "近期互动",
    "不确定信息",
    "维护备注",
)
PROFILE_INJECTION_SECTION_TITLES = (
    "身份设定",
    "关系设定",
    "稳定了解",
    "相处偏好",
    "近期互动",
)
PROFILE_REQUIRED_PREFIX = "# 人物画像"
PROFILE_EMPTY_ITEM = "- 暂无"


def _parse_profile_sections(profile_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_title = ""
    for raw_line in str(profile_text or "").splitlines():
        stripped = raw_line.rstrip().strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            current_title = title if title in PROFILE_SECTION_TITLES else ""
            if current_title:
                sections.setdefault(current_title, [])
            continue
        if current_title:
            sections.setdefault(current_title, []).append(stripped)
    return sections


def _section_is_empty(lines: Iterable[str]) -> bool:
    items = [str(line or "").strip() for line in lines]
    return not items or all(not item or item == PROFILE_EMPTY_ITEM for item in items)


def _is_structured_profile_text(profile_text: str) -> bool:
    text = str(profile_text or "").strip()
    if not text.startswith(PROFILE_REQUIRED_PREFIX):
        return False
    sections = _parse_profile_sections(text)
    return all(title in sections for title in PROFILE_SECTION_TITLES)


def build_profile_injection_text(
    profile_text: str,
    *,
    recent_limit: int = 2,
    uncertain_fallback_limit: int = 1,
) -> str:
    """Build a compact injection view from structured profile text.

    Unstructured profile text is returned unchanged for compatibility with the
    current AstrBot Memorix profile generator.
    """

    text = str(profile_text or "").strip()
    if not _is_structured_profile_text(text):
        return text

    sections = _parse_profile_sections(text)
    selected: list[str] = []
    meaningful_found = False
    for title in PROFILE_INJECTION_SECTION_TITLES:
        lines = sections.get(title, [])
        if title == "近期互动":
            lines = lines[: max(0, int(recent_limit))]
        if _section_is_empty(lines):
            continue
        meaningful_found = True
        selected.extend([f"## {title}", *lines, ""])

    if not meaningful_found:
        uncertain = sections.get("不确定信息", [])[: max(0, int(uncertain_fallback_limit))]
        if not _section_is_empty(uncertain):
            selected.extend(["## 不确定信息（未确认）", *uncertain, ""])

    return "\n".join(selected).strip()
