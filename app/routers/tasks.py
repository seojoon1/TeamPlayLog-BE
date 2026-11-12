"""F-04 할 일 보드"""

import uuid

from fastapi import APIRouter, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import clock
from app.deps import DB, CurrentUser, Membership, MemberOf, find_membership
from app.errors import AppError, conflict, forbidden, not_found
from app.models import (
    AgreementTask,
    NotificationKind,
    Record,
    RecordStatus,
    Task,
    TaskAssignee,
    TaskEvent,
    TaskStatus,
    TeamMember,
    User,
)
from app.notify import notify
from app.schemas import BoardOut, TaskCreateIn, TaskDetailOut, TaskEventOut, TaskOut, TaskUpdateIn
from app.views import active_members, assignee_ids, confirmed_agreement, d_day, refresh_overdue, task_counts, task_out

router = APIRouter(tags=["F-04 할 일"])


def _load(db: Session, task_id: uuid.UUID, user: User) -> tuple[Task, Membership]:
    task = db.get(Task, task_id)
    if task is None:
        raise not_found("할 일을 찾을 수 없습니다.")
    return task, find_membership(db, task.team_id, user)


def _check_assignees(db: Session, m: Membership, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    members = {x.id for x in active_members(db, m.team.id)}
    ids = list(dict.fromkeys(ids))
    if any(i not in members for i in ids):
        raise AppError(422, "VALIDATION_ERROR", "현재 팀원이 아닌 사람을 담당자로 정할 수 없습니다.")
    return ids


def _can_edit(db: Session, task: Task, m: Membership) -> bool:
    return m.is_leader or task.for_everyone or m.member.id in assignee_ids(db, task.id)


def _notify_assigned(db: Session, task: Task, member_ids: list[uuid.UUID], actor: TeamMember) -> None:
    targets = [db.get(TeamMember, i) for i in member_ids if i != actor.id]
    notify(
        db,
        [t.user_id for t in targets if t],
        NotificationKind.TASK_ASSIGNED,
        "새 할 일이 배정됐어요",
        f"‘{task.title}’ 담당이 되었습니다.",
        team_id=task.team_id,
        payload={"screen": "task", "task_id": str(task.id)},
    )


@router.get("/teams/{team_id}/tasks", response_model=BoardOut, summary="팀 보드 (할 일 목록)")
def board(m: MemberOf, db: DB, mine: bool = False):
    refresh_overdue(db, m.team.id)
    db.commit()
    q = select(Task).where(Task.team_id == m.team.id).order_by(Task.due_at, Task.created_at)
    if mine:
        q = q.where(
            (Task.for_everyone.is_(True)) | Task.id.in_(select(TaskAssignee.task_id).where(TaskAssignee.member_id == m.member.id))
        )
    total, done = task_counts(db, m.team.id)
    return BoardOut(
        final_due_at=m.team.final_due_at,
        d_day=d_day(m.team),
        tasks_total=total,
        tasks_done=done,
        agreement_confirmed=confirmed_agreement(db, m.team.id) is not None,
        tasks=[task_out(db, t) for t in db.scalars(q)],
    )


@router.post("/teams/{team_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED, summary="할 일 추가")
def create_task(body: TaskCreateIn, m: MemberOf, db: DB):
    """팀원은 본인이 담당인 할 일만, 조장은 누구 담당이든 추가할 수 있다."""
    ids = _check_assignees(db, m, body.assignee_member_ids or ([] if body.for_everyone else [m.member.id]))
    if not m.is_leader and (body.for_everyone or ids != [m.member.id]):
        raise forbidden("팀원은 본인이 담당하는 할 일만 추가할 수 있습니다.")
    task = Task(
        team_id=m.team.id,
        title=body.title,
        due_at=body.due_at,
        memo=body.memo,
        for_everyone=body.for_everyone,
        status=TaskStatus.OVERDUE if body.due_at < clock.now() else TaskStatus.TODO,
    )
    db.add(task)
    db.flush()
    for i in ids:
        db.add(TaskAssignee(task_id=task.id, member_id=i))
    _notify_assigned(db, task, ids, m.member)
    db.commit()
    return task_out(db, task)


@router.get("/tasks/{task_id}", response_model=TaskDetailOut, summary="할 일 상세 (변경 이력 포함)")
def get_task(task_id: uuid.UUID, user: CurrentUser, db: DB):
    task, _ = _load(db, task_id, user)
    refresh_overdue(db, task.team_id)
    db.commit()
    events = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.created_at))
    return TaskDetailOut(**task_out(db, task).model_dump(), events=[TaskEventOut.model_validate(e) for e in events])


