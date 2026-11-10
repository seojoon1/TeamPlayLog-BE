"""F-07 먼저 알림 (알림함)"""

import uuid
from datetime import datetime

from fastapi import APIRouter, status
from sqlalchemy import and_, or_, select

from app import clock
from app.deps import DB, CurrentUser
from app.errors import AppError, not_found
from app.models import Notification
from app.schemas import NotificationOut, Page

router = APIRouter(tags=["F-07 알림"])


@router.get("/notifications", response_model=Page[NotificationOut], summary="알림함")
def list_notifications(user: CurrentUser, db: DB, cursor: str | None = None, limit: int = 20, unread_only: bool = False):
    """보낼 시각이 지난 알림만 보여 준다 (조용한 시간에 쌓인 알림은 아침 8시부터 보인다)."""
    limit = max(1, min(limit, 50))
    q = select(Notification).where(Notification.user_id == user.id, Notification.scheduled_for <= clock.now())
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if cursor:
        try:
            ts, nid = cursor.rsplit("_", 1)
            ts, nid = datetime.fromisoformat(ts), uuid.UUID(nid)
        except ValueError as e:
            raise AppError(422, "VALIDATION_ERROR", "잘못된 페이지 정보입니다.") from e
        q = q.where(or_(Notification.scheduled_for < ts, and_(Notification.scheduled_for == ts, Notification.id < nid)))
    rows = list(db.scalars(q.order_by(Notification.scheduled_for.desc(), Notification.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1].scheduled_for.isoformat()}_{rows[-1].id}" if more else None
    return Page[NotificationOut](items=[NotificationOut.model_validate(n) for n in rows], next_cursor=nxt)


@router.patch("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT, summary="읽음 처리")
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DB):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise not_found("알림을 찾을 수 없습니다.")
    if n.read_at is None:
        n.read_at = clock.now()
        db.commit()
