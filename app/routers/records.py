"""F-05 10초 기록 / F-06 동료 확인 / 결과물 파일"""

import uuid
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.responses import Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import clock
from app.deps import DB, CurrentUser, Membership, MemberOf, find_membership
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    Confirmation,
    Decision,
    NotificationKind,
    Record,
    RecordRevision,
    RecordStatus,
    StoredFile,
    Task,
    TeamMember,
    User,
)
from app.notify import notify
from app.schemas import (
    ConfirmationIn,
    Page,
    RecordCreateIn,
    RecordOut,
    RecordUpdateIn,
    UploadUrlIn,
    UploadUrlOut,
)
from app.views import active_members, record_out

router = APIRouter()

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ppt", ".pptx", ".doc", ".docx",
    ".xls", ".xlsx", ".hwp", ".hwpx", ".zip", ".txt", ".md",
}  # fmt: skip

# ---- 결과물 파일 (DB 저장) -------------------------------------------------------


def _stored(db: Session, key: str) -> StoredFile | None:
    return db.scalar(select(StoredFile).where(StoredFile.key == key))


@router.post("/files/upload-url", response_model=UploadUrlOut, tags=["F-05 기록"], summary="결과물 업로드 주소 발급")
def upload_url(body: UploadUrlIn, user: CurrentUser, db: DB):
    """받은 upload_url로 파일 바이트를 PUT 하면 된다. 파일은 DB에 저장된다."""
    ext = PurePosixPath(body.filename.lower()).suffix
    if ext not in ALLOWED_EXT:
        raise AppError(422, "FILE_TYPE", "올릴 수 없는 파일 형식입니다. 이미지, PDF, 문서, 압축 파일만 올릴 수 있습니다.")
    key = f"{user.id}/{uuid.uuid4().hex}{ext}"
    db.add(StoredFile(key=key, owner_id=user.id, filename=body.filename[:200], content_type=body.content_type[:100]))
    db.commit()
    return UploadUrlOut(upload_url=f"/api/v1/files/{key}", file_key=key)


@router.put("/files/{owner}/{name}", status_code=status.HTTP_204_NO_CONTENT, tags=["F-05 기록"], summary="파일 올리기")
async def put_file(owner: str, name: str, request: Request, user: CurrentUser, db: DB):
    if owner != str(user.id):
        raise forbidden("본인에게 발급된 주소로만 올릴 수 있습니다.")
    f = _stored(db, f"{owner}/{name}")
    if f is None:
        raise not_found("업로드 주소를 다시 받아 주세요.")
    if f.uploaded_at is not None:
        raise conflict("FILE_EXISTS", "이미 올린 파일입니다. 새 주소를 받아 주세요.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE_BYTES:
            raise AppError(413, "FILE_TOO_LARGE", "파일은 10MB까지 올릴 수 있습니다.")
    if not data:
        raise AppError(422, "EMPTY_FILE", "빈 파일입니다.")
    f.data = bytes(data)
    f.size = len(data)
    f.uploaded_at = clock.now()
    db.commit()


@router.get("/files/{owner}/{name}", tags=["F-05 기록"], summary="파일 받기")
def get_file(owner: str, name: str, user: CurrentUser, db: DB):
    key = f"{owner}/{name}"
    allowed = owner == str(user.id)
    if not allowed:
        team_ids = db.scalars(select(Record.team_id).where(Record.file_key == key, Record.status != RecordStatus.WITHDRAWN))
        for tid in team_ids:
            try:
                find_membership(db, tid, user)
                allowed = True
                break
            except AppError:
                pass
    f = _stored(db, key) if allowed else None
    if f is None or f.uploaded_at is None:
        raise not_found("파일을 찾을 수 없습니다.")
    return Response(
        f.data,
        media_type=f.content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(f.filename)}"},
    )


# ---- 기록 -------------------------------------------------------------------


def _load(db: Session, record_id: uuid.UUID, user: User) -> tuple[Record, Membership]:
    r = db.get(Record, record_id)
    if r is None:
        raise not_found("기록을 찾을 수 없습니다.")
    return r, find_membership(db, r.team_id, user)


def _check_task(db: Session, team_id: uuid.UUID, task_id: uuid.UUID | None) -> Task | None:
    if task_id is None:
        return None
    task = db.get(Task, task_id)
    if task is None or task.team_id != team_id:
        raise AppError(422, "VALIDATION_ERROR", "이 팀의 할 일이 아닙니다.")
    return task


def _check_file(db: Session, user: User, key: str | None) -> None:
    if not key:
        return
    f = _stored(db, key)
    if f is None or f.owner_id != user.id or f.uploaded_at is None:
        raise AppError(422, "VALIDATION_ERROR", "올린 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요.")


