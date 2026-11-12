"""F-08 기여 리포트"""

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.config import get_settings
from app.deps import DB, CurrentUser, Membership, MemberOf, find_membership
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    Decision,
    NotificationKind,
    Record,
    RecordStatus,
    RecordType,
    Report,
    ReportStatus,
    ReportView,
    Task,
    Team,
    TeamMember,
    User,
)
from app.notify import notify
from app.report_pdf import render
from app.schemas import ReportCreateIn, ReportOut, ReportViewIn, ReportViewOut
from app.views import active_members, confirmed_agreement, member_name

router = APIRouter(tags=["F-08 기여 리포트"])

NOTICE = (
    "이 리포트는 팀원이 남기고 다른 팀원이 확인한 기록을 모은 것입니다. "
    "기록의 개수는 기여의 크기를 뜻하지 않으며, 앱은 점수나 순위를 계산하지 않습니다. "
    "평가는 기록 원문과 결과물을 보고 교수자와 팀원이 판단합니다."
)


def _snapshot(db: Session, team_id: uuid.UUID, period_from, period_to) -> dict:
    team = db.get(Team, team_id)
    ag = confirmed_agreement(db, team_id)
    members = list(db.scalars(select(TeamMember).where(TeamMember.team_id == team_id).order_by(TeamMember.joined_at)))
    records = list(
        db.scalars(
            select(Record)
            .where(
                Record.team_id == team_id,
                Record.status != RecordStatus.WITHDRAWN,
                Record.created_at >= period_from,
                Record.created_at <= period_to,
            )
            .order_by(Record.created_at)
        )
    )
    out_members = []
    for m in members:
        mine = [r for r in records if r.author_id == m.id]
        if m.left_at is not None and not mine:
            continue
        groups = {"confirmed": {t.value: [] for t in RecordType}, "unconfirmed": {t.value: [] for t in RecordType}}
        for r in mine:
            task = db.get(Task, r.task_id) if r.task_id else None
            entry = {
                "record_id": str(r.id),
                "date": r.created_at.isoformat(),
                "task": task.title if task else None,
                "note": r.note,
                "link": r.link_url,
                "file": r.file_key,
                "late": r.is_late,
                "no_output": not r.has_output,
                "status": r.status.value,
                "confirmed_by": [
                    member_name(db, c.reviewer_id) for c in r.confirmations if c.valid and c.decision == Decision.CONFIRM
                ],
            }
            bucket = "confirmed" if r.status == RecordStatus.CONFIRMED else "unconfirmed"
            groups[bucket][r.type.value].append(entry)
        out_members.append({"member_id": str(m.id), "name": m.user.display_name, "left": m.left_at is not None, **groups})
    agreement = None
    if ag:
        agreement = {
            "version": ag.version,
            "roles": [{"name": member_name(db, r.member_id), "role": r.role_name} for r in ag.roles],
            "reply_hours": ag.reply_hours,
            "meeting_rule": ag.meeting_rule,
            "rules": list(ag.free_rules or []),
        }
    return {
        "team": {"course_name": team.course_name, "project_name": team.project_name},
        "period": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "generated_at": clock.now().isoformat(),
        "notice": NOTICE,
        "agreement": agreement,
        "members": out_members,
    }


def _views(db: Session, rep: Report) -> list[ReportViewOut]:
    seen = {v.member_id: v for v in db.scalars(select(ReportView).where(ReportView.report_id == rep.id))}
    rows = []
    for m in active_members(db, rep.team_id):
        v = seen.get(m.id)
        rows.append(
            ReportViewOut(
                member_id=m.id,
                display_name=m.user.display_name,
                viewed_at=v.viewed_at if v else None,
                objection=v.objection if v else None,
            )
        )
    return rows


def _blocked_reason(db: Session, rep: Report, m: Membership) -> str | None:
    if rep.status != ReportStatus.DRAFT:
        return "이미 제출한 리포트입니다."
    if not m.is_leader:
        return "조장만 제출할 수 있습니다."
    views = _views(db, rep)
    waited = clock.now() - rep.created_at >= timedelta(hours=get_settings().report_view_wait_hours)
    if any(v.viewed_at is None for v in views) and not waited:
        return "팀원 전원이 열람해야 제출할 수 있습니다. 48시간이 지나면 미열람 표시와 함께 제출할 수 있습니다."
    return None


def _out(db: Session, rep: Report, m: Membership) -> ReportOut:
    reason = _blocked_reason(db, rep, m)
    return ReportOut(
        id=rep.id,
        serial_no=rep.serial_no,
        status=rep.status,
        period_from=rep.period_from,
        period_to=rep.period_to,
        created_at=rep.created_at,
        submitted_at=rep.submitted_at,
        content=rep.snapshot,
        views=_views(db, rep) if rep.status == ReportStatus.DRAFT else rep.snapshot.get("views_at_submit", []),
        can_submit=reason is None,
        submit_blocked_reason=reason,
    )


