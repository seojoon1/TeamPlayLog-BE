"""데이터 모델 (기능명세서 7장). 기록·약속·확인은 지우지 않고 상태와 이력으로만 바꾼다."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


def _enum(e: type[enum.Enum]) -> Enum:
    # DB에는 값 문자열로 저장한다 (SQLite·PostgreSQL 공통)
    return Enum(e, native_enum=False, length=30, values_callable=lambda x: [m.value for m in x])


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ---- 열거형 -----------------------------------------------------------------


class MemberRole(str, enum.Enum):
    LEADER = "LEADER"
    MEMBER = "MEMBER"


class AgreementStatus(str, enum.Enum):
    DRAFT = "DRAFT"  # 조장이 작성 중
    PENDING = "PENDING"  # 동의 대기
    CONFIRMED = "CONFIRMED"  # 전원 동의로 확정
    SUPERSEDED = "SUPERSEDED"  # 새 버전으로 대체됨


class TaskStatus(str, enum.Enum):
    TODO = "TODO"
    DOING = "DOING"
    DONE = "DONE"
    OVERDUE = "OVERDUE"


class RecordType(str, enum.Enum):
    OUTPUT = "OUTPUT"  # 산출물
    MEETING = "MEETING"  # 회의 참여
    COORDINATION = "COORDINATION"  # 조율·소통
    REVIEW = "REVIEW"  # 검토·피드백


class RecordStatus(str, enum.Enum):
    PENDING = "PENDING"  # 확인 대기
    CONFIRMED = "CONFIRMED"  # 확인됨
    ON_HOLD = "ON_HOLD"  # 거절되어 보류
    RECHECK = "RECHECK"  # 확인 후 수정되어 재확인 필요
    UNCONFIRMED = "UNCONFIRMED"  # 72시간 동안 아무도 확인 안 함
    WITHDRAWN = "WITHDRAWN"  # 작성자가 철회


class Decision(str, enum.Enum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


class RejectReason(str, enum.Enum):
    NO_OUTPUT = "NO_OUTPUT"  # 결과물 없음
    MISMATCH = "MISMATCH"  # 내용과 다름
    OTHER = "OTHER"


class ReportStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class NotificationKind(str, enum.Enum):
    AGREEMENT_CONSENT = "AGREEMENT_CONSENT"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    CONFIRM_REQUEST = "CONFIRM_REQUEST"
    RECORD_REJECTED = "RECORD_REJECTED"
    MUTUAL_CONFIRM_WARNING = "MUTUAL_CONFIRM_WARNING"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    REPORT_VIEW_REQUEST = "REPORT_VIEW_REQUEST"


# ---- 계정 -------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(12))
    department: Mapped[str | None] = mapped_column(String(50))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Device(TimestampMixin, Base):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    fcm_token: Mapped[str] = mapped_column(String(300), unique=True)
    platform: Mapped[str] = mapped_column(String(10), default="android")
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 팀 ---------------------------------------------------------------------


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    course_name: Mapped[str] = mapped_column(String(50))
    project_name: Mapped[str] = mapped_column(String(50))
    final_due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    max_members: Mapped[int] = mapped_column(Integer, default=6)
    invite_code: Mapped[str] = mapped_column(String(6), unique=True, index=True)
    invite_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    members: Mapped[list["TeamMember"]] = relationship(back_populates="team")


class TeamMember(TimestampMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[MemberRole] = mapped_column(_enum(MemberRole), default=MemberRole.MEMBER)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


# ---- 역할 약속 ----------------------------------------------------------------


class Agreement(TimestampMixin, Base):
    __tablename__ = "agreements"
    __table_args__ = (UniqueConstraint("team_id", "version"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[AgreementStatus] = mapped_column(_enum(AgreementStatus), default=AgreementStatus.DRAFT)
    reply_hours: Mapped[int] = mapped_column(Integer, default=24)  # 6·12·24·48
    meeting_rule: Mapped[str | None] = mapped_column(String(50))
    free_rules: Mapped[list[str]] = mapped_column(JSON, default=list)  # 최대 3줄
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    roles: Mapped[list["AgreementRole"]] = relationship(cascade="all, delete-orphan", order_by="AgreementRole.position")
    tasks: Mapped[list["AgreementTask"]] = relationship(cascade="all, delete-orphan", order_by="AgreementTask.due_at")
    consents: Mapped[list["AgreementConsent"]] = relationship(cascade="all, delete-orphan")


class AgreementRole(TimestampMixin, Base):
    """팀원별 역할명. 명세서 7장에는 없던 테이블 (agreements의 하위)."""

    __tablename__ = "agreement_roles"

    agreement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agreements.id"), index=True)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    role_name: Mapped[str] = mapped_column(String(30))
    position: Mapped[int] = mapped_column(Integer, default=0)


class AgreementTask(TimestampMixin, Base):
    """약속에 적은 할 일. 확정되면 tasks로 만들어진다. 명세서 7장에는 없던 테이블."""

    __tablename__ = "agreement_tasks"

    agreement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agreements.id"), index=True)
    assignee_member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    title: Mapped[str] = mapped_column(String(40))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))


class AgreementConsent(TimestampMixin, Base):
    __tablename__ = "agreement_consents"
    __table_args__ = (UniqueConstraint("agreement_id", "member_id"),)

    agreement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agreements.id"), index=True)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    agreed: Mapped[bool] = mapped_column(Boolean)
    change_request: Mapped[str | None] = mapped_column(String(200))
    responded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 할 일 -------------------------------------------------------------------


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    title: Mapped[str] = mapped_column(String(40))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus), default=TaskStatus.TODO)
    memo: Mapped[str | None] = mapped_column(Text)
    for_everyone: Mapped[bool] = mapped_column(Boolean, default=False)  # 담당 '전원'
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agreements.id"))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    assignees: Mapped[list["TaskAssignee"]] = relationship(cascade="all, delete-orphan")


class TaskAssignee(Base):
    __tablename__ = "task_assignees"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"), primary_key=True)


class TaskEvent(TimestampMixin, Base):
    """할 일의 마감 변경·담당 변경 이력. 명세서 7장에는 없던 테이블."""

    __tablename__ = "task_events"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    actor_member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    kind: Mapped[str] = mapped_column(String(30))  # DUE_CHANGED, ASSIGNEES_CHANGED, STATUS_CHANGED
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)


# ---- 기록과 확인 ---------------------------------------------------------------


class Record(TimestampMixin, Base):
    __tablename__ = "records"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"), index=True)
    type: Mapped[RecordType] = mapped_column(_enum(RecordType))
    link_url: Mapped[str | None] = mapped_column(String(500))
    file_key: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(60))
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)  # 사후 기록
    status: Mapped[RecordStatus] = mapped_column(_enum(RecordStatus), default=RecordStatus.PENDING, index=True)

    confirmations: Mapped[list["Confirmation"]] = relationship(order_by="Confirmation.created_at")
    revisions: Mapped[list["RecordRevision"]] = relationship(order_by="RecordRevision.created_at")

    @property
    def has_output(self) -> bool:
        return bool(self.link_url or self.file_key)


class RecordRevision(TimestampMixin, Base):
    __tablename__ = "record_revisions"

    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("records.id"), index=True)
    before: Mapped[dict] = mapped_column(JSON)


class Confirmation(TimestampMixin, Base):
    __tablename__ = "confirmations"

    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("records.id"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"), index=True)
    decision: Mapped[Decision] = mapped_column(_enum(Decision))
    reason_type: Mapped[RejectReason | None] = mapped_column(_enum(RejectReason))
    reason_text: Mapped[str | None] = mapped_column(String(100))
    # 확인 후 기록이 수정되면 이 확인은 더 이상 유효하지 않다
    valid: Mapped[bool] = mapped_column(Boolean, default=True)


# ---- 알림 -------------------------------------------------------------------


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("dedupe_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[NotificationKind] = mapped_column(_enum(NotificationKind))
    title: Mapped[str] = mapped_column(String(60))
    body: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 앱에서 이동할 화면 정보
    dedupe_key: Mapped[str | None] = mapped_column(String(200))  # 같은 알림 중복 방지
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---- 기여 리포트 ---------------------------------------------------------------


class Report(TimestampMixin, Base):
    __tablename__ = "reports"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    period_from: Mapped[datetime] = mapped_column(UTCDateTime)
    period_to: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[ReportStatus] = mapped_column(_enum(ReportStatus), default=ReportStatus.DRAFT)
    serial_no: Mapped[str] = mapped_column(String(20), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 생성 시점 내용. 제출 후 고정
    pdf_key: Mapped[str | None] = mapped_column(String(200))
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    views: Mapped[list["ReportView"]] = relationship()


class ReportView(TimestampMixin, Base):
    __tablename__ = "report_views"
    __table_args__ = (UniqueConstraint("report_id", "member_id"),)

    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_members.id"))
    viewed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    objection: Mapped[str | None] = mapped_column(String(100))
