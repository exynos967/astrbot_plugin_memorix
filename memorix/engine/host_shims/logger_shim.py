"""
Logger Host Shim.
Bridges src.common.logger.get_logger to AstrBot Logger and Python standard logging.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from astrbot.api import logger as _astrbot_logger
except ImportError:
    _astrbot_logger = None


class AstrBotLoggerAdapter:
    """包装器：将日志透明重定向至 AstrBot logger，若未初始化则回退至标准 logging。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._prefix = f"[{name}] "
        self._std_logger = logging.getLogger(name)

    def _format(self, msg: Any, *args: Any) -> str:
        text = str(msg)
        if args:
            try:
                text = text % args
            except Exception:
                text = f"{text} {' '.join(str(a) for a in args)}"
        return text

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "debug"):
            _astrbot_logger.debug(self._prefix + self._format(msg, *args))
        else:
            self._std_logger.debug(msg, *args, **kwargs)

    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "info"):
            _astrbot_logger.info(self._prefix + self._format(msg, *args))
        else:
            self._std_logger.info(msg, *args, **kwargs)

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "warning"):
            _astrbot_logger.warning(self._prefix + self._format(msg, *args))
        else:
            self._std_logger.warning(msg, *args, **kwargs)

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "error"):
            _astrbot_logger.error(self._prefix + self._format(msg, *args))
        else:
            self._std_logger.error(msg, *args, **kwargs)

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "error"):
            _astrbot_logger.error(self._prefix + self._format(msg, *args), exc_info=True)
        else:
            self._std_logger.exception(msg, *args, **kwargs)

    def critical(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        if _astrbot_logger is not None and hasattr(_astrbot_logger, "critical"):
            _astrbot_logger.critical(self._prefix + self._format(msg, *args))
        else:
            self._std_logger.critical(msg, *args, **kwargs)

    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._std_logger.log(level, msg, *args, **kwargs)

    def isEnabledFor(self, level: int) -> bool:
        return self._std_logger.isEnabledFor(level)

    def setLevel(self, level: int) -> None:
        self._std_logger.setLevel(level)


def get_logger(name: str = "A_Memorix") -> AstrBotLoggerAdapter:
    """获取具名 Logger 实例。"""
    return AstrBotLoggerAdapter(name)
