"""插件本地消息 API 垫片。

替代上游 A_memorix 反馈纠错链路对宿主 ``src.services.message_service`` 与
``src.chat.message_receive.chat_manager`` 的依赖。两条调用点：

* ``message_api.get_messages_by_time_in_chat(chat_id, start_time, end_time, limit, ...)``
  —— 反馈窗口内取用户消息，对应上游 ``_extract_feedback_messages``。
* ``chat_manager.get_existing_session_by_session_id(stream_id)`` —— 由 stream_id
  反查 group_id / user_id，对应上游 ``_retrieval_filter_context``。

本插件不引入任何宿主消息概念，统一改读插件自有的 ``transcript_sessions`` /
``transcript_messages`` 表（由 ``ingest_service`` 在每条消息摄入时维护）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from .logging import get_logger

logger = get_logger("A_Memorix.MessageAPI")


@dataclass
class FeedbackMessage:
    """反馈窗口内的一条消息快照。

    ``processed_plain_text`` 与上游 ``message_service`` 返回结构对齐，便于
    反馈分类逻辑直接复用上游 ``_extract_feedback_messages`` 的取值方式。
    """

    processed_plain_text: str = ""
    role: str = "user"
    created_at: float = 0.0
    sender_id: str = ""
    sender_name: str = ""
    session_id: str = ""


@dataclass
class SessionIdentity:
    """stream_id -> 群/用户 身份映射，对齐上游 chat_manager session 对象。"""

    group_id: str = ""
    user_id: str = ""
    stream_id: str = ""


class MessageAPI:
    """绑定一个 ``MetadataStore`` 的本地消息查询 API。

    上游 ``message_service`` / ``chat_manager`` 在宿主侧是全局单例；
    本插件为多作用域隔离，故按作用域的 metadata_store 实例化，避免全局状态。
    """

    def __init__(self, metadata_store: Any) -> None:
        self._store = metadata_store

    @staticmethod
    def _is_command_text(text: str) -> bool:
        """识别指令消息，与上游 ``filter_command`` 语义一致。"""
        stripped = text.lstrip()
        if not stripped:
            return False
        head = stripped[0]
        return head in {"/", "!", ".", "#", "\\"}

    def get_messages_by_time_in_chat(
        self,
        *,
        chat_id: str,
        start_time: float,
        end_time: float,
        limit: int = 50,
        limit_mode: str = "latest",
        filter_mai: bool = True,
        filter_command: bool = True,
    ) -> List[FeedbackMessage]:
        """按时间窗口取一个聊天流的近期消息，按时间升序返回。

        对齐上游签名（``chat_id`` / ``start_time`` / ``end_time`` / ``limit`` /
        ``limit_mode`` / ``filter_mai`` / ``filter_command``），其中
        ``filter_mai`` 在本插件无宿主消息概念，仅作参数占位。
        """

        token = str(chat_id or "").strip()
        if not token or self._store is None:
            return []

        try:
            start_ts = float(start_time)
            end_ts = float(end_time)
        except (TypeError, ValueError):
            return []

        safe_limit = max(1, int(limit or 50))
        # 上游 limit_mode 目前仅用 "latest"；本实现始终取窗口内最新 N 条。
        # filter_mai 在本插件无宿主消息概念，仅占位以对齐上游签名。
        _ = limit_mode, filter_mai

        cursor = self._store._conn.cursor()
        cursor.execute(
            """
            SELECT session_id, role, content, created_at, metadata_json
            FROM transcript_messages
            WHERE session_id = ?
              AND created_at >= ?
              AND created_at <= ?
            ORDER BY created_at DESC, position DESC, message_id DESC
            LIMIT ?
            """,
            (token, start_ts, end_ts, safe_limit),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        messages: List[FeedbackMessage] = []
        for row in rows:
            content = str(row["content"] or "").strip()
            if not content:
                continue
            if filter_command and self._is_command_text(content):
                continue
            metadata = self._store._json_loads(row["metadata_json"], {}) if hasattr(self._store, "_json_loads") else {}
            messages.append(
                FeedbackMessage(
                    processed_plain_text=content,
                    role=str(row["role"] or "user"),
                    created_at=float(row["created_at"] or 0.0),
                    sender_id=str(metadata.get("sender_id", "") or ""),
                    sender_name=str(metadata.get("sender_name", "") or ""),
                    session_id=str(row["session_id"] or ""),
                )
            )
        # 窗口内最新 N 条按时间升序返回，便于反馈分类按时间线读取。
        messages.reverse()
        return messages

    def get_existing_session_by_session_id(self, stream_id: str) -> Optional[SessionIdentity]:
        """对齐上游 ``chat_manager.get_existing_session_by_session_id``。

        从 ``transcript_sessions.metadata_json`` 中读取 group_id / user_id。
        """

        token = str(stream_id or "").strip()
        if not token or self._store is None:
            return None

        session = self._store.get_transcript_session(token) if hasattr(self._store, "get_transcript_session") else None
        if not isinstance(session, dict):
            return None

        raw_metadata = session.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        group_id = str(metadata.get("group_id", "") or "").strip()
        user_id = str(metadata.get("user_id", "") or "").strip()
        if not group_id and not user_id:
            return None
        return SessionIdentity(group_id=group_id, user_id=user_id, stream_id=token)


def get_session_group_user(message_api: MessageAPI, stream_id: str) -> SessionIdentity:
    """便捷函数：返回 stream_id 对应的群/用户身份，缺失时返回空身份。"""

    if message_api is None:
        return SessionIdentity()
    result = message_api.get_existing_session_by_session_id(stream_id)
    return result if result is not None else SessionIdentity(stream_id=str(stream_id or "").strip())