@router.patch("/tasks/{task_id}", response_model=TaskOut, summary="할 일 수정·상태 변경")
def update_task(task_id: uuid.UUID, body: TaskUpdateIn, user: CurrentUser, db: DB):
    """완료(DONE)는 연결된 기록이 1건 이상일 때만 가능하다. 마감 지남(OVERDUE)은 서버만 바꾼다."""
    task, m = _load(db, task_id, user)
    if not _can_edit(db, task, m):
        raise forbidden("담당자나 조장만 고칠 수 있습니다.")
    changes = body.model_dump(exclude_unset=True)
    if not m.is_leader and ({"assignee_member_ids", "for_everyone"} & changes.keys()):
        raise forbidden("담당자는 조장만 바꿀 수 있습니다.")

    def event(kind: str, before, after):
        db.add(TaskEvent(task_id=task.id, actor_member_id=m.member.id, kind=kind, before=before, after=after))

    if "title" in changes and body.title:
        task.title = body.title
    if "memo" in changes:
        task.memo = body.memo or None
    if "due_at" in changes and body.due_at and body.due_at != task.due_at:
        event("DUE_CHANGED", {"due_at": task.due_at.isoformat()}, {"due_at": body.due_at.isoformat()})
        task.due_at = body.due_at
        if task.status == TaskStatus.OVERDUE and body.due_at > clock.now():
            task.status = TaskStatus.TODO
    if "assignee_member_ids" in changes or "for_everyone" in changes:
        before = assignee_ids(db, task.id)
        after = _check_assignees(db, m, body.assignee_member_ids if body.assignee_member_ids is not None else before)
        everyone = body.for_everyone if body.for_everyone is not None else task.for_everyone
        if not after and not everyone:
            raise AppError(422, "VALIDATION_ERROR", "담당자를 한 명 이상 정하거나 '전원'으로 정해 주세요.")
        if set(before) != set(after) or everyone != task.for_everyone:
            event(
                "ASSIGNEES_CHANGED",
                {"member_ids": [str(i) for i in before], "for_everyone": task.for_everyone},
                {"member_ids": [str(i) for i in after], "for_everyone": everyone},
            )
            db.execute(delete(TaskAssignee).where(TaskAssignee.task_id == task.id))
            for i in after:
                db.add(TaskAssignee(task_id=task.id, member_id=i))
            task.for_everyone = everyone
            _notify_assigned(db, task, [i for i in after if i not in before], m.member)
    if body.status == TaskStatus.OVERDUE:
        raise AppError(422, "VALIDATION_ERROR", "‘마감 지남’은 직접 정할 수 없습니다.")
    if "status" in changes and body.status and body.status != task.status:
        if body.status == TaskStatus.DONE:
            records = db.scalar(
                select(func.count()).select_from(Record).where(Record.task_id == task.id, Record.status != RecordStatus.WITHDRAWN)
            )
            if not records:
                raise conflict("NEEDS_RECORD", "완료하려면 이 할 일에 기록을 1건 이상 남겨 주세요.")
            task.completed_at = clock.now()
        else:
            task.completed_at = None
        event("STATUS_CHANGED", {"status": task.status.value}, {"status": body.status.value})
        new_status = body.status
        if new_status in (TaskStatus.TODO, TaskStatus.DOING) and task.due_at < clock.now():
            new_status = TaskStatus.OVERDUE
        task.status = new_status
    db.commit()
    return task_out(db, task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="할 일 지우기")
def delete_task(task_id: uuid.UUID, user: CurrentUser, db: DB):
    """잘못 만든 할 일을 지운다. 기록이 달린 할 일은 기여의 근거를 지키기 위해 지울 수 없다."""
    task, m = _load(db, task_id, user)
    if not _can_edit(db, task, m):
        raise forbidden("담당자나 조장만 지울 수 있습니다.")
    live = db.scalar(
        select(func.count()).select_from(Record).where(Record.task_id == task.id, Record.status != RecordStatus.WITHDRAWN)
    )
    if live:
        raise conflict("HAS_RECORDS", "기록이 남아 있는 할 일은 지울 수 없어요. 기여의 근거가 사라지기 때문이에요.")
    db.execute(update(Record).where(Record.task_id == task.id).values(task_id=None))
    db.execute(update(AgreementTask).where(AgreementTask.created_task_id == task.id).values(created_task_id=None))
    db.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
    db.delete(task)
    db.commit()