def _load(db: Session, report_id: uuid.UUID, user: User) -> tuple[Report, Membership]:
    rep = db.get(Report, report_id)
    if rep is None:
        raise not_found("리포트를 찾을 수 없습니다.")
    return rep, find_membership(db, rep.team_id, user)


@router.post("/teams/{team_id}/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED, summary="리포트 만들기")
def create_report(body: ReportCreateIn, m: MemberOf, db: DB):
    period_from = body.period_from or m.team.created_at
    period_to = body.period_to or clock.now()
    if period_from > period_to:
        raise AppError(422, "VALIDATION_ERROR", "기간의 시작이 끝보다 늦습니다.")
    serial = f"TPL-{clock.now().astimezone(clock.KST):%y%m%d}-{secrets.token_hex(2).upper()}"
    rep = Report(
        team_id=m.team.id,
        created_by=m.member.id,
        period_from=period_from,
        period_to=period_to,
        serial_no=serial,
        snapshot=_snapshot(db, m.team.id, period_from, period_to),
    )
    db.add(rep)
    db.flush()
    db.add(ReportView(report_id=rep.id, member_id=m.member.id))  # 만든 사람은 본 것으로
    others = [x.user_id for x in active_members(db, m.team.id) if x.id != m.member.id]
    notify(
        db,
        others,
        NotificationKind.REPORT_VIEW_REQUEST,
        "기여 리포트를 확인해 주세요",
        "리포트를 열어 내용을 확인해 주세요. 이의가 있으면 한 줄 의견을 남길 수 있어요.",
        team_id=m.team.id,
        payload={"screen": "report", "report_id": str(rep.id)},
        dedupe=f"report:{rep.id}:created",
    )
    db.commit()
    return _out(db, rep, m)


@router.get("/teams/{team_id}/reports", response_model=list[ReportOut], summary="팀의 리포트 목록")
def list_reports(m: MemberOf, db: DB):
    reps = db.scalars(select(Report).where(Report.team_id == m.team.id).order_by(Report.created_at.desc()))
    return [_out(db, r, m) for r in reps]


@router.get("/reports/{report_id}", response_model=ReportOut, summary="리포트 내용과 열람 현황")
def get_report(report_id: uuid.UUID, user: CurrentUser, db: DB):
    rep, m = _load(db, report_id, user)
    return _out(db, rep, m)


@router.post("/reports/{report_id}/views", response_model=ReportOut, summary="열람 표시와 이의 의견")
def view_report(report_id: uuid.UUID, body: ReportViewIn, user: CurrentUser, db: DB):
    rep, m = _load(db, report_id, user)
    if rep.status != ReportStatus.DRAFT:
        raise conflict("ALREADY_SUBMITTED", "이미 제출한 리포트입니다.")
    v = db.scalar(select(ReportView).where(ReportView.report_id == rep.id, ReportView.member_id == m.member.id))
    if v is None:
        v = ReportView(report_id=rep.id, member_id=m.member.id)
        db.add(v)
    if body.objection is not None:
        v.objection = body.objection
    db.commit()
    return _out(db, rep, m)


@router.post("/reports/{report_id}/submit", response_model=ReportOut, summary="제출 (내용 고정)")
def submit_report(report_id: uuid.UUID, user: CurrentUser, db: DB):
    rep, m = _load(db, report_id, user)
    reason = _blocked_reason(db, rep, m)
    if reason:
        if not m.is_leader:
            raise forbidden(reason)
        raise conflict("CANNOT_SUBMIT", reason)
    views = [v.model_dump(mode="json") for v in _views(db, rep)]
    rep.snapshot = {**rep.snapshot, "views_at_submit": views}
    rep.status = ReportStatus.SUBMITTED
    rep.submitted_at = clock.now()
    db.commit()
    return _out(db, rep, m)


@router.get(
    "/reports/{report_id}/pdf",
    summary="PDF 다운로드",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def report_pdf(report_id: uuid.UUID, user: CurrentUser, db: DB):
    """PDF는 저장하지 않고 요청 때마다 만든다. 제출 후에는 고정된 스냅샷(열람 현황 포함)으로 만들어 항상 같은 내용이 나온다."""
    rep, _ = _load(db, report_id, user)
    if rep.status == ReportStatus.SUBMITTED:
        views = rep.snapshot.get("views_at_submit", [])
    else:
        views = [v.model_dump(mode="json") for v in _views(db, rep)]
    data = render(rep.snapshot, views, rep.serial_no, rep.submitted_at.isoformat() if rep.submitted_at else None)
    filename = f"{rep.serial_no}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
