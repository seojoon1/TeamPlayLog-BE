"""주기 작업 (기능명세서 5장). 5분마다 run_jobs를 실행한다.

- 마감 24시간 전 / 마감 시각 알림 (담당자 본인에게만)
- 마감 지난 할 일을 '마감 지남'으로
- 역할 약속 미동의자 리마인드 (24시간마다 최대 3회)
- 72시간 동안 아무도 확인하지 않은 기록을 '미확인'으로
- 리포트 미열람자 리마인드 (생성 24시간 뒤 1회)
- 보낼 시각이 된 알림 발송
"""

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Agreement,
    AgreementConsent,
    AgreementStatus,
    Confirmation,
    NotificationKind,
    Record,
    RecordStatus,
    Report,
    ReportStatus,
    ReportView,
    Task,
    TaskStatus,
    Team,
)
from app.notify import dispatch_due, notify
from app.views import active_members, refresh_overdue, responsible_members

log = logging.getLogger("teamplaylog.jobs")


def _due_reminders(db: Session) -> None:
    s = get_settings()
    now = clock.now()
    soon = db.scalars(
        select(Task).where(
            Task.status.in_([TaskStatus.TODO, TaskStatus.DOING]),
            Task.due_at > now,
            Task.due_at <= now + timedelta(hours=s.remind_before_hours),
        )
    )
    for t in soon:
        notify(
            db,
            [m.user_id for m in responsible_members(db, t)],
            NotificationKind.DUE_SOON,
            "마감이 다가오고 있어요",
            f"‘{t.title}’ 마감까지 {s.remind_before_hours}시간이 남지 않았습니다.",
            team_id=t.team_id,
            payload={"screen": "record_new", "task_id": str(t.id)},
            dedupe=f"due_soon:{t.id}:{t.due_at.isoformat()}",
        )
    refresh_overdue(db)
    db.flush()
    for t in db.scalars(select(Task).where(Task.status == TaskStatus.OVERDUE)):
        notify(
            db,
            [m.user_id for m in responsible_members(db, t)],
            NotificationKind.OVERDUE,
            "마감이 지났어요",
            f"‘{t.title}’ 마감이 지났습니다. 진행 상황을 기록해 주세요.",
            team_id=t.team_id,
            payload={"screen": "record_new", "task_id": str(t.id)},
            dedupe=f"overdue:{t.id}:{t.due_at.isoformat()}",
        )


def _agreement_reminders(db: Session) -> None:
    now = clock.now()
    for ag in db.scalars(select(Agreement).where(Agreement.status == AgreementStatus.PENDING)):
        if ag.submitted_at is None:
            continue
        rounds = int((now - ag.submitted_at) / timedelta(hours=24))
        if rounds < 1:
            continue
        rounds = min(rounds, 3)
        answered = set(db.scalars(select(AgreementConsent.member_id).where(AgreementConsent.agreement_id == ag.id)))
        waiting = [m for m in active_members(db, ag.team_id) if m.id not in answered]
        team = db.get(Team, ag.team_id)
        notify(
            db,
            [m.user_id for m in waiting],
            NotificationKind.AGREEMENT_CONSENT,
            "역할 약속 동의를 기다리고 있어요",
            f"{team.project_name} 팀의 역할 약속에 아직 응답하지 않았습니다.",
            team_id=ag.team_id,
            payload={"screen": "agreement", "team_id": str(ag.team_id)},
            dedupe=f"agreement:{ag.id}:remind:{rounds}",
        )


def _expire_unconfirmed(db: Session) -> None:
    limit = clock.now() - timedelta(hours=get_settings().confirm_wait_hours)
    has_valid = select(Confirmation.record_id).where(Confirmation.valid.is_(True))
    for r in db.scalars(
        select(Record).where(
            Record.status.in_([RecordStatus.PENDING, RecordStatus.RECHECK]),
            Record.updated_at < limit,
            Record.id.not_in(has_valid),
        )
    ):
        r.status = RecordStatus.UNCONFIRMED


def _report_reminders(db: Session) -> None:
    limit = clock.now() - timedelta(hours=24)
    for rep in db.scalars(select(Report).where(Report.status == ReportStatus.DRAFT, Report.created_at < limit)):
        viewed = set(db.scalars(select(ReportView.member_id).where(ReportView.report_id == rep.id)))
        waiting = [m for m in active_members(db, rep.team_id) if m.id not in viewed]
        notify(
            db,
            [m.user_id for m in waiting],
            NotificationKind.REPORT_VIEW_REQUEST,
            "기여 리포트를 확인해 주세요",
            "팀원 전원이 열람해야 리포트를 제출할 수 있어요.",
            team_id=rep.team_id,
            payload={"screen": "report", "report_id": str(rep.id)},
            dedupe=f"report:{rep.id}:remind",
        )


def run_jobs(db: Session) -> None:
    _due_reminders(db)
    _agreement_reminders(db)
    _expire_unconfirmed(db)
    _report_reminders(db)
    db.flush()
    dispatch_due(db)
    db.commit()


def _tick() -> None:
    db = SessionLocal()
    try:
        run_jobs(db)
    except Exception:  # 한 번 실패해도 다음 주기에 다시 돈다
        log.exception("주기 작업 실패")
        db.rollback()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_tick, "interval", minutes=5, id="teamplaylog", next_run_time=clock.now())
    sched.start()
    return sched
