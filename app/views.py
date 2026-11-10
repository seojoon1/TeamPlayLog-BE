"""DB 객체를 API 응답 형식으로 바꾸는 함수와 공통 조회."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.models import (
    Agreement,
    AgreementConsent,
    AgreementStatus,
    MemberRole,
    Record,
    RecordStatus,
    Task,
    TaskAssignee,
    TaskStatus,
    Team,
    TeamMember,
)
from app.schemas import (
    AgreementOut,
    AgreementRoleOut,
    AgreementTaskOut,
    ConfirmationOut,
    ConsentOut,
    MemberOut,
    RecordOut,
    TaskOut,
    TeamOut,
    TeamSummaryOut,
)

# ---- 팀원 -------------------------------------------------------------------


def active_members(db: Session, team_id: uuid.UUID) -> list[TeamMember]:
    return list(
        db.scalars(
            select(TeamMember)
            .where(TeamMember.team_id == team_id, TeamMember.left_at.is_(None))
            .order_by(TeamMember.joined_at)
        )
    )


def member_out(m: TeamMember) -> MemberOut:
    return MemberOut(
        member_id=m.id,
        user_id=m.user_id,
        display_name=m.user.display_name,
        role=m.role,
        left=m.left_at is not None,
    )


def member_name(db: Session, member_id: uuid.UUID) -> str:
    m = db.get(TeamMember, member_id)
    return m.user.display_name if m else "알 수 없음"


# ---- 팀 ---------------------------------------------------------------------


def task_counts(db: Session, team_id: uuid.UUID) -> tuple[int, int]:
    total = db.scalar(select(func.count()).select_from(Task).where(Task.team_id == team_id)) or 0
    done = db.scalar(
        select(func.count()).select_from(Task).where(Task.team_id == team_id, Task.status == TaskStatus.DONE)
    ) or 0
    return total, done


def latest_agreement(db: Session, team_id: uuid.UUID) -> Agreement | None:
    return db.scalar(select(Agreement).where(Agreement.team_id == team_id).order_by(Agreement.version.desc()).limit(1))


def confirmed_agreement(db: Session, team_id: uuid.UUID) -> Agreement | None:
    return db.scalar(
        select(Agreement).where(Agreement.team_id == team_id, Agreement.status == AgreementStatus.CONFIRMED)
    )


def team_summary(db: Session, team: Team, me: TeamMember) -> TeamSummaryOut:
    return TeamSummaryOut(
        id=team.id,
        course_name=team.course_name,
        project_name=team.project_name,
        final_due_at=team.final_due_at,
        member_count=len(active_members(db, team.id)),
        my_role=me.role,
    )


def team_out(db: Session, team: Team, me: TeamMember) -> TeamOut:
    members = active_members(db, team.id)
    total, done = task_counts(db, team.id)
    leader = me.role == MemberRole.LEADER
    return TeamOut(
        **team_summary(db, team, me).model_dump(),
        max_members=team.max_members,
        invite_code=team.invite_code if leader else None,
        invite_expires_at=team.invite_expires_at if leader else None,
        members=[member_out(m) for m in members],
        agreement_confirmed=confirmed_agreement(db, team.id) is not None,
        tasks_total=total,
        tasks_done=done,
    )


def d_day(team: Team) -> int:
    """최종 마감까지 남은 날 (KST 날짜 기준). 0이면 오늘, 음수면 지남."""
    today = clock.now().astimezone(clock.KST).date()
    return (team.final_due_at.astimezone(clock.KST).date() - today).days


# ---- 역할 약속 ----------------------------------------------------------------


def agreement_out(db: Session, ag: Agreement, viewer: TeamMember) -> AgreementOut:
    members = active_members(db, ag.team_id)
    consents = {c.member_id: c for c in db.scalars(select(AgreementConsent).where(AgreementConsent.agreement_id == ag.id))}
    leader = viewer.role == MemberRole.LEADER
    rows = []
    for m in members:
        c = consents.get(m.id)
        rows.append(
            ConsentOut(
                member_id=m.id,
                display_name=m.user.display_name,
                agreed=c.agreed if c else None,
                change_request=(c.change_request if c and (leader or m.id == viewer.id) else None),
                responded_at=c.responded_at if c else None,
            )
        )
    return AgreementOut(
        id=ag.id,
        version=ag.version,
        status=ag.status,
        reply_hours=ag.reply_hours,
        meeting_rule=ag.meeting_rule,
        free_rules=list(ag.free_rules or []),
        roles=[AgreementRoleOut.model_validate(r) for r in ag.roles],
        tasks=[AgreementTaskOut.model_validate(t) for t in ag.tasks],
        consents=rows,
        confirmed_at=ag.confirmed_at,
    )


# ---- 할 일 -------------------------------------------------------------------


def assignee_ids(db: Session, task_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(TaskAssignee.member_id).where(TaskAssignee.task_id == task_id)))


def responsible_members(db: Session, task: Task) -> list[TeamMember]:
    """알림을 받을 담당자. '전원'이면 현재 팀원 모두."""
    if task.for_everyone:
        return active_members(db, task.team_id)
    ids = assignee_ids(db, task.id)
    return [m for m in (db.get(TeamMember, i) for i in ids) if m and m.left_at is None]


def task_out(db: Session, task: Task) -> TaskOut:
    live = select(func.count()).select_from(Record).where(Record.task_id == task.id, Record.status != RecordStatus.WITHDRAWN)
    confirmed = select(func.count()).select_from(Record).where(
        Record.task_id == task.id, Record.status == RecordStatus.CONFIRMED
    )
    assignees = [m for m in (db.get(TeamMember, i) for i in assignee_ids(db, task.id)) if m]
    return TaskOut(
        id=task.id,
        title=task.title,
        due_at=task.due_at,
        status=task.status,
        memo=task.memo,
        for_everyone=task.for_everyone,
        assignees=[member_out(m) for m in assignees],
        record_count=db.scalar(live) or 0,
        confirmed_record_count=db.scalar(confirmed) or 0,
        completed_at=task.completed_at,
    )


def refresh_overdue(db: Session, team_id: uuid.UUID | None = None) -> list[Task]:
    """마감이 지난 미완료 할 일을 '마감 지남'으로 바꾼다. 바뀐 할 일을 돌려준다."""
    q = select(Task).where(Task.status.in_([TaskStatus.TODO, TaskStatus.DOING]), Task.due_at < clock.now())
    if team_id:
        q = q.where(Task.team_id == team_id)
    changed = list(db.scalars(q))
    for t in changed:
        t.status = TaskStatus.OVERDUE
    return changed


# ---- 기록 -------------------------------------------------------------------


def record_out(db: Session, r: Record) -> RecordOut:
    task = db.get(Task, r.task_id) if r.task_id else None
    return RecordOut(
        id=r.id,
        task_id=r.task_id,
        task_title=task.title if task else None,
        author_id=r.author_id,
        author_name=member_name(db, r.author_id),
        type=r.type,
        link_url=r.link_url,
        file_key=r.file_key,
        note=r.note,
        has_output=r.has_output,
        is_late=r.is_late,
        status=r.status,
        confirmations=[
            ConfirmationOut(
                reviewer_id=c.reviewer_id,
                reviewer_name=member_name(db, c.reviewer_id),
                decision=c.decision,
                reason_type=c.reason_type,
                reason_text=c.reason_text,
                valid=c.valid,
                created_at=c.created_at,
            )
            for c in r.confirmations
        ],
        revision_count=len(r.revisions),
        created_at=r.created_at,
    )