def _ask_confirm(db: Session, r: Record, author: TeamMember, again: bool = False) -> None:
    others = [x.user_id for x in active_members(db, r.team_id) if x.id != author.id]
    notify(
        db,
        others,
        NotificationKind.CONFIRM_REQUEST,
        "기록을 확인해 주세요" if not again else "고친 기록을 다시 확인해 주세요",
        f"{author.user.display_name}님이 기록을 남겼습니다.",
        team_id=r.team_id,
        payload={"screen": "record", "record_id": str(r.id)},
    )


@router.post("/teams/{team_id}/records", response_model=RecordOut, status_code=status.HTTP_201_CREATED, tags=["F-05 기록"], summary="기록 남기기")
def create_record(body: RecordCreateIn, m: MemberOf, db: DB, user: CurrentUser):
    task = _check_task(db, m.team.id, body.task_id)
    _check_file(db, user, body.file_key)
    if not (body.link_url or body.file_key or body.note):
        raise AppError(422, "VALIDATION_ERROR", "결과물(링크·파일)이나 한 줄 설명 중 하나는 남겨 주세요.")
    r = Record(
        team_id=m.team.id,
        task_id=task.id if task else None,
        author_id=m.member.id,
        type=body.type,
        link_url=str(body.link_url) if body.link_url else None,
        file_key=body.file_key,
        note=body.note,
        is_late=bool(task and task.due_at < clock.now()),
    )
    db.add(r)
    db.flush()
    _ask_confirm(db, r, m.member)
    db.commit()
    return record_out(db, r)


def _encode_cursor(r: Record) -> str:
    return f"{r.created_at.isoformat()}_{r.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, rid = cursor.rsplit("_", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except ValueError as e:
        raise AppError(422, "VALIDATION_ERROR", "잘못된 페이지 정보입니다.") from e


