"""F-02 팀 만들기·참여 / F-03 역할 약속"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.config import get_settings
from app.deps import DB, CurrentUser, LeaderOf, MemberOf
from app.errors import AppError, conflict, not_found
from app.models import (
    Agreement,
    AgreementConsent,
    AgreementRole,
    AgreementStatus,
    AgreementTask,
    MemberRole,
    NotificationKind,
    Task,
    TaskAssignee,
    Team,
    TeamMember,
)
from app.notify import notify
from app.schemas import (
    AgreementHistoryOut,
    AgreementIn,
    AgreementOut,
    ConsentIn,
    JoinIn,
    LeaderIn,
    TeamCreateIn,
    TeamOut,
    TeamSummaryOut,
)
from app.views import active_members, agreement_out, latest_agreement, team_out, team_summary

router = APIRouter()

# 헷갈리는 글자(0·O, 1·I)를 뺀 대문자·숫자
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_invite_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_CHARS) for _ in range(6))
        if not db.scalar(select(Team.id).where(Team.invite_code == code)):
            return code


def _invite_expiry():
    return clock.now() + timedelta(days=get_settings().invite_code_days)


# ---- F-02 ---------------------------------------------------------------------


@router.get("/teams", response_model=list[TeamSummaryOut], tags=["F-02 팀"], summary="내 팀 목록")
def list_teams(user: CurrentUser, db: DB):
    rows = db.execute(
        select(Team, TeamMember)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id, TeamMember.left_at.is_(None))
        .order_by(Team.final_due_at)
    ).all()
    return [team_summary(db, team, me) for team, me in rows]


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED, tags=["F-02 팀"], summary="팀 만들기")
def create_team(body: TeamCreateIn, user: CurrentUser, db: DB):
    team = Team(
        course_name=body.course_name,
        project_name=body.project_name,
        final_due_at=body.final_due_at,
        max_members=body.max_members,
        invite_code=_new_invite_code(db),
        invite_expires_at=_invite_expiry(),
    )
    db.add(team)
    db.flush()
    me = TeamMember(team_id=team.id, user_id=user.id, role=MemberRole.LEADER)
    db.add(me)
    db.commit()
    return team_out(db, team, me)


@router.post("/teams/join", response_model=TeamOut, tags=["F-02 팀"], summary="초대 코드로 참여")
def join_team(body: JoinIn, user: CurrentUser, db: DB):
    team = db.scalar(select(Team).where(Team.invite_code == body.invite_code))
    if team is None:
        raise not_found("초대 코드를 찾을 수 없습니다. 코드를 다시 확인해 주세요.")
    if team.invite_expires_at < clock.now():
        raise conflict("INVITE_EXPIRED", "초대 코드가 만료되었습니다. 조장에게 새 코드를 받아 주세요.")
    me = db.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
    if me and me.left_at is None:
        raise conflict("ALREADY_MEMBER", "이미 참여한 팀입니다.")
    if len(active_members(db, team.id)) >= team.max_members:
        raise conflict("TEAM_FULL", "팀 인원이 모두 찼습니다.")
    if me:  # 나갔다가 다시 들어오는 경우
        me.left_at = None
        me.role = MemberRole.MEMBER
        me.joined_at = clock.now()
    else:
        me = TeamMember(team_id=team.id, user_id=user.id, role=MemberRole.MEMBER)
        db.add(me)
    # 확정된 약속은 새 팀원의 동의를 받아야 하므로 '동의 대기'로 돌아간다
    ag = latest_agreement(db, team.id)
    if ag and ag.status == AgreementStatus.CONFIRMED:
        ag.status = AgreementStatus.PENDING
        db.flush()
        notify(
            db,
            [user.id],
            NotificationKind.AGREEMENT_CONSENT,
            "역할 약속에 동의해 주세요",
            f"{team.project_name} 팀의 역할 약속을 확인하고 동의해 주세요.",
            team_id=team.id,
            payload={"screen": "agreement", "team_id": str(team.id)},
            dedupe=f"agreement:{ag.id}:join:{me.id}",
        )
    db.commit()
    return team_out(db, team, me)


@router.get("/teams/{team_id}", response_model=TeamOut, tags=["F-02 팀"], summary="팀 상세")
def get_team(m: MemberOf, db: DB):
    return team_out(db, m.team, m.member)


@router.post("/teams/{team_id}/invite-code", response_model=TeamOut, tags=["F-02 팀"], summary="초대 코드 다시 만들기")
def regenerate_invite(m: LeaderOf, db: DB):
    m.team.invite_code = _new_invite_code(db)
    m.team.invite_expires_at = _invite_expiry()
    db.commit()
    return team_out(db, m.team, m.member)


@router.patch("/teams/{team_id}/leader", response_model=TeamOut, tags=["F-02 팀"], summary="조장 넘기기")
def change_leader(body: LeaderIn, m: LeaderOf, db: DB):
    target = db.get(TeamMember, body.member_id)
    if target is None or target.team_id != m.team.id or target.left_at is not None:
        raise not_found("팀원을 찾을 수 없습니다.")
    if target.id == m.member.id:
        raise conflict("ALREADY_LEADER", "이미 조장입니다.")
    target.role = MemberRole.LEADER
    m.member.role = MemberRole.MEMBER
    db.commit()
    return team_out(db, m.team, m.member)


@router.delete("/teams/{team_id}/members/me", status_code=status.HTTP_204_NO_CONTENT, tags=["F-02 팀"], summary="팀 나가기")
def leave_team(m: MemberOf, db: DB):
    others = [x for x in active_members(db, m.team.id) if x.id != m.member.id]
    if m.is_leader and others:
        raise conflict("TRANSFER_LEADER_FIRST", "조장 역할을 다른 팀원에게 넘긴 뒤 나갈 수 있습니다.")
    m.member.left_at = clock.now()
    # 내가 담당이던 할 일에서 빠진다. 담당자가 없어진 할 일은 조장에게 알린다.
    my_tasks = list(db.scalars(select(TaskAssignee.task_id).where(TaskAssignee.member_id == m.member.id)))
    db.execute(delete(TaskAssignee).where(TaskAssignee.member_id == m.member.id))
    db.flush()
    leader = next((x for x in others if x.role == MemberRole.LEADER), None)
    for task_id in my_tasks:
        task = db.get(Task, task_id)
        left_alone = not task.for_everyone and not db.scalar(select(TaskAssignee.member_id).where(TaskAssignee.task_id == task_id))
        if leader and left_alone:
            notify(
                db,
                [leader.user_id],
                NotificationKind.TASK_ASSIGNED,
                "담당자가 없는 할 일이 생겼어요",
                f"‘{task.title}’의 담당자가 팀을 나갔습니다. 새 담당자를 정해 주세요.",
                team_id=m.team.id,
                payload={"screen": "task", "task_id": str(task.id)},
            )
    db.commit()


# ---- F-03 ---------------------------------------------------------------------

_REPLY_HOURS = {6, 12, 24, 48}


@router.get("/teams/{team_id}/agreement", response_model=AgreementHistoryOut, tags=["F-03 역할 약속"], summary="현재 약속과 버전 이력")
def get_agreement(m: MemberOf, db: DB):
    versions = list(db.scalars(select(Agreement).where(Agreement.team_id == m.team.id).order_by(Agreement.version.desc())))
    outs = [agreement_out(db, a, m.member) for a in versions]
    return AgreementHistoryOut(current=outs[0] if outs else None, versions=outs)


@router.put("/teams/{team_id}/agreement", response_model=AgreementOut, tags=["F-03 역할 약속"], summary="약속 초안 저장")
def save_agreement(body: AgreementIn, m: LeaderOf, db: DB):
    """확정된 약속을 고치면 새 버전 초안이 만들어진다. 동의 대기 중에 고치면 받은 동의는 초기화된다."""
    if body.reply_hours not in _REPLY_HOURS:
        raise AppError(422, "VALIDATION_ERROR", "답장 기준 시간은 6, 12, 24, 48시간 중 하나여야 합니다.")
    member_ids = {x.id for x in active_members(db, m.team.id)}
    for mid in [r.member_id for r in body.roles] + [t.assignee_member_id for t in body.tasks]:
        if mid not in member_ids:
            raise AppError(422, "VALIDATION_ERROR", "현재 팀원이 아닌 사람이 포함되어 있습니다.")

    latest = latest_agreement(db, m.team.id)
    previous_items = {}
    # 한 번이라도 확정된 버전은 그대로 보존하고 새 버전을 만든다
    if latest is None or latest.confirmed_at is not None:
        if latest:  # 확정본의 할 일과 이어지도록 연결 정보를 넘긴다
            previous_items = {(t.assignee_member_id, t.title): t.created_task_id for t in latest.tasks}
        ag = Agreement(team_id=m.team.id, version=(latest.version + 1) if latest else 1)
        db.add(ag)
    else:
        ag = latest
        previous_items = {(t.assignee_member_id, t.title): t.created_task_id for t in ag.tasks}
        ag.roles.clear()
        ag.tasks.clear()
        ag.consents.clear()
        ag.status = AgreementStatus.DRAFT
        ag.submitted_at = None
    ag.reply_hours = body.reply_hours
    ag.meeting_rule = body.meeting_rule
    ag.free_rules = list(body.free_rules)
    for i, r in enumerate(body.roles):
        ag.roles.append(AgreementRole(member_id=r.member_id, role_name=r.role_name, position=i))
    for t in body.tasks:
        ag.tasks.append(
            AgreementTask(
                assignee_member_id=t.assignee_member_id,
                title=t.title,
                due_at=t.due_at,
                created_task_id=previous_items.get((t.assignee_member_id, t.title)),
            )
        )
    db.commit()
    return agreement_out(db, ag, m.member)


@router.post("/teams/{team_id}/agreement/submit", response_model=AgreementOut, tags=["F-03 역할 약속"], summary="동의 요청 보내기")
def submit_agreement(m: LeaderOf, db: DB):
    ag = latest_agreement(db, m.team.id)
    if ag is None or ag.status != AgreementStatus.DRAFT:
        raise conflict("NO_DRAFT", "동의를 요청할 약속 초안이 없습니다.")
    if not ag.roles:
        raise conflict("EMPTY_AGREEMENT", "역할을 한 명 이상 정해 주세요.")
    ag.status = AgreementStatus.PENDING
    ag.submitted_at = clock.now()
    # 조장이 요청을 보내는 것은 곧 조장의 동의로 본다
    ag.consents.append(AgreementConsent(member_id=m.member.id, agreed=True, responded_at=clock.now()))
    db.flush()
    others = [x for x in active_members(db, m.team.id) if x.id != m.member.id]
    notify(
        db,
        [x.user_id for x in others],
        NotificationKind.AGREEMENT_CONSENT,
        "역할 약속에 동의해 주세요",
        f"{m.team.project_name} 팀의 역할 약속(버전 {ag.version})을 확인하고 동의해 주세요.",
        team_id=m.team.id,
        payload={"screen": "agreement", "team_id": str(m.team.id)},
        dedupe=f"agreement:{ag.id}:submit",
    )
    _confirm_if_everyone_agreed(db, ag)
    db.commit()
    return agreement_out(db, ag, m.member)


@router.post("/teams/{team_id}/agreement/consent", response_model=AgreementOut, tags=["F-03 역할 약속"], summary="동의 또는 수정 요청")
def consent_agreement(body: ConsentIn, m: MemberOf, db: DB):
    ag = latest_agreement(db, m.team.id)
    if ag is None or ag.status != AgreementStatus.PENDING:
        raise conflict("NOT_PENDING", "지금은 동의할 약속이 없습니다.")
    if not body.agreed and not body.change_request:
        raise AppError(422, "VALIDATION_ERROR", "동의하지 않을 때는 수정 요청 사유를 적어 주세요.")
    consent = db.scalar(
        select(AgreementConsent).where(AgreementConsent.agreement_id == ag.id, AgreementConsent.member_id == m.member.id)
    )
    if consent is None:
        consent = AgreementConsent(agreement_id=ag.id, member_id=m.member.id, agreed=body.agreed)
        db.add(consent)
    consent.agreed = body.agreed
    consent.change_request = None if body.agreed else body.change_request
    consent.responded_at = clock.now()
    db.flush()
    if not body.agreed:
        leader = next((x for x in active_members(db, m.team.id) if x.role == MemberRole.LEADER), None)
        if leader:
            notify(
                db,
                [leader.user_id],
                NotificationKind.AGREEMENT_CONSENT,
                "역할 약속에 수정 요청이 왔어요",
                f"{m.member.user.display_name}: {body.change_request}",
                team_id=m.team.id,
                payload={"screen": "agreement", "team_id": str(m.team.id)},
            )
    _confirm_if_everyone_agreed(db, ag)
    db.commit()
    db.refresh(ag)
    return agreement_out(db, ag, m.member)


def _confirm_if_everyone_agreed(db: Session, ag: Agreement) -> None:
    members = active_members(db, ag.team_id)
    agreed = set(
        db.scalars(
            select(AgreementConsent.member_id).where(AgreementConsent.agreement_id == ag.id, AgreementConsent.agreed.is_(True))
        )
    )
    if not members or any(x.id not in agreed for x in members):
        return
    now = clock.now()
    for old in db.scalars(
        select(Agreement).where(
            Agreement.team_id == ag.team_id, Agreement.id != ag.id, Agreement.status != AgreementStatus.SUPERSEDED
        )
    ):
        old.status = AgreementStatus.SUPERSEDED
    ag.status = AgreementStatus.CONFIRMED
    ag.confirmed_at = now
    # 약속의 할 일을 보드에 만든다. 이전 버전에서 이어진 할 일은 마감만 맞춘다.
    member_ids = {x.id for x in members}
    for item in ag.tasks:
        if item.assignee_member_id not in member_ids:
            continue
        task = db.get(Task, item.created_task_id) if item.created_task_id else None
        if task:
            task.due_at = item.due_at
            continue
        task = Task(team_id=ag.team_id, title=item.title, due_at=item.due_at, agreement_id=ag.id)
        db.add(task)
        db.flush()
        db.add(TaskAssignee(task_id=task.id, member_id=item.assignee_member_id))
        item.created_task_id = task.id
