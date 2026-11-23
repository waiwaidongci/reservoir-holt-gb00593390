from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('Asia/Shanghai')


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=UTC).isoformat(timespec='seconds') if value else None


def to_local(value: datetime) -> datetime:
    """库内统一存 naive UTC；转现场本地时区（Asia/Shanghai）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LOCAL_TZ)


def to_utc_naive(value: datetime) -> datetime:
    """前端带时区的时间统一落为 naive UTC。"""
    if value.tzinfo is None:
        # 无时区信息时按本地时间解释，避免静默当成 UTC。
        value = value.replace(tzinfo=LOCAL_TZ)
    return value.astimezone(UTC).replace(tzinfo=None)
