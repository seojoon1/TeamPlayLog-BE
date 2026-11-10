"""현재 시각. 테스트에서 시간을 고정하거나 옮길 수 있게 한 곳에서만 읽는다."""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

_frozen: datetime | None = None
_offset = timedelta(0)


def now() -> datetime:
    base = _frozen if _frozen is not None else datetime.now(timezone.utc)
    return base + _offset


def freeze(at: datetime) -> None:
    """테스트 전용: 시계를 at에 멈춘다."""
    global _frozen, _offset
    _frozen, _offset = at, timedelta(0)


def travel(delta: timedelta) -> None:
    """테스트 전용: 시계를 delta만큼 앞으로 옮긴다."""
    global _offset
    _offset += delta


def reset() -> None:
    global _frozen, _offset
    _frozen, _offset = None, timedelta(0)
