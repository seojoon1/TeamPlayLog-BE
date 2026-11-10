"""F-01 계정"""

from datetime import timedelta

from fastapi import APIRouter, status
from sqlalchemy import select

from app.config import get_settings
from app.db import utcnow
from app.deps import DB, CurrentUser
from app.errors import AppError, conflict
from app.models import Device, TeamMember, User
from app.schemas import DeviceIn, LoginIn, RefreshIn, SignupIn, TokenOut, UserOut
from app.security import hash_password, make_tokens, read_token, verify_password

router = APIRouter(tags=["F-01 계정"])


def _tokens(user: User) -> TokenOut:
    access, refresh = make_tokens(user.id)
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/auth/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED, summary="가입")
def signup(body: SignupIn, db: DB):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise conflict("EMAIL_TAKEN", "이미 가입된 이메일입니다.")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        department=body.department,
    )
    db.add(user)
    db.commit()
    return _tokens(user)


@router.post("/auth/login", response_model=TokenOut, summary="로그인")
def login(body: LoginIn, db: DB):
    s = get_settings()
    user = db.scalar(select(User).where(User.email == body.email.lower(), User.deleted_at.is_(None)))
    bad = AppError(401, "BAD_CREDENTIALS", "이메일 또는 비밀번호가 맞지 않습니다.")
    if user is None:
        raise bad
    now = utcnow()
    if user.locked_until and user.locked_until > now:
        minutes = int((user.locked_until - now).total_seconds() // 60) + 1
        raise AppError(429, "LOGIN_LOCKED", f"로그인 시도가 많아 잠시 막혔습니다. {minutes}분 뒤에 다시 시도해 주세요.")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= s.login_fail_limit:
            user.failed_logins = 0
            user.locked_until = now + timedelta(minutes=s.login_lock_minutes)
        db.commit()
        raise bad
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    return _tokens(user)


@router.post("/auth/refresh", response_model=TokenOut, summary="액세스 토큰 재발급")
def refresh(body: RefreshIn, db: DB):
    user_id = read_token(body.refresh_token, "refresh")
    user = db.get(User, user_id) if user_id else None
    if user is None or user.deleted_at is not None:
        raise AppError(401, "UNAUTHORIZED", "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    return _tokens(user)


@router.get("/me", response_model=UserOut, summary="내 정보")
def me(user: CurrentUser):
    return user


@router.put("/me/device", status_code=status.HTTP_204_NO_CONTENT, summary="FCM 토큰 등록·갱신")
def register_device(body: DeviceIn, user: CurrentUser, db: DB):
    device = db.scalar(select(Device).where(Device.fcm_token == body.fcm_token))
    if device is None:
        db.add(Device(user_id=user.id, fcm_token=body.fcm_token, platform=body.platform))
    else:
        device.user_id = user.id  # 같은 기기에서 다른 계정으로 로그인한 경우
        device.last_seen_at = utcnow()
    db.commit()


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="탈퇴")
def delete_me(user: CurrentUser, db: DB):
    """개인정보를 지우고 팀 기록은 남긴다. 팀에는 '탈퇴한 팀원'으로 보인다."""
    now = utcnow()
    for m in db.scalars(select(TeamMember).where(TeamMember.user_id == user.id, TeamMember.left_at.is_(None))):
        m.left_at = now
    for d in db.scalars(select(Device).where(Device.user_id == user.id)):
        db.delete(d)
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.password_hash = "!"
    user.display_name = "탈퇴한 팀원"
    user.department = None
    user.deleted_at = now
    db.commit()
