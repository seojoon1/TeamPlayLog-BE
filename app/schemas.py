"""API 요청·응답 형식 (기능명세서 8장). 모든 시각은 ISO 8601 UTC."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models import (
    AgreementStatus,
    Decision,
    MemberRole,
    NotificationKind,
    RecordStatus,
    RecordType,
    RejectReason,
    ReportStatus,
    TaskStatus,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Str = lambda lo, hi: Annotated[str, StringConstraints(strip_whitespace=True, min_length=lo, max_length=hi)]  # noqa: E731


class ErrorOut(BaseModel):
    code: str
    message: str


# ---- F-01 계정 ---------------------------------------------------------------


class SignupIn(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=72)]
    display_name: Str(2, 12)
    department: Str(1, 50) | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    department: str | None


class DeviceIn(BaseModel):
    fcm_token: Str(10, 300)
    platform: str = "android"


# ---- F-02 팀 ----------------------------------------------------------------


class TeamCreateIn(BaseModel):
    course_name: Str(1, 50)
    project_name: Str(1, 50)
    final_due_at: datetime
    max_members: Annotated[int, Field(ge=2, le=8)] = 6


class JoinIn(BaseModel):
    # 소문자로 입력해도 받는다 (대문자로 바꿔서 찾는다)
    invite_code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z0-9]{6}$")]


class MemberOut(BaseModel):
    member_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str  # 탈퇴한 사용자는 '탈퇴한 팀원'
    role: MemberRole
    left: bool


class TeamSummaryOut(BaseModel):
    id: uuid.UUID
    course_name: str
    project_name: str
    final_due_at: datetime
    member_count: int
    my_role: MemberRole


class TeamOut(TeamSummaryOut):
    max_members: int
    invite_code: str | None  # 조장에게만 보인다
    invite_expires_at: datetime | None
    members: list[MemberOut]
    agreement_confirmed: bool
    tasks_total: int
    tasks_done: int


class LeaderIn(BaseModel):
    member_id: uuid.UUID


# ---- F-03 역할 약속 -------------------------------------------------------------


class AgreementRoleIn(BaseModel):
    member_id: uuid.UUID
    role_name: Str(1, 30)


class AgreementTaskIn(BaseModel):
    assignee_member_id: uuid.UUID
    title: Str(1, 40)
    due_at: datetime


class AgreementIn(BaseModel):
    roles: list[AgreementRoleIn]
    tasks: list[AgreementTaskIn] = []
    reply_hours: Annotated[int, Field()] = 24
    meeting_rule: Str(1, 50) | None = None
    free_rules: Annotated[list[Str(1, 60)], Field(max_length=3)] = []


class ConsentIn(BaseModel):
    agreed: bool
    change_request: Str(1, 200) | None = None


class AgreementRoleOut(ORM):
    member_id: uuid.UUID
    role_name: str


class AgreementTaskOut(ORM):
    assignee_member_id: uuid.UUID
    title: str
    due_at: datetime


class ConsentOut(BaseModel):
    member_id: uuid.UUID
    display_name: str
    agreed: bool | None  # None = 아직 응답 안 함
    change_request: str | None  # 조장과 본인에게만
    responded_at: datetime | None


class AgreementOut(BaseModel):
    id: uuid.UUID
    version: int
    status: AgreementStatus
    reply_hours: int
    meeting_rule: str | None
    free_rules: list[str]
    roles: list[AgreementRoleOut]
    tasks: list[AgreementTaskOut]
    consents: list[ConsentOut]
    confirmed_at: datetime | None


class AgreementHistoryOut(BaseModel):
    current: AgreementOut | None
    versions: list[AgreementOut]


# ---- F-04 할 일 ---------------------------------------------------------------


class TaskCreateIn(BaseModel):
    title: Str(1, 40)
    due_at: datetime
    assignee_member_ids: list[uuid.UUID] = []
    for_everyone: bool = False
    memo: Str(1, 500) | None = None


class TaskUpdateIn(BaseModel):
    title: Str(1, 40) | None = None
    due_at: datetime | None = None
    assignee_member_ids: list[uuid.UUID] | None = None
    for_everyone: bool | None = None
    memo: Str(0, 500) | None = None
    status: TaskStatus | None = None  # OVERDUE는 서버만 바꾼다


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    due_at: datetime
    status: TaskStatus
    memo: str | None
    for_everyone: bool
    assignees: list[MemberOut]
    record_count: int
    confirmed_record_count: int
    completed_at: datetime | None


class TaskEventOut(ORM):
    kind: str
    actor_member_id: uuid.UUID
    before: dict
    after: dict
    created_at: datetime


class TaskDetailOut(TaskOut):
    events: list[TaskEventOut]


class BoardOut(BaseModel):
    final_due_at: datetime
    d_day: int
    tasks_total: int
    tasks_done: int
    agreement_confirmed: bool
    tasks: list[TaskOut]


# ---- F-05 기록 / F-06 확인 --------------------------------------------------------


class UploadUrlIn(BaseModel):
    filename: Str(1, 200)
    content_type: Str(1, 100)
    size: Annotated[int, Field(gt=0, le=10 * 1024 * 1024)]


class UploadUrlOut(BaseModel):
    upload_url: str
    file_key: str


class RecordCreateIn(BaseModel):
    task_id: uuid.UUID | None = None
    type: RecordType
    link_url: AnyHttpUrl | None = None
    file_key: Str(1, 200) | None = None
    note: Str(1, 60) | None = None


class RecordUpdateIn(BaseModel):
    task_id: uuid.UUID | None = None
    type: RecordType | None = None
    link_url: AnyHttpUrl | None = None
    file_key: Str(1, 200) | None = None
    note: Str(0, 60) | None = None


class ConfirmationIn(BaseModel):
    decision: Decision
    reason_type: RejectReason | None = None  # REJECT이면 필수
    reason_text: Str(1, 100) | None = None  # REJECT이면 필수


class ConfirmationOut(BaseModel):
    reviewer_id: uuid.UUID
    reviewer_name: str
    decision: Decision
    reason_type: RejectReason | None
    reason_text: str | None
    valid: bool
    created_at: datetime


class RecordOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    task_title: str | None
    author_id: uuid.UUID
    author_name: str
    type: RecordType
    link_url: str | None
    file_key: str | None
    note: str | None
    has_output: bool  # False면 '설명만 있음'
    is_late: bool  # 사후 기록
    status: RecordStatus
    confirmations: list[ConfirmationOut]
    revision_count: int
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


# ---- F-07 알림 ----------------------------------------------------------------


class NotificationOut(ORM):
    id: uuid.UUID
    team_id: uuid.UUID | None
    kind: NotificationKind
    title: str
    body: str
    payload: dict
    created_at: datetime
    read_at: datetime | None


# ---- F-08 기여 리포트 ------------------------------------------------------------


class ReportCreateIn(BaseModel):
    period_from: datetime | None = None  # 기본값: 팀 생성일
    period_to: datetime | None = None  # 기본값: 지금


class ReportViewIn(BaseModel):
    objection: Str(1, 100) | None = None


class ReportViewOut(BaseModel):
    member_id: uuid.UUID
    display_name: str
    viewed_at: datetime | None
    objection: str | None


class ReportOut(BaseModel):
    id: uuid.UUID
    serial_no: str
    status: ReportStatus
    period_from: datetime
    period_to: datetime
    created_at: datetime
    submitted_at: datetime | None
    content: dict  # 팀원별·유형별 기록 묶음 (합계·순위 없음)
    views: list[ReportViewOut]
    can_submit: bool
    submit_blocked_reason: str | None
