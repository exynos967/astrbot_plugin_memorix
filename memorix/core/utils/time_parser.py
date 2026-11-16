"""
时间解析工具。

约束：
1. 查询参数（Command/Tool）优先接受结构化绝对时间：
   - YYYY/MM/DD
   - YYYY/MM/DD HH:mm
   同时兼容常见自然语言时间短语（如“昨天”“上周”“最近”）。
2. 入库时允许更宽松格式（含时间戳、YYYY-MM-DD 等）。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_QUERY_DATE_PATTERNS = ("%Y/%m/%d", "%Y-%m-%d")
_QUERY_MINUTE_PATTERNS = ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M")
_DATE_TOKEN_PATTERN = r"\d{4}[/-]\d{2}[/-]\d{2}(?: \d{2}:\d{2})?"
_NAMED_TIME_TOKENS = [
    "这个月",
    "上个月",
    "这周",
    "本周",
    "上周",
    "今天",
    "昨天",
    "前天",
    "最近",
    "刚刚",
    "刚才",
    "今年",
    "去年",
    "本月",
]
_TIME_TOKEN_PATTERN = rf"(?:{'|'.join(map(re.escape, _NAMED_TIME_TOKENS))}|{_DATE_TOKEN_PATTERN})"
_TIME_RANGE_IN_TEXT_RE = re.compile(
    rf"(?:从)?(?P<start>{_TIME_TOKEN_PATTERN})\s*(?:到|至)\s*(?P<end>{_TIME_TOKEN_PATTERN})"
)
_TIME_TOKEN_IN_TEXT_RE = re.compile(_TIME_TOKEN_PATTERN)
_TOPIC_PUNCT_RE = re.compile(r"[，。！？、,.!?:：；;（）()\[\]【】\"'“”‘’\s]+")

_INGEST_FORMATS = [
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d",
    "%Y-%m-%d",
]

_INGEST_DATE_FORMATS = {"%Y/%m/%d", "%Y-%m-%d"}
_GENERIC_PREFIXES = [
    "你还记得",
    "还记得",
    "记得",
    "帮我回忆一下",
    "回忆一下",
    "帮我回忆",
    "回忆",
]
_GENERIC_INFIXES = [
    "我说过的",
    "我提过的",
    "我聊过的",
    "我讲过的",
    "我问过的",
    "我说过",
    "我提过",
    "我聊过",
    "我讲过",
    "我问过",
    "说过的",
    "提过的",
    "聊过的",
    "讲过的",
    "问过的",
    "说过",
    "提过",
    "聊过",
    "讲过",
    "问过",
]
_GENERIC_SUFFIXES = [
    "是什么来着",
    "是什么",
    "是啥",
    "是多少",
    "有哪些",
    "有吗",
    "吗",
    "呢",
    "呀",
    "啊",
    "吧",
]
_GENERIC_ONLY_TOPICS = {
    "",
    "什么",
    "啥",
    "哪些",
    "哪件事",
    "哪件",
    "事情",
    "内容",
}
_GENERIC_SINGLE_TOKENS = {"我", "你", "他", "她", "它", "我们", "你们", "一下", "一下子"}


@dataclass(frozen=True)
class QueryTimeIntent:
    original_query: str
    cleaned_query: str
    matched_text: str
    time_from: str
    time_to: str
    query_type: str


def _start_of_day(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(value: datetime) -> datetime:
    return value.replace(hour=23, minute=59, second=0, microsecond=0)


def _start_of_week(value: datetime) -> datetime:
    return _start_of_day(value - timedelta(days=value.weekday()))


def _end_of_week(value: datetime) -> datetime:
    return _end_of_day(_start_of_week(value) + timedelta(days=6))


def _start_of_month(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _end_of_month(value: datetime) -> datetime:
    last_day = calendar.monthrange(value.year, value.month)[1]
    return value.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)


def _parse_query_structured_range(value: str) -> Optional[Tuple[float, float]]:
    text = str(value).strip()
    if not text:
        return None

    for fmt in _QUERY_MINUTE_PATTERNS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        ts = dt.timestamp()
        return ts, ts

    for fmt in _QUERY_DATE_PATTERNS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return _start_of_day(dt).timestamp(), _end_of_day(dt).timestamp()

    return None


def _resolve_named_time_range(value: str, now: Optional[datetime] = None) -> Optional[Tuple[float, float]]:
    text = str(value).strip()
    if not text:
        return None

    structured = _parse_query_structured_range(text)
    if structured is not None:
        return structured

    ref_now = now or datetime.now()
    normalized = text.replace(" ", "")

    if normalized in {"刚刚", "刚才"}:
        start = ref_now - timedelta(minutes=10)
        end = ref_now
    elif normalized == "今天":
        start = _start_of_day(ref_now)
        end = ref_now
    elif normalized == "昨天":
        base = ref_now - timedelta(days=1)
        start = _start_of_day(base)
        end = _end_of_day(base)
    elif normalized == "前天":
        base = ref_now - timedelta(days=2)
        start = _start_of_day(base)
        end = _end_of_day(base)
    elif normalized == "最近":
        start = ref_now - timedelta(days=7)
        end = ref_now
    elif normalized in {"这周", "本周"}:
        start = _start_of_week(ref_now)
        end = ref_now
    elif normalized == "上周":
        base = ref_now - timedelta(days=7)
        start = _start_of_week(base)
        end = _end_of_week(base)
    elif normalized in {"这个月", "本月"}:
        start = _start_of_month(ref_now)
        end = ref_now
    elif normalized == "上个月":
        month_anchor = _start_of_month(ref_now) - timedelta(days=1)
        start = _start_of_month(month_anchor)
        end = _end_of_month(month_anchor)
    elif normalized == "今年":
        start = ref_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = ref_now
    elif normalized == "去年":
        year = ref_now.year - 1
        start = datetime(year, 1, 1, 0, 0, 0)
        end = datetime(year, 12, 31, 23, 59, 0)
    else:
        return None

    return start.timestamp(), end.timestamp()


def _strip_prefixes(text: str) -> str:
    value = str(text or "").strip()
    changed = True
    while changed and value:
        changed = False
        for prefix in _GENERIC_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
                changed = True
    return value


def _strip_suffixes(text: str) -> str:
    value = str(text or "").strip()
    changed = True
    while changed and value:
        changed = False
        for suffix in _GENERIC_SUFFIXES:
            if value.endswith(suffix):
                value = value[: -len(suffix)].strip()
                changed = True
    return value


def _clean_topic_query(text: str) -> str:
    value = _TOPIC_PUNCT_RE.sub(" ", str(text or "")).strip()
    value = _strip_prefixes(value)
    for token in _GENERIC_INFIXES:
        value = value.replace(token, " ")
    for token in _GENERIC_SINGLE_TOKENS:
        value = value.replace(token, " ")
    value = _TOPIC_PUNCT_RE.sub(" ", value).strip()
    value = _strip_suffixes(value)
    value = _TOPIC_PUNCT_RE.sub(" ", value).strip()

    collapsed = value.replace(" ", "")
    if collapsed in _GENERIC_ONLY_TOPICS:
        return ""
    return value


def extract_query_time_intent(
    query: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[QueryTimeIntent]:
    text = str(query or "").strip()
    if not text:
        return None

    ref_now = now or datetime.now()
    range_match = _TIME_RANGE_IN_TEXT_RE.search(text)
    if range_match:
        start_range = _resolve_named_time_range(range_match.group("start"), ref_now)
        end_range = _resolve_named_time_range(range_match.group("end"), ref_now)
        if start_range and end_range:
            remaining = f"{text[: range_match.start()]} {text[range_match.end() :]}"
            cleaned = _clean_topic_query(remaining)
            return QueryTimeIntent(
                original_query=text,
                cleaned_query=cleaned,
                matched_text=range_match.group(0),
                time_from=format_timestamp(start_range[0]) or "",
                time_to=format_timestamp(end_range[1]) or "",
                query_type="hybrid" if cleaned else "time",
            )

    single_match = _TIME_TOKEN_IN_TEXT_RE.search(text)
    if single_match:
        resolved = _resolve_named_time_range(single_match.group(0), ref_now)
        if resolved:
            remaining = f"{text[: single_match.start()]} {text[single_match.end() :]}"
            cleaned = _clean_topic_query(remaining)
            return QueryTimeIntent(
                original_query=text,
                cleaned_query=cleaned,
                matched_text=single_match.group(0),
                time_from=format_timestamp(resolved[0]) or "",
                time_to=format_timestamp(resolved[1]) or "",
                query_type="hybrid" if cleaned else "time",
            )

    return None


def parse_query_datetime_to_timestamp(value: str, is_end: bool = False) -> float:
    """解析查询时间，支持结构化时间与常见自然语言时间短语。"""
    text = str(value).strip()
    if not text:
        raise ValueError("时间不能为空")

    structured = _parse_query_structured_range(text)
    if structured is not None:
        return structured[1] if is_end else structured[0]

    named = _resolve_named_time_range(text)
    if named is not None:
        return named[1] if is_end else named[0]

    raise ValueError(
        f"时间格式错误: {text}。支持 YYYY/MM/DD、YYYY-MM-DD、YYYY/MM/DD HH:mm 及常见中文时间短语"
    )


def parse_query_time_range(
    time_from: Optional[str],
    time_to: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    """解析查询窗口并验证区间。"""
    ts_from: Optional[float] = None
    ts_to: Optional[float] = None

    if time_from and not time_to:
        named_range = _resolve_named_time_range(time_from)
        if named_range is not None:
            ts_from, ts_to = named_range
        else:
            ts_from = parse_query_datetime_to_timestamp(time_from, is_end=False)
    elif time_to and not time_from:
        named_range = _resolve_named_time_range(time_to)
        if named_range is not None:
            ts_from, ts_to = named_range
        else:
            ts_to = parse_query_datetime_to_timestamp(time_to, is_end=True)
    else:
        ts_from = (
            parse_query_datetime_to_timestamp(time_from, is_end=False)
            if time_from
            else None
        )
        ts_to = (
            parse_query_datetime_to_timestamp(time_to, is_end=True)
            if time_to
            else None
        )

    if ts_from is not None and ts_to is not None and ts_from > ts_to:
        raise ValueError("time_from 不能晚于 time_to")

    return ts_from, ts_to


def parse_ingest_datetime_to_timestamp(
    value: Any,
    is_end: bool = False,
) -> Optional[float]:
    """解析入库时间，允许 timestamp/常见字符串格式。"""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    if _NUMERIC_RE.fullmatch(text):
        return float(text)

    for fmt in _INGEST_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in _INGEST_DATE_FORMATS and is_end:
                dt = dt.replace(hour=23, minute=59, second=0, microsecond=0)
            return dt.timestamp()
        except ValueError:
            continue

    raise ValueError(f"无法解析时间: {text}")


def normalize_time_meta(time_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """归一化 time_meta 到存储层字段。"""
    if not time_meta:
        return {}

    normalized: Dict[str, Any] = {}

    event_time = parse_ingest_datetime_to_timestamp(time_meta.get("event_time"))
    event_start = parse_ingest_datetime_to_timestamp(
        time_meta.get("event_time_start"),
        is_end=False,
    )
    event_end = parse_ingest_datetime_to_timestamp(
        time_meta.get("event_time_end"),
        is_end=True,
    )

    time_range = time_meta.get("time_range")
    if (
        isinstance(time_range, (list, tuple))
        and len(time_range) == 2
    ):
        if event_start is None:
            event_start = parse_ingest_datetime_to_timestamp(time_range[0], is_end=False)
        if event_end is None:
            event_end = parse_ingest_datetime_to_timestamp(time_range[1], is_end=True)

    if event_start is not None and event_end is not None and event_start > event_end:
        raise ValueError("event_time_start 不能晚于 event_time_end")

    if event_time is not None:
        normalized["event_time"] = event_time
    if event_start is not None:
        normalized["event_time_start"] = event_start
    if event_end is not None:
        normalized["event_time_end"] = event_end

    granularity = time_meta.get("time_granularity")
    if granularity:
        normalized["time_granularity"] = str(granularity)
    else:
        raw_time_values = [
            time_meta.get("event_time"),
            time_meta.get("event_time_start"),
            time_meta.get("event_time_end"),
        ]
        has_minute = any(isinstance(v, str) and ":" in v for v in raw_time_values if v is not None)
        normalized["time_granularity"] = "minute" if has_minute else "day"

    confidence = time_meta.get("time_confidence")
    if confidence is not None:
        normalized["time_confidence"] = float(confidence)

    return normalized


def format_timestamp(ts: Optional[float]) -> Optional[str]:
    """将 timestamp 格式化为 YYYY/MM/DD HH:mm。"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y/%m/%d %H:%M")
