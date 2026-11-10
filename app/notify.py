"""알림 만들기와 보내기 (기능명세서 5장).

알림은 notifications 테이블에 먼저 쌓고, 스케줄러가 보낼 시각이 된 것을 푸시로 보낸다.
앱 안 알림함은 보낼 시각이 지난 알림만 보여 준다.
"""

import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.config import get_settings
from app.models import Notification, NotificationKind

log = logging.getLogger("teamplaylog.notify")


def deliver_at(at: datetime) -> datetime:
    """조용한 시간(KST 23시~8시)이면 그날 아침 8시로 미룬다."""
    s = get_settings()
    local = at.astimezone(clock.KST)
    h = local.hour
    if h >= s.quiet_start_hour or h < s.quiet_end_hour:
        day = local.date() if h < s.quiet_end_hour else local.date() + timedelta(days=1)
        morning = datetime(day.year, day.month, day.day, s.quiet_end_hour, tzinfo=clock.KST)
        return morning.astimezone(at.tzinfo)
    return at


def notify(
    db: Session,
    user_ids: Iterable[uuid.UUID],
    kind: NotificationKind,
    title: str,
    body: str,
    *,
    team_id: uuid.UUID | None = None,
    payload: dict | None = None,
    dedupe: str | None = None,
) -> int:
    """알림을 쌓는다. dedupe가 같은 알림은 사람마다 한 번만 만든다. 만든 개수를 돌려준다."""
    when = deliver_at(clock.now())
    made = 0
    for uid in set(user_ids):
        key = f"{dedupe}:{uid}" if dedupe else None
        if key and db.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
            continue
        n = Notification(
            user_id=uid,
            team_id=team_id,
            kind=kind,
            title=title[:60],
            body=body[:200],
            payload=payload or {},
            dedupe_key=key,
            scheduled_for=when,
        )
        try:
            with db.begin_nested():
                db.add(n)
        except IntegrityError:
            continue
        made += 1
    return made


def send_push(n: Notification) -> None:
    """푸시 발송. Firebase 프로젝트가 준비되면 FCM 전송으로 바꾼다. 지금은 로그만 남긴다."""
    log.info("push %s -> %s: %s", n.kind.value, n.user_id, n.title)


def dispatch_due(db: Session) -> int:
    pending = db.scalars(
        select(Notification).where(Notification.sent_at.is_(None), Notification.scheduled_for <= clock.now())
    ).all()
    for n in pending:
        send_push(n)
        n.sent_at = clock.now()
    return len(pending)
