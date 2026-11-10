import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError, forbidden, not_found
from app.models import MemberRole, Team, TeamMember, User
from app.security import read_token

DB = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def current_user(db: DB, cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> User:
    if cred is None:
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    user_id = read_token(cred.credentials, "access")
    user = db.get(User, user_id) if user_id else None
    if user is None or user.deleted_at is not None:
        raise AppError(401, "UNAUTHORIZED", "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass
class Membership:
    team: Team
    member: TeamMember

    @property
    def is_leader(self) -> bool:
        return self.member.role == MemberRole.LEADER


def find_membership(db: Session, team_id: uuid.UUID, user: User) -> Membership:
    team = db.get(Team, team_id)
    if team is None:
        raise not_found("팀을 찾을 수 없습니다.")
    member = db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == user.id, TeamMember.left_at.is_(None)
        )
    )
    if member is None:
        # 다른 팀의 존재 여부를 드러내지 않도록 404로 통일
        raise not_found("팀을 찾을 수 없습니다.")
    return Membership(team, member)


def team_member(team_id: uuid.UUID, db: DB, user: CurrentUser) -> Membership:
    return find_membership(db, team_id, user)


def team_leader(m: Annotated[Membership, Depends(team_member)]) -> Membership:
    if not m.is_leader:
        raise forbidden("조장만 할 수 있습니다.")
    return m


MemberOf = Annotated[Membership, Depends(team_member)]
LeaderOf = Annotated[Membership, Depends(team_leader)]