@router.get("/teams/{team_id}/records", response_model=Page[RecordOut], tags=["F-05 기록"], summary="기록 피드")
def list_records(m: MemberOf, db: DB, task_id: uuid.UUID | None = None, cursor: str | None = None, limit: int = 20):
    limit = max(1, min(limit, 50))
    q = select(Record).where(Record.team_id == m.team.id, Record.status != RecordStatus.WITHDRAWN)
    if task_id:
        q = q.where(Record.task_id == task_id)
    if cursor:
        ts, rid = _decode_cursor(cursor)
        q = q.where(or_(Record.created_at < ts, and_(Record.created_at == ts, Record.id < rid)))
    rows = list(db.scalars(q.order_by(Record.created_at.desc(), Record.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page[RecordOut](items=[record_out(db, r) for r in rows], next_cursor=_encode_cursor(rows[-1]) if more else None)


@router.patch("/records/{record_id}", response_model=RecordOut, tags=["F-05 기록"], summary="기록 수정 (이력 저장)")
def update_record(record_id: uuid.UUID, body: RecordUpdateIn, user: CurrentUser, db: DB):
    r, m = _load(db, record_id, user)
    if r.author_id != m.member.id:
        raise forbidden("본인 기록만 고칠 수 있습니다.")
    if r.status == RecordStatus.WITHDRAWN:
        raise conflict("WITHDRAWN", "철회한 기록은 고칠 수 없습니다.")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return record_out(db, r)
    db.add(
        RecordRevision(
            record_id=r.id,
            before={
                "task_id": str(r.task_id) if r.task_id else None,
                "type": r.type.value,
                "link_url": r.link_url,
                "file_key": r.file_key,
                "note": r.note,
            },
        )
    )
    if "task_id" in changes:
        task = _check_task(db, r.team_id, body.task_id)
        r.task_id = task.id if task else None
    if "type" in changes and body.type:
        r.type = body.type
    if "link_url" in changes:
        r.link_url = str(body.link_url) if body.link_url else None
    if "file_key" in changes:
        _check_file(db, user, body.file_key)
        r.file_key = body.file_key
    if "note" in changes:
        r.note = body.note or None
    if not (r.link_url or r.file_key or r.note):
        raise AppError(422, "VALIDATION_ERROR", "결과물(링크·파일)이나 한 줄 설명 중 하나는 남겨 주세요.")
    # 고치면 이전 확인은 더 이상 유효하지 않다
    was_confirmed = r.status == RecordStatus.CONFIRMED
    for c in r.confirmations:
        c.valid = False
    r.status = RecordStatus.RECHECK if was_confirmed else RecordStatus.PENDING
    db.flush()
    _ask_confirm(db, r, m.member, again=True)
    db.commit()
    db.refresh(r)
    return record_out(db, r)


@router.post("/records/{record_id}/withdraw", response_model=RecordOut, tags=["F-06 동료 확인"], summary="기록 철회")
def withdraw_record(record_id: uuid.UUID, user: CurrentUser, db: DB):
    r, m = _load(db, record_id, user)
    if r.author_id != m.member.id:
        raise forbidden("본인 기록만 철회할 수 있습니다.")
    r.status = RecordStatus.WITHDRAWN
    db.commit()
    return record_out(db, r)


@router.get("/teams/{team_id}/inbox", response_model=list[RecordOut], tags=["F-06 동료 확인"], summary="내가 확인할 기록")
def inbox(m: MemberOf, db: DB):
    mine = select(Confirmation.record_id).where(Confirmation.reviewer_id == m.member.id, Confirmation.valid.is_(True))
    rows = db.scalars(
        select(Record)
        .where(
            Record.team_id == m.team.id,
            Record.author_id != m.member.id,
            Record.status.in_([RecordStatus.PENDING, RecordStatus.RECHECK, RecordStatus.UNCONFIRMED]),
            Record.id.not_in(mine),
        )
        .order_by(Record.created_at)
    )
    return [record_out(db, r) for r in rows]


@router.post("/records/{record_id}/confirmations", response_model=RecordOut, tags=["F-06 동료 확인"], summary="확인 또는 거절")
def confirm_record(record_id: uuid.UUID, body: ConfirmationIn, user: CurrentUser, db: DB):
    r, m = _load(db, record_id, user)
    if r.author_id == m.member.id:
        raise conflict("SELF_CONFIRM", "자기 기록은 확인할 수 없습니다.")
    if r.status == RecordStatus.WITHDRAWN:
        raise conflict("WITHDRAWN", "철회된 기록입니다.")
    if body.decision == Decision.REJECT and not (body.reason_type and body.reason_text):
        raise AppError(422, "VALIDATION_ERROR", "거절할 때는 사유 종류와 한 줄 사유를 적어 주세요.")
    # 같은 사람이 다시 판단하면 이전 판단은 무효
    for c in r.confirmations:
        if c.reviewer_id == m.member.id and c.valid:
            c.valid = False
    c = Confirmation(
        record_id=r.id,
        reviewer_id=m.member.id,
        decision=body.decision,
        reason_type=body.reason_type if body.decision == Decision.REJECT else None,
        reason_text=body.reason_text if body.decision == Decision.REJECT else None,
    )
    db.add(c)
    db.flush()
    db.refresh(r)
    valid = [x for x in r.confirmations if x.valid]
    # 거절이 하나라도 있으면 보류(팀 논의 필요), 아니면 확인 1건 이상이면 확인됨
    if any(x.decision == Decision.REJECT for x in valid):
        r.status = RecordStatus.ON_HOLD
    elif valid:
        r.status = RecordStatus.CONFIRMED
    author = db.get(TeamMember, r.author_id)
    if body.decision == Decision.REJECT:
        notify(
            db,
            [author.user_id],
            NotificationKind.RECORD_REJECTED,
            "기록이 보류됐어요",
            f"{m.member.user.display_name}: {body.reason_text}",
            team_id=r.team_id,
            payload={"screen": "record", "record_id": str(r.id)},
        )
    else:
        _warn_mutual_confirm(db, r.team_id)
    db.commit()
    db.refresh(r)
    return record_out(db, r)


def _warn_mutual_confirm(db: Session, team_id: uuid.UUID) -> None:
    """최근 확인 10건 중 8건 이상이 같은 두 사람 사이에서만 오가면 팀 전체에 알린다."""
    members = active_members(db, team_id)
    if len(members) < 3:
        return
    recent = db.execute(
        select(Confirmation.reviewer_id, Record.author_id)
        .join(Record, Record.id == Confirmation.record_id)
        .where(Record.team_id == team_id, Confirmation.valid.is_(True), Confirmation.decision == Decision.CONFIRM)
        .order_by(Confirmation.created_at.desc())
        .limit(10)
    ).all()
    if len(recent) < 10:
        return
    pairs: dict[frozenset, int] = {}
    for reviewer, author in recent:
        k = frozenset((reviewer, author))
        pairs[k] = pairs.get(k, 0) + 1
    pair, count = max(pairs.items(), key=lambda kv: kv[1])
    if count < 8:
        return
    key = "-".join(sorted(str(x) for x in pair))
    notify(
        db,
        [x.user_id for x in members],
        NotificationKind.MUTUAL_CONFIRM_WARNING,
        "다른 팀원도 기록을 확인해 주세요",
        "최근 확인이 두 사람 사이에서만 오가고 있어요. 다른 팀원의 확인이 있으면 기록의 신뢰도가 높아집니다.",
        team_id=team_id,
        payload={"screen": "inbox", "team_id": str(team_id)},
        dedupe=f"mutual:{team_id}:{key}",
    )
